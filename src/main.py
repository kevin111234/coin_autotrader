import sys
import os
import time

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from config.settings import get_api_config
from src.exchange import get_ohlcv
from strategy.ma_rsi import calculate_indicators, generate_signal

cfg = get_api_config()

symbol = "BTCUSDT"
interval = "1m"

while True:
    df = get_ohlcv(symbol, interval, limit=100)  # 100개 캔들
    df = calculate_indicators(df)
    signal = generate_signal(df, rsi_buy=50, rsi_sell=50) # 느슨한 조건으로 테스트
    
    if signal:
        print(f"[시그널 발생] {signal} @ {df.iloc[-1]['close']}")
    else:
        print(f"[대기] {df.iloc[-1]['close']}")
    
    time.sleep(5)
