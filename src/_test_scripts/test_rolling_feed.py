# 예: src/_test_scripts/test_rolling_feed.py
import sys
import os

# 프로젝트 루트 디렉토리의 절대 경로를 구함
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)
# src/_test_scripts/test_rolling_feed.py

from src.data.rolling_feed import RollingFeed
from src.strategy.ma_rsi import MaRsiStrategy

# 전략 인스턴스(파라미터는 YAML/ENV/기본값 등 네 방식에 맞게 주입)
ma_rsi = MaRsiStrategy(short_window=7, long_window=25, rsi_period=14, rsi_buy=30, rsi_sell=70)

RF = RollingFeed()

symbol = "BTCUSDT"
interval = "1m"
lookback = 2000

# 1) 초기 빌드(마감 캔들 + 지표 선계산 JSON 저장)
RF.warm_build_or_update(
    symbol, interval,
    lookback=lookback,
    strategies={
        "ma_rsi": ma_rsi,  # 여러 전략도 가능: {"ma_rsi": ma_rsi, "macd": macd, ...}
    }
)

# 2) 실시간 지표 스냅샷(현재가 1틱 포함)
snap = RF.snapshot_with_price(
    symbol, interval,
    strategy=ma_rsi,       # 단일 전략
    live_price=None        # None이면 REST로 현재가 조회
)

# 마지막 행이 “현재가 기반” 지표
print(snap.tail(2))
last = snap.iloc[-1]
print("now close=", last["close"], "rsi=", last.get("rsi"), "ma_short=", last.get("ma_short"))

# 전략의 generate_signal로 즉시 판단
sig = ma_rsi.generate_signal(snap)
print("signal:", sig)

# 3) 루프 중 캔들 롤오버 감지/갱신
changed = RF.rollover_if_needed(
    symbol, interval,
    lookback=lookback,
    strategies={"ma_rsi": ma_rsi}
)
if changed:
    print("[INFO] cache updated (rollover)")
