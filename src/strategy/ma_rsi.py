# src/strategy/ma_rsi.py
import pandas as pd
from .base import Strategy
from .registry import register
import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from src.indicators import add_ema, add_rsi

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
        out = add_ema(df, int(self.params.get("short_window", 7)), "ma_short")
        out = add_ema(out, int(self.params.get("long_window", 25)), "ma_long")
        out = add_rsi(out, int(self.params.get("rsi_period", 14)), "rsi")
        return out

    def generate_signal(self, df: pd.DataFrame):
        if len(df) < self.min_history(): 
            return None
        rsi_buy = float(self.params.get("rsi_buy", 30))
        rsi_sell = float(self.params.get("rsi_sell", 70))
        latest, prev = df.iloc[-1], df.iloc[-2]

        if any(pd.isna(prev[["ma_short","ma_long"]])) or pd.isna(latest["rsi"]):
            return None

        if prev["ma_short"] <= prev["ma_long"] and latest["ma_short"] > latest["ma_long"] and latest["rsi"] < rsi_buy:
            return "BUY"

        if prev["ma_short"] >= prev["ma_long"] and latest["ma_short"] < latest["ma_long"] and latest["rsi"] > rsi_sell:
            return "SELL"
        return None
