# src/order_executor.py
from __future__ import annotations
import time, uuid
from typing import Dict, Any
from decimal import Decimal, ROUND_UP
from src.exchange.market import get_price, get_symbol_info
from src.exchange.orders import place_test_order, place_order, place_oco_order
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

# =============================================================================
# 5) OCO 주문
# =============================================================================

def _ceil_qty_for_notional(min_notional: Decimal, price: Decimal, step_qty: Decimal) -> Decimal:
    """
    역할: 주어진 price에서 MIN_NOTIONAL을 만족하기 위한 최소 수량을 step에 맞춰 '올림' 계산
    """
    if price <= 0:
        return Decimal("0")
    need = (min_notional / price)
    # step_qty 단위로 올림
    q = (need / step_qty).to_integral_value(rounding=ROUND_UP) * step_qty
    return q

def oco_sell_tp_sl(
    symbol: str,
    qty: float,
    *,
    tp_price: float,           # 이익실현 limit (위쪽)
    sl_stop: float,            # 손절 stopPrice (아래쪽 트리거)
    sl_limit: float | None = None,  # 손절 체결용 limit(없으면 stop보다 한 틱 아래로 자동 설정)
    tif: str = "GTC",          # stop-limit 다리는 GTC 권장
    dry_run: bool = True,
    allow_mainnet: bool = False,
) -> Dict[str, Any]:
    """
    역할:
      - SELL OCO (TP=LIMIT_MAKER, SL=STOP_LOSS_LIMIT) 생성
      - 가격관계식/필터/정밀도/최소주문가치 검증 → 전송
      - 전송/리턴값은 문자열로 통일
    입력 연결:
      - get_symbol_info / get_price / extract_filters / normalize_* / to_api_str
    출력 연결:
      - exchange.orders.place_oco_order (실 호출)
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)  # dict: {tickSize, stepQty, minNotional, ...}

    last = Decimal(str(get_price(symbol)))
    p_tp  = normalize_price(tp_price, ff)            # 위 다리 limit
    p_stp = normalize_price(sl_stop, ff)             # 아래 다리 stopPrice
    # stopLimitPrice 기본값: stopPrice보다 한 틱 아래(SELL)
    if sl_limit is None:
        sl_limit = float(Decimal(p_stp) - (ff["tickSize"]))
    p_slm = normalize_price(sl_limit, ff)            # 아래 다리 limit(price)

    q_dec = normalize_qty(qty, ff)

    # 1) 가격 관계식 사전 검증 (SELL)
    #    LIMIT_MAKER price > last > STOP stopPrice
    if not (Decimal(p_tp) > last > Decimal(p_stp)):
        return {
            "ok": False, "reason": "PRICE_RELATION_INVALID(SELL)",
            "explain": f"tp({p_tp}) > last({to_api_str(last, ff.get('tickSize'))}) > stop({p_stp}) 이어야 함."
        }
    #    STOP_LOSS_LIMIT에서 price(=stopLimitPrice)는 보수적으로 stopPrice 이하 권장
    if not (Decimal(p_slm) <= Decimal(p_stp)):
        return {
            "ok": False, "reason": "STOP_LIMIT_RELATION_INVALID",
            "explain": f"stopLimitPrice({p_slm}) <= stopPrice({p_stp}) 권장(거부될 수 있음)."
        }

    # 2) 최소주문가치(MIN_NOTIONAL) 충족 확인(두 다리 모두)
    min_notional = ff.get("minNotional")
    if min_notional:
        q_need_tp  = _ceil_qty_for_notional(min_notional, Decimal(p_tp),  ff["stepQty"])
        q_need_slm = _ceil_qty_for_notional(min_notional, Decimal(p_slm), ff["stepQty"])
        q_need = max(q_need_tp, q_need_slm)
        if q_dec < q_need:
            return {
                "ok": False,
                "reason": "MIN_NOTIONAL_NOT_SATISFIED",
                "required_qty": to_api_str(q_need, ff.get("stepQty")),
                "given_qty": to_api_str(q_dec, ff.get("stepQty")),
                "hint": "수량을 늘리거나 가격을 조정해 최소주문가치를 만족시켜야 함."
            }

    # 3) 문자열 포맷
    qty_str   = to_api_str(q_dec, ff.get("stepQty"))
    tp_str    = to_api_str(Decimal(p_tp),  ff.get("tickSize"))
    stop_str  = to_api_str(Decimal(p_stp), ff.get("tickSize"))
    slm_str   = to_api_str(Decimal(p_slm), ff.get("tickSize"))
    last_str  = to_api_str(last, ff.get("tickSize"))

    # 4) dry_run: REST 호출 없이 “전송 페이로드”만 반환(로컬 검증 완료)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "price_relation": f"{tp_str} > last({last_str}) > {stop_str}",
            "payload": {
                "symbol": symbol,
                "side": "SELL",
                "quantity": qty_str,
                "aboveType": "LIMIT_MAKER",
                "abovePrice": tp_str,
                "belowType": "STOP_LOSS_LIMIT",
                "belowStopPrice": stop_str,
                "belowPrice": slm_str,
                "belowTimeInForce": tif,
                "newOrderRespType": "RESULT",
            }
        }

    # 5) 실주문: testnet에서 먼저 확인 권장
    try:
        res = place_oco_order(
            symbol, "SELL",
            quantity=qty_str,
            aboveType="LIMIT_MAKER", abovePrice=tp_str,
            belowType="STOP_LOSS_LIMIT", belowStopPrice=stop_str, belowPrice=slm_str,
            belowTimeInForce=tif,
            newOrderRespType="RESULT",
            allow_mainnet=allow_mainnet,
        )
        return {"ok": True, "resp": res}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def oco_buy_breakout(
    symbol: str,
    qty: float,
    *,
    entry_stop: float,         # 위쪽 돌파 진입 stopPrice
    fallback_limit: float,     # 아래쪽 방어(혹은 대안) limit 가격
    entry_limit: float | None = None,  # stop 진입시 limit(없으면 stop보다 한 틱 위로)
    tif: str = "GTC",
    dry_run: bool = True,
    allow_mainnet: bool = False,
) -> Dict[str, Any]:
    """
    역할:
      - BUY OCO (위: STOP/STOP_LIMIT, 아래: LIMIT_MAKER) → 돌파 진입 + 저가매수 대안
      - 관계식( BUY: limit < last < stop ) 검증 및 필터 대응
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)

    last = Decimal(str(get_price(symbol)))
    p_stp = normalize_price(entry_stop, ff)          # 위쪽 stopPrice
    if entry_limit is None:
        entry_limit = float(Decimal(p_stp) + ff["tickSize"])
    p_slm = normalize_price(entry_limit, ff)         # stop-limit price
    p_lim = normalize_price(fallback_limit, ff)      # 아래 limit maker

    q_dec = normalize_qty(qty, ff)

    # 1) 가격 관계식 (BUY)
    #    LIMIT_MAKER price < last < STOP stopPrice
    if not (Decimal(p_lim) < last < Decimal(p_stp)):
        return {
            "ok": False, "reason": "PRICE_RELATION_INVALID(BUY)",
            "explain": f"limit({p_lim}) < last({to_api_str(last, ff.get('tickSize'))}) < stop({p_stp}) 이어야 함."
        }
    #    STOP_LIMIT 진입의 limit(price)는 stopPrice 이상 권장(BUY)
    if not (Decimal(p_slm) >= Decimal(p_stp)):
        return {
            "ok": False, "reason": "STOP_LIMIT_RELATION_INVALID",
            "explain": f"stopLimitPrice({p_slm}) >= stopPrice({p_stp}) 권장."
        }

    # 2) 최소주문가치 확인(세 다리 중 활성화될 두 다리 대비)
    min_notional = ff.get("minNotional")
    if min_notional:
        q_need_up  = _ceil_qty_for_notional(min_notional, Decimal(p_slm), ff["stepQty"])  # 위 다리 체결가
        q_need_low = _ceil_qty_for_notional(min_notional, Decimal(p_lim), ff["stepQty"])  # 아래 다리 체결가
        q_need = max(q_need_up, q_need_low)
        if q_dec < q_need:
            return {
                "ok": False,
                "reason": "MIN_NOTIONAL_NOT_SATISFIED",
                "required_qty": to_api_str(q_need, ff.get("stepQty")),
                "given_qty": to_api_str(q_dec, ff.get("stepQty")),
                "hint": "수량을 늘리거나 가격을 조정해 최소주문가치를 만족시켜야 함."
            }

    qty_str  = to_api_str(q_dec, ff.get("stepQty"))
    lim_str  = to_api_str(Decimal(p_lim), ff.get("tickSize"))
    stop_str = to_api_str(Decimal(p_stp), ff.get("tickSize"))
    slm_str  = to_api_str(Decimal(p_slm), ff.get("tickSize"))

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "price_relation": f"{lim_str} < last({to_api_str(last, ff.get('tickSize'))}) < {stop_str}",
            "payload": {
                "symbol": symbol,
                "side": "BUY",
                "quantity": qty_str,
                "aboveType": "STOP_LOSS_LIMIT",
                "aboveStopPrice": stop_str,
                "abovePrice": slm_str,
                "aboveTimeInForce": tif,
                "belowType": "LIMIT_MAKER",
                "belowPrice": lim_str,
                "newOrderRespType": "RESULT",
            }
        }

    try:
        res = place_oco_order(
            symbol, "BUY",
            quantity=qty_str,
            aboveType="STOP_LOSS_LIMIT", aboveStopPrice=stop_str, abovePrice=slm_str, aboveTimeInForce=tif,
            belowType="LIMIT_MAKER", belowPrice=lim_str,
            newOrderRespType="RESULT",
            allow_mainnet=allow_mainnet,
        )
        return {"ok": True, "resp": res}
    except Exception as e:
        return {"ok": False, "error": str(e)}
