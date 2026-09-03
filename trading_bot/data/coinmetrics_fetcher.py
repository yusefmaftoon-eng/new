"""Real daily crypto prices from CoinMetrics' public Community Network Data.

CoinMetrics publishes free daily on-chain + market CSVs per asset at
https://github.com/coinmetrics/data (no auth, no API key). This is genuine
historical data (not synthetic) -- used here because Binance/CoinGecko/etc.
are blocked by this environment's network policy, but raw.githubusercontent.com
is reachable.
"""
from __future__ import annotations

import csv
import io

import requests

CSV_URL = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/{asset}.csv"


def fetch_daily_closes(asset: str = "btc") -> list[dict]:
    """Return [{'date': 'YYYY-MM-DD', 'close': float}, ...] sorted ascending.

    Uses the 'PriceUSD' column (CoinMetrics' end-of-day USD reference price),
    which is the column with full historical coverage in this dataset --
    'ReferenceRateUSD' is only populated for the last few most recent days.
    """
    resp = requests.get(CSV_URL.format(asset=asset), timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    out = []
    for row in reader:
        price = row.get("PriceUSD")
        if not price:
            continue
        try:
            out.append({"date": row["time"][:10], "close": float(price)})
        except (KeyError, ValueError):
            continue
    out.sort(key=lambda r: r["date"])
    return out
