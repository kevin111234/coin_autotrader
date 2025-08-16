# src/order_executor.py
from __future__ import annotations
import time, uuid
from typing import Dict, Any
from decimal import Decimal

from src.exchange.market import get_price, get_symbol_info
from src.exchange.orders import place_test_order, place_order
from src.exchange.filters import (
    extract_filters, normalize_qty, normalize_price, ensure_min_notional, to_api_str
)

# ----- 재시도/백오프 정책 -----
RETRYABLE_HTTP = {429, 418, 500, 502, 503, 504}
RETRYABLE_CODE = {-1021, -1003}  # 시간오류/레이트리밋 등(core.request가 1차 방어)
MAX_RETRY = 2
BACKOFF_S = [0.5, 1.5]  # 1차 0.5s, 2차 1.5s

def _new_client_id(prefix: str="bot") -> str:
    """
    역할: clientOrderId 생성(아이템포턴시 보장용)
    input: prefix (주문 유형 식별자)
    output: "{prefix}-{uuid}" 문자열
    연결:
      - 본 모듈 내 모든 실주문(place_order)에서 사용
      - 동일 주문 재시도 시 중복 체결 방지(서버가 clientOrderId 기준 dedup 가능)
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"

def _should_retry(resp: Dict[str, Any] | None, status: int | None) -> bool:
    """
    역할: 응답/상태코드로 재시도 여부 판단
    (현재 core 예외에서 status/code 파싱 연계 전이므로 미사용. 훗날 연결 예정)
    """
    if status and status in RETRYABLE_HTTP:
        return True
    if isinstance(resp, dict) and "code" in resp and resp["code"] in RETRYABLE_CODE:
        return True
    return False

# =============================================================================
# 1) 시장가 매수 (quote 기준)
# =============================================================================
def market_buy_by_quote(
    symbol: str,
    quote_usdt: float,
    *,
    dry_run: bool = True,
    allow_mainnet: bool = False
) -> Dict[str, Any]:
    """
    역할:
      - quote(USDT) 예산만큼 시장가 매수. 필터(LOT/MIN_NOTIONAL) 충족하도록 qty 보정.
      - /order/test(dry_run=True)로 먼저 유효성 확보 → 실체결 전환(dry_run=False).
    반환: price/qty는 Binance 전송 포맷(문자열)로 일관 출력.
    """
    # 1) 심볼 정보 & 필터 추출
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)

    # 2) 현재가 조회 → 예산 대비 raw qty 계산
    px_dec = Decimal(str(get_price(symbol)))
    raw_qty = Decimal(str(quote_usdt)) / px_dec

    # 3) LOT_SIZE 보정 → MIN_NOTIONAL 충족 시도
    q1 = normalize_qty(raw_qty, ff)
    px_adj, q2, ok = ensure_min_notional(px_dec, q1, ff)
    qty_dec = q2 if ok else q1

    # 문자열(전송 포맷)로 변환
    price_str = to_api_str(px_adj, ff.get("tickSize"))
    qty_str   = to_api_str(qty_dec, ff.get("stepQty"))

    if qty_dec <= 0:
        return {"ok": False, 
                "reason": "MIN_QTY_NOT_SATISFIED",
                "price": price_str, 
                "qty": qty_str}

    cid = _new_client_id("mbuy")

    def _call():
        # 실제 REST 호출 래퍼 (test/real 공용) — 숫자는 모두 문자열로 전송
        if dry_run:
            return place_test_order(symbol, "BUY", "MARKET", quantity=qty_str)
        return place_order(symbol, "BUY", "MARKET",
                           quantity=qty_str,
                           newClientOrderId=cid,
                           allow_mainnet=allow_mainnet)

    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            res = _call()
            return {"ok": True, 
                    "resp": res, 
                    "price": price_str, 
                    "qty": qty_str, 
                    "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY:
                time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, 
            "error": str(last_err), 
            "price": price_str, 
            "qty": qty_str, 
            "clientOrderId": cid}

# =============================================================================
# 2) 지정가 매수 (price/qty 기준)
# =============================================================================
def limit_buy(
    symbol: str,
    price: float,
    qty: float,
    tif: str = "GTC",
    *,
    dry_run: bool = True,
    allow_mainnet: bool = False
) -> Dict[str, Any]:
    """
    역할:
      - 지정가 매수. PRICE_FILTER/LOT_SIZE/MIN_NOTIONAL 모두 만족하도록 보정.
    반환: price/qty는 Binance 전송 포맷(문자열)로 일관 출력.
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)

    p_dec = normalize_price(price, ff)
    q_dec = normalize_qty(qty, ff)
    p_adj, q_adj, ok = ensure_min_notional(p_dec, q_dec, ff)

    price_str = to_api_str(p_adj, ff.get("tickSize"))
    qty_str   = to_api_str(q_adj, ff.get("stepQty"))

    if (q_adj <= 0) or (not ok):
        return {"ok": False, 
                "reason": "MIN_NOTIONAL_NOT_SATISFIED", 
                "price": price_str, 
                "qty": qty_str}

    cid = _new_client_id("lbuy")

    def _call():
        if dry_run:
            return place_test_order(symbol, "BUY", "LIMIT",
                                    quantity=qty_str, price=price_str, timeInForce=tif)
        return place_order(symbol, "BUY", "LIMIT",
                           quantity=qty_str, price=price_str, timeInForce=tif,
                           newClientOrderId=cid, allow_mainnet=allow_mainnet)

    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            res = _call()
            return {"ok": True, 
                    "resp": res, 
                    "price": price_str, 
                    "qty": qty_str, 
                    "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY:
                time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, 
            "error": str(last_err), 
            "price": price_str, 
            "qty": qty_str, 
            "clientOrderId": cid}

# =============================================================================
# 3) 시장가 매도 (수량 기준)
# =============================================================================
def market_sell_qty(
    symbol: str,
    qty: float,
    *,
    dry_run: bool = True,
    allow_mainnet: bool = False
) -> Dict[str, Any]:
    """
    역할:
      - 수량 기준 시장가 매도. LOT_SIZE 제약만 맞추면 됨(가격은 MARKET).
    반환: qty는 Binance 전송 포맷(문자열)로 일관 출력.
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)

    q_dec = normalize_qty(qty, ff)
    qty_str = to_api_str(q_dec, ff.get("stepQty"))

    if q_dec <= 0:
        return {"ok": False, 
                "reason": "MIN_QTY_NOT_SATISFIED", 
                "qty": qty_str}

    cid = _new_client_id("msell")

    def _call():
        if dry_run:
            return place_test_order(symbol, "SELL", "MARKET", quantity=qty_str)
        return place_order(symbol, "SELL", "MARKET",
                           quantity=qty_str, newClientOrderId=cid, allow_mainnet=allow_mainnet)

    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            res = _call()
            return {"ok": True, 
                    "resp": res, 
                    "qty": qty_str, 
                    "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY:
                time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, 
            "error": str(last_err), 
            "qty": qty_str, 
            "clientOrderId": cid}

# =============================================================================
# 4) 지정가 매도 (price/qty 기준)
# =============================================================================
def limit_sell(
    symbol: str,
    price: float,
    qty: float,
    tif: str = "GTC",
    *,
    dry_run: bool = True,
    allow_mainnet: bool = False
) -> Dict[str, Any]:
    """
    역할: 지정가 매도. PRICE/LOT/MIN_NOTIONAL 보정. 전송/리턴값 문자열.
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)

    p_dec = normalize_price(price, ff)
    q_dec = normalize_qty(qty, ff)
    p_adj, q_adj, ok = ensure_min_notional(p_dec, q_dec, ff)

    price_str = to_api_str(p_adj, ff.get("tickSize"))
    qty_str   = to_api_str(q_adj, ff.get("stepQty"))

    if (q_adj <= 0) or (not ok):
        return {"ok": False, 
                "reason": "MIN_NOTIONAL_NOT_SATISFIED", 
                "price": price_str, 
                "qty": qty_str}

    cid = _new_client_id("lsell")

    def _call():
        if dry_run:
            return place_test_order(symbol, "SELL", "LIMIT",
                                    quantity=qty_str, price=price_str, timeInForce=tif)
        return place_order(symbol, "SELL", "LIMIT",
                           quantity=qty_str, price=price_str, timeInForce=tif,
                           newClientOrderId=cid, allow_mainnet=allow_mainnet)

    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            res = _call()
            return {"ok": True, 
                    "resp": res, 
                    "price": price_str, 
                    "qty": qty_str, 
                    "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY: time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, 
            "error": str(last_err), 
            "price": price_str, 
            "qty": qty_str, 
            "clientOrderId": cid}