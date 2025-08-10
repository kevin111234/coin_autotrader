import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from src.exchange import sync_time, ping, get_account, get_price, place_test_order

if __name__ == "__main__":
    sync_time()
    print("ping:", ping())
    acct = get_account()
    print("balances(top2):", acct["balances"][:2])
    print("BTCUSDT price:", get_price("BTCUSDT"))
    print("test order:", place_test_order("BTCUSDT", "BUY", "MARKET", quote_order_qty=10))
