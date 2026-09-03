"""Real historical 1-minute OHLCV from Dukascopy's free, no-auth historical
data feed (datafeed.dukascopy.com). Used to get real gold history deeper
than Yahoo's free 60-day intraday cap -- Dukascopy's XAUUSD (spot gold)
tracks GC futures closely enough to substitute for a longer-history test.

File format (reverse-engineered and verified against known Jan-1-2024 spot
gold price ~$2062-2066, one file per UTC day):
  LZMA-compressed; each decompressed record is 24 bytes, big-endian:
  uint32 seconds-since-day-start, int32 open, int32 close, int32 low,
  int32 high, float32 volume(lots) -- prices are the real price x 1000
  (XAUUSD is quoted to 3 decimals in this feed).

The feed rate-limits aggressively (503s after a handful of rapid requests) --
this module paces requests and retries with backoff.
"""
from __future__ import annotations

import lzma
import struct
import subprocess
import time
from datetime import date, timedelta

URL = "https://datafeed.dukascopy.com/datafeed/{symbol}/{year}/{month:02d}/{day:02d}/BID_candles_min_1.bi5"
RECORD_FMT = ">Iiiiif"
RECORD_SIZE = struct.calcsize(RECORD_FMT)
PRICE_SCALE = 1000.0


def fetch_day(symbol: str, d: date, retries: int = 4, pause: float = 0.4) -> list[dict]:
    """Return 1-min bars for one UTC calendar day, or [] if unavailable
    (weekend/holiday -- these come back as empty/near-empty low-volume data
    or a 404, both treated as 'no data')."""
    url = URL.format(symbol=symbol, year=d.year, month=d.month - 1, day=d.day)
    content = b""
    for attempt in range(retries):
        # shells out to curl (one fresh connection per request) rather than a
        # pooled requests.Session -- this environment's egress proxy was
        # dropping pooled/keep-alive connections to this host mid-exchange.
        proc = subprocess.run(
            ["curl", "-sS", "-m", "30", "-w", "\n%{http_code}", url],
            capture_output=True, timeout=35,
        )
        if proc.returncode != 0:
            time.sleep(pause * (2 ** attempt) + 2.0)
            continue
        body, _, code = proc.stdout.rpartition(b"\n")
        status = int(code) if code.isdigit() else 0
        if status in (503, 429, 0):
            time.sleep(pause * (2 ** attempt) + 2.0)
            continue
        if status == 404 or not body:
            return []
        if status != 200:
            time.sleep(pause * (2 ** attempt) + 2.0)
            continue
        content = body
        break
    else:
        raise RuntimeError(f"Dukascopy still rate-limited/unreachable after {retries} retries for {symbol} {d}")

    try:
        raw = lzma.decompress(content)
    except lzma.LZMAError:
        return []

    import calendar
    day_start_ts = calendar.timegm(d.timetuple())

    bars = []
    for i in range(0, len(raw) - RECORD_SIZE + 1, RECORD_SIZE):
        secs, o, c, l, h, v = struct.unpack(RECORD_FMT, raw[i:i + RECORD_SIZE])
        if o == 0:
            continue
        bars.append({
            "t": day_start_ts + secs,
            "o": o / PRICE_SCALE, "h": h / PRICE_SCALE, "l": l / PRICE_SCALE, "c": c / PRICE_SCALE,
        })
    time.sleep(pause)
    return bars


def fetch_range(symbol: str, start: date, end: date, pause: float = 0.4,
                 retries: int = 6) -> tuple[list[dict], list[date]]:
    """Fetch every UTC calendar day from start to end (inclusive), skipping
    weekends (market closed, no file). Ascending by time. A day that's still
    rate-limited/unreachable after its own retries is SKIPPED (not fatal to
    the whole range) and returned in the second element so gaps are visible
    rather than silently dropped."""
    bars = []
    failed = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri; FX/gold trades ~Sun 5pm ET - Fri 5pm ET but daily files
            try:
                bars.extend(fetch_day(symbol, d, pause=pause, retries=retries))
            except RuntimeError:
                failed.append(d)
        d += timedelta(days=1)
    return bars, failed
