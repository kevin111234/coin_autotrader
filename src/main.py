# -*- coding: utf-8 -*-
"""
메인 실행:
- RollingFeed로 심볼/인터벌별 '마감창'을 캐시(warm_build)
- 매 틱: (1) 롤오버 감지/갱신 → (2) 캐시된 마감창 + 현재가 1틱을 붙여 스냅샷 DF 생성
- 지표/시그널 계산은 기존 StrategyRunner.compute(symbol, df)로 그대로 수행
- 초기 1회만 지표 NaN의 leading 구간을 잘라내어 워밍업 문제 제거
"""

import time
from typing import Dict
import pandas as pd

from config.config_loader import load_config
from src.exchange import get_price
from src.strategy_manager import StrategyRunner

# RollingFeed(파일기반 작은DB + 롤오버 감지)
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
# 유틸: 현재가 1틱 스냅샷 생성(공용)
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
      - 지표 계산은 Runner.compute(symbol, df)에서 수행한다(여긴 OHLCV만 구성).
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

    # 컬럼 정규화(혹시 타입/정렬 흔들릴 수 있어 보정)
    df_rt["open_time"] = pd.to_datetime(df_rt["open_time"], utc=True).dt.tz_convert(None)
    for c in ["open", "high", "low", "close", "volume"]:
        df_rt[c] = pd.to_numeric(df_rt[c], errors="coerce")
    df_rt = df_rt.dropna(subset=["open_time","open","high","low","close","volume"]).reset_index(drop=True)
    return df_rt

# -------------------------------------------
# 초기화: RollingFeed warm + NaN leading cut
# -------------------------------------------
def init_with_rolling_feed(
    runner: StrategyRunner,
    *,
    lookback_min: int = 300,
    nan_mode: str = "leading",
) -> Dict[str, pd.DataFrame]:
    """
    메인 루프 '직전' 1회만 호출:
      1) 각 심볼/인터벌에 대해 RollingFeed.warm_build_or_update 실행(전략 미지정: 지표 선계산 안 함)
      2) feed의 '마감창' + 현재가 1틱 스냅샷 DF 생성
      3) Runner.compute(symbol, df_rt)로 지표 선계산
      4) 지표 NaN의 leading 구간 절단
      5) {symbol: cleaned_df} 반환 → 첫 루프 1회에 사용
    """
    feed = RollingFeed()
    interval = runner.interval
    bootstrap: Dict[str, pd.DataFrame] = {}

    for spec in runner.targets:
        symbol = spec.symbol
        need = runner.required_history(symbol)
        lookback = max(need, lookback_min)

        # (1) 마감창 캐시 빌드(전략 dict은 비움: 데이터 창만 관리)
        feed.warm_build_or_update(
            symbol, interval,
            lookback=lookback,
            strategies={},                 # 전략 지표는 Runner가 계산하므로 이곳에선 비움
            fetch_limit_per_call=1000,
            # trim_leading_nans는 feed가 관리하는 지표 없으므로 의미없음(어차피 Runner에서 자름)
        )

        # (2) 마감창 + 현재가 1틱 스냅샷 구성
        df_rt = build_snapshot_from_feed(feed, symbol, interval, live_price=None)

        # (3) 지표 계산은 Runner에 일임
        df2, _ = runner.compute(symbol, df_rt)

        # (4) 초기 1회 NaN 정리
        df2c = _drop_indicator_nans(df2, mode=nan_mode)
        bootstrap[symbol] = df2c

        print(f"[INIT] {symbol}: snap_rows={len(df2)} -> cleaned_rows={len(df2c)} (mode={nan_mode})")

    return bootstrap, feed

# -----------
# 메인 루프
# -----------
def main():
    CFG = load_config("config/base.yaml")
    runner = StrategyRunner(CFG)
    interval = runner.interval

    # (A) RollingFeed로 초기화 + 초기 1회 NaN 정리
    bootstrap, feed = init_with_rolling_feed(
        runner,
        lookback_min=300,
        nan_mode="leading",
    )

    first_tick = True
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

            # (2) 마감창 + 현재가 1틱 스냅샷
            if first_tick and symbol in bootstrap and not bootstrap[symbol].empty:
                # 초기 1회는 '정리된 DF'를 바로 사용
                df2 = bootstrap[symbol]
                # 시그널 재획득이 필요하면 다시 compute 호출(지표 최신화 및 일관성)
                df2, signal = runner.compute(symbol, df2)
            else:
                # 평소에는 feed 기반 스냅샷 생성 → compute
                df_rt = build_snapshot_from_feed(feed, symbol, interval, live_price=None)
                df2, signal = runner.compute(symbol, df_rt)

            price = float(df2.iloc[-1]["close"])
            print(f"[{symbol}] {signal or 'WAIT'} @ {price}")

        first_tick = False
        time.sleep(1)  # 1초 단위 판단(원하면 조절)

if __name__ == "__main__":
    # 실행코드: python -m src.main
    main()