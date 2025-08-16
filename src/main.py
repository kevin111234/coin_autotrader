# src/main.py
import time
from config.config_loader import load_config
from src.exchange import get_ohlcv
from src.strategy_manager import StrategyRunner

def main():
    CFG = load_config("config/base.yaml")
    runner = StrategyRunner(CFG)
    interval = runner.interval

    while True:
        for spec in runner.targets:
            symbol = spec.symbol
            need = runner.required_history(symbol)
            df = get_ohlcv(symbol, interval, limit=max(need, 200))
            df2, signal = runner.compute(symbol, df)
            price = float(df2.iloc[-1]["close"])
            print(f"[{symbol}] {signal or 'WAIT'} @ {price}")
        time.sleep(60)

if __name__ == "__main__":
    # 실행코드: python -m src.main
    main()