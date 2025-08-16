# src/exchange/core.py
import os, time, hmac, hashlib, requests
from urllib.parse import urlencode
from typing import Dict, Any, Optional, Tuple

from config.settings import get_api_config

_cfg = get_api_config()
BASE_URL = _cfg["BASE_URL"]
API_KEY  = _cfg["API_KEY"]
API_SECRET = _cfg["API_SECRET"]

ENV = os.getenv("BINANCE_ENV", "testnet")
RECV_WINDOW = int(os.getenv("BINANCE_RECV_WINDOW", "5000"))  # ms (동적으로 조정 가능)
MAX_RETRY_ON_1021 = 1  # -1021 감지시 재동기화 후 재시도 횟수

_TIME_OFFSET_MS = 0  # 서버시간 - 로컬시간 (요청 timestamp 보정에 사용)

# -------------------- 내부 유틸 --------------------
def headers(signed: bool=False) -> Dict[str,str]:
    return {"X-MBX-APIKEY": API_KEY} if signed else {}

def now_ms() -> int:
    return int(time.time()*1000) + _TIME_OFFSET_MS

def sign(params: Dict[str, Any]) -> str:
    from urllib.parse import urlencode
    qs = urlencode(params, doseq=True)
    return hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()

def _raw_request(method: str, path: str, params: Optional[Dict[str,Any]]=None,
                 signed: bool=False, timeout: int=10):
    params = params or {}
    if signed:
        params.update({"timestamp": now_ms(), "recvWindow": RECV_WINDOW})
        params["signature"] = sign(params)
    url = f"{BASE_URL}{path}"
    return requests.request(method, url, headers=headers(signed), params=params, timeout=timeout)

def request(method: str, path: str, params: Optional[Dict[str,Any]]=None,
            signed: bool=False, timeout: int=10):
    """
    -1021(Timestamp outside recvWindow) 발생 시: sync_time() 수행 후 최대 1회 자동 재시도
    """
    params = params or {}
    for attempt in range(MAX_RETRY_ON_1021 + 1):
        r = _raw_request(method, path, params.copy(), signed=signed, timeout=timeout)
        if r.ok:
            return r.json() if r.text else {}
        # 오류 파싱
        try:
            j = r.json()
            code = j.get("code")
            msg = j.get("msg", "")
        except Exception:
            j, code, msg = None, None, r.text

        # -1021: 시간 오차 → 재동기화하고 한 번만 재시도
        if code == -1021 and attempt < MAX_RETRY_ON_1021:
            print("[info] -1021 detected. Resyncing time and retrying once...")
            sync_time(auto_increase_recv_window=True)
            continue

        # 그 외 오류는 그대로 raise
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            raise RuntimeError(f"HTTP error {r.status_code} code={code} msg={msg}") from e

# -------------------- 시간 동기화 --------------------
def server_time() -> int:
    return int(request("GET", "/api/v3/time")["serverTime"])

def _measure_offset_once() -> Tuple[int, int]:
    """
    RTT 보정된 오프셋 측정.
    return: (offset_ms, rtt_ms)
      offset_ms = srv_time - midpoint_local_time
    """
    t0 = int(time.time()*1000)
    r = requests.get(f"{BASE_URL}/api/v3/time", timeout=5)
    t1 = int(time.time()*1000)
    r.raise_for_status()
    srv = int(r.json()["serverTime"])
    rtt = t1 - t0
    mid = t0 + rtt//2  # 요청-응답 중간시점이 서버 응답시각에 가장 근접
    offset = srv - mid
    return offset, rtt

def sync_time(samples: int = 5, max_drift_ms: int = 1000, auto_increase_recv_window: bool = False) -> int:
    """
    서버-로컬 시간 오차를 여러 번 측정해 median으로 보정.
    - samples: 측정 횟수(>=3 권장)
    - max_drift_ms: 허용 오차. 초과 시 경고 출력.
    - auto_increase_recv_window: 큰 drift면 recvWindow를 자동 확장(안전 마진 500ms)
    """
    global _TIME_OFFSET_MS, RECV_WINDOW
    offsets, rtts = [], []

    for _ in range(max(3, samples)):
        off, rtt = _measure_offset_once()
        offsets.append(off); rtts.append(rtt)
        time.sleep(0.05)  # 짧은 간격

    # 이상치 제거: 상하위 1개씩 제거(샘플 충분할 때)
    offs_sorted = sorted(offsets)
    if len(offs_sorted) >= 5:
        offs_trim = offs_sorted[1:-1]
    else:
        offs_trim = offs_sorted

    median_off = int(offs_trim[len(offs_trim)//2])
    _TIME_OFFSET_MS = median_off

    # drift 경고 및 recvWindow 자동 확장(선택)
    if abs(_TIME_OFFSET_MS) > max_drift_ms:
        print(f"[warn] clock drift: {_TIME_OFFSET_MS} ms (applied)")
        if auto_increase_recv_window:
            # 최소 필요창 = |offset| + 네트워크 여유(500ms)
            need = abs(_TIME_OFFSET_MS) + 500
            if need > RECV_WINDOW:
                RECV_WINDOW = need
                print(f"[info] recvWindow increased to {RECV_WINDOW} ms")

    return _TIME_OFFSET_MS

def ping() -> bool:
    request("GET", "/api/v3/ping")
    return True
