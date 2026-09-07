"""Trade simulator for crt_pine_strategy.py -- a faithful port of the state
machine in crt_a_plus_mes.pine (flat -> pending -> mss_broken -> entered/
abandoned), not the vectorized/tap-based approach the other two strategies
in this package use. The Pine version's entries and exits are inseparable
from that state machine, so unlike futures_engine.py / crt_engine.py this
doesn't separate "detect setups" from "simulate trades" -- it's one pass.
"""
from __future__ import annotations

import math

import pandas as pd

from ..strategies.crt_pine_strategy import completed_1h_ranges, one_bar_fractal_track, in_pine_killzone

TICK_SIZE = 0.25  # MES and MNQ both trade in 0.25-point ticks


def run_crt_pine_backtest(
    sym: str,
    df_5m: pd.DataFrame,
    df_1h: pd.DataFrame,
    dollars_per_point: float = 5.0,
    risk_per_trade: float = 200.0,
    max_contracts: int = 20,
    commission_per_contract_per_side: float = 0.47,
    slippage_ticks: float = 1.0,
    use_london: bool = True,
    use_silver_bullet: bool = True,
    session_end_hour: int = 16,
    min_rr: float | None = None,
) -> list[dict]:
    """min_rr: if set, skip a setup at the FVG-check bar when reward/risk
    (target distance / stop distance, from that bar's close) falls below
    this -- not in the original Pine source, added to test whether the
    strategy's negative expectancy is fixable by refusing bad-R:R trades
    rather than accepting whatever the state machine hands it."""
    range_high, range_low = completed_1h_ranges(df_5m, df_1h)
    last_swing_high, last_swing_low = one_bar_fractal_track(df_5m)

    n = len(df_5m)
    opens, highs, lows, closes = (df_5m["open"].values, df_5m["high"].values,
                                   df_5m["low"].values, df_5m["close"].values)
    idx = df_5m.index
    slip_pts = slippage_ticks * TICK_SIZE

    state = "flat"          # "flat" | "pending" | "mss_broken"
    direction = None
    sweep_extreme = None
    mss_bar = None
    period_start = None
    open_trade = None
    trades: list[dict] = []

    for i in range(2, n):
        ts = idx[i]
        t = ts.timetz().replace(tzinfo=None)
        this_period = ts.floor("h")

        if open_trade is not None:
            ot = open_trade
            if ot["dir"] == "long":
                hit_stop = lows[i] <= ot["stop"]
                hit_target = highs[i] >= ot["target"]
            else:
                hit_stop = highs[i] >= ot["stop"]
                hit_target = lows[i] <= ot["target"]
            flatten = t.hour >= session_end_hour

            exit_price = reason = None
            if hit_stop:
                # a triggered stop becomes a market order -> gets slipped same as entry
                exit_price = ot["stop"] - slip_pts if ot["dir"] == "long" else ot["stop"] + slip_pts
                reason = "stop"
            elif hit_target:
                exit_price, reason = ot["target"], "target"  # limit order: no adverse slippage
            elif flatten:
                exit_price, reason = closes[i], "session_end"
            if exit_price is not None:
                pts = (exit_price - ot["entry"]) if ot["dir"] == "long" else (ot["entry"] - exit_price)
                gross = pts * dollars_per_point * ot["contracts"]
                commission = commission_per_contract_per_side * 2 * ot["contracts"]
                trades.append({**ot, "exit": exit_price, "exit_time": ts, "reason": reason,
                               "points": pts, "gross_pnl": gross, "commission": commission,
                               "pnl": gross - commission})
                open_trade = None
            continue

        if period_start is None or this_period != period_start:
            period_start = this_period
            if state in ("pending", "mss_broken"):
                state, direction = "flat", None

        rh, rl = range_high[i], range_low[i]
        have_range = rh == rh and rl == rl  # not NaN

        if state == "flat" and have_range and in_pine_killzone(t, use_london, use_silver_bullet):
            if highs[i] > rh:
                state, direction, sweep_extreme = "pending", "short", highs[i]
            elif lows[i] < rl:
                state, direction, sweep_extreme = "pending", "long", lows[i]

        if state in ("pending", "mss_broken"):
            sweep_extreme = max(sweep_extreme, highs[i]) if direction == "short" else min(sweep_extreme, lows[i])

        if state == "pending":
            lsl, lsh = last_swing_low[i], last_swing_high[i]
            if direction == "short" and lsl == lsl and closes[i] < lsl:
                state, mss_bar = "mss_broken", i
            elif direction == "long" and lsh == lsh and closes[i] > lsh:
                state, mss_bar = "mss_broken", i

        elif state == "mss_broken" and i >= mss_bar + 2:
            # One-shot check: the first bar reaching here resets state to "flat" in every
            # branch below, so (matching the Pine source) this only ever fires once per setup.
            bear_fvg = lows[i - 2] > highs[i]
            bull_fvg = highs[i - 2] < lows[i]
            if direction == "short" and bear_fvg and rl < closes[i]:
                stop_pts = sweep_extreme - closes[i]
                reward_pts = closes[i] - rl
                rr_ok = min_rr is None or (stop_pts > 0 and reward_pts / stop_pts >= min_rr)
                contracts = min(max_contracts, math.floor(risk_per_trade / (stop_pts * dollars_per_point))) if stop_pts > 0 and rr_ok else 0
                if contracts >= 1 and i + 1 < n:
                    entry_price = opens[i + 1] + slip_pts  # market fill, next bar open, slipped against us
                    entry_ts = idx[i + 1]
                    open_trade = {"symbol": sym, "dir": "short", "entry": entry_price, "entry_time": entry_ts,
                                   "stop": sweep_extreme, "target": rl, "contracts": contracts,
                                   "range_high": rh, "range_low": rl}
            elif direction == "long" and bull_fvg and rh > closes[i]:
                stop_pts = closes[i] - sweep_extreme
                reward_pts = rh - closes[i]
                rr_ok = min_rr is None or (stop_pts > 0 and reward_pts / stop_pts >= min_rr)
                contracts = min(max_contracts, math.floor(risk_per_trade / (stop_pts * dollars_per_point))) if stop_pts > 0 and rr_ok else 0
                if contracts >= 1 and i + 1 < n:
                    entry_price = opens[i + 1] - slip_pts
                    entry_ts = idx[i + 1]
                    open_trade = {"symbol": sym, "dir": "long", "entry": entry_price, "entry_time": entry_ts,
                                   "stop": sweep_extreme, "target": rh, "contracts": contracts,
                                   "range_high": rh, "range_low": rl}
            state, direction = "flat", None

    return trades


def summarize_trades(trades: list[dict]) -> dict:
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
    return {
        "num_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": 100 * len(wins) / len(trades),
        "avg_contracts": sum(t["contracts"] for t in trades) / len(trades),
        "total_commission": sum(t["commission"] for t in trades),
        "profit_factor": (gross_win / gross_loss) if gross_loss else math.inf,
        "total_pnl": sum(pnls),
        "max_drawdown": mdd,
    }
