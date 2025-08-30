# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, time, urllib.request

SLACK_TOKEN = os.getenv("SLACK_API_KEY")  # settings.get_api_config()로도 가능하면 그쪽을 사용
DEFAULT_CHANNEL = os.getenv("SLACK_CHANNEL", "#trading-bot")

def _post_json(url: str, data: dict, token: str):
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"))
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))

def notify(text: str, *, channel: str | None = None, blocks: list | None = None) -> dict:
    """
    역할: Slack 채널에 메시지 전송
    input: text(필수), channel(없으면 DEFAULT_CHANNEL), blocks(선택)
    output: Slack API 응답(dict)
    """
    ch = channel or DEFAULT_CHANNEL
    if not SLACK_TOKEN:
        return {"ok": False, "error": "SLACK_TOKEN_MISSING", "text": text, "channel": ch}

    payload = {"channel": ch, "text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        return _post_json("https://slack.com/api/chat.postMessage", payload, SLACK_TOKEN)
    except Exception as e:
        return {"ok": False, "error": str(e), "text": text, "channel": ch}

def fmt_order_msg(
    *, title: str, symbol: str, side: str, price: str | float | None, qty: str | float | None,
    extra: dict | None = None
) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    p = f"{price}" if price is not None else "-"
    q = f"{qty}" if qty is not None else "-"
    tail = f" | {extra}" if extra else ""
    return f"[{ts}] {title} | {symbol} {side} @ {p} x {q}{tail}"
