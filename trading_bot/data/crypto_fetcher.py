"""Historical OHLCV data from Coinbase Exchange's public REST API (no auth required).

Originally written against Binance, but this environment's network egress
blocks api.binance.com (HTTP 451, geo-restricted). Kraken's public OHLC
endpoint is reachable but only retains the most recent ~720 candles per pair
(no real historical pagination), which silently truncates any multi-year
backtest. Coinbase Exchange's `/candles` endpoint supports proper start/end
pagination back to each product's listing date, so it's the default source.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{product}/candles"
MAX_CANDLES_PER_CALL = 300
HEADERS = {"User-Agent": "trading-bot-research/1.0"}

# Binance-style tickers (what the rest of this codebase/CLI uses) mapped to
# Coinbase product IDs.
SYMBOL_MAP = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSDT": "SOL-USD",
    "XRPUSDT": "XRP-USD",
    "DOGEUSDT": "DOGE-USD",
    "ADAUSDT": "ADA-USD",
    "LTCUSDT": "LTC-USD",
    "LINKUSDT": "LINK-USD",
}

# Coinbase only supports these native granularities (seconds); "4h" is built
# by resampling 1h candles since Coinbase has no native 4h bucket.
GRANULARITY_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
    "4h": 14_400_000, "6h": 21_600_000, "1d": 86_400_000,
}


def _to_product(symbol: str) -> str:
    symbol = symbol.upper()
    if symbol in SYMBOL_MAP:
        return SYMBOL_MAP[symbol]
    if symbol.endswith("USDT"):
        return symbol[:-4] + "-USD"
    return symbol


def _fetch_native(product: str, granularity_s: int, start: datetime, end: datetime) -> pd.DataFrame:
    rows = []
    window = timedelta(seconds=granularity_s * MAX_CANDLES_PER_CALL)
    cursor = start
    url = COINBASE_CANDLES_URL.format(product=product)

    while cursor < end:
        window_end = min(cursor + window, end)
        params = {
            "start": cursor.isoformat().replace("+00:00", "Z"),
            "end": window_end.isoformat().replace("+00:00", "Z"),
            "granularity": granularity_s,
        }
        resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        if resp.status_code == 429:
            time.sleep(1.0)
            continue
        resp.raise_for_status()
        batch = resp.json()
        if isinstance(batch, dict) and batch.get("message"):
            raise RuntimeError(f"Coinbase API error for {product}: {batch['message']}")
        rows.extend(batch)
        cursor = window_end
        time.sleep(0.35)  # public endpoint rate limit is ~10 req/s; be conservative

    if not rows:
        raise RuntimeError(f"no data returned for {product} between {start} and {end}")

    df = pd.DataFrame(rows, columns=["open_time", "low", "high", "open", "close", "volume"])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="s", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df = df.set_index("open_time")[["open", "high", "low", "close", "volume"]]
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def fetch_crypto_ohlc(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Fetch OHLCV candles for `symbol` (Binance-style, e.g. 'BTCUSDT') from Coinbase
    Exchange between two 'YYYY-MM-DD' dates (UTC)."""
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval {interval!r}, choose from {list(INTERVAL_MS)}")

    product = _to_product(symbol)
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    if interval == "4h":
        hourly = _fetch_native(product, GRANULARITY_SECONDS["1h"], start_dt, end_dt)
        df = hourly.resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
        return df

    return _fetch_native(product, GRANULARITY_SECONDS[interval], start_dt, end_dt)


# Backwards-compatible alias (old name referenced Binance specifically).
fetch_binance_klines = fetch_crypto_ohlc


def periods_per_year(interval: str) -> float:
    return (365 * 86_400_000) / INTERVAL_MS[interval]
