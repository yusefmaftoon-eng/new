"""Historical data from Kalshi's public REST API (no auth required for market data).

Verified live against api.elections.kalshi.com from this environment:
  - GET /trade-api/v2/markets?series_ticker=...&status=settled  (resolved markets + result)
  - GET /trade-api/v2/series/{series_ticker}/markets/{ticker}/candlesticks  (price history)
"""
from __future__ import annotations

import time
from typing import Iterator

import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
HEADERS = {"User-Agent": "trading-bot-research/1.0"}


def _get_with_retry(url: str, params: dict, max_retries: int = 6) -> dict:
    delay = 1.0
    for attempt in range(max_retries):
        resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        if resp.status_code == 429:
            time.sleep(delay)
            delay = min(delay * 1.8, 15.0)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def fetch_settled_markets(series_ticker: str, max_markets: int = 500,
                           page_size: int = 200) -> Iterator[dict]:
    """Yield settled (resolved) markets for a series, newest first.

    Only yields markets with a clean yes/no result (skips voided/ambiguous ones).
    """
    fetched = 0
    cursor = None
    while fetched < max_markets:
        params = {
            "series_ticker": series_ticker,
            "status": "settled",
            "limit": min(page_size, max_markets - fetched),
        }
        if cursor:
            params["cursor"] = cursor
        payload = _get_with_retry(f"{BASE_URL}/markets", params)
        batch = payload.get("markets", [])
        if not batch:
            return
        for m in batch:
            if m.get("result") not in ("yes", "no"):
                continue
            yield m
            fetched += 1
            if fetched >= max_markets:
                return
        cursor = payload.get("cursor")
        if not cursor:
            return
        time.sleep(0.15)


def fetch_candlesticks(series_ticker: str, ticker: str, start_ts: int, end_ts: int,
                        period_interval: int = 60, max_retries: int = 5) -> list[dict]:
    """Return candlestick history for one market. period_interval is in minutes
    (Kalshi accepts 1, 60, or 1440)."""
    url = f"{BASE_URL}/series/{series_ticker}/markets/{ticker}/candlesticks"
    params = {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval}
    return _get_with_retry(url, params, max_retries=max_retries).get("candlesticks", [])


def candle_price(candle: dict) -> float | None:
    """Extract a usable YES price (dollars) from a candlestick, falling back to the
    carried-forward 'previous' price when the bucket had no trades."""
    price = candle.get("price", {})
    val = price.get("close_dollars") or price.get("mean_dollars") or price.get("previous_dollars")
    return float(val) if val is not None else None


def kalshi_taker_fee(contracts: float, price: float, fee_multiplier: float = 0.07) -> float:
    """Kalshi's standard quadratic taker fee: ceil(multiplier * C * P * (1-P) * 100) / 100,
    rounded up to the next cent."""
    import math
    raw_cents = fee_multiplier * contracts * price * (1 - price) * 100
    return math.ceil(raw_cents) / 100
