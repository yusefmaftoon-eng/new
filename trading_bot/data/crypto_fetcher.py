"""Historical OHLCV data from Binance's public REST API (no auth required)."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd
import requests

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
MAX_KLINES_PER_CALL = 1000

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
    "4h": 14_400_000, "1d": 86_400_000,
}


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_binance_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Fetch OHLCV candles for `symbol` (e.g. 'BTCUSDT') between two 'YYYY-MM-DD' dates."""
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval {interval!r}, choose from {list(INTERVAL_MS)}")

    start_ms, end_ms = _to_ms(start), _to_ms(end)
    step_ms = INTERVAL_MS[interval]
    rows = []
    cursor = start_ms

    while cursor < end_ms:
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": MAX_KLINES_PER_CALL,
        }
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        last_open_time = batch[-1][0]
        next_cursor = last_open_time + step_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < MAX_KLINES_PER_CALL:
            break
        time.sleep(0.2)  # be polite to the public API

    if not rows:
        raise RuntimeError(f"no data returned for {symbol} {interval} {start}..{end}")

    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "num_trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df = df.set_index("open_time")[["open", "high", "low", "close", "volume"]]
    df = df[~df.index.duplicated(keep="first")]
    return df


def periods_per_year(interval: str) -> float:
    return (365 * 86_400_000) / INTERVAL_MS[interval]
