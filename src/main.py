"""
메인 실행(부분 재계산 적용판):
- RollingFeed로 심볼/인터벌별 '마감창'을 캐시(warm_build)
- 매 틱:
    (1) 롤오버 감지/갱신
    (2) 마감창 + 현재가 1틱 스냅샷(OHLCV만)
    (3) 부분 재계산: 지표가 비어있는 가장 이른 행부터 끝까지 compute_indicators만 호출
    (4) Strategy.generate_signal(df_cache[symbol])로 신호 생성
- 초기 1회만 지표 NaN의 leading 구간을 잘라내어 워밍업 문제 제거
"""

import time
from typing import Dict
import pandas as pd

from config.config_loader import load_config
from src.exchange import get_price
from src.strategy_manager import StrategyRunner
from src.indicators.partial_utils import partial_recompute_indicators
from src.data.rolling_feed import RollingFeed, interval_to_ms

_BASE_OHLCV = {"open_time", "open", "high", "low", "close", "volume"}

# -----------------------
# 유틸: 지표 NaN 초기 정리
# -----------------------
def _drop_indicator_nans(df: pd.DataFrame, *, mode: str = "leading") -> pd.DataFrame:
    """
    메인 루프 시작 '직전' 1회만 사용할 NaN 트리머.
    - mode="leading": 맨 앞 워밍업 구간의 연속 NaN만 절단(권장)
    - mode="any": 지표 중 하나라도 NaN이면 그 행 제거(공격적)
    """
    if df.empty:
        return df
    indicator_cols = [c for c in df.columns if c not in _BASE_OHLCV]
    if not indicator_cols:
        return df.reset_index(drop=True)
    if mode == "any":
        return df.dropna(subset=indicator_cols).reset_index(drop=True)
    mask = df[indicator_cols].notna().all(axis=1)
    if not mask.any():
        return df.iloc[0:0].reset_index(drop=True)
    first_valid_idx = int(mask.idxmax())
    return df.loc[first_valid_idx:].reset_index(drop=True)

# -------------------------------
# 유틸: 현재가 1틱 스냅샷 생성(OHLCV)
# -------------------------------
def build_snapshot_from_feed(
    feed: RollingFeed,
    symbol: str,
    interval: str,
    *,
    live_price: float | None = None,
) -> pd.DataFrame:
    """
    역할:
      - feed.get_closed_window()로 캐시된 '마감창' DF를 불러온 뒤,
        현재가 1틱을 맨 뒤에 붙여 실시간 스냅샷을 만든다.
      - 지표 계산은 외부에서(partial_recompute_indicators) 수행.
    """
    closed = feed.get_closed_window(symbol, interval)
    if closed.empty:
        raise RuntimeError(f"[snapshot] closed window empty: {symbol} {interval} (warm_build 먼저)")

    px = float(live_price) if live_price is not None else float(get_price(symbol))

    interval_ms = interval_to_ms(interval)
    last_close = float(closed.iloc[-1]["close"])
    syn = {
        "open_time": pd.to_datetime(closed.iloc[-1]["open_time"]) + pd.Timedelta(milliseconds=interval_ms),
        "open": last_close,
        "high": max(last_close, px),
        "low": min(last_close, px),
        "close": px,
        "volume": 0.0,
    }
    df_rt = pd.concat([closed, pd.DataFrame([syn])], ignore_index=True)

    # 컬럼 정규화
    df_rt["open_time"] = pd.to_datetime(df_rt["open_time"], utc=True).dt.tz_convert(None)
    for c in ["open", "high", "low", "close", "volume"]:
        df_rt[c] = pd.to_numeric(df_rt[c], errors="coerce")
    df_rt = df_rt.dropna(subset=["open_time","open","high","low","close","volume"]).reset_index(drop=True)
    return df_rt

# ------------------------------
# 전략 인스턴스 가져오기 헬퍼
# ------------------------------
def _strategy_for(runner: StrategyRunner, symbol: str):
    """
    역할: 심볼에 해당하는 '전략 인스턴스'를 반환.
    - spec.strategy 가 이미 인스턴스면 그대로 반환
    - 문자열이면 registry를 통해 인스턴스화 (spec.params 주입)
    - runner.strategy_map 이 있으면 거기서도 검색
    """
    from src.strategy.base import Strategy  # isinstance 체크용

    # 0) runner.targets 에서 심볼 스펙 찾기
    spec = None
    if hasattr(runner, "targets"):
        for s in runner.targets:
            if getattr(s, "symbol", None) == symbol:
                spec = s
                break
    if spec is None:
        raise RuntimeError(f"strategy spec not found for symbol={symbol}")

    # 1) spec.strategy 가 인스턴스인 경우
    strat_attr = getattr(spec, "strategy", None)
    if isinstance(strat_attr, Strategy):
        return strat_attr

    # 2) 문자열인 경우 → registry 통해 생성
    name = None
    if isinstance(strat_attr, str):
        name = strat_attr
    else:
        # 필드명이 다른 경우들 대비
        for key in ("strategy_name", "name"):
            v = getattr(spec, key, None)
            if isinstance(v, str):
                name = v
                break
    params = getattr(spec, "params", {}) or {}

    if name:
        try:
            import src.strategy.registry as reg
            # 선호: 생성 헬퍼가 있는 경우
            for factory in ("create", "make", "instantiate"):
                if hasattr(reg, factory):
                    return getattr(reg, factory)(name, **params)
            # 클래스 조회 후 뉴
            for getter in ("get", "get_strategy", "get_class"):
                if hasattr(reg, getter):
                    cls = getattr(reg, getter)(name)
                    return cls(**params)
            # 딕셔너리 레지스트리 케이스
            for table in ("REGISTRY", "registry", "STRATEGIES", "STRATEGY_REGISTRY"):
                if hasattr(reg, table):
                    cls = getattr(reg, table).get(name)
                    if cls is not None:
                        return cls(**params)
        except Exception as e:
            raise RuntimeError(f"strategy registry resolve failed for '{name}': {e}")

    # 3) runner.strategy_map 이 있으면 활용
    if hasattr(runner, "strategy_map"):
        m = getattr(runner, "strategy_map")
        st = m.get(symbol)
        if isinstance(st, Strategy):
            return st

    raise RuntimeError(
        f"Strategy instance not found for symbol={symbol} "
        f"(got '{strat_attr}'). registry 연결 혹은 runner.targets[*].strategy 인스턴스 주입 필요."
    )

# -------------------------------------------
# 초기화: RollingFeed warm + 초기 전체 계산
# -------------------------------------------
def init_with_rolling_feed_and_full_compute(
    runner: StrategyRunner,
    *,
    lookback_min: int = 300,
    nan_mode: str = "leading",
) -> tuple[Dict[str, pd.DataFrame], RollingFeed]:
    """
    메인 루프 '직전' 1회만 호출:
      1) 각 심볼/인터벌에 대해 RollingFeed.warm_build_or_update 실행(전략 미지정)
      2) feed의 '마감창' + 현재가 1틱 스냅샷(OHLCV) 생성
      3) '전체 지표 계산'으로 df_cache[symbol] 채움
      4) 지표 NaN의 leading 구간 절단
    반환: (df_cache, feed)
    """
    feed = RollingFeed()
    interval = runner.interval
    df_cache: Dict[str, pd.DataFrame] = {}

    for spec in runner.targets:
        symbol = spec.symbol
        need = runner.required_history(symbol)
        lookback = max(need, lookback_min)

        # (1) 마감창 캐시 빌드(전략 dict 비움: 데이터 창만 관리)
        feed.warm_build_or_update(
            symbol, interval,
            lookback=lookback,
            strategies={},  # 지표는 여기서 계산 안 함
            fetch_limit_per_call=1000,
        )

        # (2) 마감창 + 현재가 1틱 스냅샷(OHLCV)
        df_base = build_snapshot_from_feed(feed, symbol, interval, live_price=None)

        # (3) 전체 지표 1회 계산 → 캐시에 저장
        strat = _strategy_for(runner, symbol)
        df_full = strat.compute_indicators(df_base.copy())

        # (4) 초기 NaN 정리
        df_full = _drop_indicator_nans(df_full, mode=nan_mode)

        df_cache[symbol] = df_full
        print(f"[INIT/FULL] {symbol}: rows={len(df_full)} (nan_mode={nan_mode})")

    return df_cache, feed

# -----------
# 메인 루프
# -----------
def main():
    CFG = load_config("config/base.yaml")
    runner = StrategyRunner(CFG)
    interval = runner.interval

    # (A) RollingFeed 초기화 + 전체 1회 계산으로 캐시 채우기
    df_cache, feed = init_with_rolling_feed_and_full_compute(
        runner,
        lookback_min=300,
        nan_mode="leading",
    )

    # (B) 루프: 부분 재계산 + 신호 판단
    while True:
        for spec in runner.targets:
            symbol = spec.symbol
            need = runner.required_history(symbol)
            lookback = max(need, 300)

            # (1) 롤오버 감지/갱신 (전략 dict 비워서 데이터 창만 관리)
            feed.rollover_if_needed(
                symbol, interval,
                lookback=lookback,
                strategies={}
            )

            # (2) 스냅샷(OHLCV만)
            df_base = build_snapshot_from_feed(feed, symbol, interval, live_price=None)

            # (3) 부분 재계산: 캐시된 지표 DF(df_cache[symbol])를 기반으로 '필요한 뒤쪽만' 갱신
            strat = _strategy_for(runner, symbol)
            df_cache[symbol], meta = partial_recompute_indicators(
                strat,
                df_with_ind=df_cache[symbol],  # 직전까지 지표 포함 DF
                df_new_base=df_base,           # 이번 틱 OHLCV 스냅샷
                safety_buffer=2                # 필요시 조정/해제 가능
            )
            # 디버깅/관찰용:
            # print(f"[{symbol}] partial meta: {meta}")

            # (4) 신호 판단(전략 표준 인터페이스)
            signal = strat.generate_signal(df_cache[symbol])

            price = float(df_cache[symbol].iloc[-1]["close"])
            print(f"[{symbol}] {signal or 'WAIT'} @ {price}")

        time.sleep(1)  # 1초 단위 판단(원하면 조절)

if __name__ == "__main__":
    # 실행코드: python -m src.main
    main()