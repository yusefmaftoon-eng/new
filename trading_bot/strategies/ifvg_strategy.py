"""Signal detection for the ICT-style inverse fair value gap (IFVG) strategy.

Unlike the crypto strategies in this package (which return a per-bar {0,1}
position weight for the vectorized engine in backtest/engine.py), this
strategy is inherently event-based and needs two correlated instruments at
once (for SMT divergence) plus path-dependent stop/target simulation. So it
pairs with backtest/futures_engine.py instead of the vectorized engine.

Rule set:
  1. Bias (1H, as of 09:30 ET): confirmed 2-bar-fractal swing structure.
     Higher-high + higher-low over the last two confirmed swings -> bullish.
     Lower-high + lower-low -> bearish. Anything mixed -> no bias / no trade.
  2. SMT divergence (5m): at a swing pivot on instrument A, instrument B is
     sampled in a matched time window around both the new pivot and its prior
     comparable pivot. Divergence: A confirms a new extreme, B does not.
  3. Fair value gap (5m): 3-candle imbalance (candle1 vs candle3 gap). It
     "inverts" the first time a later candle closes fully through it, and
     flips polarity (bullish FVG invalidated -> becomes resistance, and
     vice versa).
"""
from __future__ import annotations

from datetime import time
import pandas as pd

# ICT NY AM + NY PM killzones -- entries only fire inside these windows.
KILLZONES = [(time(9, 30), time(11, 0)), (time(13, 30), time(16, 0))]
FLATTEN_TIMES = [time(12, 0), time(16, 15)]
BIAS_CUTOFF = time(9, 30)


def in_killzone(t: time) -> bool:
    return any(start <= t <= end for start, end in KILLZONES)


def flatten_deadline(t: time) -> time:
    for ft in FLATTEN_TIMES:
        if t <= ft:
            return ft
    return FLATTEN_TIMES[-1]


def find_fractal_swings(df: pd.DataFrame, left: int = 2, right: int = 2) -> list[tuple]:
    """Confirmed swing highs/lows as (bar_pos, timestamp, price, 'H'|'L').

    A pivot at i is only confirmed once `right` bars have printed after it,
    so this never looks ahead of what would have been known at the time.
    """
    highs, lows = df["high"].values, df["low"].values
    n = len(df)
    swings = []
    for i in range(left, n - right):
        window_h = highs[i - left:i + right + 1]
        if highs[i] == window_h.max() and (highs[i] > highs[i - left:i]).all() and (highs[i] > highs[i + 1:i + right + 1]).all():
            swings.append((i, df.index[i], highs[i], "H"))
        window_l = lows[i - left:i + right + 1]
        if lows[i] == window_l.min() and (lows[i] < lows[i - left:i]).all() and (lows[i] < lows[i + 1:i + right + 1]).all():
            swings.append((i, df.index[i], lows[i], "L"))
    swings.sort(key=lambda s: s[0])
    return swings


def compute_daily_bias(df_1h: pd.DataFrame, swings_1h: list[tuple]) -> dict:
    """HTF bias per session date, using only swings confirmed before 09:30 ET that day."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")

    bias_by_date = {}
    for d in sorted(set(ts.date() for ts in df_1h.index)):
        cutoff = datetime.combine(d, BIAS_CUTOFF, tzinfo=et)
        pos = df_1h.index.searchsorted(pd.Timestamp(cutoff))
        avail = [s for s in swings_1h if s[0] < pos]
        h = [s for s in avail if s[3] == "H"][-2:]
        l = [s for s in avail if s[3] == "L"][-2:]
        if len(h) < 2 or len(l) < 2:
            bias_by_date[d] = None
            continue
        hh, hl = h[-1][2] > h[-2][2], l[-1][2] > l[-2][2]
        lh, ll = h[-1][2] < h[-2][2], l[-1][2] < l[-2][2]
        bias_by_date[d] = "bull" if (hh and hl) else "bear" if (lh and ll) else None
    return bias_by_date


def detect_fvgs(df: pd.DataFrame) -> list[dict]:
    """3-candle fair value gaps over the whole series."""
    fvgs = []
    highs, lows = df["high"].values, df["low"].values
    for i in range(2, len(df)):
        c1h, c1l, c3h, c3l = highs[i - 2], lows[i - 2], highs[i], lows[i]
        if c1h < c3l:
            fvgs.append({"kind": "bull", "top": c3l, "bottom": c1h, "formed_pos": i,
                         "formed_time": df.index[i], "inverted_pos": None, "tapped_pos": None})
        elif c1l > c3h:
            fvgs.append({"kind": "bear", "top": c1l, "bottom": c3h, "formed_pos": i,
                         "formed_time": df.index[i], "inverted_pos": None, "tapped_pos": None})
    return fvgs


def mark_inversions(df: pd.DataFrame, fvgs: list[dict]) -> list[dict]:
    """Flag the first bar (if any) where each FVG is inverted by a single candle that
    completely engulfs it -- opens beyond the far edge and closes beyond the near edge,
    spanning the whole gap in one move. A candle that only grinds its close past one
    edge (without its open already clearing the other side) doesn't count: that's a
    slow erosion of the gap, not the decisive reversal candle IFVG entries are built on.
    """
    opens, closes = df["open"].values, df["close"].values
    for g in fvgs:
        for j in range(g["formed_pos"] + 1, len(df)):
            if g["kind"] == "bull" and opens[j] >= g["top"] and closes[j] < g["bottom"]:
                g["inverted_pos"], g["inverted_time"] = j, df.index[j]
                break
            if g["kind"] == "bear" and opens[j] <= g["bottom"] and closes[j] > g["top"]:
                g["inverted_pos"], g["inverted_time"] = j, df.index[j]
                break
    return fvgs


def nearest_swing_before(swings: list[tuple], pos: int, kind: str):
    cands = [s for s in swings if s[0] < pos and s[3] == kind]
    return cands[-1] if cands else None


def _local_extreme(df_other: pd.DataFrame, ts: pd.Timestamp, kind: str, window_bars: int = 6):
    pos = df_other.index.searchsorted(ts)
    lo, hi = max(0, pos - window_bars), min(len(df_other), pos + window_bars + 1)
    if lo >= hi:
        return None
    sl = df_other.iloc[lo:hi]
    return sl["low"].min() if kind == "L" else sl["high"].max()


def smt_divergence_at(swing_pos: int, kind: str, swings_a: list[tuple], df_b: pd.DataFrame,
                       window_bars: int = 6) -> bool:
    """kind='L': A prints an equal/lower low while B's matched-window low is higher (bullish
    divergence). kind='H': mirrored for highs (bearish divergence)."""
    a_list = [s for s in swings_a if s[3] == kind]
    idx = next((k for k, s in enumerate(a_list) if s[0] == swing_pos), None)
    if idx is None or idx == 0:
        return False
    a_new, a_prior = a_list[idx], a_list[idx - 1]
    b_new = _local_extreme(df_b, a_new[1], kind, window_bars)
    b_prior = _local_extreme(df_b, a_prior[1], kind, window_bars)
    if b_new is None or b_prior is None:
        return False
    if kind == "L":
        return a_new[2] <= a_prior[2] and b_new > b_prior
    return a_new[2] >= a_prior[2] and b_new < b_prior
