"""Long-history proxy data (1m bars) from histdata.com, aggregated to 5m CSV.

Why a proxy: the MyFundedPerps feed only retains ~5000 bars for the TradFi (hyperliquid|xyz:*)
markets (~17 days of 5m, ~208 days of 1h; xyz:SP500 only listed 2026-03-18), so 12+ months of
intraday history is not available from the platform itself. histdata spot/CFD prices are
checked against the platform's own bars in validate_proxy.py before use.

histdata documents its stamps as fixed EST, but checked against the platform's own bars they
are UTC-5 in winter / UTC-4 in summer switching on the *EU* DST dates (MT4-style server time,
= Europe/Athens minus 7h). In the US-vs-EU DST gap weeks a NY-local reading is 1h off.
Converted here to UTC epoch ms.
histdata has no volume, so v = number of 1m bars in the 5m bar (a time-at-price proxy).

python research/histdata.py XAUUSD 2024-09 2026-09
"""
import csv
import io
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
PAGE = "https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{p}/{y}/{m}"
GET = "https://www.histdata.com/get.php"
SERVER = ZoneInfo("Europe/Athens")    # stamp + 7h is Athens local time
UA = {"User-Agent": "Mozilla/5.0"}


def months(a, b):
    y, m = map(int, a.split("-"))
    y2, m2 = map(int, b.split("-"))
    while (y, m) <= (y2, m2):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def download_month(pair, y, m):
    """Monthly archive; completed past years are only published as one annual archive (m=None)."""
    path = os.path.join(ROOT, "histdata", pair, f"{y}{m:02d}.zip" if m else f"{y}.zip")
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    page_url = PAGE.format(p=pair.lower(), y=y, m=m) if m else PAGE.format(p=pair.lower(), y=y, m="").rstrip("/")
    html = requests.get(page_url, headers=UA, timeout=30).text
    form = dict(re.findall(r'name="(tk|date|datemonth|platform|timeframe|fxpair)" id="\w+" value="([^"]*)"', html))
    if "tk" not in form:
        return None                                         # month not published
    r = requests.post(GET, data=form, headers={**UA, "Referer": page_url}, timeout=120)
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        return None
    with open(path + ".tmp", "wb") as f:
        f.write(r.content)
    os.replace(path + ".tmp", path)
    time.sleep(1)
    return path


def read_month(path):
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        text = z.read(name).decode()
    out = []
    for row in csv.reader(io.StringIO(text), delimiter=";"):
        ts = (datetime.strptime(row[0], "%Y%m%d %H%M%S") + timedelta(hours=7)).replace(tzinfo=SERVER)
        o, h, l, c = map(float, row[1:5])
        out.append({"t": int(ts.timestamp() * 1000), "o": o, "h": h, "l": l, "c": c})
    return out


def to_5m(bars_1m):
    out, cur = [], None
    for b in bars_1m:
        k = b["t"] // 300_000 * 300_000
        if cur is None or cur["t"] != k:
            if cur:
                out.append(cur)
            cur = dict(b, t=k, v=1.0)
        else:
            cur["h"], cur["l"], cur["c"] = max(cur["h"], b["h"]), min(cur["l"], b["l"]), b["c"]
            cur["v"] += 1
    if cur:
        out.append(cur)
    return out


def build(pair, start, end):
    one, years_done = [], set()
    for y, m in months(start, end):
        p = download_month(pair, y, m)
        if not p and y not in years_done:
            p = download_month(pair, y, None)
            years_done.add(y)
        elif not p:
            continue
        print(pair, y, m, "ok" if p else "not available", flush=True)
        if p:
            one += read_month(p)
    lo = int(datetime.strptime(start, "%Y-%m").replace(tzinfo=timezone.utc).timestamp() * 1000)
    one = sorted({b["t"]: b for b in one if b["t"] >= lo}.values(), key=lambda b: b["t"])
    bars = to_5m(one)
    out = os.path.join(ROOT, f"{pair}_5m.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "o", "h", "l", "c", "v"])
        w.writeheader()
        w.writerows(bars)
    print(pair, "->", out, len(bars), "bars", flush=True)


if __name__ == "__main__":
    build(sys.argv[1].upper(), sys.argv[2], sys.argv[3])
