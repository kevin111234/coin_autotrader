"""
주문 상태 레지스트리(파일 영속) + 폴링 동기화
- 목표:
  * 엔트리 주문과 OCO(주문 리스트) 식별자 저장
  * 중복 OCO 부착 방지 (심볼 단위 Active OCO 존재 시 차단)
  * 재시작/크래시 후 복구 (파일 로드)
  * 폴링으로 상태 동기화 (User Data Stream 미사용 시 대체)
  * '재부착 필요' 신호 제공 (엔트리 체결 완료 & OCO 없음)

설계:
  - group_id: 엔트리 주문의 clientOrderId를 그룹 키로 사용 (엔트리↔OCO 연동)
  - 파일 포맷: JSON(단일 파일), 원자적 저장 (tmp → replace)
  - 외부 의존:
      - src.exchange.orders.get_order / get_order_list (상태 조회)
  - 사용 흐름:
      1) 시장가 엔트리 체결 후 -> record_entry(...)
      2) OCO 생성 성공 -> record_oco(...)
      3) 부착 전에 can_attach_oco(symbol, group_id)로 중복 방지
      4) 주기적으로 sync_active(...) 실행해 상태 최신화
      5) needs_oco(symbol, group_id)로 재부착 필요 여부 판단
"""

from __future__ import annotations
import os, json, time, threading
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Optional, List

from src.exchange.orders import get_order, get_order_list

# -------------------------
# 데이터 모델 (직렬화 친화)
# -------------------------

@dataclass
class OrderLeg:
    type: str = ""               # LIMIT_MAKER or STOP_LOSS_LIMIT 등
    orderId: int = 0
    clientOrderId: str = ""
    status: str = ""             # NEW/PARTIALLY_FILLED/FILLED/CANCELED/REJECTED/EXPIRED
    price: str = ""
    stopPrice: str = ""
    timeInForce: str = ""

@dataclass
class EntryOrder:
    symbol: str
    side: str                    # BUY/SELL
    type: str                    # MARKET/LIMIT/...
    orderId: int
    clientOrderId: str
    status: str
    executedQty: str = "0"
    cummulativeQuoteQty: str = "0"
    price: str = ""              # '0' for MARKET
    ts: int = 0                  # transactTime
    group_id: str = ""           # 기본: clientOrderId

@dataclass
class OCOList:
    symbol: str
    orderListId: int
    listClientOrderId: str
    listStatusType: str          # EXEC_STARTED/EXECUTING/ALL_DONE 등
    listOrderStatus: str         # EXECUTING/ALL_DONE 등
    status_ts: int = 0           # transactionTime
    legs: List[OrderLeg] = field(default_factory=list)
    group_id: str = ""           # 엔트리와 연결 (entry.clientOrderId)

# -------------------------
# 레지스트리 본체
# -------------------------

class OrderRegistry:
    """
    in-memory + JSON file persistence
    """
    def __init__(self, path: str = "runtime/orders_state.json"):
        self.path = path
        self._lock = threading.Lock()
        self.version = 1
        self.entries: Dict[str, EntryOrder] = {}         # key = clientOrderId
        self.ocolists: Dict[str, OCOList] = {}           # key = str(orderListId)
        self.active_by_symbol: Dict[str, Dict[str, Any]] = {}  # symbol -> {"active_oco_ids":[...], "updated": ts}
        self._load()

    # --------------- 파일 IO ----------------

    def _ensure_dir(self):
        d = os.path.dirname(self.path)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            self._ensure_dir()
            self._save()  # 초기 파일 생성
            return
        except Exception:
            # 손상되었으면 백업 떠두고 초기화
            try:
                os.replace(self.path, self.path + ".corrupt")
            except Exception:
                pass
            self._save()
            return

        self.version = data.get("version", 1)
        for cid, od in data.get("entries", {}).items():
            self.entries[cid] = EntryOrder(**od)
        for k, ol in data.get("ocolists", {}).items():
            legs = [OrderLeg(**lg) for lg in ol.get("legs", [])]
            ol["legs"] = legs
            self.ocolists[k] = OCOList(**ol)
        self.active_by_symbol = data.get("active_by_symbol", {})

    def _save(self):
        self._ensure_dir()
        data = {
            "version": self.version,
            "entries": {k: asdict(v) for k, v in self.entries.items()},
            "ocolists": {k: asdict(v) for k, v in self.ocolists.items()},
            "active_by_symbol": self.active_by_symbol,
            "saved_at": int(time.time() * 1000),
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"), indent=2)
        os.replace(tmp, self.path)

    # --------------- 기록/조회 API ----------------

    def record_entry_from_resp(self, resp: Dict[str, Any]) -> EntryOrder:
        """
        역할: /api/v3/order 응답(JSON)으로 Entry 저장
        """
        eo = EntryOrder(
            symbol=resp["symbol"],
            side=resp["side"],
            type=resp["type"],
            orderId=int(resp["orderId"]),
            clientOrderId=resp["clientOrderId"],
            status=resp["status"],
            executedQty=resp.get("executedQty", "0"),
            cummulativeQuoteQty=resp.get("cummulativeQuoteQty", "0"),
            price=resp.get("price", ""),
            ts=int(resp.get("transactTime", resp.get("workingTime", 0))),
            group_id=resp["clientOrderId"],   # 기본: 엔트리 CID를 그룹키로
        )
        with self._lock:
            self.entries[eo.clientOrderId] = eo
        self._save()
        return eo

    def record_oco_from_resp(self, resp: Dict[str, Any], *, group_id: str) -> OCOList:
        """
        역할: /api/v3/orderList 응답(JSON)으로 OCO 저장 (엔트리 group_id 연결)
        """
        orderReports = resp.get("orderReports") or []
        symbol = resp.get("symbol")
        ol = OCOList(
            symbol=symbol,
            orderListId=int(resp["orderListId"]),
            listClientOrderId=resp["listClientOrderId"],
            listStatusType=resp.get("listStatusType", ""),
            listOrderStatus=resp.get("listOrderStatus", ""),
            status_ts=int(resp.get("transactionTime", 0)),
            legs=[
                OrderLeg(
                    type=lr.get("type", ""),
                    orderId=int(lr.get("orderId", 0)),
                    clientOrderId=lr.get("clientOrderId", ""),
                    status=lr.get("status", ""),
                    price=lr.get("price", ""),
                    stopPrice=lr.get("stopPrice", ""),
                    timeInForce=lr.get("timeInForce", ""),
                ) for lr in orderReports
            ],
            group_id=group_id,
        )
        with self._lock:
            self.ocolists[str(ol.orderListId)] = ol
            ab = self.active_by_symbol.setdefault(symbol, {"active_oco_ids": [], "updated": 0})
            if str(ol.orderListId) not in ab["active_oco_ids"]:
                ab["active_oco_ids"].append(str(ol.orderListId))
            ab["updated"] = int(time.time() * 1000)
        self._save()
        return ol

    def can_attach_oco(self, symbol: str, *, group_id: str) -> bool:
        """
        역할: 중복 부착 방지 — 해당 심볼에 아직 '활성 OCO'가 있으면 False
        - 활성 기준: listStatusType!=ALL_DONE and 리스트에 최소 한 다리가 NEW/WORKING
        """
        with self._lock:
            ab = self.active_by_symbol.get(symbol)
            if not ab:
                return True
            for oid in list(ab.get("active_oco_ids", [])):
                oc = self.ocolists.get(oid)
                if not oc:
                    continue
                if oc.listStatusType not in ("ALL_DONE",):
                    # 아직 진행 중
                    return False
        return True

    def needs_oco(self, symbol: str, *, group_id: str) -> bool:
        """
        역할: '재부착 필요' 판단
        - 엔트리(status=FILLED) 존재
        - 해당 group_id로 연결된 OCO가 없음 또는 모두 종료(ALL_DONE/CANCELED)
        """
        with self._lock:
            entry = self.entries.get(group_id)
            if not entry or entry.status != "FILLED":
                return False
            for oc in self.ocolists.values():
                if oc.symbol == symbol and oc.group_id == group_id:
                    if oc.listStatusType not in ("ALL_DONE",):
                        return False
            return True

    def link_entry_status(self, symbol: str, *, clientOrderId: str) -> EntryOrder | None:
        """
        역할: 서버에서 최신 주문 상태를 조회해 Entry 갱신
        """
        try:
            r = get_order(symbol, origClientOrderId=clientOrderId)
        except Exception:
            return None
        with self._lock:
            eo = self.entries.get(clientOrderId)
            if not eo:
                eo = self.record_entry_from_resp(r)
            else:
                eo.status = r.get("status", eo.status)
                eo.executedQty = r.get("executedQty", eo.executedQty)
                eo.cummulativeQuoteQty = r.get("cummulativeQuoteQty", eo.cummulativeQuoteQty)
                eo.price = r.get("price", eo.price)
                eo.ts = int(r.get("transactTime", eo.ts))
                self.entries[clientOrderId] = eo
            self._save()
            return eo

    def link_oco_status(self, *, orderListId: int) -> OCOList | None:
        """
        역할: 서버에서 최신 OCO 상태 조회해 갱신
        - GET 응답엔 보통 orderReports가 없음 → 기존 legs 보존
        """
        try:
            r = get_order_list(orderListId=orderListId)
        except Exception:
            return None

        # GET에는 orderReports가 거의 없음 → 기존 legs 유지
        new_listStatusType = r.get("listStatusType", "")
        new_listOrderStatus = r.get("listOrderStatus", "")
        new_status_ts = int(r.get("transactionTime", 0))
        symbol = r.get("symbol", "")

        with self._lock:
            oc = self.ocolists.get(str(orderListId))
            if not oc:
                # 최초 GET인데 로컬에 없으면 최소 필드로 생성(legs는 비움)
                oc = OCOList(
                    symbol=symbol,
                    orderListId=int(r["orderListId"]),
                    listClientOrderId=r.get("listClientOrderId", ""),
                    listStatusType=new_listStatusType,
                    listOrderStatus=new_listOrderStatus,
                    status_ts=new_status_ts,
                    legs=[],                   # 알 수 없으니 비워둠
                    group_id="",               # 외부에서 매칭 가능
                )
            else:
                oc.listStatusType = new_listStatusType or oc.listStatusType
                oc.listOrderStatus = new_listOrderStatus or oc.listOrderStatus
                oc.status_ts = new_status_ts or oc.status_ts
                # ★ 중요: orderReports 없으면 oc.legs 를 덮어쓰지 않는다

            self.ocolists[str(orderListId)] = oc

            # active set 관리
            ab = self.active_by_symbol.setdefault(oc.symbol, {"active_oco_ids": [], "updated": 0})
            if oc.listStatusType in ("ALL_DONE",):
                if str(orderListId) in ab["active_oco_ids"]:
                    ab["active_oco_ids"].remove(str(orderListId))
            else:
                if str(orderListId) not in ab["active_oco_ids"]:
                    ab["active_oco_ids"].append(str(orderListId))
            ab["updated"] = int(time.time() * 1000)
            self._save()
            return oc

    # --------------- 폴링 동기화 ----------------

    def sync_active(self) -> Dict[str, Any]:
        """
        역할: 활성 OCO 및 최신 엔트리 상태를 폴링하여 동기화
        반환: {"entries": N, "ocolists": M}
        """
        with self._lock:
            active_ids = []
            for sym, v in self.active_by_symbol.items():
                active_ids.extend(v.get("active_oco_ids", []))
            entry_cids = list(self.entries.keys())

        # OCO 리스트 상태 갱신
        for oid in active_ids:
            try:
                self.link_oco_status(orderListId=int(oid))
            except Exception:
                pass

        # 엔트리 상태 갱신
        for cid in entry_cids:
            eo = self.entries.get(cid)
            if not eo:
                continue
            try:
                self.link_entry_status(eo.symbol, clientOrderId=cid)
            except Exception:
                pass

        return {"entries": len(entry_cids), "ocolists": len(active_ids)}

    # --------------- 요약/디버그 ----------------

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "entries": {k: asdict(v) for k, v in self.entries.items()},
                "ocolists": {k: asdict(v) for k, v in self.ocolists.items()},
                "active_by_symbol": self.active_by_symbol,
            }
