# -*- coding: utf-8 -*-
"""
롤링 피드(파일 기반 JSON 캐시):
- 직전(마감된) 캔들까지만 lookback 창을 유지하고, 각 전략의 compute_indicators()로 '선계산'해 JSON 저장
- 실시간 의사결정 시 현재가 1틱을 붙여 해당 전략의 compute_indicators()만 빠르게 재계산
- 캔들 롤오버(마감) 시 캐시 갱신

전략 인터페이스:
  class Strategy:
      def min_history(self) -> int
      def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame
      def generate_signal(self, df: pd.DataFrame) -> Optional[str]

JSON 경로:
  runtime/data/{SYMBOL}/{INTERVAL}.json
"""

from __future__ import annotations
import os, json, time, pathlib
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import pandas as pd

from src.exchange.market import get_ohlcv, get_price
from src.strategy.base import Strategy  # 타입 힌트용

# --------- interval → ms ---------
_INTERVAL_MS = {
    "1s":   1000,
    "1m":   60_000,
    "3m":   180_000,
    "5m":   300_000,
    "15m":  900_000,
    "30m":  1_800_000,
    "1h":   3_600_000,
    "2h":   7_200_000,
    "4h":   14_400_000,
    "6h":   21_600_000,
    "8h":   28_800_000,
    "12h":  43_200_000,
    "1d":   86_400_000,
}
def interval_to_ms(interval: str) -> int:
    if interval not in _INTERVAL_MS:
        raise ValueError(f"unsupported interval: {interval}")
    return _INTERVAL_MS[interval]

# --------- 파일 저장(JSON “작은 DB”) ---------
@dataclass
class JsonStore:
    root: str = "runtime/data"

    def path(self, symbol: str, interval: str) -> pathlib.Path:
        d = pathlib.Path(self.root) / symbol
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{interval}.json"

    def load(self, symbol: str, interval: str) -> Dict[str, Any] | None:
        p = self.path(symbol, interval)
        if not p.exists(): return None
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, symbol: str, interval: str, data: Dict[str, Any]) -> None:
        p = self.path(symbol, interval)
        tmp = p.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"), indent=2)
        os.replace(tmp, p)

# --------- DF ↔ JSON 직렬화 ---------
def df_to_bars_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out = []
    for _, r in df.iterrows():
        out.append({
            "open_time": pd.to_datetime(r["open_time"], utc=True).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]),   "close": float(r["close"]),
            "volume": float(r["volume"]),
        })
    return out

def bars_records_to_df(rec: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rec:
        return pd.DataFrame(columns=["open_time","open","high","low","close","volume"])
    df = pd.DataFrame(rec)
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().sort_values("open_time").reset_index(drop=True)
    df["open_time"] = df["open_time"].dt.tz_convert(None)  # tz-naive UTC로 통일
    return df

def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["open_time","open","high","low","close","volume"]
    out = df[cols].copy()
    out["open_time"] = pd.to_datetime(out["open_time"], utc=True, errors="coerce").dt.tz_convert(None)
    for c in ["open","high","low","close","volume"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=cols).sort_values("open_time").reset_index(drop=True)
    return out

# --------- 핵심 매니저 ---------
class RollingFeed:
    """
    역할:
      - 직전(마감) 캔들까지만 lookback 창 유지 + 각 전략의 지표 선계산을 JSON에 보관
      - 실시간 판단 시 현재가 1틱 붙여 해당 전략의 지표만 즉시 재계산
      - 캔들 롤오버 감지 시 캐시 갱신

    사용 흐름:
      1) warm_build_or_update(symbol, interval, lookback, strategies)
      2) snapshot_with_price(symbol, interval, live_price, strategy) → 마지막 행이 '현재가 기반'
      3) 루프 중 rollover_if_needed(...)로 캐시 자동 갱신
    """

    def __init__(self, store: JsonStore | None = None):
        self.store = store or JsonStore()

    # 1) 초기 빌드/업데이트 (마감 캔들만, 전략들 선계산)
    def warm_build_or_update(
        self,
        symbol: str,
        interval: str,
        *,
        lookback: int,
        strategies: Dict[str, Strategy],   # {"ma_rsi": Strategy 인스턴스, ...}
        fetch_limit_per_call: int = 1000
    ) -> Dict[str, Any]:
        """
        역할:
          - 최근 캔들 불러와 마지막 진행중 캔들을 제외 → 마감 창만 lookback 확보
          - 각 전략의 compute_indicators(df)를 호출해 지표를 선계산
          - 결과를 JSON에 저장
        반환: 저장된 JSON dict
        """
        # (1) 필요한 최소 길이 산정: 각 전략의 min_history 고려
        need_min = max(
            lookback,
            max((s.min_history() for s in strategies.values()), default=lookback)
        )

        # (2) 최근 캔들 로드
        raw = get_ohlcv(symbol, interval, limit=min(fetch_limit_per_call, need_min + 3))
        if len(raw) == 0:
            raise RuntimeError("no klines returned")

        # (3) '마감 캔들'만 유지 (마지막 1개는 진행중일 수 있으므로 제외)
        closed = raw.iloc[:-1].copy() if len(raw) > 1 else raw.copy()
        closed = _normalize_ohlcv(closed)
        if len(closed) > need_min:
            closed = closed.iloc[-need_min:].reset_index(drop=True)

        # (4) 전략별 선계산 지표 저장
        indicators_blob: Dict[str, Any] = {}
        base_cols = {"open_time","open","high","low","close","volume"}
        for name, strat in strategies.items():
            d = strat.compute_indicators(closed.copy())
            indicator_cols = [c for c in d.columns if c not in base_cols]
            payload = {
                "columns": indicator_cols,
                "values": d[indicator_cols].astype(float).where(pd.notnull(d[indicator_cols]), None).values.tolist()
            }
            indicators_blob[name] = payload

        # (5) JSON 저장
        last_closed_ot = pd.to_datetime(closed["open_time"].iloc[-1], utc=True).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = {
            "meta": {
                "symbol": symbol,
                "interval": interval,
                "lookback": need_min,
                "last_closed_open_time": last_closed_ot,
                "saved_at": int(time.time()*1000)
            },
            "bars_closed": df_to_bars_records(closed),
            "indicators_closed": indicators_blob
        }
        self.store.save(symbol, interval, data)
        return data

    # 2) 캐시에서 “마감 창” 불러오기 (전략 독립적)
    def get_closed_window(self, symbol: str, interval: str) -> pd.DataFrame:
        js = self.store.load(symbol, interval)
        if not js:
            return pd.DataFrame(columns=["open_time","open","high","low","close","volume"])
        return bars_records_to_df(js.get("bars_closed", []))

    # 3) 현재가 1틱을 붙여 특정 전략의 지표 즉시 계산
    def snapshot_with_price(
        self,
        symbol: str,
        interval: str,
        *,
        strategy: Strategy,               # 단일 전략 인스턴스
        live_price: Optional[float] = None
    ) -> pd.DataFrame:
        """
        역할:
          - 캐시된 마감 창에 “현재 틱” 1행을 붙여 strategy.compute_indicators(df) 계산
          - 반환 DF의 마지막 행이 현재가 기반 지표값
        """
        closed = self.get_closed_window(symbol, interval)
        if len(closed) == 0:
            raise RuntimeError("closed window is empty; call warm_build_or_update first")

        # 현재가 조회(없으면 REST)
        px = float(live_price) if live_price is not None else float(get_price(symbol))

        # synthetic 행 생성:
        #  - open_time: 마지막 마감 open_time + interval
        #  - close: 현재가 px
        #  - open/high/low: 보수적으로 마지막 close를 기준으로 px 반영
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
        df_rt = _normalize_ohlcv(df_rt)

        # 전략 지표 즉시 계산
        out = strategy.compute_indicators(df_rt)
        return out

    # 4) 롤오버 감지 → 캐시 갱신
    def rollover_if_needed(
        self,
        symbol: str,
        interval: str,
        *,
        lookback: int,
        strategies: Dict[str, Strategy]
    ) -> bool:
        """
        역할:
          - 최근 캔들(마감 창)이 저장된 JSON과 달라졌는지 비교 (길이/마지막 몇 개 바의 O/H/L/C/V)
          - 달라졌다면 warm_build_or_update 재실행
        반환: True(갱신됨) / False(변화없음)
        """
        js = self.store.load(symbol, interval)
        # 캐시가 없으면 빌드
        if not js:
            self.warm_build_or_update(symbol, interval, lookback=lookback, strategies=strategies)
            return True

        # need_min: 전략별 min_history 고려
        need_min = max(
            lookback,
            max((s.min_history() for s in strategies.values()), default=lookback)
        )

        recent = get_ohlcv(symbol, interval, limit=min(need_min + 3, 1000))
        if len(recent) == 0:
            return False

        new_closed = recent.iloc[:-1].copy() if len(recent) > 1 else recent.copy()
        new_closed = _normalize_ohlcv(new_closed)
        if len(new_closed) > need_min:
            new_closed = new_closed.iloc[-need_min:].reset_index(drop=True)

        old_closed = bars_records_to_df(js.get("bars_closed", []))

        need_update = False
        if len(new_closed) != len(old_closed):
            need_update = True
        else:
            tail = min(3, len(new_closed))
            a = new_closed.tail(tail).reset_index(drop=True)
            b = old_closed.tail(tail).reset_index(drop=True)
            cmp_cols = ["open_time","open","high","low","close","volume"]
            if not a[cmp_cols].equals(b[cmp_cols]):
                need_update = True

        if need_update:
            self.warm_build_or_update(symbol, interval, lookback=lookback, strategies=strategies)
            return True
        return False
