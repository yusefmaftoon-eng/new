"""Turtle Soup: fade a false breakout of the prior day's high/low. The
simplest of the liquidity-sweep-reversal family in this package -- no HTF
bias, no killzone restriction, no MSS/FVG confirmation like CRT and IFVG.
Classic Turtle Soup uses a rolling N-day extreme (originally 20 days, the
inverse of the Turtle system's own breakout entry); this uses N=1 (prior
day's high/low) since only 60 days of 5-minute history is available and a
20-day extreme would rarely be swept at all in that window.

Entry candle must fully engulf the level (open still beyond it, close back
inside) -- same discipline used for IFVG's inversion rule, applied here to a
single prior-day extreme instead of a two-sided gap or hourly range.
"""
from __future__ import annotations

import pandas as pd


def detect_setups(df_5m: pd.DataFrame) -> list[dict]:
    dates = sorted(set(ts.date() for ts in df_5m.index))
    prev_day_hl = {}
    for i, d in enumerate(dates):
        if i == 0:
            continue
        mask = df_5m.index.date == dates[i - 1]
        if mask.any():
            prev_day_hl[d] = (df_5m.loc[mask, "high"].max(), df_5m.loc[mask, "low"].min())

    opens, closes = df_5m["open"].values, df_5m["close"].values
    setups = []
    for i in range(len(df_5m)):
        d = df_5m.index[i].date()
        if d not in prev_day_hl:
            continue
        pdh, pdl = prev_day_hl[d]
        mid = (pdh + pdl) / 2.0
        if opens[i] > pdh and closes[i] < pdh:
            setups.append({"pos": i, "dir": "short", "stop": max(df_5m["high"].values[i], pdh) + 1.0, "target": mid})
        elif opens[i] < pdl and closes[i] > pdl:
            setups.append({"pos": i, "dir": "long", "stop": min(df_5m["low"].values[i], pdl) - 1.0, "target": mid})
    return setups
