"""Historical data from Polymarket's public APIs (no auth required).

Uses:
  - Gamma API (gamma-api.polymarket.com) to list resolved markets and their
    final outcome/prices.
  - CLOB API (clob.polymarket.com) to pull a token's price history leading
    up to resolution.

NOTE: this was written from Polymarket's public API documentation without
being able to hit the live endpoints from this environment (network egress
was blocked here — see README). Response field names are correct as
documented at the time of writing, but double check against a live call
before relying on this, since public APIs do change shape over time.
"""
from __future__ import annotations

import json
from typing import Iterator

import requests

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
CLOB_PRICE_HISTORY_URL = "https://clob.polymarket.com/prices-history"


def fetch_resolved_markets(max_markets: int = 200, page_size: int = 100) -> Iterator[dict]:
    """Yield resolved (closed) binary markets with their final outcome prices."""
    fetched = 0
    offset = 0
    while fetched < max_markets:
        params = {
            "closed": "true",
            "limit": min(page_size, max_markets - fetched),
            "offset": offset,
            "order": "volume",
            "ascending": "false",
        }
        resp = requests.get(GAMMA_MARKETS_URL, params=params, timeout=15)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            return
        for market in batch:
            token_ids = market.get("clobTokenIds")
            if isinstance(token_ids, str):
                try:
                    token_ids = json.loads(token_ids)
                except json.JSONDecodeError:
                    token_ids = None
            outcome_prices = market.get("outcomePrices")
            if isinstance(outcome_prices, str):
                try:
                    outcome_prices = json.loads(outcome_prices)
                except json.JSONDecodeError:
                    outcome_prices = None
            if not token_ids or len(token_ids) < 2 or not outcome_prices:
                continue
            yield {
                "id": market.get("id"),
                "question": market.get("question"),
                "yes_token_id": token_ids[0],
                "no_token_id": token_ids[1],
                "final_yes_price": float(outcome_prices[0]),
                "closed_time": market.get("closedTime") or market.get("endDate"),
                "volume": float(market.get("volume") or 0.0),
            }
            fetched += 1
        offset += page_size


def fetch_price_history(token_id: str, start_ts: int, end_ts: int, fidelity_minutes: int = 60):
    """Return a list of {'t': unix_ts, 'p': price} points for a CLOB token."""
    params = {
        "market": token_id,
        "startTs": start_ts,
        "endTs": end_ts,
        "fidelity": fidelity_minutes,
    }
    resp = requests.get(CLOB_PRICE_HISTORY_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("history", [])
