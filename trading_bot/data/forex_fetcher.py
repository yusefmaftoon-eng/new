"""Daily forex rates from the Frankfurter API (ECB reference rates, no auth, no key).

https://api.frankfurter.dev — free, no API key, business-day daily closes back
to 1999. This is a single official daily fixing per pair (no intraday OHLC),
which is the honest limitation documented in run_propfirm_backtest.py: daily
close-to-close is the finest granularity available, so "daily loss" and
"max drawdown" rule checks below are an approximation, not a true intraday
simulation.
"""
from __future__ import annotations

import pandas as pd
import requests

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/{start}..{end}"
HEADERS = {"User-Agent": "trading-bot-research/1.0"}


def fetch_forex_daily(base: str, quote: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily `quote` price of 1 unit of `base` (e.g. base='EUR', quote='USD'
    for EUR/USD) between two 'YYYY-MM-DD' dates. Returns a DataFrame with a
    'close' column and a DatetimeIndex (business days only, ECB doesn't publish
    on weekends/EU holidays)."""
    url = FRANKFURTER_URL.format(start=start, end=end)
    resp = requests.get(url, params={"from": base, "to": quote}, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    rates = payload.get("rates", {})
    if not rates:
        raise RuntimeError(f"no data returned for {base}/{quote} {start}..{end}")

    rows = [(pd.Timestamp(date, tz="UTC"), values[quote]) for date, values in rates.items()]
    rows.sort(key=lambda r: r[0])
    df = pd.DataFrame(rows, columns=["date", "close"]).set_index("date")
    # Synthesize open/high/low from consecutive closes so the existing engine's
    # 'close'-only usage still works; this is daily-fixing data, not real OHLC.
    df["open"] = df["close"].shift(1).fillna(df["close"].iloc[0])
    df["high"] = df[["open", "close"]].max(axis=1)
    df["low"] = df[["open", "close"]].min(axis=1)
    return df[["open", "high", "low", "close"]]


def periods_per_year() -> float:
    return 252.0  # business days/year, standard for daily forex/equity data
