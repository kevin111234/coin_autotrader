# -*- coding: utf-8 -*-
from __future__ import annotations
import time
from typing import Dict, Optional
import pandas as pd

from config.config_loader import load_config
from src.strategy_manager import StrategyRunner
from src.data.rolling_feed import RollingFeed
from src.indicators.partial_utils import partial_recompute_indicators
from src.notifier.slack_notifier import notify
from src.trade.order_manager import OrderManager, load_state, save_state  # 네가 만든 JSON state
from src.trade.signal_router import SignalRouter, DEFAULTS
from src.main import build_snapshot_from_feed, _drop_indicator_nans, _strategy_for  # 재사용

COOLDOWN_S = 10  # 심볼당 신호 실행 쿨다운

def main():
    CFG = load_config("config/base.yaml")
    runner = StrategyRunner(CFG)
    interval = runner.interval

    # 주문 상태 로딩 + 라우터
    om = OrderManager(state_path="data/orders_state.json")
    router = SignalRouter(om, dry_run=True, allow_mainnet=False)  # 실제 돌릴 땐 dry_run=False

    # 초기 캐시
    feed = RollingFeed()
    df_cache: Dict[str, pd.DataFrame] = {}
    last_signal: Dict[str, Optional[str]] = {}
    last_exec_ts: Dict[str, float] = {}

    # 부팅: warm + 전체 1회 계산
    for spec in runner.targets:
        symbol = spec.symbol
        need = runner.required_history(symbol)
        lookback = max(need, 300)

        feed.warm_build_or_update(symbol, interval, lookback=lookback, strategies={}, fetch_limit_per_call=1000)
        df_base = build_snapshot_from_feed(feed, symbol, interval)
        strat = _strategy_for(runner, symbol)

        df_full = strat.compute_indicators(df_base.copy())
        df_full = _drop_indicator_nans(df_full, mode="leading")

        df_cache[symbol] = df_full
        last_signal[symbol] = None
        last_exec_ts[symbol] = 0.0

        print(f"[INIT/FULL] {symbol}: rows={len(df_full)}")

    # 루프
    while True:
        for spec in runner.targets:
            symbol = spec.symbol
            need = runner.required_history(symbol)
            lookback = max(need, 300)

            # 롤오버
            feed.rollover_if_needed(symbol, interval, lookback=lookback, strategies={})

            # 스냅샷(OHLCV)
            df_base = build_snapshot_from_feed(feed, symbol, interval)

            # 부분 재계산
            strat = _strategy_for(runner, symbol)
            df_cache[symbol], meta = partial_recompute_indicators(
                strat, df_with_ind=df_cache[symbol], df_new_base=df_base, safety_buffer=2
            )
            # 신호
            signal = strat.generate_signal(df_cache[symbol])
            price = float(df_cache[symbol].iloc[-1]["close"])
            print(f"[{symbol}] {signal or 'WAIT'} @ {price}")

            # 디바운스 & 쿨다운
            now = time.time()
            if signal and signal != last_signal[symbol] and (now - last_exec_ts[symbol] >= COOLDOWN_S):
                # 주문 실행
                res = router.handle_signal(
                    symbol=symbol,
                    signal=signal,
                    meta={"from": strat.name()},
                    # 전략별 파라미터 오버라이드 가능:
                    buy_quote_usdt=DEFAULTS["buy_quote_usdt"],
                    tp_pct=DEFAULTS["tp_pct"],
                    sl_pct=DEFAULTS["sl_pct"],
                    tif=DEFAULTS["tif"],
                    auto_adjust=DEFAULTS["auto_adjust"],
                )
                last_signal[symbol] = signal
                last_exec_ts[symbol] = now

                # 상태 저장(활성 OCO 갱신 등은 너의 OrderManager.sync_*에 따라 별도 주기 동기)
                om.persist()

        time.sleep(1)

if __name__ == "__main__":
    # 실행: python -m src.main_trade
    main()
