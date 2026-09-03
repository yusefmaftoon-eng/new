"""Backtest for: liquidity sweep of a session/prior-day high or low (wick OR
close through) -> the FIRST fair value gap that forms in the reversal
direction after the sweep -> enter at that candle's close (no retrace/
inversion wait -- confirmed against a real trade example: sweep London
High/Asian High, bearish FVG prints, short entered right on that candle's
close) -> fixed-point stop, target = nearest unswept opposing session/
prior-day level, stop moved to breakeven once price reaches 1R in favor.

Definitions used here (state them explicitly since this exact playbook has no
single universal definition -- adjust the constants below to match yours):

  Sessions, in US/Eastern:
    Asia session:   19:00-24:00 ET (previous evening)
    London session: 02:00-05:00 ET
    Previous day:   full prior calendar day, 00:00-23:59 ET
  Each produces a high and a low -> 6 tracked levels: asia_high/low,
  london_high/low, pdh/pdl. A level is live (tradeable) from the moment its
  session closes until that session type next completes and replaces it.

  Liquidity sweep: the first live bar whose high exceeds a HIGH-type level
  (or whose low undercuts a LOW-type level). Classified 'wick' (close back on
  the origin side) or 'close_through' (close beyond it too) -- both count as
  a sweep, per your rules.

  FVG (3-candle imbalance): bullish when bars[i-2].high < bars[i].low
  (gap = that range); bearish when bars[i-2].low > bars[i].high. After a HIGH
  sweep (short bias), the first BEARISH FVG to form -> enter short at that
  candle's close. Mirror image after a LOW sweep (long bias, first BULLISH
  FVG -> enter long at its close).

  Stop: fixed points from entry (your number, per instrument). Moved to
  breakeven (= entry price) the first bar AFTER price reaches 1R in favor
  (the bar that reaches 1R is still evaluated against the original stop --
  the move only protects bars after that, no lookahead).
  Target: nearest currently-live opposing-side level ahead of entry price.
  No target available -> trade is skipped (not given a fabricated target).
  Both stop and target touched within the same bar -> stop assumed first
  (conservative; 5-minute bars can't otherwise resolve intrabar order).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

HIGH_LEVELS = ["asia_high", "london_high", "pdh"]
LOW_LEVELS = ["asia_low", "london_low", "pdl"]
MAX_SETUP_BARS = 48   # abandon a pending sweep->iFVG setup if it doesn't complete within this many bars


@dataclass
class Levels:
    values: dict = field(default_factory=lambda: {k: None for k in HIGH_LEVELS + LOW_LEVELS})
    swept: dict = field(default_factory=lambda: {k: False for k in HIGH_LEVELS + LOW_LEVELS})


def _in_window(hour: float, start: float, end: float) -> bool:
    return start <= hour < end


def run_backtest(bars: list[dict], stop_points: float, breakeven_at_r: float | None = 1.0,
                  confirmation: str = "fvg", sweep_hour_window: tuple[float, float] | None = None) -> dict:
    """bars: [{'t': unix_ts, 'o','h','l','c'}, ...] ascending, one instrument.

    confirmation: which LTF entry trigger to use after the sweep --
      'fvg'     -- first reversal-direction FVG, enter at its close (validated
                   against your real trade example).
      'cisd'    -- 'change in state of delivery': wait for a close beyond the
                   most recent 1-bar-fractal swing point (low for shorts, high
                   for longs) formed after the sweep, enter at that close.
                   1-bar fractal = a bar whose high/low is more extreme than
                   both immediate neighbors, confirmed 1 bar later (no lookahead).
      'bos_fvg' -- the cisd break-of-structure above, THEN the first FVG that
                   forms after that break -- enter at its close (stacks both
                   confirmations from the document's LTF-trigger list).
    These are ONE reasonable, precisely-defined reading of genuinely ambiguous
    terms (the source material lists 5 different 'LTF confirmation' options
    and 7 different 'HTF PDA' options with no selection criteria) -- not the
    only possible one.

    sweep_hour_window: (start_hour, end_hour) in ET -- only sweeps whose bar
    falls in this window can start a new pending setup. None = no restriction.
    """
    et_bars = []
    for b in bars:
        dt = datetime.fromtimestamp(b["t"], tz=timezone.utc).astimezone(ET)
        et_bars.append({**b, "dt": dt, "hour": dt.hour + dt.minute / 60})

    levels = Levels()
    asia_acc = {"h": None, "l": None}
    london_acc = {"h": None, "l": None}
    day_acc = {"h": None, "l": None, "date": None}
    in_asia_prev = in_london_prev = False
    swing_lows: list[tuple[int, float]] = []
    swing_highs: list[tuple[int, float]] = []

    pending = None   # dict: sweep setup in progress
    open_trade = None
    trades = []
    skipped_no_target = 0

    def latest_swing(kind: str, after_idx: int):
        pts = swing_lows if kind == "low" else swing_highs
        for idx, price in reversed(pts):
            if idx > after_idx:
                return idx, price
        return None, None

    def update_sessions(i: int):
        nonlocal in_asia_prev, in_london_prev
        b = et_bars[i]
        h, l = b["h"], b["l"]

        in_asia = _in_window(b["hour"], 19, 24)
        if in_asia:
            asia_acc["h"] = h if asia_acc["h"] is None else max(asia_acc["h"], h)
            asia_acc["l"] = l if asia_acc["l"] is None else min(asia_acc["l"], l)
        elif in_asia_prev and asia_acc["h"] is not None:
            levels.values["asia_high"], levels.values["asia_low"] = asia_acc["h"], asia_acc["l"]
            levels.swept["asia_high"] = levels.swept["asia_low"] = False
            asia_acc["h"] = asia_acc["l"] = None
        in_asia_prev = in_asia

        in_london = _in_window(b["hour"], 2, 5)
        if in_london:
            london_acc["h"] = h if london_acc["h"] is None else max(london_acc["h"], h)
            london_acc["l"] = l if london_acc["l"] is None else min(london_acc["l"], l)
        elif in_london_prev and london_acc["h"] is not None:
            levels.values["london_high"], levels.values["london_low"] = london_acc["h"], london_acc["l"]
            levels.swept["london_high"] = levels.swept["london_low"] = False
            london_acc["h"] = london_acc["l"] = None
        in_london_prev = in_london

        today = b["dt"].date()
        if day_acc["date"] is None:
            day_acc.update(date=today, h=h, l=l)
        elif today != day_acc["date"]:
            levels.values["pdh"], levels.values["pdl"] = day_acc["h"], day_acc["l"]
            levels.swept["pdh"] = levels.swept["pdl"] = False
            day_acc.update(date=today, h=h, l=l)
        else:
            day_acc["h"] = max(day_acc["h"], h)
            day_acc["l"] = min(day_acc["l"], l)

    def nearest_target(direction: str, entry_price: float):
        candidates = HIGH_LEVELS if direction == "long" else LOW_LEVELS
        best = None
        for name in candidates:
            v = levels.values[name]
            if v is None:
                continue
            if direction == "long" and v > entry_price:
                if best is None or v < best:
                    best = v
            elif direction == "short" and v < entry_price:
                if best is None or v > best:
                    best = v
        return best

    for i in range(len(et_bars)):
        update_sessions(i)
        bar = et_bars[i]

        if i >= 2:
            prev, prev2 = et_bars[i - 1], et_bars[i - 2]
            if prev["l"] < prev2["l"] and prev["l"] < bar["l"]:
                swing_lows.append((i - 1, prev["l"]))
            if prev["h"] > prev2["h"] and prev["h"] > bar["h"]:
                swing_highs.append((i - 1, prev["h"]))

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
            continue  # one position at a time; don't also process new setups this bar

        if pending is not None and i - pending["sweep_idx"] > MAX_SETUP_BARS:
            pending = None

        if pending is None:
            in_sweep_window = sweep_hour_window is None or _in_window(bar["hour"], *sweep_hour_window)
            if in_sweep_window:
                for name in HIGH_LEVELS:
                    lvl = levels.values[name]
                    if lvl is not None and not levels.swept[name] and bar["h"] > lvl:
                        levels.swept[name] = True
                        pending = {"direction": "short", "sweep_idx": i, "level": name,
                                   "sweep_type": "wick" if bar["c"] <= lvl else "close_through",
                                   "phase": "await_bos"}
                        break
                if pending is None:
                    for name in LOW_LEVELS:
                        lvl = levels.values[name]
                        if lvl is not None and not levels.swept[name] and bar["l"] < lvl:
                            levels.swept[name] = True
                            pending = {"direction": "long", "sweep_idx": i, "level": name,
                                       "sweep_type": "wick" if bar["c"] >= lvl else "close_through",
                                       "phase": "await_bos"}
                            break
            continue

        if i < 2 or i <= pending["sweep_idx"]:
            continue

        entry_price = None

        if confirmation == "fvg":
            b0, b2 = et_bars[i - 2], bar
            is_fvg = (pending["direction"] == "short" and b0["l"] > b2["h"]) or \
                     (pending["direction"] == "long" and b0["h"] < b2["l"])
            if is_fvg:
                entry_price = bar["c"]

        elif confirmation == "cisd":
            kind = "low" if pending["direction"] == "short" else "high"
            _, swing_price = latest_swing(kind, pending["sweep_idx"])
            if swing_price is not None:
                if pending["direction"] == "short" and bar["c"] < swing_price:
                    entry_price = bar["c"]
                elif pending["direction"] == "long" and bar["c"] > swing_price:
                    entry_price = bar["c"]

        elif confirmation == "bos_fvg":
            if pending["phase"] == "await_bos":
                kind = "low" if pending["direction"] == "short" else "high"
                _, swing_price = latest_swing(kind, pending["sweep_idx"])
                if swing_price is not None:
                    broke = (pending["direction"] == "short" and bar["c"] < swing_price) or \
                            (pending["direction"] == "long" and bar["c"] > swing_price)
                    if broke:
                        pending["phase"] = "await_fvg"
                        pending["bos_idx"] = i
            else:
                if i > pending["bos_idx"]:
                    b0, b2 = et_bars[i - 2], bar
                    is_fvg = (pending["direction"] == "short" and b0["l"] > b2["h"]) or \
                             (pending["direction"] == "long" and b0["h"] < b2["l"])
                    if is_fvg:
                        entry_price = bar["c"]

        if entry_price is None:
            continue

        target = nearest_target(pending["direction"], entry_price)
        if target is None:
            skipped_no_target += 1
        else:
            stop_price = entry_price + stop_points if pending["direction"] == "short" \
                else entry_price - stop_points
            open_trade = {"direction": pending["direction"], "entry_idx": i, "entry_price": entry_price,
                           "stop_price": stop_price, "target_price": target, "moved_to_be": False,
                           "level": pending["level"], "sweep_type": pending["sweep_type"]}
        pending = None

    open_at_end = 1 if open_trade is not None else 0
    for t in trades:
        pnl_points = (t["entry_price"] - t["exit_price"]) if t["direction"] == "short" \
            else (t["exit_price"] - t["entry_price"])
        t["pnl_points"] = pnl_points
        t["r_multiple"] = pnl_points / stop_points

    n = len(trades)
    wins = [t for t in trades if t["result"] == "win"]
    return {
        "n_trades": n,
        "n_skipped_no_target": skipped_no_target,
        "n_open_at_data_end": open_at_end,
        "win_rate_pct": round(100 * len(wins) / n, 2) if n else None,
        "avg_r": round(sum(t["r_multiple"] for t in trades) / n, 3) if n else None,
        "avg_win_r": round(sum(t["r_multiple"] for t in wins) / len(wins), 3) if wins else None,
        "total_r": round(sum(t["r_multiple"] for t in trades), 2) if n else None,
        "by_level": _breakdown_by_level(trades),
        "trades": trades,
    }


def _breakdown_by_level(trades: list[dict]) -> dict:
    out = {}
    for name in HIGH_LEVELS + LOW_LEVELS:
        sub = [t for t in trades if t["level"] == name]
        if not sub:
            continue
        wins = [t for t in sub if t["result"] == "win"]
        out[name] = {
            "n": len(sub),
            "win_rate_pct": round(100 * len(wins) / len(sub), 1),
            "avg_r": round(sum(t["r_multiple"] for t in sub) / len(sub), 3),
        }
    return out
