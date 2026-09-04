"""Intraday OHLCV for CME micro futures from Yahoo Finance's public chart API.

Marketstack (equities/FX/crypto EOD+intraday) does not carry CME futures bars,
so MES/MNQ data comes from Yahoo Finance instead. 5-minute bars are capped at
60 days of history by Yahoo regardless of requested range.
"""
from __future__ import annotations

import requests
import pandas as pd

CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

CONTRACT_MULTIPLIER = {"MES": 5.0, "MNQ": 2.0}   # USD per index point
TICK_SIZE = {"MES": 0.25, "MNQ": 0.25}
YAHOO_SYMBOL = {"MES": "MES=F", "MNQ": "MNQ=F"}


def fetch_yahoo_intraday(symbol: str, interval: str = "5m", range_: str = "60d") -> pd.DataFrame:
    """symbol: a Yahoo ticker like 'MES=F'. Returns a tz-aware (America/New_York) OHLCV frame."""
    resp = requests.get(
        CHART_URL.format(symbol=symbol),
        params={"interval": interval, "range": range_},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    df = pd.DataFrame(
        {"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"]},
        index=pd.to_datetime(ts, unit="s", utc=True),
    ).dropna()
    df.index = df.index.tz_convert("America/New_York")
    df = df.sort_index()
    return df[~df.index.duplicated(keep="first")]


def fetch_micro_future(sym: str, interval: str = "5m", range_: str = "60d") -> pd.DataFrame:
    """sym: 'MES' or 'MNQ'."""
    if sym not in YAHOO_SYMBOL:
        raise ValueError(f"unsupported symbol {sym!r}, choose from {list(YAHOO_SYMBOL)}")
    return fetch_yahoo_intraday(YAHOO_SYMBOL[sym], interval, range_)
