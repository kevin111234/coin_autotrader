# src/order_executor.py
# -*- coding: utf-8 -*-
"""
주문 실행 레이어(Spot, OCO 포함)

설계 원칙
---------
1) 숫자 일관성:
   - Binance API로 "전송"되는 숫자(가격/수량/quote)는 항상 문자열(to_api_str)로 포맷
   - 함수의 "리턴"도 동일 문자열로 반환 → 로깅/슬랙/리포트/리플레이 시 혼동 없음

2) 필터/정밀도:
   - symbol별 PRICE_FILTER(tickSize), LOT_SIZE(stepQty), MIN_NOTIONAL(minNotional)을 엄격 준수
   - normalize_price/normalize_qty/ensure_min_notional + to_api_str 조합으로 보장

3) 안정성:
   - 단일 주문: 간단 재시도(HTTP 429/5xx/-1021/-1003 텍스트 매칭)
   - OCO: 아이템포턴시(list/above/below clientOrderId 고정) + 동일 재시도
   - OCO 전송 직전 last 재검증(시장 변동에 의한 관계식 위반을 사전 차단)
   - 필요 시 auto_adjust=True로 tickSize 기반 메이커 보정(조건 자동 복구)

4) 안전 가드:
   - mainnet 실주문은 하위 레이어(place_order/place_oco_order)에서 allow_mainnet=True가 아니면 차단
   - 이 레이어는 그 플래그를 받기만 하고 판단은 하위 모듈이 수행

외부 연결
---------
- src.exchange.market: get_price, get_symbol_info
- src.exchange.filters: extract_filters, normalize_*, ensure_min_notional, to_api_str
- src.exchange.orders: place_test_order, place_order, place_oco_order
- src.exchange.account: get_balances_map, get_symbol_assets
"""

from __future__ import annotations
import time, uuid
from typing import Dict, Any
from decimal import Decimal, ROUND_UP

from src.exchange.account import get_balances_map, get_symbol_assets
from src.exchange.market import get_price, get_symbol_info
from src.exchange.orders import place_test_order, place_order, place_oco_order
from src.exchange.filters import (
    extract_filters, normalize_qty, normalize_price, ensure_min_notional, to_api_str
)

# =========================
# 공용 정책/유틸
# =========================

# 재시도 대상(경험칙 + Binance 문서 기반)
RETRYABLE_HTTP = {429, 418, 500, 502, 503, 504}  # 레이트리밋/캡차/서버오류
RETRYABLE_CODE = {-1021, -1003}  # 서버시간오류/레이트리밋 등 (core.request 1차 방어 이후)
MAX_RETRY = 2                     # 최대 2회 재시도(총 3번 시도)
BACKOFF_S = [0.5, 1.5]            # 지수 백오프 유사: 0.5s → 1.5s

def _new_client_id(prefix: str = "bot") -> str:
    """
    역할: 개별 주문(clientOrderId) 생성(아이템포턴시 보장)
    - 동일 clientOrderId로 재전송하면 서버가 중복주문을 dedup 가능
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"

def _new_list_ids(prefix: str) -> dict[str, str]:
    """
    역할: OCO 전용 아이디 3종 생성
    - listClientOrderId: OCO 리스트 자체의 ID
    - aboveClientOrderId: 위 다리(above)의 주문 ID
    - belowClientOrderId: 아래 다리(below)의 주문 ID
    """
    rid = uuid.uuid4().hex[:12]
    return {
        "listClientOrderId":  f"{prefix}-lst-{rid}",
        "aboveClientOrderId": f"{prefix}-a-{rid}",
        "belowClientOrderId": f"{prefix}-b-{rid}",
    }

def _retry_oco(call):
    """
    역할: OCO 전송용 간단 재시도 래퍼
    - 텍스트 매칭으로 429/5xx/-1021/-1003 추정 시 재시도
    - place_oco_order는 실패시 예외를 던진다고 가정
    반환: (True, result) 또는 (False, last_exception)
    """
    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            return True, call()
        except Exception as e:
            last_err = e
            s = str(e)
            # 간단한 휴리스틱 매칭(필요하면 core.request에서 status/코드 넘기도록 확장)
            if i < MAX_RETRY and any(x in s for x in (" 429", " 500", " 502", " 503", " 504", "-1021", "-1003")):
                time.sleep(BACKOFF_S[i]); continue
            break
    return False, last_err

def _max_required_quote_for_buy(q_dec: Decimal, prices: list[Decimal]) -> Decimal:
    """
    역할: BUY 시 후보 체결가들 중 '최대 notional(=필요 Quote)' 계산
    - OCO(BUY)의 두 다리 중 어떤 게 체결될지 모르므로 최악(가장 큰 notional)을 잡는다.
    """
    if not prices:
        return Decimal("0")
    return q_dec * max(prices)

def _ceil_qty_for_notional(min_notional: Decimal, price: Decimal, step_qty: Decimal) -> Decimal:
    """
    역할: 주어진 price에서 MIN_NOTIONAL 만족을 위한 '최소 수량'을 step 단위로 올림
    - OCO의 두 다리 각각에 대해 검사할 때 사용
    """
    if price <= 0:
        return Decimal("0")
    need = (min_notional / price)
    q = (need / step_qty).to_integral_value(rounding=ROUND_UP) * step_qty
    return q

# (현재 미사용) 응답 기반 재시도 판단 훅. core.request 확장 시 연결할 것.
def _should_retry(resp: Dict[str, Any] | None, status: int | None) -> bool:
    if status and status in RETRYABLE_HTTP: return True
    if isinstance(resp, dict) and "code" in resp and resp["code"] in RETRYABLE_CODE: return True
    return False


# =========================
# 1) 시장가 매수 (quote 기준)
# =========================
def market_buy_by_quote(
    symbol: str,
    quote_usdt: float,
    *,
    dry_run: bool = True,
    allow_mainnet: bool = False,
    use_quote_order_qty: bool = False,  # True면 quoteOrderQty로 전송(정밀도 걱정↓)
) -> Dict[str, Any]:
    """
    역할
    ----
    - USDT 예산(quote_usdt)만큼 MARKET 매수를 수행한다.
    - 기본은 quantity 경로(예산/현재가로 qty 산출)이며,
      옵션 `use_quote_order_qty=True`면 quoteOrderQty 파라미터로 직접 전송.

    입력
    ----
    symbol: "BTCUSDT" 등
    quote_usdt: 사용할 USDT 예산
    dry_run: True면 /order/test 사용(실체결 없음)
    allow_mainnet: True여야 mainnet에서 실주문 허용
    use_quote_order_qty: True면 quoteOrderQty로 전송(정밀도 이슈 감소)

    출력
    ----
    {"ok":bool, "resp":dict|{}, "price":str, "qty":str, "quote":str, "clientOrderId":str, "error"?:str}
    - price/qty/quote 모두 문자열
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)
    tick = ff.get("tickSize")
    step = ff.get("stepQty")

    # 현재가 및 예산 → 수량 산출
    px_dec = Decimal(str(get_price(symbol)))
    raw_qty = Decimal(str(quote_usdt)) / px_dec

    # LOT_SIZE/STEP 보정 및 MIN_NOTIONAL 충족 시도
    q1 = normalize_qty(raw_qty, ff)
    px_adj, q2, ok = ensure_min_notional(px_dec, q1, ff)
    qty_dec = q2 if ok else q1

    # 문자열 포맷 (전송/리턴 일치)
    price_str = to_api_str(px_adj, tick)
    qty_str   = to_api_str(qty_dec, step)
    quote_str = to_api_str(Decimal(str(quote_usdt)))

    cid = _new_client_id("mbuy")

    # 두 경로를 함수로 분리
    def _call_quantity():
        if dry_run:
            return place_test_order(symbol, "BUY", "MARKET", quantity=qty_str)
        return place_order(symbol, "BUY", "MARKET",
                           quantity=qty_str, newClientOrderId=cid, allow_mainnet=allow_mainnet)

    def _call_quote():
        # NOTE: 일부 환경에서 /order/test가 quoteOrderQty를 미지원할 가능성 있음 → testnet에서 실검증 권장
        if dry_run:
            return place_test_order(symbol, "BUY", "MARKET", quoteOrderQty=quote_str)
        return place_order(symbol, "BUY", "MARKET",
                           quoteOrderQty=quote_str, newClientOrderId=cid, allow_mainnet=allow_mainnet)

    call = _call_quote if use_quote_order_qty else _call_quantity

    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            res = call()
            # 두 경로 모두 qty/quote를 함께 리턴 → 로깅 일관성
            return {"ok": True, "resp": res, "price": price_str, "qty": qty_str, "quote": quote_str, "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY:
                time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, "error": str(last_err), "price": price_str, "qty": qty_str, "quote": quote_str, "clientOrderId": cid}


# =========================
# 2) 지정가 매수
# =========================
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
    역할: 지정가 매수(LIMIT). PRICE/LOT/MIN_NOTIONAL 보정 후 전송.
    리턴: price/qty 문자열.
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)
    tick = ff.get("tickSize")
    step = ff.get("stepQty")

    p_dec = normalize_price(price, ff)
    q_dec = normalize_qty(qty, ff)
    p_adj, q_adj, ok = ensure_min_notional(p_dec, q_dec, ff)

    price_str = to_api_str(p_adj, tick)
    qty_str   = to_api_str(q_adj, step)

    if (q_adj <= 0) or (not ok):
        return {"ok": False, "reason": "MIN_NOTIONAL_NOT_SATISFIED", "price": price_str, "qty": qty_str}

    cid = _new_client_id("lbuy")

    def _call():
        if dry_run:
            return place_test_order(symbol, "BUY", "LIMIT", quantity=qty_str, price=price_str, timeInForce=tif)
        return place_order(symbol, "BUY", "LIMIT",
                           quantity=qty_str, price=price_str, timeInForce=tif,
                           newClientOrderId=cid, allow_mainnet=allow_mainnet)

    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            res = _call()
            return {"ok": True, "resp": res, "price": price_str, "qty": qty_str, "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY: time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, "error": str(last_err), "price": price_str, "qty": qty_str, "clientOrderId": cid}


# =========================
# 3) 시장가 매도
# =========================
def market_sell_qty(
    symbol: str,
    qty: float,
    *,
    dry_run: bool = True,
    allow_mainnet: bool = False
) -> Dict[str, Any]:
    """
    역할: 수량 기준 시장가 매도(MARKET). LOT_SIZE(step)만 맞추면 됨.
    리턴: qty 문자열.
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)
    step = ff.get("stepQty")

    q_dec = normalize_qty(qty, ff)
    qty_str = to_api_str(q_dec, step)

    if q_dec <= 0:
        return {"ok": False, "reason": "MIN_QTY_NOT_SATISFIED", "qty": qty_str}

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
            return {"ok": True, "resp": res, "qty": qty_str, "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY: time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, "error": str(last_err), "qty": qty_str, "clientOrderId": cid}


# =========================
# 4) 지정가 매도
# =========================
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
    역할: 지정가 매도(LIMIT). PRICE/LOT/MIN_NOTIONAL 보정 후 전송.
    리턴: price/qty 문자열.
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)
    tick = ff.get("tickSize")
    step = ff.get("stepQty")

    p_dec = normalize_price(price, ff)
    q_dec = normalize_qty(qty, ff)
    p_adj, q_adj, ok = ensure_min_notional(p_dec, q_dec, ff)

    price_str = to_api_str(p_adj, tick)
    qty_str   = to_api_str(q_adj, step)

    if (q_adj <= 0) or (not ok):
        return {"ok": False, "reason": "MIN_NOTIONAL_NOT_SATISFIED", "price": price_str, "qty": qty_str}

    cid = _new_client_id("lsell")

    def _call():
        if dry_run:
            return place_test_order(symbol, "SELL", "LIMIT", quantity=qty_str, price=price_str, timeInForce=tif)
        return place_order(symbol, "SELL", "LIMIT",
                           quantity=qty_str, price=price_str, timeInForce=tif,
                           newClientOrderId=cid, allow_mainnet=allow_mainnet)

    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            res = _call()
            return {"ok": True, "resp": res, "price": price_str, "qty": qty_str, "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY: time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, "error": str(last_err), "price": price_str, "qty": qty_str, "clientOrderId": cid}


# =========================
# 5) OCO: SELL (TP/SL)
# =========================
def oco_sell_tp_sl(
    symbol: str,
    qty: float,
    *,
    tp_price: float,                 # 위 다리: LIMIT_MAKER (이익실현)
    sl_stop: float,                  # 아래 다리: STOP_LOSS_LIMIT의 stopPrice(손절 트리거)
    sl_limit: float | None = None,   # 아래 다리: stop 발동 후 체결용 limit(price) / None이면 stop - 1tick
    tif: str = "GTC",
    dry_run: bool = True,
    allow_mainnet: bool = False,
    auto_adjust: bool = False,       # True면 전송 직전 메이커 보정(조건 깨지면 tick 단위 자동 조정)
) -> Dict[str, Any]:
    """
    역할
    ----
    - 보유 포지션 청산용 OCO(SELL)를 생성한다.
      * 위 다리: LIMIT_MAKER(TP)
      * 아래 다리: STOP_LOSS_LIMIT(SL) (stopPrice + limit(price))
    - 가격 관계식(SELL):  TP(limit) > Last > SL(stop)  을 사전 검증
    - stopLimitPrice는 stopPrice 이하 권장(즉시체결 방지)
    - 전송 직전(last 재조회) auto_adjust=True면 tickSize로 자동 보정

    입력/출력
    --------
    (symbol, qty, tp_price, sl_stop, sl_limit, tif, dry_run, allow_mainnet, auto_adjust)
    -> {"ok":bool, "resp"?:dict, "dry_run"?:True, "price_relation"?:str, "payload"?:dict,
        "listClientOrderId"?:str, "aboveClientOrderId"?:str, "belowClientOrderId"?:str, "error"?:str}
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)
    tick = ff.get("tickSize")
    step = ff.get("stepQty")
    base, quote = get_symbol_assets(sx)

    # 현재가 및 가격 정규화
    last = Decimal(str(get_price(symbol)))
    p_tp  = normalize_price(tp_price, ff)
    p_stp = normalize_price(sl_stop,  ff)
    if sl_limit is None:
        # 보수적: stop - 1tick
        sl_limit = float(Decimal(p_stp) - (tick))
    p_slm = normalize_price(sl_limit, ff)

    # 수량 정규화
    q_dec = normalize_qty(qty, ff)

    # (1) 잔고 사전검증: 베이스 자산이 충분한가
    balances = get_balances_map()
    base_free = balances.get(base, Decimal("0"))
    if q_dec > base_free:
        return {"ok": False, "reason": "INSUFFICIENT_BASE_BALANCE",
                "required_qty": to_api_str(q_dec, step),
                "base_free": to_api_str(base_free, step), "asset": base}

    # (2) 가격 관계식 1차 검증: tp > last > stop
    if not (Decimal(p_tp) > last > Decimal(p_stp)):
        return {"ok": False, "reason": "PRICE_RELATION_INVALID(SELL)",
                "explain": f"tp({p_tp}) > last({to_api_str(last, tick)}) > stop({p_stp})"}
    # SL limit은 stop 이하 권장
    if not (Decimal(p_slm) <= Decimal(p_stp)):
        return {"ok": False, "reason": "STOP_LIMIT_RELATION_INVALID",
                "explain": f"stopLimitPrice({p_slm}) <= stopPrice({p_stp}) 권장"}

    # (3) 전송 직전(last 재조회) + 자동 보정(옵션)
    last2 = Decimal(str(get_price(symbol)))
    if auto_adjust:
        # SELL LIMIT_MAKER: 지정가가 반드시 last2보다 커야 메이커 보장
        if Decimal(p_tp) <= last2:
            p_tp = normalize_price(float(last2 + tick), ff)
        # STOP_LIMIT의 limit(price)는 stop 이하로(즉시체결 방지)
        if Decimal(p_slm) > Decimal(p_stp):
            p_slm = normalize_price(float(Decimal(p_stp) - tick), ff)

    # 보정 후에도 관계식 깨지면 실패
    if not (Decimal(p_tp) > last2 > Decimal(p_stp)):
        return {"ok": False, "reason": "PRICE_RELATION_CHANGED",
                "prev": to_api_str(last, tick), "now": to_api_str(last2, tick),
                "hint": "auto_adjust=False이거나 보정 한계 초과"}

    # (4) MIN_NOTIONAL: TP/SL 두 다리 대비(더 큰 요구치 기준)
    min_notional = ff.get("minNotional")
    if min_notional:
        q_need_tp  = _ceil_qty_for_notional(min_notional, Decimal(p_tp),  step)
        q_need_slm = _ceil_qty_for_notional(min_notional, Decimal(p_slm), step)
        q_need = max(q_need_tp, q_need_slm)
        if q_dec < q_need:
            return {"ok": False, "reason": "MIN_NOTIONAL_NOT_SATISFIED",
                    "required_qty": to_api_str(q_need, step),
                    "given_qty": to_api_str(q_dec, step),
                    "hint": "수량을 늘리거나 가격 조정 필요"}

    # (5) 문자열 포맷(전송/리턴 일치)
    qty_str   = to_api_str(q_dec, step)
    tp_str    = to_api_str(Decimal(p_tp),  tick)
    stop_str  = to_api_str(Decimal(p_stp), tick)
    slm_str   = to_api_str(Decimal(p_slm), tick)
    last_str  = to_api_str(last2,          tick)

    # (6) dry_run: 실제 호출 없이 payload 미리보기
    if dry_run:
        return {"ok": True, "dry_run": True,
                "price_relation": f"{tp_str} > last({last_str}) > {stop_str}",
                "payload": {"symbol": symbol, "side": "SELL", "quantity": qty_str,
                            "aboveType": "LIMIT_MAKER", "abovePrice": tp_str,
                            "belowType": "STOP_LOSS_LIMIT", "belowStopPrice": stop_str,
                            "belowPrice": slm_str, "belowTimeInForce": tif,
                            "newOrderRespType": "RESULT"}}

    # (7) 실주문: 아이템포턴시 ID 고정 + 간단 재시도
    ids = _new_list_ids("oco-sell")
    def _call():
        return place_oco_order(
            symbol, "SELL",
            quantity=qty_str,
            aboveType="LIMIT_MAKER", abovePrice=tp_str,
            belowType="STOP_LOSS_LIMIT", belowStopPrice=stop_str, belowPrice=slm_str,
            belowTimeInForce=tif,
            listClientOrderId=ids["listClientOrderId"],
            aboveClientOrderId=ids["aboveClientOrderId"],
            belowClientOrderId=ids["belowClientOrderId"],
            newOrderRespType="RESULT",
            allow_mainnet=allow_mainnet,
        )
    ok, res = _retry_oco(_call)
    if ok: return {"ok": True, "resp": res, **ids}
    msg = str(res)
    if "insufficient balance" in msg.lower() or "-2010" in msg:
        return {"ok": False, "error": "INSUFFICIENT_BALANCE", "detail": msg, **ids}
    return {"ok": False, "error": msg, **ids}


# =========================
# 6) OCO: BUY (돌파 + 대안)
# =========================
def oco_buy_breakout(
    symbol: str,
    qty: float,
    *,
    entry_stop: float,                 # 위 다리: STOP(돌파 진입) stopPrice
    fallback_limit: float,             # 아래 다리: LIMIT_MAKER(저가 대안)
    entry_limit: float | None = None,  # stop 진입시 체결용 limit(price). None이면 stop + 1tick
    tif: str = "GTC",
    dry_run: bool = True,
    allow_mainnet: bool = False,
    auto_adjust: bool = False,         # True면 전송 직전 메이커 보정
) -> Dict[str, Any]:
    """
    역할
    ----
    - 돌파 진입용 OCO(BUY)를 생성한다.
      * 위 다리: STOP_LOSS_LIMIT(entry_stop/entry_limit)
      * 아래 다리: LIMIT_MAKER(fallback_limit)
    - 가격 관계식(BUY):  limit < Last < stop  을 사전 검증
    - stopLimitPrice는 stopPrice 이상 권장(BUY)
    - 전송 직전(last 재조회) auto_adjust=True면 tickSize로 자동 보정

    출력
    ----
    {"ok":bool, "resp"?:dict, "dry_run"?:True, "price_relation"?:str, "payload"?:dict,
     "listClientOrderId"?:str, "aboveClientOrderId"?:str, "belowClientOrderId"?:str, "error"?:str}
    """
    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)
    tick = ff.get("tickSize")
    step = ff.get("stepQty")
    base, quote = get_symbol_assets(sx)

    # 현재가 및 가격 정규화
    last = Decimal(str(get_price(symbol)))
    p_stp = normalize_price(entry_stop,  ff)     # 위 다리 stopPrice
    if entry_limit is None:
        # 보수적: stop + 1tick
        entry_limit = float(Decimal(p_stp) + tick)
    p_slm = normalize_price(entry_limit, ff)     # 위 다리 limit(price)
    p_lim = normalize_price(fallback_limit, ff)  # 아래 다리 limit maker

    # 수량 정규화
    q_dec = normalize_qty(qty, ff)

    # (1) 잔고 사전검증: 필요한 quote(USDT) 추정(두 후보 가격 중 최대 notional 기준)
    cand_prices = [Decimal(p_slm), Decimal(p_lim)]
    need_quote = _max_required_quote_for_buy(q_dec, cand_prices)
    balances = get_balances_map()
    quote_free = balances.get(quote, Decimal("0"))
    if need_quote > quote_free:
        return {"ok": False, "reason": "INSUFFICIENT_QUOTE_BALANCE",
                "required_quote": to_api_str(need_quote, tick),
                "quote_free": to_api_str(quote_free, tick), "asset": quote}

    # (2) 가격 관계식 1차 검증: limit < last < stop
    if not (Decimal(p_lim) < last < Decimal(p_stp)):
        return {"ok": False, "reason": "PRICE_RELATION_INVALID(BUY)",
                "explain": f"limit({p_lim}) < last({to_api_str(last, tick)}) < stop({p_stp})"}
    # BUY에서 stopLimitPrice는 stopPrice 이상 권장(즉시체결 방지)
    if not (Decimal(p_slm) >= Decimal(p_stp)):
        return {"ok": False, "reason": "STOP_LIMIT_RELATION_INVALID",
                "explain": f"stopLimitPrice({p_slm}) >= stopPrice({p_stp}) 권장"}

    # (3) 전송 직전(last 재조회) + 자동 보정(옵션)
    last2 = Decimal(str(get_price(symbol)))
    if auto_adjust:
        # BUY LIMIT_MAKER: 지정가가 반드시 last2보다 작아야 메이커 보장
        if Decimal(p_lim) >= last2:
            p_lim = normalize_price(float(last2 - tick), ff)
        # STOP_LIMIT의 limit(price)는 stop 이상
        if Decimal(p_slm) < Decimal(p_stp):
            p_slm = normalize_price(float(Decimal(p_stp) + tick), ff)

    # 보정 후에도 관계식 깨지면 실패
    if not (Decimal(p_lim) < last2 < Decimal(p_stp)):
        return {"ok": False, "reason": "PRICE_RELATION_CHANGED",
                "prev": to_api_str(last, tick), "now": to_api_str(last2, tick),
                "hint": "auto_adjust=False이거나 보정 한계 초과"}

    # (4) MIN_NOTIONAL: 위/아래 다리 대비(더 큰 요구치 기준)
    min_notional = ff.get("minNotional")
    if min_notional:
        q_need_up  = _ceil_qty_for_notional(min_notional, Decimal(p_slm), step)
        q_need_low = _ceil_qty_for_notional(min_notional, Decimal(p_lim), step)
        q_need = max(q_need_up, q_need_low)
        if q_dec < q_need:
            return {"ok": False, "reason": "MIN_NOTIONAL_NOT_SATISFIED",
                    "required_qty": to_api_str(q_need, step),
                    "given_qty": to_api_str(q_dec, step),
                    "hint": "수량을 늘리거나 가격 조정 필요"}

    # (5) 문자열 포맷(전송/리턴 일치)
    qty_str  = to_api_str(q_dec, step)
    lim_str  = to_api_str(Decimal(p_lim), tick)
    stop_str = to_api_str(Decimal(p_stp), tick)
    slm_str  = to_api_str(Decimal(p_slm), tick)
    last_str = to_api_str(last2,          tick)

    # (6) dry_run: 실제 호출 없이 payload 미리보기
    if dry_run:
        return {"ok": True, "dry_run": True,
                "price_relation": f"{lim_str} < last({last_str}) < {stop_str}",
                "payload": {"symbol": symbol, "side": "BUY", "quantity": qty_str,
                            "aboveType": "STOP_LOSS_LIMIT", "aboveStopPrice": stop_str,
                            "abovePrice": slm_str, "aboveTimeInForce": tif,
                            "belowType": "LIMIT_MAKER", "belowPrice": lim_str,
                            "newOrderRespType": "RESULT"}}

    # (7) 실주문: 아이템포턴시 ID 고정 + 간단 재시도
    ids = _new_list_ids("oco-buy")
    def _call():
        return place_oco_order(
            symbol, "BUY",
            quantity=qty_str,
            aboveType="STOP_LOSS_LIMIT", aboveStopPrice=stop_str, abovePrice=slm_str, aboveTimeInForce=tif,
            belowType="LIMIT_MAKER",    belowPrice=lim_str,
            listClientOrderId=ids["listClientOrderId"],
            aboveClientOrderId=ids["aboveClientOrderId"],
            belowClientOrderId=ids["belowClientOrderId"],
            newOrderRespType="RESULT",
            allow_mainnet=allow_mainnet,
        )
    ok, res = _retry_oco(_call)
    if ok: return {"ok": True, "resp": res, **ids}
    msg = str(res)
    if "insufficient balance" in msg.lower() or "-2010" in msg:
        return {"ok": False, "error": "INSUFFICIENT_BALANCE", "detail": msg, **ids}
    return {"ok": False, "error": msg, **ids}
