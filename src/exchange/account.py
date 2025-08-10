# src/exchange/account.py
from typing import Optional, Dict, Any
from .core import request

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
