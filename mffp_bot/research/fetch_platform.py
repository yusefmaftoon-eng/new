"""Cache the platform's own candles (whatever the feed retains) to data/platform_<coin>_<iv>.csv.

python research/fetch_platform.py xyz:GOLD 1h 220
"""
import csv
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from market_data import fetch_range  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def path_for(coin, iv):
    return os.path.join(ROOT, f"platform_{coin.replace(':', '_')}_{iv}.csv")


def fetch(provider, coin, iv, days):
    end = int(time.time() * 1000)
    bars = fetch_range(provider, coin, iv, end - days * 86_400_000, end)
    os.makedirs(ROOT, exist_ok=True)
    with open(path_for(coin, iv), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "o", "h", "l", "c", "v"])
        w.writeheader()
        w.writerows(bars)
    return bars


if __name__ == "__main__":
    coin, iv, days = sys.argv[1], sys.argv[2], int(sys.argv[3])
    provider = sys.argv[4] if len(sys.argv) > 4 else ("hyperliquid" if coin.startswith("xyz:") else "binance")
    b = fetch(provider, coin, iv, days)
    print(coin, iv, len(b), "bars ->", path_for(coin, iv))
