# src/exchange/core.py
import os, time, hmac, hashlib, requests
from urllib.parse import urlencode
from typing import Dict, Any, Optional
from config.settings import get_api_config

_cfg = get_api_config()
BASE_URL = _cfg["BASE_URL"]
API_KEY  = _cfg["API_KEY"]
API_SECRET = _cfg["API_SECRET"]
ENV = os.getenv("BINANCE_ENV", "testnet")
RECV_WINDOW = int(os.getenv("BINANCE_RECV_WINDOW", "5000"))

_TIME_OFFSET_MS = 0

def headers(signed: bool=False) -> Dict[str,str]:
    return {"X-MBX-APIKEY": API_KEY} if signed else {}

def now_ms() -> int:
    return int(time.time()*1000) + _TIME_OFFSET_MS

def sign(params: Dict[str, Any]) -> str:
    qs = urlencode(params, doseq=True)
    return hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()

def request(method: str, path: str, params: Optional[Dict[str,Any]]=None,
            signed: bool=False, timeout: int=10):
    params = params or {}
    if signed:
        params.update({"timestamp": now_ms(), "recvWindow": RECV_WINDOW})
        params["signature"] = sign(params)
    url = f"{BASE_URL}{path}"
    r = requests.request(method, url, headers=headers(signed), params=params, timeout=timeout)
    if not r.ok:
        try: detail = r.json()
        except Exception: detail = r.text
        r.raise_for_status()
    return r.json() if r.text else {}

def server_time() -> int:
    return int(request("GET", "/api/v3/time")["serverTime"])

def sync_time(max_drift_ms: int=1000) -> int:
    global _TIME_OFFSET_MS
    srv = server_time()
    loc = int(time.time()*1000)
    _TIME_OFFSET_MS = srv - loc
    if abs(_TIME_OFFSET_MS) > max_drift_ms:
        print(f"[warn] clock drift: {_TIME_OFFSET_MS} ms (applied)")
    return _TIME_OFFSET_MS

def ping() -> bool:
    request("GET", "/api/v3/ping")
    return True
