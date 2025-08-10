# src/exchange/orders.py
from typing import Optional
from .core import request, ENV

def place_test_order(symbol: str, side: str, type_: str="MARKET",
                     quantity: float=None, quote_order_qty: float=None, **extra):
    params = {"symbol": symbol, "side": side.upper(), "type": type_.upper(), **extra}
    if quantity is not None: params["quantity"] = str(quantity)
    if quote_order_qty is not None: params["quoteOrderQty"] = str(quote_order_qty)
    return request("POST", "/api/v3/order/test", params, signed=True)

def place_order(symbol: str, side: str, type_: str="MARKET",
                quantity: float=None, quote_order_qty: float=None,
                price: float=None, timeInForce: str=None,
                newClientOrderId: Optional[str]=None, allow_mainnet: bool=False, **extra):
    if ENV == "mainnet" and not allow_mainnet:
        raise RuntimeError("Mainnet 보호: allow_mainnet=True로 명시적으로 허용해야 함")
    params = {"symbol": symbol, "side": side.upper(), "type": type_.upper(), **extra}
    if newClientOrderId: params["newClientOrderId"] = newClientOrderId
    t = type_.upper()
    if t == "MARKET":
        if (quantity is None) and (quote_order_qty is None):
            raise ValueError("MARKET: quantity 또는 quote_order_qty 필요")
        if quantity is not None: params["quantity"] = str(quantity)
        if quote_order_qty is not None: params["quoteOrderQty"] = str(quote_order_qty)
    elif t == "LIMIT":
        if price is None or timeInForce is None or quantity is None:
            raise ValueError("LIMIT: price, timeInForce, quantity 필요")
        params.update({"price": str(price), "timeInForce": timeInForce, "quantity": str(quantity)})
    return request("POST", "/api/v3/order", params, signed=True)

def cancel_order(symbol: str, orderId: int=None, clientOrderId: str=None):
    if not orderId and not clientOrderId:
        raise ValueError("orderId 또는 clientOrderId 필요")
    params = {"symbol": symbol}
    if orderId: params["orderId"] = orderId
    if clientOrderId: params["origClientOrderId"] = clientOrderId
    return request("DELETE", "/api/v3/order", params, signed=True)

def cancel_open_orders(symbol: str):
    return request("DELETE", "/api/v3/openOrders", {"symbol": symbol}, signed=True)
