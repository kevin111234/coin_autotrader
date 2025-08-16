# src/exchange/account.py
from typing import Optional, Dict, Any, Tuple
from .core import request
from decimal import Decimal
from src.exchange.core import request

def get_account() -> Dict[str, Any]:
    return request("GET", "/api/v3/account", signed=True)

def get_open_orders(symbol: Optional[str]=None):
    params = {"symbol": symbol} if symbol else None
    return request("GET", "/api/v3/openOrders", params, signed=True)

def get_order(symbol: str, orderId: int=None, clientOrderId: str=None):
    if not orderId and not clientOrderId:
        raise ValueError("orderId 또는 clientOrderId 필요")
    params = {"symbol": symbol}
    if orderId: params["orderId"] = orderId
    if clientOrderId: params["origClientOrderId"] = clientOrderId
    return request("GET", "/api/v3/order", params, signed=True)

def get_balances_map() -> Dict[str, Decimal]:
    """
    역할: 계정의 free 잔고를 {asset: Decimal} 맵으로 반환
    """
    acc = get_account()
    out: Dict[str, Decimal] = {}
    for b in acc.get("balances", []):
        out[b["asset"]] = Decimal(b["free"])
    return out

def get_symbol_assets(symbol_info: Dict[str, Any]) -> Tuple[str, str]:
    """
    역할: 심볼 엔트리에서 (baseAsset, quoteAsset) 튜플 추출
    """
    return symbol_info["baseAsset"], symbol_info["quoteAsset"]