# src/exchange.py

import requests
import pandas as pd
import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from config.settings import get_api_config

# 가격데이터 수집 함수
def get_ohlcv(symbol="BTCUSDT", interval="1m", limit=100):
    """
    Binance에서 OHLCV 데이터 가져오기
    :param symbol: 거래쌍 (예: 'BTCUSDT')
    :param interval: 캔들 간격 (예: '1m', '5m', '1h', '1d')
    :param limit: 최대 데이터 개수 (1~1000)
    :return: pandas.DataFrame
    """
    cfg = get_api_config()
    url = f"{cfg['BASE_URL']}/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    try:
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        # DataFrame 변환
        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
        ])

        # 타입 변환 + 시간 변환
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        numeric_cols = ["open", "high", "low", "close", "volume"]
        df[numeric_cols] = df[numeric_cols].astype(float)

        return df[["open_time", "open", "high", "low", "close", "volume"]]

    except requests.exceptions.RequestException as e:
        print(f"[❌] 가격 데이터 수집 실패: {e}")
        return None

# 실행 테스트
if __name__ == "__main__":
    df = get_ohlcv(symbol="BTCUSDT", interval="1m", limit=5)
    if df is not None:
        print(df)
