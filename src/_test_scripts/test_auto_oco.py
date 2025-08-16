# src/_test_scripts/test_auto_oco.py
import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

from src.exchange.auto_oco import market_buy_then_attach_oco

# DRY-RUN: 현재가 가정 + OCO payload 미리보기
# print(market_buy_then_attach_oco(
#     "BTCUSDT",
#     quote_usdt=10,
#     tp_pct=0.01,        # +1%
#     sl_pct=0.005,       # -0.5%
#     dry_run=True,
#     auto_adjust=True
# ))

res = market_buy_then_attach_oco(
    "BTCUSDT",
    quote_usdt=10,
    tp_pct=0.01, sl_pct=0.005,
    dry_run=False,           # ← 실호출
    allow_mainnet=False,     # ← 안전 가드 유지
    auto_adjust=True
)
print(res)
