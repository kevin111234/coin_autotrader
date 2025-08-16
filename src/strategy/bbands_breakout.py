import pandas as pd
from .base import Strategy
from .registry import register

from src.indicators import add_bbands

@register("bb_breakout")
class BollingerBreakout(Strategy):
    def name(self) -> str:
        return "bb_breakout"

    def min_history(self) -> int:
        period = int(self.params.get("period", 20))
        return period + 2

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        return add_bbands(df, int(self.params.get("period", 20)), float(self.params.get("k", 2.0)))


    def generate_signal(self, df: pd.DataFrame):
        if len(df) < self.min_history():
            return None
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # 상단선 돌파 → 매수, 하단선 이탈 → 매도 (단순 예시)
        if prev["close"] <= prev["bb_up"] and latest["close"] > latest["bb_up"]:
            return "BUY"
        if prev["close"] >= prev["bb_dn"] and latest["close"] < latest["bb_dn"]:
            return "SELL"
        return None
