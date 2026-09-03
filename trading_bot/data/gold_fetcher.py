"""Real monthly gold prices from the World Bank Commodity Markets portal,
mirrored (no auth) by the 'datasets' open-data project on GitHub.

NOTE: this is MONTHLY resolution. Kalshi's actual gold markets settle daily
(sometimes hourly), so this is a coarser proxy than the real thing -- useful
for a directional sanity check, not a faithful simulation of Kalshi's gold
markets. No daily-resolution free gold dataset was reachable from this
environment (spot gold APIs like Yahoo/AlphaVantage are blocked here; see
README) -- provide a CSV of real daily gold closes if you want a real
daily-cadence backtest.
"""
from __future__ import annotations

import csv
import io

import requests

CSV_URL = "https://raw.githubusercontent.com/datasets/gold-prices/main/data/monthly-processed.csv"


def fetch_monthly_closes() -> list[dict]:
    """Return [{'date': 'YYYY-MM-DD', 'close': float}, ...] sorted ascending."""
    resp = requests.get(CSV_URL, timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    out = []
    for row in reader:
        try:
            out.append({"date": row["Date"], "close": float(row["Price"])})
        except (KeyError, ValueError):
            continue
    out.sort(key=lambda r: r["date"])
    return out
