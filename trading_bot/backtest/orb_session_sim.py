"""Opening Range Breakout (ORB), session-bound: define a range early in the
NY day session, trade the first breakout of it, and force the exit at
session close if neither stop nor target has hit -- no overnight/multi-
session hold, per your ask.

Definitions:
  Session: NY day session, 09:30-16:00 ET (standard US equity/futures RTH
  convention -- adjust if you actually trade the full Globex session).
  Opening range: high/low of bars in [09:30, 10:00) ET.
  Entry: the first close beyond the opening range high (long) or low
  (short), between 10:00 and --entry-cutoff-hour ET (a breakout too close
  to the close doesn't get a fair shot at the target). Only one trade/day.
  Stop: the OPPOSITE side of the opening range (standard ORB placement --
  adaptive to that day's actual range, not an arbitrary fixed number).
  Target: entry +/- (target_r_multiple x opening range size).
  If neither stop nor target hits by 16:00 ET, exit at the last close at/
  before session end -- labeled 'time_exit', not a win or loss.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def run_backtest(bars: list[dict], target_r_multiple: float = 2.0,
                  or_start_hour: float = 9.5, or_end_hour: float = 10.0,
                  entry_cutoff_hour: float = 15.0, session_end_hour: float = 16.0) -> dict:
    et_bars = []
    for b in bars:
        dt = datetime.fromtimestamp(b["t"], tz=timezone.utc).astimezone(ET)
        et_bars.append({**b, "dt": dt, "hour": dt.hour + dt.minute / 60, "date": dt.date()})

    by_day: dict = {}
    for b in et_bars:
        by_day.setdefault(b["date"], []).append(b)

    trades = []
    for date, day_bars in by_day.items():
        or_bars = [b for b in day_bars if or_start_hour <= b["hour"] < or_end_hour]
        if not or_bars:
            continue
        or_high = max(b["h"] for b in or_bars)
        or_low = min(b["l"] for b in or_bars)
        or_range = or_high - or_low
        if or_range <= 0:
            continue

        session_bars = [b for b in day_bars if or_end_hour <= b["hour"] < session_end_hour]
        open_trade = None
        traded_today = False
        for j, bar in enumerate(session_bars):
            if open_trade is None:
                if traded_today or bar["hour"] >= entry_cutoff_hour:
                    continue
                if bar["c"] > or_high:
                    entry = bar["c"]
                    open_trade = {"direction": "long", "entry_price": entry,
                                   "stop_price": or_low, "target_price": entry + target_r_multiple * or_range,
                                   "date": date, "or_range": or_range}
                    traded_today = True
                elif bar["c"] < or_low:
                    entry = bar["c"]
                    open_trade = {"direction": "short", "entry_price": entry,
                                   "stop_price": or_high, "target_price": entry - target_r_multiple * or_range,
                                   "date": date, "or_range": or_range}
                    traded_today = True
                continue

            t = open_trade
            if t["direction"] == "long":
                if bar["l"] <= t["stop_price"]:
                    t.update(exit_price=t["stop_price"], result="loss"); trades.append(t); open_trade = None
                elif bar["h"] >= t["target_price"]:
                    t.update(exit_price=t["target_price"], result="win"); trades.append(t); open_trade = None
            else:
                if bar["h"] >= t["stop_price"]:
                    t.update(exit_price=t["stop_price"], result="loss"); trades.append(t); open_trade = None
                elif bar["l"] <= t["target_price"]:
                    t.update(exit_price=t["target_price"], result="win"); trades.append(t); open_trade = None

        if open_trade is not None:
            last_close = session_bars[-1]["c"] if session_bars else open_trade["entry_price"]
            open_trade.update(exit_price=last_close, result="time_exit")
            trades.append(open_trade)

    for t in trades:
        stop_dist = abs(t["entry_price"] - t["stop_price"])
        pnl_points = (t["exit_price"] - t["entry_price"]) if t["direction"] == "long" \
            else (t["entry_price"] - t["exit_price"])
        t["pnl_points"] = pnl_points
        t["r_multiple"] = pnl_points / stop_dist if stop_dist else 0.0

    n = len(trades)
    wins = [t for t in trades if t["result"] == "win"]
    losses = [t for t in trades if t["result"] == "loss"]
    time_exits = [t for t in trades if t["result"] == "time_exit"]
    return {
        "n_trades": n,
        "n_wins": len(wins), "n_losses": len(losses), "n_time_exits": len(time_exits),
        "win_rate_pct": round(100 * len(wins) / n, 2) if n else None,
        "avg_r": round(sum(t["r_multiple"] for t in trades) / n, 3) if n else None,
        "avg_time_exit_r": round(sum(t["r_multiple"] for t in time_exits) / len(time_exits), 3) if time_exits else None,
        "total_r": round(sum(t["r_multiple"] for t in trades), 2) if n else None,
        "trades": trades,
    }
