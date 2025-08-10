# src/strategy/ma_rsi.py

import pandas as pd

def calculate_indicators(df: pd.DataFrame, short_window=7, long_window=25, rsi_period=14):
    df = df.copy()
    
    # MA 계산
    df['ma_short'] = df['close'].rolling(window=short_window).mean()
    df['ma_long'] = df['close'].rolling(window=long_window).mean()
    
    # RSI 계산
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    return df

def generate_signal(df: pd.DataFrame, rsi_buy=30, rsi_sell=70):
    if len(df) < 25:
        return None  # 데이터 부족
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    signal = None
    # 골든크로스 + RSI 매수 조건
    if prev['ma_short'] <= prev['ma_long'] and latest['ma_short'] > latest['ma_long'] and latest['rsi'] < rsi_buy:
        signal = "BUY"
    
    # 데드크로스 + RSI 매도 조건
    elif prev['ma_short'] >= prev['ma_long'] and latest['ma_short'] < latest['ma_long'] and latest['rsi'] > rsi_sell:
        signal = "SELL"
    
    return signal
