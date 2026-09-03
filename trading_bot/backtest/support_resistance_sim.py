"""Backtest for classic support/resistance trading, since you weren't sure of
exact rules -- these are MY concrete choices for two standard, well-known
approaches, not a strategy you described. Read the definitions before
trusting any result.

Levels: swing highs/lows via an N-bar fractal (a bar whose high/low is the
most extreme within N bars on both sides), confirmed N bars later (no
lookahead). The most recent K confirmed swing highs are 'resistance'
candidates, the most recent K swing lows are 'support' candidates -- a
level drops out of consideration once a trade is taken off it (avoids
immediately re-triggering the same touch).

Two modes:
  'bounce'   -- price wicks into a level and closes back away from it
                (rejection) -> enter fading the level, at that candle's close.
  'breakout' -- price CLOSES through a level (having been on the other side)
                -> enter in the breakout direction, at that close.

Stop: fixed points from entry (same convention as the other backtests here).
Target: nearest active opposing-side level ahead of entry price; no target
available -> trade skipped. Breakeven-at-1R management, same as elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def run_backtest(bars: list[dict], stop_points: float, mode: str = "bounce",
                  fractal_n: int = 6, max_active_levels: int = 3,
                  breakeven_at_r: float | None = 1.0) -> dict:
    et_bars = []
    for b in bars:
        dt = datetime.fromtimestamp(b["t"], tz=timezone.utc).astimezone(ET)
        et_bars.append({**b, "dt": dt})

    resistances: list[dict] = []   # [{'idx','price','active'}]
    supports: list[dict] = []

    open_trade = None
    trades = []
    skipped_no_target = 0
    n = len(et_bars)

    def nearest_target(direction: str, entry_price: float):
        pool = resistances if direction == "long" else supports
        best = None
        for lvl in pool:
            if not lvl["active"]:
                continue
            v = lvl["price"]
            if direction == "long" and v > entry_price:
                if best is None or v < best:
                    best = v
            elif direction == "short" and v < entry_price:
                if best is None or v > best:
                    best = v
        return best

    for i in range(n):
        bar = et_bars[i]

        if i >= 2 * fractal_n:
            center = i - fractal_n
            window = et_bars[i - 2 * fractal_n: i + 1]
            cb = et_bars[center]
            if cb["h"] == max(b["h"] for b in window):
                resistances.append({"idx": center, "price": cb["h"], "active": True})
                resistances = resistances[-max_active_levels:]
            if cb["l"] == min(b["l"] for b in window):
                supports.append({"idx": center, "price": cb["l"], "active": True})
                supports = supports[-max_active_levels:]

        if open_trade is not None:
            t = open_trade
            if t["direction"] == "short":
                if bar["h"] >= t["stop_price"]:
                    result = "breakeven" if t["moved_to_be"] else "loss"
                    t.update(exit_idx=i, exit_price=t["stop_price"], result=result)
                    trades.append(t); open_trade = None
                elif bar["l"] <= t["target_price"]:
                    t.update(exit_idx=i, exit_price=t["target_price"], result="win")
                    trades.append(t); open_trade = None
                elif not t["moved_to_be"] and breakeven_at_r is not None \
                        and bar["l"] <= t["entry_price"] - breakeven_at_r * stop_points:
                    t["moved_to_be"] = True
                    t["stop_price"] = t["entry_price"]
            else:
                if bar["l"] <= t["stop_price"]:
                    result = "breakeven" if t["moved_to_be"] else "loss"
                    t.update(exit_idx=i, exit_price=t["stop_price"], result=result)
                    trades.append(t); open_trade = None
                elif bar["h"] >= t["target_price"]:
                    t.update(exit_idx=i, exit_price=t["target_price"], result="win")
                    trades.append(t); open_trade = None
                elif not t["moved_to_be"] and breakeven_at_r is not None \
                        and bar["h"] >= t["entry_price"] + breakeven_at_r * stop_points:
                    t["moved_to_be"] = True
                    t["stop_price"] = t["entry_price"]
            continue

        entry_price = direction = level_price = None

        if mode == "bounce":
            for lvl in resistances:
                if lvl["active"] and lvl["idx"] < i and bar["h"] >= lvl["price"] and bar["c"] < lvl["price"]:
                    direction, entry_price, level_price = "short", bar["c"], lvl["price"]
                    lvl["active"] = False
                    break
            if direction is None:
                for lvl in supports:
                    if lvl["active"] and lvl["idx"] < i and bar["l"] <= lvl["price"] and bar["c"] > lvl["price"]:
                        direction, entry_price, level_price = "long", bar["c"], lvl["price"]
                        lvl["active"] = False
                        break
        else:  # breakout
            for lvl in resistances:
                if lvl["active"] and lvl["idx"] < i and bar["c"] > lvl["price"] and et_bars[i - 1]["c"] <= lvl["price"]:
                    direction, entry_price, level_price = "long", bar["c"], lvl["price"]
                    lvl["active"] = False
                    break
            if direction is None:
                for lvl in supports:
                    if lvl["active"] and lvl["idx"] < i and bar["c"] < lvl["price"] and et_bars[i - 1]["c"] >= lvl["price"]:
                        direction, entry_price, level_price = "short", bar["c"], lvl["price"]
                        lvl["active"] = False
                        break

        if direction is None:
            continue

        target = nearest_target(direction, entry_price)
        if target is None:
            skipped_no_target += 1
            continue

        stop_price = entry_price + stop_points if direction == "short" else entry_price - stop_points
        open_trade = {"direction": direction, "entry_idx": i, "entry_price": entry_price,
                       "stop_price": stop_price, "target_price": target, "moved_to_be": False,
                       "level_price": level_price}

    open_at_end = 1 if open_trade is not None else 0
    for t in trades:
        pnl_points = (t["entry_price"] - t["exit_price"]) if t["direction"] == "short" \
            else (t["exit_price"] - t["entry_price"])
        t["pnl_points"] = pnl_points
        t["r_multiple"] = pnl_points / stop_points

    m = len(trades)
    wins = [t for t in trades if t["result"] == "win"]
    return {
        "n_trades": m,
        "n_skipped_no_target": skipped_no_target,
        "n_open_at_data_end": open_at_end,
        "win_rate_pct": round(100 * len(wins) / m, 2) if m else None,
        "avg_r": round(sum(t["r_multiple"] for t in trades) / m, 3) if m else None,
        "total_r": round(sum(t["r_multiple"] for t in trades), 2) if m else None,
        "trades": trades,
    }
