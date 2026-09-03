"""Real intraday OHLC for CME futures continuous contracts from Yahoo Finance's
chart API. No auth, but Yahoo blocks the default curl/requests User-Agent with
a 429 -- a browser UA is required.

Yahoo's free intraday history is capped by the API itself: ~60 days at 5m,
~7-8 days at 1m. This module uses 5m since the strategy operates on session
(hours-long) structure, not minute scalps.
"""
from __future__ import annotations

import requests

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch_intraday(symbol: str, interval: str = "5m", range_: str = "60d") -> list[dict]:
    """Return [{'t': unix_ts, 'o','h','l','c'}, ...] ascending, real trades only
    (Yahoo includes null bars for gaps/halts -- those are dropped)."""
    resp = requests.get(CHART_URL.format(symbol=symbol),
                         params={"interval": interval, "range": range_},
                         headers=HEADERS, timeout=20)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    ts = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
        if None in (o, h, l, c):
            continue
        bars.append({"t": t, "o": o, "h": h, "l": l, "c": c})
    return bars
