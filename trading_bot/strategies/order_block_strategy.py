"""Order block retest: find the last down-close candle immediately before a
strong up-move (a bullish order block), then trade the retest of that
candle's own range as support, targeting the high the impulsive move
reached. Mirrored for bearish order blocks (last up-close candle before a
strong down-move, retested as resistance, targeting the impulse leg's low).

"Strong" move = a single candle's body at least `displacement_mult` times
the rolling ATR -- a displacement candle, distinguishing a real impulse from
routine chop. No HTF bias, no killzone, no SMT: independent of the other
strategies in this package, trading whenever the pattern occurs.
"""
from __future__ import annotations

import pandas as pd


def atr(df_5m: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df_5m["high"], df_5m["low"], df_5m["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def detect_setups(df_5m: pd.DataFrame, displacement_mult: float = 1.5, max_lookahead: int = 100) -> list[dict]:
    a = atr(df_5m, 14).values
    opens, highs, lows, closes = (df_5m["open"].values, df_5m["high"].values,
                                   df_5m["low"].values, df_5m["close"].values)
    n = len(df_5m)

    setups = []
    active_bullish = []  # each: {"ob_low", "ob_high", "impulse_high", "expires"}
    active_bearish = []  # each: {"ob_low", "ob_high", "impulse_low", "expires"}

    for i in range(1, n):
        # Check existing (already-formed) OBs against this bar's price BEFORE adding any new
        # one detected at this same bar -- a fresh OB must not be able to "retest" itself using
        # the very displacement candle that created it.
        still_active = []
        for ob in active_bullish:
            if i > ob["expires"]:
                continue
            ob["impulse_high"] = max(ob["impulse_high"], highs[i])
            if lows[i] <= ob["ob_high"]:
                if lows[i] >= ob["ob_low"] and ob["impulse_high"] > ob["ob_high"]:
                    setups.append({"pos": i, "dir": "long", "stop": ob["ob_low"] - 1.0, "target": ob["impulse_high"]})
                continue  # tapped (or broken through) -> resolved either way, drop it
            still_active.append(ob)
        active_bullish = still_active

        still_active = []
        for ob in active_bearish:
            if i > ob["expires"]:
                continue
            ob["impulse_low"] = min(ob["impulse_low"], lows[i])
            if highs[i] >= ob["ob_low"]:
                if highs[i] <= ob["ob_high"] and ob["impulse_low"] < ob["ob_low"]:
                    setups.append({"pos": i, "dir": "short", "stop": ob["ob_high"] + 1.0, "target": ob["impulse_low"]})
                continue
            still_active.append(ob)
        active_bearish = still_active

        if a[i] == a[i] and a[i] > 0:  # ATR ready
            body = closes[i] - opens[i]
            if body > displacement_mult * a[i] and closes[i - 1] < opens[i - 1]:
                active_bullish.append({"ob_low": lows[i - 1], "ob_high": highs[i - 1],
                                        "impulse_high": highs[i], "expires": i + max_lookahead})
            elif -body > displacement_mult * a[i] and closes[i - 1] > opens[i - 1]:
                active_bearish.append({"ob_low": lows[i - 1], "ob_high": highs[i - 1],
                                        "impulse_low": lows[i], "expires": i + max_lookahead})

    return setups
