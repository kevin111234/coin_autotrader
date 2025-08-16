import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

from src.exchange.core import sync_time
from src.order_executor import market_buy_by_quote, limit_buy, market_sell_qty, limit_sell, oco_sell_tp_sl, oco_buy_breakout
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

    # 4) 지정가 매도
    r4 = limit_sell("BTCUSDT", price=118000.0, qty=0.0002, tif="GTC", dry_run=True)
    print("limit_sell:",r4)

    # SELL OCO: 보유분 청산 + 손절
    print("SELL OCO:",oco_sell_tp_sl(
        "BTCUSDT",
        qty=0.0002,
        tp_price=118000.0,
        sl_stop=116500.0,
        sl_limit=116480.0,   # 미지정 시 stop보다 한 틱 아래 자동 설정
        tif="GTC",
        dry_run=True,        # testnet 실호출 전에는 True로 확인
    ))

    # BUY OCO: 돌파 진입 + 저가 대안
    print("BUY OCO",oco_buy_breakout(
        "BTCUSDT",
        qty=0.0002,
        entry_stop=118200.0,    # 위쪽 돌파
        entry_limit=None,       # 미지정 시 stop보다 한 틱 위로
        fallback_limit=116800.0,# 아래 대안(limit maker)
        tif="GTC",
        dry_run=True,
    ))
