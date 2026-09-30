"""Long-history proxy data from Dukascopy's public datafeed (1m BID candles, one file per UTC day).

The MyFundedPerps feed only retains ~5000 bars for the TradFi (hyperliquid|xyz:*) markets
(~17 days of 5m, ~208 days of 1h), so 12+ months of intraday history has to come from a proxy.
Dukascopy spot/CFD prices track the perps closely during liquid hours; see validate_proxy.py.

Files are cached raw under data/duka/<inst>/YYYY-MM-DD.bi5 and aggregated to data/<inst>_5m.csv.
"""
import csv
import lzma
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
URL = "https://datafeed.dukascopy.com/datafeed/{inst}/{y:04d}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"
POINT = {"XAUUSD": 1e3, "USA500IDXUSD": 1e3, "EURUSD": 1e5, "USDJPY": 1e3}


def _get(url, tries=12):
    delay = 2.0
    for _ in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=30).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            if e.code != 429 and e.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(delay)
        delay = min(delay * 1.7, 60)
    raise RuntimeError(f"gave up on {url}")


def download_day(inst, day):
    path = os.path.join(ROOT, "duka", inst, f"{day.isoformat()}.bi5")
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = _get(URL.format(inst=inst, y=day.year, m=day.month - 1, d=day.day))   # month is 0-based
    with open(path + ".tmp", "wb") as f:
        f.write(raw)
    os.replace(path + ".tmp", path)
    time.sleep(0.4)
    return path


def read_day(inst, day):
    path = os.path.join(ROOT, "duka", inst, f"{day.isoformat()}.bi5")
    with open(path, "rb") as f:
        raw = f.read()
    if not raw:
        return []
    data = lzma.decompress(raw)
    t0 = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
    p = POINT[inst]
    out = []
    for i in range(len(data) // 24):
        sec, o, c, lo, hi, vol = struct.unpack(">5if", data[i * 24:(i + 1) * 24])
        if vol <= 0:            # Dukascopy pads closed minutes with flat zero-volume candles
            continue
        out.append({"t": t0 + sec * 1000, "o": o / p, "h": hi / p, "l": lo / p, "c": c / p, "v": float(vol)})
    return out


def to_5m(bars_1m):
    out, cur = [], None
    for b in bars_1m:
        k = b["t"] // 300_000 * 300_000
        if cur is None or cur["t"] != k:
            if cur:
                out.append(cur)
            cur = dict(b, t=k)
        else:
            cur["h"], cur["l"], cur["c"] = max(cur["h"], b["h"]), min(cur["l"], b["l"]), b["c"]
            cur["v"] += b["v"]
    if cur:
        out.append(cur)
    return out


def build(inst, start, end):
    days = [start + timedelta(n) for n in range((end - start).days)]
    for n, d in enumerate(days):
        download_day(inst, d)
        if n % 50 == 0:
            print(inst, d, f"{n}/{len(days)}", flush=True)
    bars = []
    for d in days:
        bars += to_5m(read_day(inst, d))
    out = os.path.join(ROOT, f"{inst}_5m.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "o", "h", "l", "c", "v"])
        w.writeheader()
        w.writerows(bars)
    print(inst, "->", out, len(bars), "bars", flush=True)


if __name__ == "__main__":
    s = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2024, 9, 30)
    e = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else date(2026, 9, 30)
    build(sys.argv[1], s, e)
