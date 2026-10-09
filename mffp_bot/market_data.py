"""Candles from the public MyFundedPerps market-data WebSocket (no API key needed)."""
import itertools
import json
import time

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

WS_URL = "wss://api-stream.myfundedperpetuals.com/v1/market-data"
_ids = itertools.count(1)


def _bar(c):
    return {"t": int(c["openTime"]), "o": float(c["open"]), "h": float(c["high"]),
            "l": float(c["low"]), "c": float(c["close"]), "v": float(c.get("volume") or 0)}


def fetch_candles(provider, symbol, interval="5m", limit=400, start_ms=None, end_ms=None, timeout=20):
    payload = {"provider": provider, "symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        payload["startTime"] = int(start_ms)
    if end_ms is not None:
        payload["endTime"] = int(end_ms)
    rid = next(_ids)
    with connect(WS_URL, open_timeout=timeout, close_timeout=5, max_size=2 ** 22) as ws:
        ws.send(json.dumps({"op": "req", "id": rid, "method": "candles.history", "payload": payload}))
        deadline = time.time() + timeout
        while True:
            frame = json.loads(ws.recv(timeout=max(0.1, deadline - time.time())))
            if frame.get("id") != rid:
                continue
            if frame.get("op") == "res":
                bars = {b["t"]: b for b in map(_bar, frame.get("result") or [])}
                return [bars[k] for k in sorted(bars)]
            if frame.get("op") == "err":
                raise RuntimeError(f"candles.history failed: {frame.get('error')}")


def fetch_range(provider, symbol, interval, start_ms, end_ms, page=500):
    """Page through history in fixed windows (the server returns the newest bars in a window).
    Retention is provider-bounded, so you may get less than asked."""
    unit = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[interval[-1]] * int(interval[:-1])
    out, cur = {}, int(start_ms)
    while cur < end_ms:
        w_end = min(cur + page * unit - 1, int(end_ms))
        for attempt in range(6):
            try:
                batch = fetch_candles(provider, symbol, interval, limit=page, start_ms=cur, end_ms=w_end)
                break
            except (RuntimeError, TimeoutError, OSError, WebSocketException):   # incl. dropped connections
                if attempt == 5:
                    raise
                time.sleep(min(2 ** attempt, 20))
        for b in batch:
            out[b["t"]] = b
        cur = w_end + 1
        time.sleep(0.15)
    return [out[k] for k in sorted(out)]
