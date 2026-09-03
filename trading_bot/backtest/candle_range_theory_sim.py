"""Candle Range Theory (CRT): a higher-timeframe (HTF) candle's high/low
defines a range; price in the NEXT HTF candle sweeps one side of that range
(wick beyond it) and closes back inside (rejection) -> enter toward the
opposite side of the range.

Definitions:
  Range: the high/low of the immediately preceding COMPLETED HTF candle
  (e.g. the last fully-closed 1H or 4H bar) -- only usable once that candle
  has actually closed, no lookahead.
  Sweep+reject: within the current (forming) HTF period, the first 5-min bar
  whose high exceeds the range high (or low undercuts the range low) starts
  a pending setup; the first subsequent bar (starting with the sweep bar
  itself) that CLOSES back inside the range confirms it -> enter at that
  close.
  Stop: the actual extreme reached during the sweep (the sweeping bar's high
  for a short, low for a long) -- literally 'beyond the manipulation wick',
  no arbitrary buffer.
  Target: the opposite boundary of the same range.
  Session-bound: forces an exit at 16:00 ET if neither stop nor target has
  hit, consistent with the no-overnight-hold rule used elsewhere in this repo.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _aggregate(bars: list[dict], minutes: int) -> list[dict]:
    bars = sorted(bars, key=lambda b: b["t"])
    out = []
    bucket = None
    bucket_start = None
    span = minutes * 60
    for b in bars:
        bt = (b["t"] // span) * span
        if bucket is None or bt != bucket_start:
            if bucket is not None:
                out.append(bucket)
            bucket_start = bt
            bucket = {"t": bt, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            bucket["h"] = max(bucket["h"], b["h"])
            bucket["l"] = min(bucket["l"], b["l"])
            bucket["c"] = b["c"]
    if bucket is not None:
        out.append(bucket)
    return out


def run_backtest(bars_5m: list[dict], range_minutes: int = 60,
                  session_end_hour: float = 16.0) -> dict:
    htf = _aggregate(bars_5m, range_minutes)
    htf_period_of = {}  # maps 5m bar index -> completed HTF range (high, low) active during it
    et_5m = []
    for b in bars_5m:
        dt = datetime.fromtimestamp(b["t"], tz=timezone.utc).astimezone(ET)
        et_5m.append({**b, "dt": dt, "hour": dt.hour + dt.minute / 60})

    span = range_minutes * 60
    # for each 5m bar, the "active range" is the most recently CLOSED htf candle
    # strictly before the htf period this bar falls in
    htf_by_start = {h["t"]: h for h in htf}
    sorted_starts = sorted(htf_by_start)

    open_trade = None
    trades = []
    pending = None

    for i, bar in enumerate(et_5m):
        period_start = (bar["t"] // span) * span
        # find previous htf period's candle (already closed)
        prev_start = period_start - span
        range_candle = htf_by_start.get(prev_start)

        if open_trade is not None:
            t = open_trade
            timed_out = bar["hour"] >= session_end_hour
            if t["direction"] == "short":
                if bar["h"] >= t["stop_price"]:
                    t.update(exit_price=t["stop_price"], result="loss"); trades.append(t); open_trade = None
                elif bar["l"] <= t["target_price"]:
                    t.update(exit_price=t["target_price"], result="win"); trades.append(t); open_trade = None
                elif timed_out:
                    t.update(exit_price=bar["c"], result="time_exit"); trades.append(t); open_trade = None
            else:
                if bar["l"] <= t["stop_price"]:
                    t.update(exit_price=t["stop_price"], result="loss"); trades.append(t); open_trade = None
                elif bar["h"] >= t["target_price"]:
                    t.update(exit_price=t["target_price"], result="win"); trades.append(t); open_trade = None
                elif timed_out:
                    t.update(exit_price=bar["c"], result="time_exit"); trades.append(t); open_trade = None
            continue

        if range_candle is None:
            continue
        range_high, range_low = range_candle["h"], range_candle["l"]

        if pending is not None and pending["range_start"] != prev_start:
            pending = None  # range rolled over before confirmation -> stale, abandon

        if pending is None:
            if bar["h"] > range_high:
                pending = {"direction": "short", "sweep_extreme": bar["h"], "range_start": prev_start}
            elif bar["l"] < range_low:
                pending = {"direction": "long", "sweep_extreme": bar["l"], "range_start": prev_start}
            else:
                continue
            # same-bar rejection check falls through below

        if pending["direction"] == "short":
            pending["sweep_extreme"] = max(pending["sweep_extreme"], bar["h"])
            if bar["c"] < range_high:
                entry_price = bar["c"]
                open_trade = {"direction": "short", "entry_price": entry_price, "date": bar["dt"].date(),
                               "stop_price": pending["sweep_extreme"], "target_price": range_low}
                pending = None
        else:
            pending["sweep_extreme"] = min(pending["sweep_extreme"], bar["l"])
            if bar["c"] > range_low:
                entry_price = bar["c"]
                open_trade = {"direction": "long", "entry_price": entry_price, "date": bar["dt"].date(),
                               "stop_price": pending["sweep_extreme"], "target_price": range_high}
                pending = None

    for t in trades:
        stop_dist = abs(t["entry_price"] - t["stop_price"])
        pnl_points = (t["entry_price"] - t["exit_price"]) if t["direction"] == "short" \
            else (t["exit_price"] - t["entry_price"])
        t["pnl_points"] = pnl_points
        t["r_multiple"] = pnl_points / stop_dist if stop_dist else 0.0

    n = len(trades)
    wins = [t for t in trades if t["result"] == "win"]
    return {
        "n_trades": n,
        "n_wins": len(wins),
        "n_losses": len([t for t in trades if t["result"] == "loss"]),
        "n_time_exits": len([t for t in trades if t["result"] == "time_exit"]),
        "win_rate_pct": round(100 * len(wins) / n, 2) if n else None,
        "avg_r": round(sum(t["r_multiple"] for t in trades) / n, 3) if n else None,
        "total_r": round(sum(t["r_multiple"] for t in trades), 2) if n else None,
        "trades": trades,
    }
