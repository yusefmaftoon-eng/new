"""Check that histdata proxy bars track the platform's own perp bars.

For each pair: data coverage, best timestamp alignment, return correlation on overlapping
5m bars (last ~17 days) and 1h bars (~208 days), split by NY session vs off-hours, and the
price ratio (perp basis) so we know levels can be used as-is or only in % terms.
"""
import csv
import os
import statistics
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
PAIRS = [("XAUUSD", "xyz_GOLD"), ("SPXUSD", "xyz_SP500"), ("EURUSD", "xyz_EUR"), ("USDJPY", "xyz_JPY")]


def load(path):
    with open(path) as f:
        return [{k: float(v) for k, v in r.items()} for r in csv.DictReader(f)]


def resample(bars, span):
    out = {}
    for b in bars:
        k = int(b["t"]) // span * span
        if k not in out:
            out[k] = dict(b, t=k)
        else:
            o = out[k]
            o["h"], o["l"], o["c"] = max(o["h"], b["h"]), min(o["l"], b["l"]), b["c"]
    return out


def corr(xs, ys):
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** .5
    sy = sum((y - my) ** 2 for y in ys) ** .5
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else float("nan")


def rets(d, span):
    ks = sorted(d)
    return {k: d[k]["c"] / d[p]["c"] - 1 for p, k in zip(ks, ks[1:]) if k - p == span}


def compare(proxy, plat, span, shift_ms=0, session=None):
    pr = resample([dict(b, t=b["t"] + shift_ms) for b in proxy], span)
    pl = resample(plat, span)
    rp, rl = rets(pr, span), rets(pl, span)
    keys = [k for k in rl if k in rp]
    if session is not None:
        keys = [k for k in keys if (9 <= datetime.fromtimestamp(k / 1000, NY).hour < 16) == session
                and datetime.fromtimestamp(k / 1000, NY).weekday() < 5]
    if len(keys) < 30:
        return float("nan"), len(keys), float("nan")
    ratio = statistics.median(pl[k]["c"] / pr[k]["c"] for k in keys)
    return corr([rp[k] for k in keys], [rl[k] for k in keys]), len(keys), ratio


def main():
    for px, pf in PAIRS:
        proxy = load(os.path.join(ROOT, f"{px}_5m.csv"))
        p5 = load(os.path.join(ROOT, f"platform_{pf}_5m.csv"))
        p1 = load(os.path.join(ROOT, f"platform_{pf}_1h.csv"))
        f = lambda ms: datetime.fromtimestamp(ms / 1000, NY).strftime("%Y-%m-%d")
        print(f"\n{px} proxy {f(proxy[0]['t'])}..{f(proxy[-1]['t'])} ({len(proxy)} 5m bars)  vs  {pf}")
        best = max(range(-3, 4), key=lambda h: compare(proxy, p5, 300_000, h * 3_600_000, True)[0])
        print(f"  best alignment shift: {best:+d}h (0 = timestamps already aligned)")
        for label, data, span in (("5m", p5, 300_000), ("1h", p1, 3_600_000)):
            for sess, name in ((True, "NY 09-16 weekdays"), (False, "other hours")):
                c, n, r = compare(proxy, data, span, best * 3_600_000, sess)
                print(f"  {label} {name:<18} corr {c:5.3f}  n={n:<5} median perp/proxy price {r:.5f}")


if __name__ == "__main__":
    sys.exit(main())
