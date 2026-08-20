"""Entry-signal generators for Polymarket binary markets.

Each function takes a price history (list of {'t': unix_ts, 'p': price} for
the YES token, oldest first) and returns either None (no trade) or a dict
{'t': entry_ts, 'price': entry_price, 'side': 'YES' | 'NO'}.

Only one entry per market is modeled: buy-and-hold-to-resolution, which is
how most Polymarket positions are actually taken.
"""
from __future__ import annotations


def favorite_longshot_signal(history: list[dict], entry_threshold: float = 0.90) -> dict | None:
    """
    Exploits the well-documented 'favorite-longshot bias' in prediction/betting
    markets: heavy favorites are historically slightly UNDERpriced (they resolve
    YES more often than their price implies) while longshots are OVERpriced.
    Buys YES the first time the price crosses `entry_threshold`.
    """
    for point in history:
        if point["p"] >= entry_threshold:
            return {"t": point["t"], "price": point["p"], "side": "YES"}
    return None


def momentum_signal(history: list[dict], lookback: int = 5, min_move: float = 0.05,
                     min_price: float = 0.15, max_price: float = 0.85) -> dict | None:
    """
    Buys YES the first time price has risen by at least `min_move` over the
    trailing `lookback` snapshots, while still in a non-extreme price band
    (extremes are left to the favorite-longshot strategy above).
    """
    for i in range(lookback, len(history)):
        now = history[i]["p"]
        past = history[i - lookback]["p"]
        if min_price <= now <= max_price and (now - past) >= min_move:
            return {"t": history[i]["t"], "price": now, "side": "YES"}
    return None


STRATEGIES = {
    "favorite_longshot": favorite_longshot_signal,
    "momentum": momentum_signal,
}
