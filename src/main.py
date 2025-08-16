import sys
import os
import time

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

import pandas as pd
from config.config_loader import load_config
from src.exchange import get_ohlcv
from src.strategy_manager import StrategyRunner

CFG = load_config(os.path.join(project_root, "config", "base.yaml"))
runner = StrategyRunner(CFG)

def fetch_df(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    return get_ohlcv(symbol, interval, limit=limit)

if __name__ == "__main__":
    interval = runner.interval
    while True:
        for spec in runner.targets:
            symbol = spec.symbol
            need = runner.required_history(symbol)
            df = fetch_df(symbol, interval, limit=max(need, 200))
            df2, signal = runner.compute(symbol, df)
            price = float(df2.iloc[-1]["close"])
            print(f"[{symbol}] {signal or 'WAIT'} @ {price}")

        time.sleep(60)
