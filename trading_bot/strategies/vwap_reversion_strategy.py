"""VWAP mean-reversion: a standard, non-ICT intraday strategy included as a
baseline contrast to the ICT-style strategies elsewhere in this package.

Session VWAP resets each calendar day (ET). A rolling standard deviation of
the close-to-VWAP distance (causal: pandas .rolling() only looks backward)
defines a band; when price closes outside `band_mult` standard deviations,
that's a fade signal back toward VWAP. Restricted to RTH (09:30-16:00 ET),
the conventional window VWAP mean-reversion is traded in -- unlike the other
strategies here, this one has no killzone/bias/SMT machinery, deliberately,
since that's not part of how this strategy is normally defined.
"""
from __future__ import annotations

from datetime import time

import pandas as pd

RTH_START, RTH_END = time(9, 30), time(16, 0)


def compute_vwap_and_band(df_5m: pd.DataFrame, std_window: int = 60):
    typical = (df_5m["high"] + df_5m["low"] + df_5m["close"]) / 3.0
    dates = pd.Series(df_5m.index.date, index=df_5m.index)
    pv = typical * df_5m["volume"]
    cum_pv = pv.groupby(dates).cumsum()
    cum_vol = df_5m["volume"].groupby(dates).cumsum().replace(0, float("nan"))
    vwap = cum_pv / cum_vol
    vwap = vwap.ffill()  # zero-volume bars (e.g. stale last bar): hold the last valid VWAP
    dev = df_5m["close"] - vwap
    std = dev.rolling(std_window, min_periods=20).std()
    return vwap, dev, std


def detect_setups(df_5m: pd.DataFrame, band_mult: float = 2.0, stop_mult: float = 3.0) -> list[dict]:
    vwap, dev, std = compute_vwap_and_band(df_5m, std_window=60)
    v, d, s = vwap.values, dev.values, std.values
    n = len(df_5m)
    setups = []
    for i in range(1, n):
        t = df_5m.index[i].timetz().replace(tzinfo=None)
        if not (RTH_START <= t <= RTH_END) or s[i] != s[i]:  # NaN std -> not enough of today's session yet
            continue
        band = band_mult * s[i]
        stop_band = stop_mult * s[i]
        prev_d = d[i - 1]
        if prev_d <= band < d[i]:
            # closed above the upper band having been inside it last bar -> fade short back to VWAP
            setups.append({"pos": i, "dir": "short", "stop": v[i] + stop_band, "target": v[i]})
        elif prev_d >= -band > d[i]:
            setups.append({"pos": i, "dir": "long", "stop": v[i] - stop_band, "target": v[i]})
    return setups
