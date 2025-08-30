# -*- coding: utf-8 -*-
from __future__ import annotations
import pandas as pd
from typing import List, Tuple, Optional, Dict, Any

_BASE_OHLCV = {"open_time","open","high","low","close","volume"}

def _infer_indicator_cols(df: pd.DataFrame) -> List[str]:
    """DF에서 지표 컬럼만 골라냄(= 전체 - 기본 OHLCV)."""
    return [c for c in df.columns if c not in _BASE_OHLCV]

def _find_first_uncomputed_idx(df: pd.DataFrame, indicator_cols: List[str]) -> int:
    """
    지표가 계산되지 않은 '가장 이른 행'의 인덱스를 찾음.
    - 규칙: indicator_cols 중 어느 하나라도 NaN이면 '미계산'으로 간주
    - 없으면 len(df) 반환(= 재계산 불필요)
    """
    if not indicator_cols or df.empty:
        return len(df)
    mask_valid = df[indicator_cols].notna().all(axis=1)
    if mask_valid.all():
        return len(df)
    # 첫 번째로 유효하지 않은(=NaN 포함) 위치
    return int((~mask_valid).idxmax())

def _stitch_indicators(
    df_base: pd.DataFrame,
    df_slice_with_ind: pd.DataFrame,
    indicator_cols: List[str],
    start_idx: int
) -> pd.DataFrame:
    """
    df_base[start_idx:] 구간의 지표 컬럼을 df_slice_with_ind의 값으로 덮어씀.
    (베이스 OHLCV는 df_base 것을 그대로 유지)
    """
    out = df_base.copy()
    slice_len = len(df_slice_with_ind)
    if slice_len == 0:
        return out
    end_idx = start_idx + slice_len
    # 덮어쓸 대상 구간 길이 보정
    end_idx = min(end_idx, len(out))
    src = df_slice_with_ind[indicator_cols].iloc[:(end_idx - start_idx)].reset_index(drop=True)
    out.loc[start_idx:end_idx-1, indicator_cols] = src.values
    return out

# src/indicators/partial_utils.py

def partial_recompute_indicators(
    strategy,
    df_with_ind: pd.DataFrame,
    df_new_base: pd.DataFrame,
    *,
    safety_buffer: Optional[int] = None
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    # 0) 행 정렬을 맞춘 'merged' 생성: df_new_base(OHLCV) 기준으로 시작
    merged = df_new_base.copy()

    # 이전 DF의 지표 컬럼 목록
    prev_ind_cols = [c for c in df_with_ind.columns if c not in _BASE_OHLCV]

    # 새 DF에 지표 컬럼이 없다면 만들고(NaN), "겹치는 행 길이"만큼 값 복사
    for c in prev_ind_cols:
        if c not in merged.columns:
            merged[c] = pd.NA
    # ★ 겹치는 구간 길이 계산
    n = min(len(df_with_ind), len(merged))
    if n > 0 and prev_ind_cols:
        # 이전 DF의 지표값을 merged 앞쪽 n행에 복사
        merged.loc[:n-1, prev_ind_cols] = df_with_ind.loc[:n-1, prev_ind_cols].values

    # 1) 이번에도 지표 컬럼은 "현재 merged에 존재하는 지표 컬럼"으로 판단
    indicator_cols = [c for c in merged.columns if c not in _BASE_OHLCV]

    # 2) 가장 이른 미계산 인덱스 탐지 (겹치는 구간은 값이 복사되어 있으므로 보통 n 근처부터 시작)
    start = _find_first_uncomputed_idx(merged, indicator_cols)

    # 3) 안전 버퍼
    if safety_buffer:
        start = max(0, start - int(safety_buffer))

    # 4) 재계산 필요 없으면 그대로
    if start >= len(merged):
        return merged.reset_index(drop=True), {
            "recompute_start": len(merged),
            "slice_rows": 0,
            "indicator_cols": indicator_cols,
        }

    # 5) 부분 슬라이스 재계산
    df_slice = merged.iloc[start:].copy()
    df_slice_ind = strategy.compute_indicators(df_slice)

    # 6) 재계산 결과를 덮어쓰기
    out = _stitch_indicators(merged, df_slice_ind, indicator_cols, start_idx=start)

    meta = {
        "recompute_start": start,
        "slice_rows": len(df_slice_ind),
        "indicator_cols": indicator_cols,
    }
    return out.reset_index(drop=True), meta

