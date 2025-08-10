from typing import Dict, Type
from .base import Strategy

REGISTRY: Dict[str, Type[Strategy]] = {}

def register(name: str):
    """데코레이터: 전략을 이름으로 등록"""
    def deco(cls: Type[Strategy]):
        if name in REGISTRY:
            raise ValueError(f"중복 전략명: {name}")
        REGISTRY[name] = cls
        return cls
    return deco

def create_strategy(name: str, **params) -> Strategy:
    if name not in REGISTRY:
        raise ValueError(f"미등록 전략: {name}. 등록된 전략: {list(REGISTRY.keys())}")
    return REGISTRY[name](**params)
