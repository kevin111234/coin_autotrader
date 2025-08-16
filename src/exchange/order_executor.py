# src/order_executor.py
from __future__ import annotations
import time, uuid
from typing import Optional, Dict, Any
from decimal import Decimal

from src.exchange.market import get_price, get_exchange_info
from src.exchange.orders import place_test_order, place_order
from src.exchange.core import ENV  # mainnet 보호에 사용(allow_mainnet과 결합)
from src.exchange.filters import (
    extract_filters, normalize_qty, normalize_price, ensure_min_notional
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
    input:
      - resp: (가능하면) 서버 JSON 응답
      - status: HTTP 상태 코드
    output: True/False
    연결:
      - 아래 주문 함수들의 재시도 루프에서 사용
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
    input:
      - symbol: "BTCUSDT" 같은 심볼 (exchange.market.get_price/get_exchange_info에 연결)
      - quote_usdt: 사용할 USDT 예산
      - dry_run: True면 place_test_order, False면 place_order
      - allow_mainnet: True여야 mainnet에서 실체결 허용(기본 False 보호)
    output:
      - dict: {"ok": bool, "resp"?: dict, "price": float, "qty": float, "clientOrderId": str, "error"?: str, "reason"?: str}
    주요 연결:
      - 입력: exchange.market.get_exchange_info(), get_price()
      - 내부: filters.extract_filters/normalize_qty/ensure_min_notional
      - 출력: exchange.orders.place_test_order/place_order 로 전송
      - 후속: 상위 레이어(main/runner)가 주문 결과를 로그/Slack/notifier로 전달
    """
    # 1) 심볼 정보 & 필터 추출
    sx = get_exchange_info(symbol)
    ff = extract_filters(sx)

    # 2) 현재가 조회 → 예산 대비 raw qty 계산
    px = Decimal(str(get_price(symbol)))
    raw_qty = Decimal(str(quote_usdt)) / px

    # 3) LOT_SIZE 보정 → MIN_NOTIONAL 충족 시도
    q1 = normalize_qty(raw_qty, ff)
    px, q2, ok = ensure_min_notional(px, q1, ff)
    qty = q2 if ok else q1
    if qty <= 0:
        return {"ok": False, "reason": "MIN_QTY_NOT_SATISFIED", "price": float(px), "qty": float(qty)}

    cid = _new_client_id("mbuy")

    def _call():
        # 역할 (Role):
        #   Binance REST API 요청을 공통적으로 처리하는 내부 유틸 함수.
        #   - HTTP 요청 전송 (GET/POST/DELETE)
        #   - API Key 헤더 및 HMAC-SHA256 시그니처 생성
        #   - 서버 타임스탬프 보정 (sync_time 기반)
        #   - 에러 처리 및 JSON 응답 반환
        #
        # Input (호출 시 인자):
        #   - method (str): "GET", "POST", "DELETE" 중 하나
        #   - path (str): 엔드포인트 경로 (예: "/api/v3/order")
        #   - params (dict): 요청 파라미터 (symbol, side, type, quantity 등)
        #   - auth (bool): True면 시그니처/타임스탬프 필요 (private endpoint)
        #
        # 연결(입력)되는 상위 함수/파일:
        #   - create_order() (주문 생성)
        #   - get_order() (주문 조회)
        #   - cancel_order() (주문 취소)
        #   - get_balance() (잔고 조회)
        #   => 모두 order_executor.py 혹은 order_api.py 에서 호출
        #
        # Output (반환값):
        #   - dict: Binance 응답 JSON (성공 시)
        #   - {} 또는 None: 실패 시 (에러 로그 출력 후)
        #
        # 연결(출력)되는 하위 함수/파일:
        #   - order_executor.py 의 고수준 함수들이 결과 JSON을 받아
        #     portfolio.py, strategy 모듈, main.py 등에서 활용
        # ============================================================
        if dry_run:
            return place_test_order(symbol, "BUY", "MARKET", quantity=float(qty))
        return place_order(symbol, "BUY", "MARKET",
                           quantity=float(qty),
                           newClientOrderId=cid,
                           allow_mainnet=allow_mainnet)

    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            res = _call()  # core.request 내부에서 HTTP 에러 raise
            return {"ok": True, "resp": res, "price": float(px), "qty": float(qty), "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY:
                time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, "error": str(last_err), "price": float(px), "qty": float(qty), "clientOrderId": cid}

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
    input:
      - symbol: 심볼
      - price, qty: 사용자가 의도한 가격/수량
      - tif: TimeInForce ("GTC","IOC","FOK")
      - dry_run, allow_mainnet: 위와 동일
    output:
      - dict: {"ok": bool, "resp"?: dict, "price": float, "qty": float, "clientOrderId": str, "error"?: str, "reason"?: str}
    연결:
      - 입력: exchange.market.get_exchange_info()
      - 내부: filters.normalize_price/normalize_qty/ensure_min_notional
      - 출력: exchange.orders.place_test_order/place_order
      - 후속: 상위 레이어에서 주문 ID 기록/추적(부분체결 모니터링 등)
    """
    sx = get_exchange_info(symbol)
    ff = extract_filters(sx)

    p = normalize_price(price, ff)
    q = normalize_qty(qty, ff)
    p, q, ok = ensure_min_notional(p, q, ff)
    if (q <= 0) or (not ok):
        return {"ok": False, "reason": "MIN_NOTIONAL_NOT_SATISFIED", "price": float(p), "qty": float(q)}

    cid = _new_client_id("lbuy")

    def _call():
        if dry_run:
            return place_test_order(symbol, "BUY", "LIMIT", quantity=float(q), price=float(p), timeInForce=tif)
        return place_order(symbol, "BUY", "LIMIT",
                           quantity=float(q), price=float(p), timeInForce=tif,
                           newClientOrderId=cid, allow_mainnet=allow_mainnet)

    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            res = _call()
            return {"ok": True, "resp": res, "price": float(p), "qty": float(q), "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY:
                time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, "error": str(last_err), "price": float(p), "qty": float(q), "clientOrderId": cid}

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
    input:
      - symbol: 심볼
      - qty: 매도할 수량(정규화 대상)
      - dry_run/allow_mainnet: 위와 동일
    output:
      - dict: {"ok": bool, "resp"?: dict, "qty": float, "clientOrderId": str, "error"?: str, "reason"?: str}
    연결:
      - 입력: exchange.market.get_exchange_info()
      - 내부: filters.normalize_qty
      - 출력: exchange.orders.place_test_order/place_order
      - 후속: 상위 레이어가 체결 결과에 따라 포지션/잔고 업데이트
    """
    sx = get_exchange_info(symbol)
    ff = extract_filters(sx)

    q = normalize_qty(qty, ff)
    if q <= 0:
        return {"ok": False, "reason": "MIN_QTY_NOT_SATISFIED", "qty": float(q)}

    cid = _new_client_id("msell")

    def _call():
        if dry_run:
            return place_test_order(symbol, "SELL", "MARKET", quantity=float(q))
        return place_order(symbol, "SELL", "MARKET",
                           quantity=float(q), newClientOrderId=cid, allow_mainnet=allow_mainnet)

    last_err = None
    for i in range(MAX_RETRY + 1):
        try:
            res = _call()
            return {"ok": True, "resp": res, "qty": float(q), "clientOrderId": cid}
        except Exception as e:
            last_err = e
            if i < MAX_RETRY:
                time.sleep(BACKOFF_S[i]); continue
            break
    return {"ok": False, "error": str(last_err), "qty": float(q), "clientOrderId": cid}
