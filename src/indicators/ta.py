import pandas as pd
import numpy as np
from .utils import ensure_ohlcv, to_float

# === 이동평균 ===
def add_sma(df: pd.DataFrame, period: int, col_out: str = None) -> pd.DataFrame:
    ensure_ohlcv(df); to_float(df, ["close"])
    out = df.copy()
    col_out = col_out or f"sma_{period}"
    out[col_out] = out["close"].rolling(period, min_periods=period).mean()
    return out

def add_ema(df: pd.DataFrame, period: int, col_out: str = None) -> pd.DataFrame:
    ensure_ohlcv(df); to_float(df, ["close"])
    out = df.copy()
    col_out = col_out or f"ema_{period}"
    out[col_out] = out["close"].ewm(span=period, adjust=False, min_periods=period).mean()
    return out

# === RSI (Wilder) ===
def add_rsi(df: pd.DataFrame, period: int = 14, col_out: str = None) -> pd.DataFrame:
    ensure_ohlcv(df); to_float(df, ["close"])
    out = df.copy()
    delta = out["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()

    rs = avg_gain / (avg_loss.replace(0, np.nan))
    rsi = 100 - (100 / (1 + rs))
    col_out = col_out or f"rsi_{period}"
    out[col_out] = rsi
    return out

# === MACD ===
def add_macd(df: pd.DataFrame, fast=12, slow=26, signal=9,
             col_macd="macd", col_signal="macd_signal", col_hist="macd_hist") -> pd.DataFrame:
    ensure_ohlcv(df); to_float(df, ["close"])
    out = df.copy()
    ema_fast = out["close"].ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = out["close"].ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False, min_periods=signal).mean()
    out[col_macd] = macd
    out[col_signal] = macd_signal
    out[col_hist] = macd - macd_signal
    return out

# === Bollinger Bands ===
def add_bbands(df: pd.DataFrame, period=20, k=2.0,
               col_mid="bb_mid", col_up="bb_up", col_dn="bb_dn") -> pd.DataFrame:
    ensure_ohlcv(df); to_float(df, ["close"])
    out = df.copy()
    ma = out["close"].rolling(period, min_periods=period).mean()
    std = out["close"].rolling(period, min_periods=period).std(ddof=0)
    out[col_mid] = ma
    out[col_up]  = ma + k * std
    out[col_dn]  = ma - k * std
    return out

# === ATR (Average True Range) ===
def add_atr(df: pd.DataFrame, period=14, col_out="atr") -> pd.DataFrame:
    ensure_ohlcv(df); to_float(df, ["high","low","close"])
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat([
        (out["high"] - out["low"]).abs(),
        (out["high"] - prev_close).abs(),
        (out["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    out[col_out] = atr
    return out

# === VWAP ===
def add_vwap(df: pd.DataFrame, col_out="vwap") -> pd.DataFrame:
    ensure_ohlcv(df); to_float(df, ["high","low","close","volume"])
    out = df.copy()
    tp = (out["high"] + out["low"] + out["close"]) / 3.0
    cum_v = out["volume"].cumsum()
    cum_vp = (tp * out["volume"]).cumsum()
    out[col_out] = cum_vp / (cum_v.replace(0, np.nan))
    return out

# === 편의: 한번에 여러 지표 추가 ===
def add_indicators(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """
    spec 예:
    {
      "ema": [20, 60],
      "rsi": {"period": 14, "col_out": "rsi"},
      "macd": {"fast":12,"slow":26,"signal":9},
      "bbands": {"period":20,"k":2.0}
    }
    """
    out = df.copy()
    if "sma" in spec:
        for p in spec["sma"]:
            out = add_sma(out, p)
    if "ema" in spec:
        for p in spec["ema"]:
            out = add_ema(out, p)
    if "rsi" in spec:
        params = spec["rsi"] if isinstance(spec["rsi"], dict) else {"period": int(spec["rsi"])}
        out = add_rsi(out, **params)
    if "macd" in spec:
        out = add_macd(out, **spec["macd"])
    if "bbands" in spec:
        out = add_bbands(out, **spec["bbands"])
    if "atr" in spec:
        params = spec["atr"] if isinstance(spec["atr"], dict) else {"period": int(spec["atr"])}
        out = add_atr(out, **params)
    if "vwap" in spec:
        out = add_vwap(out)
    return out
