"""Long-only signal generators for crypto OHLCV data.

Each function takes a DataFrame with a 'close' column and returns a Series
of target positions in {0, 1} (flat / fully long), indexed the same way.
"""
from __future__ import annotations

import pandas as pd


def sma_crossover_signal(df: pd.DataFrame, fast: int = 20, slow: int = 50) -> pd.Series:
    """Trend-following: long while the fast SMA is above the slow SMA."""
    fast_sma = df["close"].rolling(fast).mean()
    slow_sma = df["close"].rolling(slow).mean()
    signal = (fast_sma > slow_sma).astype(int)
    signal[fast_sma.isna() | slow_sma.isna()] = 0
    return signal


def rsi_mean_reversion_signal(df: pd.DataFrame, period: int = 14,
                               oversold: float = 30, overbought: float = 50) -> pd.Series:
    """Mean-reversion: buy after RSI dips below `oversold`, exit once it recovers past `overbought`."""
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    position = pd.Series(0, index=df.index)
    in_position = False
    for i in range(len(df)):
        r = rsi.iloc[i]
        if pd.isna(r):
            position.iloc[i] = 0
            continue
        if not in_position and r < oversold:
            in_position = True
        elif in_position and r > overbought:
            in_position = False
        position.iloc[i] = 1 if in_position else 0
    return position


STRATEGIES = {
    "sma_crossover": sma_crossover_signal,
    "rsi_mean_reversion": rsi_mean_reversion_signal,
}
