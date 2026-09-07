"""Signal detection for the ICT-style Candle Range Theory (CRT) strategy.

CRT: take the most recently completed 1H candle as a "range" [low, high].
During the next 1H period, price sweeps liquidity beyond one side of that
range (manipulation) and a single candle reverses straight back through the
level (distribution) -- open still beyond the level, close back inside. That
reversal candle is the entry signal, mirroring the same "must fully engulf"
discipline used for IFVG's inversion rule, just anchored to one range
boundary instead of a two-sided gap.

Shares its bias/killzone/target machinery with strategies/ifvg_strategy.py
so the two strategies are comparable on equal footing: same HTF bias filter,
same killzones, same resting-liquidity targets, same R:R gate. What differs
is the core signal (a 1H range sweep-and-reclaim, no second instrument, no
SMT divergence) and the stop reference (the reversal candle's own wick,
rather than a separate fractal swing pivot).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def completed_1h_ranges(df_5m: pd.DataFrame, df_1h: pd.DataFrame):
    """For every 5m bar, the high/low of the most recently *completed* 1H
    candle (i.e. the 1H bar whose [start, start+1h) window has fully
    elapsed as of that 5m bar's timestamp). Returns (range_high, range_low)
    numpy arrays aligned to df_5m, with NaN where no 1H candle has closed yet.
    """
    threshold = df_5m.index - pd.Timedelta(hours=1)
    pos = df_1h.index.searchsorted(threshold, side="right") - 1
    valid = pos >= 0
    highs = df_1h["high"].values
    lows = df_1h["low"].values
    range_high = np.where(valid, highs[np.clip(pos, 0, None)], np.nan)
    range_low = np.where(valid, lows[np.clip(pos, 0, None)], np.nan)
    return range_high, range_low


def detect_setups(df_5m: pd.DataFrame, range_high, range_low, bias_by_date: dict) -> list[dict]:
    """A setup fires at bar c when a single candle sweeps and reclaims one
    side of the active range, in the direction the day's bias wants:

      bullish: open[c] < range_low[c]  and  close[c] > range_low[c]
      bearish: open[c] > range_high[c] and  close[c] < range_high[c]

    (open beyond the level implies the candle's low/high already swept it,
    so this single condition captures both manipulation and reclaim.)
    """
    opens, closes = df_5m["open"].values, df_5m["close"].values
    setups = []
    for c in range(len(df_5m)):
        rh, rl = range_high[c], range_low[c]
        if rh != rh or rl != rl:  # NaN -- no completed 1H range yet
            continue
        ts = df_5m.index[c]
        bias = bias_by_date.get(ts.date())
        if bias == "bull" and opens[c] < rl and closes[c] > rl:
            setups.append({"pos": c, "time": ts, "dir": "long", "bias": bias,
                            "sweep_extreme": df_5m["low"].values[c],
                            "zone_bottom": rl, "zone_top": closes[c], "range_high": rh, "range_low": rl})
        elif bias == "bear" and opens[c] > rh and closes[c] < rh:
            setups.append({"pos": c, "time": ts, "dir": "short", "bias": bias,
                            "sweep_extreme": df_5m["high"].values[c],
                            "zone_bottom": closes[c], "zone_top": rh, "range_high": rh, "range_low": rl})
    return setups
