# src/trade/auto_oco.py
# -*- coding: utf-8 -*-
"""
시장가 진입 직후 OCO(TP/SL) 자동 부착 유틸 (Spot/롱 기준)

흐름
----
market BUY(quote 또는 qty) → 체결 대기(wait_fill) → 평균 체결가/체결수량 산출
→ TP/SL 가격 계산(퍼센트 또는 절대가) → OCO SELL 부착(안전검증/메이커 보정)

특징
----
- fast-path: 시장가 응답이 이미 FILLED/PARTIALLY_FILLED면 폴링 생략
- 체결 대기: NEW/PARTIALLY_FILLED → FILLED까지 폴링 (타임아웃/취소 처리)
- 평균 체결가: cummulativeQuoteQty / executedQty (Binance 응답)로 계산
- 가격계산: pct/absolute 혼합지원 + 심볼 필터 정규화
- OCO 부착: order_executor.oco_sell_tp_sl 사용(auto_adjust 지원)
- dry_run=True: "시장가 체결"은 현재가를 가정, OCO는 payload 미리보기
"""

from __future__ import annotations
import time
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple

from src.exchange.registry import OrderRegistry
from src.exchange.orders import get_order, get_order_safe
from src.exchange.market import get_symbol_info, get_price
from src.exchange.filters import extract_filters, normalize_price, to_api_str
from src.order_executor import (
    market_buy_by_quote,  # 시장가 매수(quote 기반)
    oco_sell_tp_sl        # OCO 부착(SELL TP/SL)
)

# -------------------------------
# 내부: 주문 체결 대기/요약 추출
# -------------------------------

def _wait_fill(symbol: str, *, orderId: int | None, clientOrderId: str | None,
               timeout_s: float = 15.0, poll_s: float = 0.5) -> Dict[str, Any]:
    """
    역할: 주문 상태 폴링. orderId 우선 → 실패 시 origClientOrderId 폴백.
    반환:
      {"status": ..., "executedQty": Decimal, "avgPrice": Decimal|None, "raw": resp, "lastError": str|None}
    """
    t0 = time.time()
    last = None
    last_err = None
    while time.time() - t0 <= timeout_s:
        try:
            r = get_order_safe(symbol, orderId=orderId, origClientOrderId=clientOrderId)
            last = r
            st = r.get("status")
            exec_qty = Decimal(r.get("executedQty", "0"))
            cum_quote = Decimal(r.get("cummulativeQuoteQty", "0"))
            avg_px = (cum_quote / exec_qty) if exec_qty > 0 else None
            if st in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                return {"status": st, "executedQty": exec_qty, "avgPrice": avg_px, "raw": r, "lastError": None}
            # NEW/PARTIALLY_FILLED → 대기
        except Exception as e:
            last_err = str(e)
        time.sleep(poll_s)

    # 타임아웃 요약(부분체결 있으면 그 값 유지)
    exec_qty = Decimal(last.get("executedQty", "0")) if last else Decimal("0")
    cum_quote = Decimal(last.get("cummulativeQuoteQty", "0")) if last else Decimal("0")
    avg_px = (cum_quote / exec_qty) if exec_qty > 0 else None
    return {"status": "TIMEOUT", "executedQty": exec_qty, "avgPrice": avg_px, "raw": last, "lastError": last_err}

# -----------------------------------------
# 내부: TP/SL 가격 계산(퍼센트/절대 혼합 지원)
# -----------------------------------------

def _calc_tp_sl_prices(avg_fill: Decimal,
                       ff: Dict[str, Any],
                       *,
                       tp_pct: Optional[float],
                       sl_pct: Optional[float],
                       tp_abs: Optional[float],
                       sl_abs: Optional[float]) -> tuple[str, str]:
    """
    역할: 평균 체결가 기준 TP/SL 목표가 계산 후 필터에 맞게 정규화 → 문자열 반환
    우선순위:
      - 절대가(tp_abs/sl_abs)가 주어지면 그것을 사용
      - 아니면 pct(tp_pct/sl_pct) 사용
    """
    # tickSize 사용
    tick = ff.get("tickSize")
    if tp_abs is not None:
        tp_raw = Decimal(str(tp_abs))
    else:
        gain = Decimal(str(tp_pct or 0))          # 예: 0.01 = +1%
        tp_raw = avg_fill * (Decimal("1") + gain)

    if sl_abs is not None:
        sl_raw = Decimal(str(sl_abs))
    else:
        loss = Decimal(str(sl_pct or 0))          # 예: 0.005 = -0.5%
        sl_raw = avg_fill * (Decimal("1") - loss)

    # 가격 정규화
    tp_norm = normalize_price(float(tp_raw), ff)
    sl_norm = normalize_price(float(sl_raw), ff)
    tp_str = to_api_str(Decimal(tp_norm), tick)
    sl_str = to_api_str(Decimal(sl_norm), tick)
    return tp_str, sl_str

def _avg_from_resp(resp: Dict[str, Any], symbol: str) -> Tuple[Decimal, Decimal]:
    """
    역할: 주문 응답에서 executedQty/avgPrice를 뽑아낸다.
    - avgPrice = cummulativeQuoteQty / executedQty (없으면 현재가 보정)
    반환: (executedQty, avgPrice) — 모두 Decimal
    """
    exec_qty = Decimal(resp.get("executedQty", "0"))
    cum_quote = Decimal(resp.get("cummulativeQuoteQty", "0"))
    if exec_qty > 0:
        avg_px = cum_quote / exec_qty
    else:
        avg_px = Decimal(str(get_price(symbol)))
    return exec_qty, avg_px

# =========================================
# 공개: 시장가 매수 → OCO SELL 자동 부착
# =========================================

def market_buy_then_attach_oco(
    symbol: str,
    *,
    # 진입 파라미터(둘 중 하나 지정)
    quote_usdt: Optional[float] = None,    # 예산 기반 진입(권장)
    buy_qty: Optional[float] = None,       # 수량 기반 진입(미구현)
    use_quote_order_qty: bool = False,     # 시장가에 quoteOrderQty 사용 여부(정밀도 안전)
    # OCO 파라미터
    tp_pct: Optional[float] = 0.01,        # +1% 기본
    sl_pct: Optional[float] = 0.005,       # -0.5% 기본
    tp_abs: Optional[float] = None,        # 절대가 우선
    sl_abs: Optional[float] = None,
    tif: str = "GTC",
    auto_adjust: bool = True,              # 메이커 보정 기본 ON
    # 실행 제어
    dry_run: bool = True,
    allow_mainnet: bool = False,
    wait_timeout_s: float = 15.0,
    poll_s: float = 0.5,
) -> Dict[str, Any]:
    """
    역할
    ----
    1) 시장가 매수 실행(quote_usdt 또는 buy_qty)
    2) 체결 대기(wait_fill) 후 평균체결가/체결수량 산출 (fast-path 우선)
    3) TP/SL 가격 계산(퍼센트 또는 절대가)
    4) OCO SELL(TP/SL) 부착 (auto_adjust로 메이커/관계식 안전보정)

    반환
    ----
    {
      "ok": bool,
      "entry": {...},                 # 시장가 진입 응답/요약
      "oco":   {...} | None,          # OCO 응답/페이로드
      "avg_fill_price": str | None,
      "filled_qty": str,
      "note"?: str
    }
    """
    REG = OrderRegistry()  # 기본 경로 runtime/orders_state.json
    if not quote_usdt and not buy_qty:
        return {"ok": False, "error": "quote_usdt 또는 buy_qty 중 하나는 필요"}

    sx = get_symbol_info(symbol)
    ff = extract_filters(sx)
    tick = ff.get("tickSize")
    step = ff.get("stepQty")

    # --------------------------
    # 1) 시장가 매수 실행
    # --------------------------
    if quote_usdt is not None:
        ent = market_buy_by_quote(
            symbol, quote_usdt,
            dry_run=dry_run, allow_mainnet=allow_mainnet,
            use_quote_order_qty=use_quote_order_qty
        )
        entry_qty_str = ent.get("qty", "0")
    else:
        # buy_qty 기반 매수 래퍼 미구현(quote_usdt 경로 사용 권장)
        return {"ok": False, "error": "buy_qty 기반 시장가 매수 래퍼는 아직 미구현(quote_usdt 사용 권장)"}

    if not ent.get("ok"):
        return {"ok": False, "entry": ent, "oco": None}

    # DRY-RUN: 현재가/요청수량으로 가정 → OCO payload 미리보기
    if dry_run:
        last = Decimal(str(get_price(symbol)))
        # 평균체결가 = 현재가 가정
        avg_price_dec = last
        # 체결수량 = 요청 qty(정규화 이미 되어 있음)
        filled_qty_dec = Decimal(entry_qty_str)
        # TP/SL 계산
        tp_str, sl_str = _calc_tp_sl_prices(avg_price_dec, ff,
                                            tp_pct=tp_pct, sl_pct=sl_pct,
                                            tp_abs=tp_abs, sl_abs=sl_abs)
        oco = oco_sell_tp_sl(
            symbol, float(filled_qty_dec),
            tp_price=float(tp_str), sl_stop=float(sl_str),
            sl_limit=None, tif=tif,
            dry_run=True, auto_adjust=auto_adjust, allow_mainnet=False
        )
        return {
            "ok": True,
            "entry": ent,
            "avg_fill_price": to_api_str(avg_price_dec, tick),
            "filled_qty": to_api_str(filled_qty_dec, step),
            "oco": oco,
            "note": "dry_run: 현재가 기준 체결 가정 + OCO payload 미리보기"
        }

    # --------------------------
    # 2) 체결 대기 및 평균체결가 산출
    # --------------------------
    resp = ent.get("resp", {}) or {}
    st0 = resp.get("status")
    ord_id = resp.get("orderId")
    entry_cid = ent.get("clientOrderId")

    # (A) fast-path: 이미 FILLED/PARTIALLY_FILLED면 폴링 생략
    if st0 in {"FILLED", "PARTIALLY_FILLED"}:
        exec_qty_dec, avg_price_dec = _avg_from_resp(resp, symbol)
    else:
        # (B) 폴링: orderId 우선, 안되면 origClientOrderId 폴백
        w = _wait_fill(symbol, orderId=ord_id, clientOrderId=entry_cid,
                       timeout_s=wait_timeout_s, poll_s=poll_s)
        st = w["status"]
        exec_qty_dec = w["executedQty"]
        avg_price_dec = w["avgPrice"] if w["avgPrice"] is not None else Decimal(str(get_price(symbol)))

        if st in ("CANCELED", "REJECTED", "EXPIRED"):
            return {"ok": False, "entry": ent, "oco": None, "error": f"entry {st}", "lastError": w.get("lastError")}
        # TIMEOUT/NEW/PARTIALLY_FILLED이라도 exec_qty가 있으면 그 수량으로 진행
        if exec_qty_dec <= 0:
            return {"ok": False, "entry": ent, "oco": None, "error": "entry fill timeout", "lastError": w.get("lastError")}
    resp = ent.get("resp") or {}
    if not dry_run and resp:
        REG.record_entry_from_resp(resp)

    # --------------------------
    # 3) TP/SL 가격 계산
    # --------------------------
    tp_str, sl_str = _calc_tp_sl_prices(avg_price_dec, ff,
                                        tp_pct=tp_pct, sl_pct=sl_pct,
                                        tp_abs=tp_abs, sl_abs=sl_abs)

    # OCO SELL 부착 전 중복 방지 체크
    group_id = (resp.get("clientOrderId") if resp else ent.get("clientOrderId")) or ""
    if not dry_run:
        if not REG.can_attach_oco(symbol, group_id=group_id):
            return {
                "ok": False,
                "entry": ent,
                "oco": None,
                "error": "DUPLICATE_OCO_BLOCKED",
                "note": "해당 심볼에 진행 중인 OCO가 있어 부착 차단됨"
            }

    # --------------------------
    # 4) OCO SELL 부착 (filled 수량 기준)
    # --------------------------
    oco = oco_sell_tp_sl(
        symbol, float(exec_qty_dec),
        tp_price=float(tp_str), sl_stop=float(sl_str),
        sl_limit=None, tif=tif,
        dry_run=False, auto_adjust=auto_adjust, allow_mainnet=allow_mainnet
    )

    # OCO 생성 성공 뒤 기록
    if not dry_run and oco.get("ok"):
        REG.record_oco_from_resp(oco["resp"], group_id=group_id)

    return {
        "ok": bool(oco.get("ok")),
        "entry": ent,
        "avg_fill_price": to_api_str(avg_price_dec, tick) if avg_price_dec is not None else None,
        "filled_qty": to_api_str(exec_qty_dec, step),
        "oco": oco
    }
