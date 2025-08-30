# -*- coding: utf-8 -*-
"""
OrderManager: 주문/주문리스트(OCO) 상태를 파일(JSON)로 관리하는 경량 매니저.

역할
----
- 엔트리(단일 주문)와 OCO(Order List) 메타를 기록/동기화/청소/직렬화
- 심볼별 활성 OCO 존재 여부로 '중복 진입'을 차단하는 간단한 가드 제공

입출력/연결
-----------
- 입력(기록): market/limit 체결 응답(dict), OCO 생성 응답(dict)
- 동기화: src.exchange.orders.get_order(), get_order_list(), cancel_order_list()
- 출력: state JSON 파일(data/orders_state.json 기본) + in-memory 상태

데이터 구조(JSON)
------------------
{
  "version": 1,
  "entries": {
    "<clientOrderId>": {
      "symbol": "BTCUSDT",
      "orderId": 15061567,
      "clientOrderId": "mbuy-xxxx",
      "side": "BUY",
      "type": "MARKET",
      "status": "FILLED",
      "cummulativeQuoteQty": "9.42",
      "executedQty": "0.00008",
      "price": "0.0",
      "ts": 1755321208587,
      "group_id": "mbuy-xxxx"  # 엔트리와 이후 OCO를 묶는 그룹 키
    },
    ...
  },
  "ocolists": {
    "<orderListId>": {
      "symbol": "BTCUSDT",
      "orderListId": 526042,
      "listClientOrderId": "oco-sell-lst-xxxx",
      "listStatusType": "EXEC_STARTED",
      "listOrderStatus": "EXECUTING",
      "status_ts": 1755321209008,
      "group_id": "mbuy-xxxx",
      "legs": [
        {"orderId": 15061568, "clientOrderId": "...", "type": "STOP_LOSS_LIMIT", "price": "...", "stopPrice": "...", "status": "NEW", "timeInForce": "GTC"},
        {"orderId": 15061569, "clientOrderId": "...", "type": "LIMIT_MAKER",     "price": "...", "stopPrice": "",   "status": "NEW", "timeInForce": "GTC"}
      ]
    }
  },
  "active_by_symbol": {
    "BTCUSDT": { "active_oco_ids": ["526042"], "updated": 1755321209008 }
  },
  "saved_at": 1755321209100
}
"""

from __future__ import annotations
import os, json, time
from typing import Dict, Any, Optional, Tuple, List

from src.exchange.orders import (
    get_order, get_order_list, cancel_order_list
)

def _now_ms() -> int:
    return int(time.time() * 1000)

def _ensure_dir(p: str) -> None:
    d = os.path.dirname(p)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

DEFAULT_STATE = {
    "version": 1,
    "entries": {},
    "ocolists": {},
    "active_by_symbol": {},
    "saved_at": 0
}

class OrderManager:
    """
    역할: 주문/주문리스트 상태를 파일에 저장하고, Binance API로 동기화한다.

    주요 메서드
    ----------
    - record_entry(resp, group_id): 시장가/지정가 '단일 주문' 기록
    - record_oco_attached(resp, group_id): OCO 생성 결과 기록(+심볼 활성 OCO 업데이트)
    - sync_open_entries(): entries 상태 최신화(NEW/PARTIALLY_FILLED → FILLED 등)
    - sync_open_ocolists(): OCO 상태 최신화(실행/완료/취소 감지)
    - persist(): JSON 저장, reset(): 초기화
    - get_active_oco_ids(symbol): 활성 OCO 리스트 id 배열
    """

    def __init__(self, state_path: str = "data/orders_state.json") -> None:
        self.state_path = state_path
        self.state: Dict[str, Any] = self._load_or_init(state_path)

    # ------------------
    # 저장/로드/초기화
    # ------------------
    def _load_or_init(self, path: str) -> Dict[str, Any]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                # 필수 키 보정
                for k, v in DEFAULT_STATE.items():
                    if k not in obj:
                        obj[k] = v if k != "saved_at" else 0
                return obj
            except Exception:
                # 손상 시 백업 후 초기화
                try:
                    os.rename(path, path + f".corrupt.{_now_ms()}")
                except Exception:
                    pass
        return DEFAULT_STATE.copy()

    def persist(self) -> None:
        self.state["saved_at"] = _now_ms()
        _ensure_dir(self.state_path)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def reset(self) -> None:
        """모든 상태 초기화(테스트 리셋용)."""
        self.state = DEFAULT_STATE.copy()
        self.persist()

    # ------------------
    # 조회/헬퍼
    # ------------------
    def get_active_oco_ids(self, symbol: str) -> List[str]:
        rec = self.state["active_by_symbol"].get(symbol, {})
        return list(rec.get("active_oco_ids", []))

    def _set_active_oco(self, symbol: str, ids: List[str]) -> None:
        self.state["active_by_symbol"][symbol] = {
            "active_oco_ids": ids,
            "updated": _now_ms()
        }

    # ------------------
    # 기록(엔트리/ OCO)
    # ------------------
    def record_entry(self, entry_resp: Dict[str, Any], group_id: Optional[str] = None) -> str:
        """
        역할: '단일 주문'(시장가/지정가) 응답을 entries에 기록.
        input:
          - entry_resp: Binance 주문 응답(dict)
        return:
          - clientOrderId (키)
        """
        r = entry_resp
        cid = r.get("clientOrderId") or r.get("origClientOrderId")
        if not cid:
            raise ValueError("record_entry: clientOrderId 누락")

        symbol = r.get("symbol")
        self.state["entries"][cid] = {
            "symbol": symbol,
            "orderId": r.get("orderId"),
            "clientOrderId": cid,
            "side": r.get("side"),
            "type": r.get("type"),
            "status": r.get("status"),
            "price": r.get("price"),
            "executedQty": r.get("executedQty"),
            "cummulativeQuoteQty": r.get("cummulativeQuoteQty"),
            "ts": r.get("transactTime") or _now_ms(),
            "group_id": group_id or cid
        }
        return cid

    def record_oco_attached(self, oco_resp: Dict[str, Any], group_id: Optional[str] = None) -> str:
        """
        역할: OCO 생성 응답을 상태에 기록하고 심볼별 활성 OCO 목록을 갱신.
        return: orderListId (키)
        """
        r = oco_resp
        olid = str(r["orderListId"])
        sym = r["symbol"]
        self.state["ocolists"][olid] = {
            "symbol": sym,
            "orderListId": r["orderListId"],
            "listClientOrderId": r.get("listClientOrderId"),
            "listStatusType": r.get("listStatusType"),
            "listOrderStatus": r.get("listOrderStatus"),
            "status_ts": r.get("transactionTime") or _now_ms(),
            "group_id": group_id or r.get("listClientOrderId"),
            "legs": [
                {
                    "orderId": o.get("orderId"),
                    "clientOrderId": o.get("clientOrderId"),
                    "type": orp.get("type") if (orp := self._find_order_report(r, o.get("orderId"))) else None,
                    "price": orp.get("price") if orp else None,
                    "stopPrice": orp.get("stopPrice") if orp else "",
                    "status": orp.get("status") if orp else "NEW",
                    "timeInForce": orp.get("timeInForce") if orp else None,
                }
                for o in r.get("orders", [])
            ],
        }
        # 심볼 활성 OCO 업데이트
        ids = self.get_active_oco_ids(sym)
        if olid not in ids:
            ids.append(olid)
        self._set_active_oco(sym, ids)
        return olid

    @staticmethod
    def _find_order_report(oco_resp: Dict[str, Any], order_id: int | None) -> Optional[Dict[str, Any]]:
        for rep in oco_resp.get("orderReports", []):
            if rep.get("orderId") == order_id:
                return rep
        return None

    # ------------------
    # 동기화
    # ------------------
    def sync_open_entries(self) -> Dict[str, int]:
        """
        역할: entries 중 아직 열려 있을 수 있는 주문의 상태를 최신화.
        - FILLED/EXPIRED/REJECTED/CANCELED는 그대로 기록만 갱신(삭제는 하지 않음; 감사용)
        - 반환: {"checked": N}
        """
        entries = self.state["entries"]
        n = 0
        for cid, e in list(entries.items()):
            symbol = e.get("symbol")
            order_id = e.get("orderId")
            try:
                r = get_order(symbol, orderId=order_id, origClientOrderId=cid)
                e["status"] = r.get("status", e.get("status"))
                e["executedQty"] = r.get("executedQty", e.get("executedQty"))
                e["cummulativeQuoteQty"] = r.get("cummulativeQuoteQty", e.get("cummulativeQuoteQty"))
                e["price"] = r.get("price", e.get("price"))
                e["ts"] = r.get("updateTime", e.get("ts"))
                n += 1
            except Exception:
                # 조회 실패는 무시(일시 오류/삭제된 주문 등)
                pass
        return {"checked": n}

    def sync_open_ocolists(self) -> Dict[str, int]:
        """
        역할: OCO 상태 최신화 및 비활성 정리.
        - listStatusType/listOrderStatus 갱신
        - 모든 leg가 종료되면 active_by_symbol에서 제거
        """
        ocols = self.state["ocolists"]
        n = 0
        for olid, o in list(ocols.items()):
            sym = o["symbol"]
            try:
                r = get_order_list(orderListId=int(olid))
            except Exception:
                # 못 가져오면 일단 스킵
                continue

            o["listStatusType"] = r.get("listStatusType", o.get("listStatusType"))
            o["listOrderStatus"] = r.get("listOrderStatus", o.get("listOrderStatus"))
            o["status_ts"] = r.get("transactionTime", o.get("status_ts"))

            # legs 갱신(가능한 매칭)
            rep_map = {rep.get("orderId"): rep for rep in r.get("orderReports", [])}
            new_legs = []
            for leg in o.get("legs", []):
                rep = rep_map.get(leg.get("orderId"))
                if rep:
                    leg.update({
                        "status": rep.get("status", leg.get("status")),
                        "price": rep.get("price", leg.get("price")),
                        "stopPrice": rep.get("stopPrice", leg.get("stopPrice")),
                        "timeInForce": rep.get("timeInForce", leg.get("timeInForce")),
                        "type": rep.get("type", leg.get("type")),
                    })
                new_legs.append(leg)
            o["legs"] = new_legs
            n += 1

            # 비활성 판단: legs 가 모두 종결 상태면 active 목록에서 제거
            if self._is_list_inactive(o):
                ids = self.get_active_oco_ids(sym)
                ids = [x for x in ids if x != str(olid)]
                self._set_active_oco(sym, ids)

        return {"checked": n}

    @staticmethod
    def _is_list_inactive(ol: Dict[str, Any]) -> bool:
        """
        종료 판정: 모든 leg가 FILLED/CANCELED/EXPIRED/REJECTED 이면 비활성
        """
        terminal = {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}
        legs = ol.get("legs", [])
        if not legs:
            return False
        return all((leg.get("status") in terminal) for leg in legs)

    # ------------------
    # 유지보수/청소
    # ------------------
    def purge_old(self, keep_ms: int = 7 * 24 * 3600 * 1000) -> Dict[str, int]:
        """
        오래된 엔트리/리스트 메타를 정리(감사용). 기본 7일 유지.
        """
        cutoff = _now_ms() - keep_ms
        e_del = 0
        for cid, e in list(self.state["entries"].items()):
            if (e.get("ts") or 0) < cutoff:
                del self.state["entries"][cid]
                e_del += 1
        l_del = 0
        for lid, o in list(self.state["ocolists"].items()):
            if (o.get("status_ts") or 0) < cutoff:
                del self.state["ocolists"][lid]
                l_del += 1
        return {"entries": e_del, "ocolists": l_del}

# ------------------
# 모듈 레벨 헬퍼(선택)
# ------------------
def load_state(path: str = "data/orders_state.json") -> Dict[str, Any]:
    mgr = OrderManager(path)
    return mgr.state

def save_state(state: Dict[str, Any], path: str = "data/orders_state.json") -> None:
    _ensure_dir(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
