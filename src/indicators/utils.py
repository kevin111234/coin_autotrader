import pandas as pd

REQUIRED_COLS = ["open", "high", "low", "close", "volume"]

def ensure_ohlcv(df: pd.DataFrame):
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV 컬럼 누락: {missing}")

def to_float(df: pd.DataFrame, cols):
    for c in cols:
        if c in df.columns and not pd.api.types.is_float_dtype(df[c].dtype):
            df[c] = df[c].astype(float)

def max_window(*windows):
    """필요 최소 히스토리 산출에 사용."""
    vals = [int(w) for w in windows if w is not None]
    return max(vals) if vals else 1
