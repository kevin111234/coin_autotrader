# src/exchange/market.py
import pandas as pd
from typing import Optional, Dict, Any
from .core import request

def get_price(symbol: str) -> float:
    return float(request("GET", "/api/v3/ticker/price", {"symbol": symbol})["price"])

def get_exchange_info(symbol: Optional[str]=None) -> Dict[str, Any]:
    params = {"symbol": symbol} if symbol else None
    return request("GET", "/api/v3/exchangeInfo", params)

def get_ohlcv(symbol="BTCUSDT", interval="1m", limit=100) -> pd.DataFrame:
    data = request("GET", "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_asset_volume","number_of_trades",
        "taker_buy_base_asset_volume","taker_buy_quote_asset_volume","ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return df[["open_time","open","high","low","close","volume"]]
