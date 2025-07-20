from binance.client import Client
import time
import math
from datetime import datetime, timedelta
import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from src.config import Config
from src.data_control import Data_Control
import src.utills

def round_up_to_next_hour(dt: datetime) -> datetime:
    """
    dt 시각을 받아, 다음 정각(시+1, 분=0, 초=0, 마이크로초=0)을 반환.
    예) 12:47 -> 13:00, 13:00 -> 14:00
    """
    # 현재 시각의 시, 분, 초, 마이크로초를 추출
    year = dt.year
    month = dt.month
    day = dt.day
    hour = dt.hour

    # 만약 현재 시간이 이미 xx:00 정각이라면, 바로 다음 정각은 hour+1
    if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return datetime(year, month, day, hour) + timedelta(hours=1)
    else:
        return datetime(year, month, day, hour) + timedelta(hours=1)

def main():
    print("투자 프로그램을 시작합니다.")
    # Config에서 환경변수 및 고정변수 불러오기
    config = Config()
    ticker_list = config.coin_tickers.split(" ")
    future_use = bool(config.futures_use)
    if future_use:
        future_ticker_list = config.futures_coin_tickers.split(" ")
    client = Client(config.binance_access_key, config.binance_secret_key)
    data_control = Data_Control()

    # 서버 시간과 로컬 시간 동기화
    local_time = int(time.time() * 1000)
    server_time = client.get_server_time()
    time_diff = server_time['serverTime'] - local_time

    if abs(time_diff) > 1000:
        print(f"시간 차이 발생: {time_diff}ms, 시스템 시간 동기화 필요")
        time.sleep(time_diff / 1000)


    # 초기 데이터 조회 - data_control.py
    initial_data = {}
    vp_data = {}
    tpo_data = {}
    futures_data = {}
    futures_vp_data = {}
    futures_tpo_data = {}

    spot_symbol_info = {}
    future_symbol_info = {}

    buy_sell_status = {ticker: {"buy_stage": 0} for ticker in ticker_list}
    if future_use: futures_status = {ticker: {"position": None, "stage": 0} for ticker in future_ticker_list}
    for symbol in ticker_list:
        initial_data[symbol] = {}
        vp_data[symbol] = {}
        tpo_data[symbol] = {}
        spot_symbol_info[symbol] = src.utills.get_symbol_info(f"{symbol}USDT", client)

        # 1분봉, 5분봉, 1시간봉 각각 300개의 데이터 조회
        for timeframe in ["1m", "5m", "1h"]:
            data = data_control.data(client, symbol, timeframe, limit=300)

            # 각 데이터에 대한 기술적 지표 계산
            data = data_control.cal_indicator(data)

            # 비어있는 값 제거
            data = data.dropna()
            
            # 데이터 길이는 140개로 제한. (120일 이평선 계산을 위함.)
            if len(data) > 140:
                data = data.iloc[-140:].reset_index(drop=True)
            initial_data[symbol][timeframe] = data
            # 남은 데이터에 대한 VP, TPO 계산

        if future_use:
            for symbol in future_ticker_list:
                futures_data[symbol] = {}
                futures_vp_data[symbol] = {}
                futures_tpo_data[symbol] = {}
                future_symbol_info[symbol] = src.utills.get_symbol_info(f"{symbol}USDT", client)
                for timeframe in ["1m", "5m", "1h"]:
                    future_data = data_control.data(client, symbol, timeframe, limit=300, futures=True)
                    # 각 데이터에 대한 기술적 지표 계산
                    future_data = data_control.cal_indicator(future_data)

                    # 비어있는 값 제거
                    future_data = future_data.dropna()
                    
                    # 데이터 길이는 140개로 제한. (120일 이평선 계산을 위함.)
                    if len(future_data) > 140:
                        future_data = future_data.iloc[-140:].reset_index(drop=True)
                    futures_data[symbol][timeframe] = future_data
