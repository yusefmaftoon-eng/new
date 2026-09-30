import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from strategy import SPAN, ny_ms  # noqa: E402

DAY = date(2026, 3, 18)          # a Wednesday
PREV = date(2026, 3, 17)         # Tuesday


def flat(t0, t1, px):
    """Quiet 5m bars from t0 (inclusive) to t1 (exclusive) around px."""
    return [{"t": t, "o": px, "h": px + 0.2, "l": px - 0.2, "c": px, "v": 1.0} for t in range(t0, t1, SPAN)]


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": 1.0}


def at(hh, mm=0, d=DAY):
    return ny_ms(d, hh, mm)


def until_signal(bars, fn, cfg):
    """Feed bars one at a time like the bot/backtester; return (index, signal) of the first signal."""
    for i in range(3, len(bars) + 1):
        s = fn(bars[:i], cfg)
        if s:
            return i - 1, s
    return None, None
