# src/_test_scripts/reset_all.py
from pprint import pprint
import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

from src._test_scripts.reset import full_reset

# 테스트할 심볼 목록
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

res = full_reset(SYMBOLS, registry_path="runtime/orders_state.json", allow_mainnet=False)
pprint(res)
