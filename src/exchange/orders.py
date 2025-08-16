# src/exchange/orders.py
from __future__ import annotations
from typing import Optional, Dict, Any
from src.exchange.core import request  # 서명/타임스탬프/recvWindow 처리
from src.exchange.core import ENV      # mainnet 보호 가드에 사용

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

def place_oco_order(
    symbol: str,
    side: str,  # "BUY" | "SELL"
    *,
    quantity: str,
    aboveType: str,
    belowType: str,
    abovePrice: Optional[str] = None,
    aboveStopPrice: Optional[str] = None,
    aboveTimeInForce: Optional[str] = None,
    belowPrice: Optional[str] = None,
    belowStopPrice: Optional[str] = None,
    belowTimeInForce: Optional[str] = None,
    listClientOrderId: Optional[str] = None,
    aboveClientOrderId: Optional[str] = None,
    belowClientOrderId: Optional[str] = None,
    newOrderRespType: str = "RESULT",
    allow_mainnet: bool = False,
) -> Dict[str, Any]:
    """
    역할: Spot OCO 생성 (신규 엔드포인트 /api/v3/orderList/oco).
    input: Binance가 요구하는 OCO 파라미터들(문자열로 전달 권장)
    output: Binance 응답 JSON(dict)
    주의: OCO는 /order/test가 없음 → 실제 엔진으로 들어감. 테스트는 testnet에서만.
    """
    if (ENV == "mainnet") and (not allow_mainnet):
        raise RuntimeError("mainnet OCO blocked (allow_mainnet=False)")

    params = {
        "symbol": symbol,
        "side": side.upper(),
        "quantity": quantity,
        "aboveType": aboveType,
        "belowType": belowType,
        "newOrderRespType": newOrderRespType,
    }
    if listClientOrderId:  params["listClientOrderId"]  = listClientOrderId
    if aboveClientOrderId: params["aboveClientOrderId"] = aboveClientOrderId
    if belowClientOrderId: params["belowClientOrderId"] = belowClientOrderId
    if abovePrice:         params["abovePrice"]         = abovePrice
    if aboveStopPrice:     params["aboveStopPrice"]     = aboveStopPrice
    if aboveTimeInForce:   params["aboveTimeInForce"]   = aboveTimeInForce
    if belowPrice:         params["belowPrice"]         = belowPrice
    if belowStopPrice:     params["belowStopPrice"]     = belowStopPrice
    if belowTimeInForce:   params["belowTimeInForce"]   = belowTimeInForce

    return request("POST", "/api/v3/orderList/oco", params, signed=True)

def cancel_order_list(*, orderListId: int | None = None, listClientOrderId: str | None = None,
                      allow_mainnet: bool = False) -> Dict[str, Any]:
    if (ENV == "mainnet") and (not allow_mainnet):
        raise RuntimeError("mainnet cancel order list blocked")
    if not orderListId and not listClientOrderId:
        raise ValueError("orderListId 또는 listClientOrderId 중 하나는 필요")

    params: Dict[str, Any] = {}
    if orderListId:       params["orderListId"] = orderListId
    if listClientOrderId: params["listClientOrderId"] = listClientOrderId
    return request("DELETE", "/api/v3/orderList", params, signed=True)

def get_order_list(*, orderListId: int | None = None, listClientOrderId: str | None = None) -> Dict[str, Any]:
    if not orderListId and not listClientOrderId:
        raise ValueError("orderListId 또는 listClientOrderId 중 하나는 필요")
    params: Dict[str, Any] = {}
    if orderListId:       params["orderListId"] = orderListId
    if listClientOrderId: params["listClientOrderId"] = listClientOrderId
    return request("GET", "/api/v3/orderList", params, signed=True)

def get_order(symbol: str, *,
              orderId: Optional[int] = None,
              origClientOrderId: Optional[str] = None) -> Dict[str, Any]:
    if not orderId and not origClientOrderId:
        raise ValueError("orderId 또는 origClientOrderId 중 하나는 필요")
    params: Dict[str, Any] = {"symbol": symbol}
    if orderId: params["orderId"] = orderId
    if origClientOrderId: params["origClientOrderId"] = origClientOrderId
    return request("GET", "/api/v3/order", params, signed=True)

def get_order_safe(symbol: str, *,
                   orderId: Optional[int] = None,
                   origClientOrderId: Optional[str] = None) -> Dict[str, Any]:
    try:
        return get_order(symbol, orderId=orderId, origClientOrderId=origClientOrderId)
    except Exception:
        if orderId and origClientOrderId:
            return get_order(symbol, orderId=None, origClientOrderId=origClientOrderId)
        raise

def get_open_order_lists() -> Dict[str, Any]:
    # GET /api/v3/openOrderList (서명 필요)
    return request("GET", "/api/v3/openOrderList", {}, signed=True)
