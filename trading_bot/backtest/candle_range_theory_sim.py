"""Candle Range Theory (CRT), per the 3-candle model:

  Candle 1 (range): a completed HTF candle. CRH/CRL = its high/low.
  Candle 2 (sweep):  the NEXT HTF candle. Price must wick beyond CRH or CRL
                      (liquidity sweep) at some point during its formation.
  Candle 3 (target):  price delivers toward the opposite extreme of Candle 1's
                      range.

  Entry styles:
    'candle3_open' -- the basic version. Requires Candle 2 to have wicked
      beyond the range AND its own final close to land back INSIDE the range
      (this is checked without lookahead: candle 2 has already closed by the
      time you'd act on candle 3's open, so this isn't cheating). If Candle 2
      closes OUTSIDE the range instead, the setup is invalid -- no trade.
      Entry: the open of Candle 3, fading the sweep direction.
    'ltf_confirm' -- the 'advanced' version: instead of waiting for the whole
      of Candle 2 to close, enter in real time during Candle 2 as soon as a
      reversal-direction fair value gap forms after the sweep wick (same
      definition already validated in liquidity_sweep_ifvg_sim.py). The
      'must close back inside' rule does NOT apply here -- it's a real-time
      confirmation trigger, not something you'd retroactively cancel a live
      trade over once Candle 2 finishes forming.
    'mss_fvg' -- the 'A+' LTF-confluence version: after the sweep, require a
      Market Structure Shift (a close beyond the most recent post-sweep
      1-bar-fractal swing point in the reversal direction -- same definition
      used as 'cisd' in liquidity_sweep_ifvg_sim.py) BEFORE looking for the
      FVG; only the first FVG forming AFTER that structure break counts.
      Strictly more selective than 'ltf_confirm' (every mss_fvg trade is also
      an ltf_confirm-eligible sweep, but not the reverse).

  sweep_hour_window: (start_hour, end_hour) in ET -- restricts which sweeps
  (Candle 2's wick beyond the range) can start a new pending setup, e.g. to
  London Open or an NY 'kill zone'. None = no restriction. This models the
  'A+ setup' timing requirement -- NOT implemented: the 'A+' requirement that
  Candle 1 sit inside a higher-timeframe Point of Interest (a Daily Order
  Block, weekly liquidity pool, etc.) -- order-block detection is separate,
  more subjective logic this module doesn't have.

  Stop: the actual extreme wick reached during the sweep.
  Target: 'opposite' -- the far boundary of Candle 1's range (CRL for a
    short, CRH for a long); 'halfback' -- the 50% midpoint of Candle 1's
    range (both are explicitly named in the source material).
  Session-bound: forced exit at 16:00 ET if neither stop nor target hits,
  consistent with the no-overnight-hold convention used elsewhere here.
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


def _in_window(hour: float, start: float, end: float) -> bool:
    return start <= hour < end


def run_backtest(bars_5m: list[dict], range_minutes: int = 60,
                  entry_style: str = "ltf_confirm", target_mode: str = "opposite",
                  session_end_hour: float = 16.0,
                  sweep_hour_window: tuple[float, float] | list[tuple[float, float]] | None = None) -> dict:
    span = range_minutes * 60
    htf = {h["t"]: h for h in _aggregate(bars_5m, span // 60)}

    et_5m = []
    for b in sorted(bars_5m, key=lambda b: b["t"]):
        dt = datetime.fromtimestamp(b["t"], tz=timezone.utc).astimezone(ET)
        et_5m.append({**b, "dt": dt, "hour": dt.hour + dt.minute / 60})
    n_total = len(et_5m)
    idx_by_period_start: dict = {}
    for i, b in enumerate(et_5m):
        idx_by_period_start.setdefault((b["t"] // span) * span, []).append(i)

    # global, causal 1-bar-fractal swing points (confirmed 1 bar later, no
    # lookahead) -- used by 'mss_fvg' to detect a market structure shift.
    swing_lows: list[tuple[int, float]] = []
    swing_highs: list[tuple[int, float]] = []
    for i in range(1, n_total - 1):
        prev, cur, nxt = et_5m[i - 1], et_5m[i], et_5m[i + 1]
        if cur["l"] < prev["l"] and cur["l"] < nxt["l"]:
            swing_lows.append((i, cur["l"]))
        if cur["h"] > prev["h"] and cur["h"] > nxt["h"]:
            swing_highs.append((i, cur["h"]))

    def latest_swing(kind: str, after_idx: int):
        pts = swing_lows if kind == "low" else swing_highs
        for idx, price in reversed(pts):
            if idx > after_idx:
                return price
        return None

    sorted_c1_starts = sorted(htf)
    trades = []

    for c1_start in sorted_c1_starts:
        c2_start = c1_start + span
        c1 = htf.get(c1_start)
        c2_idxs = idx_by_period_start.get(c2_start)
        if c1 is None or not c2_idxs:
            continue
        range_high, range_low = c1["h"], c1["l"]
        if range_high <= range_low:
            continue

        pending = None
        entry_price = entry_stop = entry_direction = entry_global_idx = None

        for j in c2_idxs:
            bar = et_5m[j]
            if pending is None:
                if sweep_hour_window is None:
                    in_window = True
                else:
                    windows = sweep_hour_window if isinstance(sweep_hour_window[0], (tuple, list)) \
                        else [sweep_hour_window]
                    in_window = any(_in_window(bar["hour"], *w) for w in windows)
                if not in_window:
                    continue
                if bar["h"] > range_high:
                    pending = {"direction": "short", "extreme": bar["h"], "sweep_idx": j, "mss_broken": False}
                elif bar["l"] < range_low:
                    pending = {"direction": "long", "extreme": bar["l"], "sweep_idx": j, "mss_broken": False}
                else:
                    continue
            else:
                if pending["direction"] == "short":
                    pending["extreme"] = max(pending["extreme"], bar["h"])
                else:
                    pending["extreme"] = min(pending["extreme"], bar["l"])

            if entry_style in ("ltf_confirm", "either") and j >= c2_idxs[0] + 2:
                b0, b2 = et_5m[j - 2], bar
                is_fvg = (pending["direction"] == "short" and b0["l"] > b2["h"]) or \
                         (pending["direction"] == "long" and b0["h"] < b2["l"])
                if is_fvg:
                    entry_price, entry_stop, entry_direction = bar["c"], pending["extreme"], pending["direction"]
                    entry_global_idx = j
                    break

            if entry_style == "mss_fvg":
                if not pending["mss_broken"]:
                    kind = "low" if pending["direction"] == "short" else "high"
                    swing_price = latest_swing(kind, pending["sweep_idx"])
                    if swing_price is not None:
                        broke = (pending["direction"] == "short" and bar["c"] < swing_price) or \
                                (pending["direction"] == "long" and bar["c"] > swing_price)
                        if broke:
                            pending["mss_broken"] = True
                            pending["mss_idx"] = j
                elif j >= pending["mss_idx"] + 2:
                    b0, b2 = et_5m[j - 2], bar
                    is_fvg = (pending["direction"] == "short" and b0["l"] > b2["h"]) or \
                             (pending["direction"] == "long" and b0["h"] < b2["l"])
                    if is_fvg:
                        entry_price, entry_stop, entry_direction = bar["c"], pending["extreme"], pending["direction"]
                        entry_global_idx = j
                        break

        # 'either': ltf_confirm always resolves (or doesn't) before candle 3 even
        # opens, since it's checked bar-by-bar during candle 2 -- so falling back
        # to candle3_open here only fires on setups ltf_confirm missed (no FVG
        # ever formed), not a duplicate of the same setup.
        if entry_style in ("candle3_open", "either") and entry_price is None and pending is not None:
            c2_close = et_5m[c2_idxs[-1]]["c"]
            closed_inside = range_low <= c2_close <= range_high
            c3_idxs = idx_by_period_start.get(c2_start + span)
            if closed_inside and c3_idxs:
                c3_first = c3_idxs[0]
                candidate_price = et_5m[c3_first]["o"]
                # Candle 3 could in principle gap open already beyond the sweep
                # extreme (rare on liquid futures, but real on session-boundary
                # gaps) -- that would make a stop-hit compute a positive pnl.
                # Skip rather than let that distort the numbers.
                stop_ok = (pending["direction"] == "short" and candidate_price < pending["extreme"]) or \
                          (pending["direction"] == "long" and candidate_price > pending["extreme"])
                if stop_ok:
                    entry_price = candidate_price
                    entry_stop = pending["extreme"]
                    entry_direction = pending["direction"]
                    entry_global_idx = c3_first

        if entry_price is None:
            continue

        if target_mode == "halfback":
            midpoint = (range_high + range_low) / 2
            target_price = midpoint
        else:
            target_price = range_low if entry_direction == "short" else range_high

        # skip a target that's already behind the entry (can happen for candle3_open
        # if price has run past the target before candle 3 even opens)
        if entry_direction == "short" and target_price >= entry_price:
            continue
        if entry_direction == "long" and target_price <= entry_price:
            continue

        exit_price = exit_result = None
        mfe_points = mae_points = 0.0
        mfe_bar_offset = mae_bar_offset = None
        for k in range(entry_global_idx + 1, n_total):
            bar = et_5m[k]
            timed_out = bar["hour"] >= session_end_hour

            # track max favorable/adverse excursion (in points, always >= 0) and
            # WHICH one happened first -- needed downstream to know whether an
            # intraday-trailing-drawdown floor had already ratcheted up (via this
            # trade's own floating profit) before its worst dip, or not yet.
            if entry_direction == "short":
                fav = entry_price - bar["l"]
                adv = bar["h"] - entry_price
            else:
                fav = bar["h"] - entry_price
                adv = entry_price - bar["l"]
            if fav > mfe_points:
                mfe_points, mfe_bar_offset = fav, k
            if adv > mae_points:
                mae_points, mae_bar_offset = adv, k

            if entry_direction == "short":
                if bar["h"] >= entry_stop:
                    exit_price, exit_result = entry_stop, "loss"; break
                elif bar["l"] <= target_price:
                    exit_price, exit_result = target_price, "win"; break
                elif timed_out:
                    exit_price, exit_result = bar["c"], "time_exit"; break
            else:
                if bar["l"] <= entry_stop:
                    exit_price, exit_result = entry_stop, "loss"; break
                elif bar["h"] >= target_price:
                    exit_price, exit_result = target_price, "win"; break
                elif timed_out:
                    exit_price, exit_result = bar["c"], "time_exit"; break
        if exit_price is None:
            continue  # ran off the end of data without resolving

        stop_dist = abs(entry_price - entry_stop)
        pnl_points = (entry_price - exit_price) if entry_direction == "short" else (exit_price - entry_price)
        mfe_first = mfe_bar_offset is not None and (mae_bar_offset is None or mfe_bar_offset <= mae_bar_offset)
        trades.append({
            "date": et_5m[entry_global_idx]["dt"].date(), "direction": entry_direction,
            "entry_price": entry_price, "exit_price": exit_price, "result": exit_result,
            "stop_points": stop_dist, "mfe_points": mfe_points, "mae_points": mae_points, "mfe_first": mfe_first,
            "pnl_points": pnl_points, "r_multiple": pnl_points / stop_dist if stop_dist else 0.0,
        })

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
