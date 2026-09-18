"""Shared trade simulator for the three non-ORB strategies (vwap_reversion,
turtle_soup, order_block). Each strategy module only detects setups --
{pos, dir, stop, target} at a signal bar -- this module turns them into
trades: fill at the next bar's open, exit on whichever of stop/target is
hit first (checked bar by bar, no lookahead), or flatten at a fixed ET hour.

Pulled out as one function instead of three copies (as the IFVG/CRT/CRT-pine
engines each did their own) because these three strategies share the exact
same fill/exit/flatten mechanics and differ only in setup detection.
"""
from __future__ import annotations

from datetime import time

import pandas as pd


def simulate_trades(sym: str, df_5m: pd.DataFrame, setups: list[dict], dollars_per_point: float,
                     flatten_hour: int = 16, one_at_a_time: bool = True,
                     min_stop_distance: float = 0.0) -> list[dict]:
    """setups: list of {"pos": int, "dir": "long"|"short", "stop": float, "target": float, ...}
    sorted or unsorted (sorted internally by pos). Extra keys are carried through to the trade
    dict. Fills at df_5m's bar `pos+1` open; exits at first stop/target touch or session-end close.

    min_stop_distance: reject a setup if |entry - stop| at actual fill time is below this (in
    price points). A stop computed from a rolling reference (e.g. VWAP's rolling std) that was
    degenerately small at the signal bar can survive the wrong-side check below yet still be
    only a fraction of a tick from entry -- not a real stop, and it blows up risk-based position
    sizing (dividing by a near-zero stop distance) to the maximum contract cap. Pass something
    like a few ticks for the instrument being traded.
    """
    setups = sorted(setups, key=lambda s: s["pos"])
    n = len(df_5m)
    opens, highs, lows, closes = (df_5m["open"].values, df_5m["high"].values,
                                   df_5m["low"].values, df_5m["close"].values)
    idx = df_5m.index

    trades: list[dict] = []
    open_trade = None
    setup_i = 0

    for i in range(1, n):
        ts = idx[i]

        if open_trade is not None:
            ot = open_trade
            if ot["dir"] == "long":
                hit_stop = lows[i] <= ot["stop"]
                hit_target = highs[i] >= ot["target"]
                adverse_this_bar = lows[i] - ot["entry"]
            else:
                hit_stop = highs[i] >= ot["stop"]
                hit_target = lows[i] <= ot["target"]
                adverse_this_bar = ot["entry"] - highs[i]
            ot["mae_pts"] = min(ot["mae_pts"], adverse_this_bar)
            flatten = (ts.date() != ot["entry_time"].date()) or (ts.timetz().replace(tzinfo=None) >= time(flatten_hour, 0))

            exit_price = reason = None
            if hit_stop:
                exit_price, reason = ot["stop"], "stop"
            elif hit_target:
                exit_price, reason = ot["target"], "target"
            elif flatten:
                exit_price, reason = closes[i], "flatten"
            if exit_price is not None:
                pts = (exit_price - ot["entry"]) if ot["dir"] == "long" else (ot["entry"] - exit_price)
                # mae_pts is the worst (most negative) unrealized excursion in points reached
                # at any point during the trade's life -- for real-time drawdown enforcement,
                # not just the final realized P&L.
                trades.append({**ot, "exit": exit_price, "exit_time": ts, "reason": reason,
                               "points": pts, "pnl": pts * dollars_per_point,
                               "mae_pts": min(ot["mae_pts"], pts)})
                open_trade = None
            if one_at_a_time:
                continue

        while setup_i < len(setups) and setups[setup_i]["pos"] < i - 1:
            setup_i += 1
        if setup_i < len(setups) and setups[setup_i]["pos"] == i - 1 and open_trade is None:
            s = setups[setup_i]
            setup_i += 1
            entry_price = opens[i]
            # The stop was computed at the signal bar; entry fills at the *next* bar's open, which
            # can gap past it (most likely when the reference the strategy sized the stop off of --
            # e.g. VWAP's rolling std -- was degenerately small at that exact bar). A stop that ends
            # up on the wrong side of the actual fill isn't a stop at all -- reject the setup rather
            # than "protect" the position with a level that's already been breached at entry.
            invalid_stop = (s["dir"] == "long" and s["stop"] >= entry_price) or \
                            (s["dir"] == "short" and s["stop"] <= entry_price)
            if invalid_stop or abs(entry_price - s["stop"]) < min_stop_distance:
                continue
            rr = abs(s["target"] - entry_price) / abs(entry_price - s["stop"])
            entry_bar_adverse = (lows[i] - entry_price) if s["dir"] == "long" else (entry_price - highs[i])
            open_trade = {k: v for k, v in s.items() if k not in ("pos",)}
            open_trade.update({"symbol": sym, "entry": entry_price, "entry_time": ts, "rr_at_entry": rr,
                                "mae_pts": min(0.0, entry_bar_adverse)})

    return trades


def summarize_trades(trades: list[dict]) -> dict:
    import math
    if not trades:
        return {"num_trades": 0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    equity = peak = mdd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    rrs = [t["rr_at_entry"] for t in trades if t.get("rr_at_entry") is not None]
    return {
        "num_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": 100 * len(wins) / len(trades),
        "profit_factor": (gross_win / gross_loss) if gross_loss else math.inf,
        "total_pnl": sum(pnls),
        "max_drawdown": mdd,
        "median_rr": sorted(rrs)[len(rrs) // 2] if rrs else None,
    }
