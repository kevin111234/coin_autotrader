import sys
import os
import time

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

import pandas as pd
from config.settings import get_api_config
from src.exchange import get_ohlcv
from src.strategy_manager import StrategyRunner

CFG = get_api_config()

SYMBOLS_CFG = os.path.join(project_root, "config", "symbols.json")
runner = StrategyRunner(SYMBOLS_CFG)

def fetch_df(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    return get_ohlcv(symbol, interval, limit=limit)

if __name__ == "__main__":
    interval = runner.interval

    while True:
        for target in runner.targets:
            symbol = target["symbol"]
            need = runner.required_history(symbol)
            df = fetch_df(symbol, interval, limit=max(need, 100))

            df2, signal = runner.compute(symbol, df)

            last_close = float(df2.iloc[-1]["close"])
            if signal:
                print(f"[{symbol}] SIGNAL={signal} @ {last_close}")
                # TODO: 주문/Slack 연동 지점
            else:
                print(f"[{symbol}] WAIT @ {last_close}")

        time.sleep(60)
