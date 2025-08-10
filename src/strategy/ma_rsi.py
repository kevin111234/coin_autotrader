# src/strategy/ma_rsi.py
import pandas as pd
from .base import Strategy
from .registry import register

@register("ma_rsi")
class MaRsiStrategy(Strategy):
    def name(self) -> str:
        return "ma_rsi"

    def min_history(self) -> int:
        sw = int(self.params.get("short_window", 7))
        lw = int(self.params.get("long_window", 25))
        rsi = int(self.params.get("rsi_period", 14))
        return max(sw, lw, rsi) + 2  # 직전 캔들 비교 여유분

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        sw = int(self.params.get("short_window", 7))
        lw = int(self.params.get("long_window", 25))
        rsi_period = int(self.params.get("rsi_period", 14))

        df["ma_short"] = df["close"].rolling(sw).mean()
        df["ma_long"] = df["close"].rolling(lw).mean()

        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))
        return df

    def generate_signal(self, df: pd.DataFrame):
        rsi_buy = float(self.params.get("rsi_buy", 30))
        rsi_sell = float(self.params.get("rsi_sell", 70))

        if len(df) < self.min_history(): 
            return None

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        if pd.isna(prev["ma_short"]) or pd.isna(prev["ma_long"]) or pd.isna(latest["rsi"]):
            return None

        if prev["ma_short"] <= prev["ma_long"] and latest["ma_short"] > latest["ma_long"] and latest["rsi"] < rsi_buy:
            return "BUY"
        if prev["ma_short"] >= prev["ma_long"] and latest["ma_short"] < latest["ma_long"] and latest["rsi"] > rsi_sell:
            return "SELL"
        return None
