import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

from pprint import pprint
from src.exchange.registry import OrderRegistry
REG = OrderRegistry("runtime/orders_state.json")

print("== BEFORE ==")
pprint(REG.summary())

print("== SYNC ==")
print(REG.sync_active())

print("== AFTER ==")
pprint(REG.summary())
