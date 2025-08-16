# src/exchange/filters.py
from __future__ import annotations
from typing import Dict, Any, Tuple
from decimal import Decimal, ROUND_DOWN

def _to_dec(x) -> Decimal:
    """
    역할: 안전한 Decimal 변환 (float → 문자열 경유로 정확도 보존)
    input: 임의의 수치형
    output: Decimal 인스턴스
    연결: 내부 보정 계산에서만 사용 (외부 모듈과 직접 연결 없음)
    """
    return x if isinstance(x, Decimal) else Decimal(str(x))

def _quantize_down(val: Decimal, step: Decimal) -> Decimal:
    """
    역할: step 격자(LOT_SIZE/PRICE_FILTER)에 맞춰 '내림' 정규화
    input:
      - val: 정규화 대상 수치
      - step: 격자 크기(stepSize, tickSize)
    output: 격자에 맞춘 Decimal 값
    연결:
      - normalize_qty(), normalize_price()에서 사용
      - 최종 결과는 order_executor.* 에서 place_* 주문에 전달됨
    """
    q = (val / step).to_integral_value(rounding=ROUND_DOWN)
    return (q * step).normalize()

def extract_filters(symbol_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    역할: exchangeInfo 응답에서 LOT_SIZE/PRICE_FILTER/MIN_NOTIONAL 관련 필터만 추출
    input:
      - symbol_info: exchange.market.get_exchange_info()가 준 dict
    output:
      - {'minQty','maxQty','stepQty','minPrice','maxPrice','tickSize','minNotional'} 중 일부를 포함한 dict
    연결:
      - order_executor.* 가 수량/가격 보정 전에 호출
    주의:
      - MIN_NOTIONAL/NOTIONAL 키 이름은 환경에 따라 다름 → 둘 다 대응
    """
    out = {}
    for f in symbol_info["filters"]:
        t = f["filterType"]
        if t == "LOT_SIZE":
            out["minQty"]  = Decimal(f["minQty"])
            out["maxQty"]  = Decimal(f["maxQty"])
            out["stepQty"] = Decimal(f["stepSize"])
        elif t == "PRICE_FILTER":
            out["minPrice"]  = Decimal(f["minPrice"])
            out["maxPrice"]  = Decimal(f["maxPrice"])
            out["tickSize"]  = Decimal(f["tickSize"])
        elif t in ("MIN_NOTIONAL", "NOTIONAL"):
            if "minNotional" in f:
                out["minNotional"] = Decimal(str(f["minNotional"]))
            elif "notional" in f:
                out["minNotional"] = Decimal(str(f["notional"]))
    return out

def normalize_qty(raw_qty, f: Dict[str, Any]) -> Decimal:
    """
    역할: LOT_SIZE 제약(최소/최대/스텝)에 맞게 '수량' 정규화
    input:
      - raw_qty: 사용자가 의도한 수량 (Decimal/float/int)
      - f: extract_filters()가 뽑아준 필터 dict
    output:
      - 격자에 맞춘 qty (Decimal). 최소 미만이면 0 반환(주문 스킵 판단용)
    연결:
      - order_executor.market_buy_by_quote / limit_buy / market_sell_qty
      - 최종 qty는 exchange.orders.place_* 로 전달
    """
    q = _to_dec(raw_qty)
    if "stepQty" in f:
        q = _quantize_down(q, f["stepQty"])
    if "minQty" in f and q < f["minQty"]:
        return Decimal("0")
    if "maxQty" in f and q > f["maxQty"]:
        q = f["maxQty"]
    return q

def normalize_price(raw_price, f: Dict[str, Any]) -> Decimal:
    """
    역할: PRICE_FILTER 제약(최소/최대/틱)에 맞게 '가격' 정규화
    input:
      - raw_price: 사용자가 의도한 가격
      - f: extract_filters() 결과
    output:
      - 격자에 맞춘 price (Decimal)
    연결:
      - order_executor.limit_buy 등 지정가 주문 경로
      - 최종 price는 exchange.orders.place_order 로 전달
    """
    p = _to_dec(raw_price)
    if "tickSize" in f:
        p = _quantize_down(p, f["tickSize"])
    if "minPrice" in f and p < f["minPrice"]:
        p = f["minPrice"]
    if "maxPrice" in f and p > f["maxPrice"]:
        p = f["maxPrice"]
    return p

def ensure_min_notional(price, qty, f: Dict[str, Any]) -> Tuple[Decimal, Decimal, bool]:
    """
    역할: MIN_NOTIONAL 제약(가격*수량) 충족 여부 보장
    input:
      - price: 주문 가격 (Decimal/float)
      - qty: 정규화된 수량(Decimal/float)
      - f: extract_filters() 결과
    output:
      - (price, qty, ok)
        * ok=True면 notional 충족
        * ok=False면 qty 상향 불가로 실패(상위 로직에서 주문 스킵/재계산 결정)
    연결:
      - order_executor.limit_buy / market_buy_by_quote
      - 결과는 그대로 exchange.orders.place_* 로 전달
    구현:
      - stepQty 격자에 맞춰 qty를 가능한 최소로 상향 조정해 minNotional 충족 시도
    """
    p = _to_dec(price); q = _to_dec(qty)
    if "minNotional" not in f:
        return p, q, True
    notional = p * q
    if notional >= f["minNotional"]:
        return p, q, True
    if "stepQty" in f and f["stepQty"] > 0:
        needed = (f["minNotional"] / p)
        steps = (needed / f["stepQty"]).to_integral_value(rounding=ROUND_DOWN)
        q2 = (steps * f["stepQty"]).normalize()
        if (q2 * p) < f["minNotional"]:
            q2 = (q2 + f["stepQty"]).normalize()
        q2 = normalize_qty(q2, f)
        if q2 > 0 and (q2 * p) >= f["minNotional"]:
            return p, q2, True
    return p, q, False
