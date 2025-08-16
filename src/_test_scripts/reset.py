# src/orders/reset.py
# -*- coding: utf-8 -*-
"""
테스트 초기화(클린 슬레이트) 유틸
- 심볼별 오픈 주문/오더리스트(OCO) 전부 취소
- 로컬 레지스트리(runtime/orders_state.json) 초기화
- mainnet 보호 가드 유지(allow_mainnet=False 기본)

의존:
  - exchange.orders.cancel_open_orders, cancel_order_list
  - orders.registry.OrderRegistry
사용 시나리오:
  - 테스트 전에 항상 호출해서 과거 상태(열린 OCO/주문) 제거
  - 실패 테스트 중간에 상태 꼬였을 때 강제 복구
주의:
  - Spot 보유자산(코인 잔고)는 변경하지 않음(포지션 청산 아님)
"""

from __future__ import annotations
import os, json, time
from typing import List, Dict, Any
import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

from src.exchange.orders import cancel_open_orders, cancel_order_list
from src.exchange.registry import OrderRegistry

def _safe(fn, *args, **kwargs):
    try:
        return {"ok": True, "resp": fn(*args, **kwargs)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def reset_exchange_state(
    symbols: List[str],
    *,
    registry_path: str = "runtime/orders_state.json",
    allow_mainnet: bool = False,
) -> Dict[str, Any]:
    """
    역할:
      - 심볼별 '열린 일반 주문' 전부 취소 (DELETE /api/v3/openOrders)
      - 레지스트리에 기록된 '활성 OCO' 전부 취소 (DELETE /api/v3/orderList)
    input:
      - symbols: ["BTCUSDT", "ETHUSDT", ...]
      - registry_path: 레지스트리 파일 경로
      - allow_mainnet: True면 mainnet에서도 허용(기본 False)
    output: 요약 딕셔너리(성공/실패 내역 포함)
    연결:
      - 입력: registry.active_by_symbol로 활성 OCO 목록 조회
      - 출력: 취소 결과를 리스트로 반환
    """
    reg = OrderRegistry(registry_path)
    summary = {"cancel_open_orders": [], "cancel_oco_lists": []}

    # 1) 심볼별 오픈 주문 전체 취소
    for sym in symbols:
        r = _safe(cancel_open_orders, sym)
        summary["cancel_open_orders"].append({"symbol": sym, **r})

    # 2) 레지스트리 기준 활성 OCO 리스트 취소
    active = reg.summary().get("active_by_symbol", {})
    for sym, meta in active.items():
        for oid in list(meta.get("active_oco_ids", [])):
            try:
                oid_int = int(oid)
            except Exception:
                continue
            r = _safe(cancel_order_list, orderListId=oid_int, allow_mainnet=allow_mainnet)
            summary["cancel_oco_lists"].append({"symbol": sym, "orderListId": oid_int, **r})

    return summary

def clear_registry_file(path: str = "runtime/orders_state.json") -> Dict[str, Any]:
    """
    역할: 레지스트리 파일을 '빈 상태'로 초기화(원자적 쓰기)
    output: {"ok": bool, "path": str}
    """
    try:
        d = os.path.dirname(path)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        data = {
            "version": 1,
            "entries": {},
            "ocolists": {},
            "active_by_symbol": {},
            "saved_at": int(time.time() * 1000),
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "path": path, "error": str(e)}

def full_reset(
    symbols: List[str],
    *,
    registry_path: str = "runtime/orders_state.json",
    allow_mainnet: bool = False,
) -> Dict[str, Any]:
    """
    역할: '오픈 주문/활성 OCO 취소' + '레지스트리 초기화' 풀 패키지
    """
    ex = reset_exchange_state(symbols, registry_path=registry_path, allow_mainnet=allow_mainnet)
    reg = clear_registry_file(registry_path)
    return {"exchange": ex, "registry": reg}
