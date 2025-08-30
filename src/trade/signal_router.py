# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Optional, Dict, Any
from decimal import Decimal

from src.exchange.account import get_balances_map, get_symbol_assets
from src.exchange.market import get_price, get_symbol_info
from src.exchange.filters import extract_filters, normalize_qty, to_api_str
from src.order_executor import (
    market_buy_by_quote, market_sell_qty, limit_sell
)
from src.exchange.auto_oco import market_buy_then_attach_oco
from src.trade.order_manager import OrderManager
from src.notifier.slack_notifier import notify, fmt_order_msg

DEFAULTS = {
    "buy_quote_usdt": 10.0,      # 기본 진입 예산
    "tp_pct": 0.01,              # +1%
    "sl_pct": 0.005,             # -0.5%
    "tif": "GTC",
    "auto_adjust": True,
}

class SignalRouter:
    """
    역할: 전략 신호('BUY'/'SELL')를 받아 주문 실행 + OCO 부착 + 알림 전송을 담당.
    - 중복 보호: active OCO/entry 존재 시 신규 진입 차단
    - dry_run: 전역 시뮬/리허설 모드
    """
    def __init__(self, order_manager: OrderManager, *, dry_run: bool = True, allow_mainnet: bool = False):
        self.om = order_manager
        self.dry_run = dry_run
        self.allow_mainnet = allow_mainnet

    # ----------------------------
    # 내부 헬퍼: 보유 베이스 수량 계산
    # ----------------------------
    def _free_base_qty(self, symbol: str) -> Decimal:
        sx = get_symbol_info(symbol)
        base, _ = get_symbol_assets(sx)
        bmap = get_balances_map()
        return bmap.get(base, Decimal("0"))

    def can_open_new_position(self, symbol: str) -> bool:
        """
        역할: 활성 OCO가 있거나 최근 엔트리가 살아있으면 신규 진입 차단
        """
        active = self.om.state.active_by_symbol.get(symbol)
        if not active:
            return True
        # 활성 OCO가 있으면 False
        ocols = active.get("active_oco_ids", [])
        return len(ocols) == 0

    # ----------------------------
    # 공개 API: BUY/SELL 라우팅
    # ----------------------------
    def handle_signal(
        self,
        *,
        symbol: str,
        signal: Optional[str],     # "BUY" | "SELL" | None
        meta: Dict[str, Any] | None = None,
        # 전략별 파라미터 오버라이드 (없으면 DEFAULTS)
        buy_quote_usdt: Optional[float] = None,
        tp_pct: Optional[float] = None,
        sl_pct: Optional[float] = None,
        tif: Optional[str] = None,
        auto_adjust: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        반환: {"ok": bool, "action": str, "entry"?: dict, "oco"?: dict, "sell"?: dict}
        """
        if not signal:
            return {"ok": True, "action": "NOOP"}

        # 파라미터 머지
        pq = buy_quote_usdt if buy_quote_usdt is not None else DEFAULTS["buy_quote_usdt"]
        _tp = tp_pct if tp_pct is not None else DEFAULTS["tp_pct"]
        _sl = sl_pct if sl_pct is not None else DEFAULTS["sl_pct"]
        _tif = tif if tif is not None else DEFAULTS["tif"]
        _adj = auto_adjust if auto_adjust is not None else DEFAULTS["auto_adjust"]

        if signal == "BUY":
            if not self.can_open_new_position(symbol):
                notify(fmt_order_msg(title="SKIP BUY (active OCO exists)", symbol=symbol, side="BUY",
                                     price=None, qty=None, extra=meta))
                return {"ok": False, "action": "SKIP_BUY_ACTIVE_OCO"}

            # 시장가 진입 + OCO 자동 부착
            res = market_buy_then_attach_oco(
                symbol,
                quote_usdt=float(pq),
                use_quote_order_qty=True,     # 정밀도/잔돈 처리 안전
                tp_pct=float(_tp),
                sl_pct=float(_sl),
                tif=_tif,
                auto_adjust=_adj,
                dry_run=self.dry_run,
                allow_mainnet=self.allow_mainnet,
                wait_timeout_s=15.0, poll_s=0.5,
            )
            if res.get("ok"):
                notify(fmt_order_msg(
                    title="BUY+OCO OK",
                    symbol=symbol, side="BUY",
                    price=res.get("avg_fill_price"), qty=res.get("filled_qty"),
                    extra={"tp_pct": _tp, "sl_pct": _sl, "dry_run": self.dry_run}
                ))
                return {"ok": True, "action": "BUY_OCO", **res}
            else:
                notify(fmt_order_msg(
                    title="BUY+OCO FAIL",
                    symbol=symbol, side="BUY",
                    price=None, qty=None,
                    extra={"err": res.get("error"), "entry": res.get("entry")}
                ))
                return {"ok": False, "action": "BUY_OCO_FAIL", **res}

        elif signal == "SELL":
            # 보유 수량 전부(또는 일부) 시장가 청산 예시
            sx = get_symbol_info(symbol)
            ff = extract_filters(sx)
            free_qty = self._free_base_qty(symbol)
            sell_qty = normalize_qty(free_qty, ff)
            qty_str = to_api_str(sell_qty, ff.get("stepQty"))

            if sell_qty <= 0:
                notify(fmt_order_msg(title="SKIP SELL (no position)", symbol=symbol, side="SELL",
                                     price=None, qty=None, extra=meta))
                return {"ok": False, "action": "SKIP_SELL_NO_POS"}

            r = market_sell_qty(
                symbol, float(qty_str),
                dry_run=self.dry_run, allow_mainnet=self.allow_mainnet
            )
            if r.get("ok"):
                notify(fmt_order_msg(
                    title="SELL OK",
                    symbol=symbol, side="SELL",
                    price=None, qty=r.get("qty"),
                    extra={"dry_run": self.dry_run}
                ))
                return {"ok": True, "action": "SELL", "sell": r}
            else:
                notify(fmt_order_msg(
                    title="SELL FAIL",
                    symbol=symbol, side="SELL",
                    price=None, qty=r.get("qty"),
                    extra={"err": r.get("error")}
                ))
                return {"ok": False, "action": "SELL_FAIL", "sell": r}

        else:
            return {"ok": True, "action": f"IGNORE({signal})"}
