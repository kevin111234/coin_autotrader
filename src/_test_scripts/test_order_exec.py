import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

from src.exchange.core import sync_time
from src.order_executor import market_buy_by_quote, limit_buy, market_sell_qty
from src.exchange.market import get_price

if __name__ == "__main__":
    sync_time()  # -1021 방지

    sym = "BTCUSDT"
    px = get_price(sym)
    print("price:", px)

    # 1) 시장가 매수 (10 USDT 구매) — dry_run=True → /order/test
    r1 = market_buy_by_quote(sym, quote_usdt=10, dry_run=True)
    print("market_buy_by_quote:", r1)

    # 2) 지정가 매수 (현재가보다 0.5% 아래에 GTC 주문) — dry_run=True
    r2 = limit_buy(sym, price=px*0.995, qty=0.0002, tif="GTC", dry_run=True)
    print("limit_buy:", r2)

    # 3) 시장가 매도 (수량 예시) — dry_run=True
    r3 = market_sell_qty(sym, qty=0.0002, dry_run=True)
    print("market_sell_qty:", r3)
