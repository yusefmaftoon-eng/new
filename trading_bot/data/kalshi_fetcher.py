"""Historical data from Kalshi's public REST API (read endpoints need no auth).

Uses the v2 trade API:
  - GET /markets              to list settled (resolved) binary markets.
  - GET /series/{series_ticker}/markets/{ticker}/candlesticks
                               for a market's price history leading up to close.

Kalshi quotes prices in cents (1-99) for a $1-payout contract; this module
converts everything to a 0-1 probability scale so the rest of the framework
(shared with Polymarket, also 0-1) doesn't need to know the difference.

NOTE: this environment can't reach api.elections.kalshi.com directly (see
README), but the endpoint paths, param names, and response field names below
ARE verified -- against Kalshi's own official `kalshi-python` OpenAPI-
generated client (installed from PyPI, which IS reachable here), not just
documentation. What's still unverified is runtime behavior this environment
can't observe: actual response values/edge cases, rate limits, and whether
the live API has moved on since that client was published.
"""
from __future__ import annotations

from typing import Iterator

import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
MARKETS_URL = f"{BASE_URL}/markets"


def fetch_resolved_markets(max_markets: int = 200, page_size: int = 100) -> Iterator[dict]:
    """Yield settled binary markets with their final YES/NO result."""
    fetched = 0
    cursor = None
    while fetched < max_markets:
        params = {
            "status": "settled",
            "limit": min(page_size, max_markets - fetched),
        }
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(MARKETS_URL, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        batch = payload.get("markets", [])
        if not batch:
            return
        for market in batch:
            result = market.get("result")  # "yes" or "no"
            if result not in ("yes", "no"):
                continue
            yield {
                "ticker": market.get("ticker"),
                "series_ticker": market.get("series_ticker") or market.get("event_ticker"),
                "title": market.get("title"),
                # kept as *_price (0.0/1.0) rather than a bool so this dict is a
                # drop-in match for backtest.polymarket_engine, which is market-
                # agnostic over any 0-1-priced binary market.
                "final_yes_price": 1.0 if result == "yes" else 0.0,
                "close_time": market.get("close_time"),
                "volume": float(market.get("volume") or 0.0),
            }
            fetched += 1
        cursor = payload.get("cursor")
        if not cursor:
            return


def fetch_price_history(series_ticker: str, ticker: str, start_ts: int, end_ts: int,
                         period_interval: str = "1h") -> list[dict]:
    """Return [{'t': unix_ts, 'p': yes_probability_0_to_1}, ...] for a market.

    period_interval: one of the API's string codes, e.g. '1m', '5m', '1h', '1d'
    (per kalshi_python.MarketsApi.get_market_candlesticks -- NOT a minute count).
    """
    url = f"{BASE_URL}/series/{series_ticker}/markets/{ticker}/candlesticks"
    params = {
        "start_ts": start_ts,
        "end_ts": end_ts,
        "period_interval": period_interval,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    candles = resp.json().get("candlesticks", [])
    history = []
    for candle in candles:
        # kalshi_python.models.Candlestick: start_ts, end_ts, open, high, low, close, volume
        close = candle.get("close")
        if close is None:
            continue
        history.append({"t": candle.get("end_ts"), "p": close / 100.0})
    return history
