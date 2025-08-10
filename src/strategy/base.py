from abc import ABC, abstractmethod
import pandas as pd
from typing import Any, Dict, Optional

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
