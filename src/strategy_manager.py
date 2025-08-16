import json
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd

from src.strategy.registry import create_strategy  # noqa
# 전략 모듈 import 해서 레지스트리에 등록(사이드 이펙트)
from src.strategy import ma_rsi as _m1  # noqa
from src.strategy import bbands_breakout as _m2  # noqa

class StrategyRunner:
    def __init__(self, cfg):
        self.interval = cfg.trading.interval
        self.targets = cfg.trading.symbols
        self.strategies = {
            spec.symbol: create_strategy(spec.strategy, **spec.params)
            for spec in self.targets
        }

    def required_history(self, symbol: str) -> int:
        return self.strategies[symbol].min_history()

    def compute(self, symbol: str, df: pd.DataFrame):
        st = self.strategies[symbol]
        df2 = st.compute_indicators(df)
        signal = st.generate_signal(df2)
        return df2, signal
