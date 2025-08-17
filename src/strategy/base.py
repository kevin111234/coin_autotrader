from abc import ABC, abstractmethod
import pandas as pd
from typing import Any, Dict, Optional, List

class Strategy(ABC):
    """모든 전략이 따라야 하는 인터페이스"""
    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def min_history(self) -> int:
        """지표 계산에 필요한 최소 캔들 수"""
        ...

    @abstractmethod
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """필요 지표 컬럼을 df에 추가해서 반환"""
        ...

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Optional[str]:
        """'BUY'|'SELL'|None 반환"""
        ...

    def __repr__(self):
        return f"{self.__class__.__name__}({self.params})"

    # ---------- 공용 유틸: 지표 NaN 제거 ----------
    def drop_indicator_nans(
        self,
        df: pd.DataFrame,
        indicator_cols: List[str],
        mode: str = "leading",
    ) -> pd.DataFrame:
        """
        역할: 기술적 지표 계산 후 생기는 NaN을 제거.
        - mode="leading": 맨 앞쪽(워밍업 구간)의 연속 NaN만 잘라냄(권장)
        - mode="any": indicator_cols 중 NaN 있는 모든 행 제거(공격적)

        주의:
        - EMA/RSI는 초기에만 NaN이 생기므로 leading 방식이 일반적으로 안전함.
        - any 모드는 최신 행에서도 NaN이 생기면 날려버리므로, 실시간 스냅샷에서
          의도치 않게 마지막 행이 사라질 수 있음.
        """
        if not indicator_cols:
            return df

        out = df.copy()

        if mode == "any":
            return out.dropna(subset=indicator_cols).reset_index(drop=True)

        # mode == "leading": 첫 "모든 지표가 유효"한 인덱스를 찾아 앞부분 절단
        valid_mask = out[indicator_cols].notna().all(axis=1)
        if not valid_mask.any():
            # 전부 NaN이면 빈 DF 반환
            return out.iloc[0:0].reset_index(drop=True)
        first_valid_idx = valid_mask.idxmax()  # 첫 True의 인덱스
        return out.loc[first_valid_idx:].reset_index(drop=True)