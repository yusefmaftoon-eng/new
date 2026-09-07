"""Python port of crt_a_plus_mes.pine (the user's actual TradingView CRT strategy),
NOT the earlier crt_strategy.py guess -- that one used a different, simpler
sweep-and-engulf rule and is kept only as a distinct, separately-run comparison.

Rules, straight from the Pine source:
  1. Range (Candle 1): the last CLOSED 60-minute candle's [low, high].
  2. Kill zones: London Open (02:00-05:00 ET) and/or NY Silver Bullet
     (10:00-11:00 ET) -- not IFVG's AM/PM killzones.
  3. Sweep: while flat and in a kill zone, a bar trading above Candle 1's
     high sets up a short (liquidity grab up); below Candle 1's low sets up
     a long. The swept extreme is tracked (extended) on every bar while the
     setup is unresolved.
  4. MSS (market structure shift): using 1-bar fractals (pivothigh/pivotlow
     with 1 bar each side, confirmed 1 bar later) -- the setup only
     progresses once price closes back through the most recent opposite-side
     swing point (close < last swing low for a short setup, close > last
     swing high for a long).
  5. FVG confirmation: checked at EXACTLY the 2nd bar after the MSS bar (a
     one-shot check, not a search window) -- a 3-candle fair value gap in
     the reversal direction, plus a "room to run" check (close hasn't
     already reached the opposite side of the range). If it doesn't line up
     on that exact bar, the setup is abandoned outright.
  6. Any unresolved setup (pending or past MSS but not yet entered) is
     abandoned the moment the current 60-minute period rolls over -- one
     attempt per hourly range, not an open-ended wait.
  7. Stop: the swept extreme (no buffer -- unlike the other CRT/IFVG
     variants in this package). Target: the opposite side of Candle 1.
  8. Position sizing: floor(risk_per_trade / (stop_points * $/point)),
     capped at max_contracts; skip the trade if that's 0.
  9. Force-flat at a fixed ET hour regardless of stop/target.
"""
from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

# Distinct from ifvg_strategy.KILLZONES -- this strategy trades London Open and
# the NY "Silver Bullet" hour, not the AM/PM NY killzones IFVG and the other
# CRT variant use.
LONDON_OPEN = (time(2, 0), time(5, 0))
SILVER_BULLET = (time(10, 0), time(11, 0))


def in_pine_killzone(t: time, use_london: bool = True, use_silver_bullet: bool = True) -> bool:
    if use_london and LONDON_OPEN[0] <= t < LONDON_OPEN[1]:
        return True
    if use_silver_bullet and SILVER_BULLET[0] <= t < SILVER_BULLET[1]:
        return True
    return False


def completed_1h_ranges(df_5m: pd.DataFrame, df_1h: pd.DataFrame):
    """Same definition as crt_strategy.completed_1h_ranges: for each 5m bar,
    the high/low of the most recently fully-closed 1H candle."""
    threshold = df_5m.index - pd.Timedelta(hours=1)
    pos = df_1h.index.searchsorted(threshold, side="right") - 1
    valid = pos >= 0
    highs, lows = df_1h["high"].values, df_1h["low"].values
    range_high = np.where(valid, highs[np.clip(pos, 0, None)], np.nan)
    range_low = np.where(valid, lows[np.clip(pos, 0, None)], np.nan)
    return range_high, range_low


def one_bar_fractal_track(df: pd.DataFrame):
    """Mirrors Pine's `ta.pivothigh(1,1)` / `ta.pivotlow(1,1)` plus the
    `var float lastSwingHigh/Low` pattern: a pivot at bar p (left=1, right=1)
    confirms at bar p+1, and the tracked "last known" value holds from
    confirmation until the next one. Returns (last_swing_high, last_swing_low)
    arrays aligned to df, NaN before the first pivot confirms."""
    highs, lows = df["high"].values, df["low"].values
    n = len(df)
    last_high = np.full(n, np.nan)
    last_low = np.full(n, np.nan)
    cur_high, cur_low = np.nan, np.nan
    for i in range(n):
        if i >= 2:
            ph = highs[i - 1]
            if highs[i - 2] < ph and highs[i] < ph:
                cur_high = ph
            pl = lows[i - 1]
            if lows[i - 2] > pl and lows[i] > pl:
                cur_low = pl
        last_high[i] = cur_high
        last_low[i] = cur_low
    return last_high, last_low
