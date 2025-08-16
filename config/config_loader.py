from __future__ import annotations
import os, pathlib, re, time, threading
from typing import Any, Dict, List, Optional
from ruamel.yaml import YAML
from pydantic import BaseModel, Field

yaml = YAML(typ="safe")

# ENV 치환 ${VAR:-default}
_env_re = re.compile(r"\$\{([A-Z0-9_]+)(:-([^}]*))?\}")
def _env_expand(v: Any) -> Any:
    if isinstance(v, str):
        def repl(m):
            var, _, default = m.groups()
            return os.getenv(var, default or "")
        return _env_re.sub(repl, v)
    if isinstance(v, dict):
        return {k: _env_expand(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_env_expand(x) for x in v]
    return v

def _load_yaml(path: pathlib.Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.load(f) or {}
    return _env_expand(data)

def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

# ---- 스키마(현재 스코프 최소본) ----
class SymbolSpec(BaseModel):
    symbol: str
    strategy: str
    params: Dict[str, Any] = Field(default_factory=dict)

class TradingSpec(BaseModel):
    interval: str
    symbols: List[SymbolSpec]

class AlertsSpec(BaseModel):
    warn: Dict[str, float] = Field(default_factory=dict)
    critical: Dict[str, float] = Field(default_factory=dict)

class ClockGuard(BaseModel):
    max_offset_ms: int = 1000

class RootConfig(BaseModel):
    version: int
    project: str
    trading: TradingSpec
    alerts: AlertsSpec = AlertsSpec()
    clock_guard: ClockGuard = ClockGuard()
    class Config: extra = "forbid"

def load_config(base_path: str, overlays: Optional[List[str]] = None) -> RootConfig:
    merged = _load_yaml(pathlib.Path(base_path))
    for ov in (overlays or []):
        merged = _deep_merge(merged, _load_yaml(pathlib.Path(ov)))
    return RootConfig(**merged)

# ---- 파일 변경 감지(선택) ----
class ConfigWatcher:
    def __init__(self, base: str, overlays: Optional[List[str]] = None, interval=1.0):
        self.base = pathlib.Path(base)
        self.overlays = [pathlib.Path(p) for p in (overlays or [])]
        self.interval = interval
        self._cfg = load_config(str(self.base), [str(p) for p in self.overlays])
        self._sig = {}
        self._stop = False
        self._t = threading.Thread(target=self._loop, daemon=True); self._t.start()

    @property
    def cfg(self) -> RootConfig: return self._cfg
    def _files(self): return [self.base] + self.overlays
    def _finger(self, p: pathlib.Path): st = p.stat(); return (st.st_mtime_ns, st.st_size)

    def _loop(self):
        self._sig = {p: self._finger(p) for p in self._files()}
        while not self._stop:
            time.sleep(self.interval)
            changed = any(self._finger(p) != self._sig[p] for p in self._files())
            if changed:
                self._cfg = load_config(str(self.base), [str(p) for p in self.overlays])
                for p in self._files(): self._sig[p] = self._finger(p)
                print("[cfg] reloaded")

    def stop(self):
        self._stop = True
        self._t.join(timeout=2)
