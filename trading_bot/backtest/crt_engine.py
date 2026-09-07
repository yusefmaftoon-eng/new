"""Trade-based backtest engine for the CRT strategy (strategies/crt_strategy.py).

Mirrors backtest/futures_engine.py's shape (discrete entries, path-dependent
stop/target, not a per-bar position weight) but with CRT's simpler setup
detection: no second instrument, no SMT divergence -- the reversal candle
itself is both signal and stop reference.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from ..data.futures_fetcher import CONTRACT_MULTIPLIER
from ..strategies.ifvg_strategy import find_fractal_swings, nearest_swing_before, in_killzone, flatten_deadline
from ..strategies.crt_strategy import completed_1h_ranges, detect_setups
from .futures_engine import summarize_trades  # noqa: F401 (re-exported for parity with run_ifvg_backtest.py)


def run_crt_backtest(sym: str, df_5m: pd.DataFrame, df_1h: pd.DataFrame, bias_by_date: dict,
                      entry_mode: str = "retrace", retrace_lookahead: int = 24) -> tuple[list[dict], list[dict]]:
    """entry_mode: 'retrace' (wait for a tap back to the range level), 'immediate'
    (next bar's open), or 'on_close' (the reversal candle's own close). See
    backtest/futures_engine.py's run_ifvg_backtest for the same three modes'
    tradeoffs -- identical reasoning applies here."""
    if entry_mode not in ("retrace", "immediate", "on_close"):
        raise ValueError(f"unsupported entry_mode {entry_mode!r}")
    multiplier = CONTRACT_MULTIPLIER[sym]

    range_high, range_low = completed_1h_ranges(df_5m, df_1h)
    setups = detect_setups(df_5m, range_high, range_low, bias_by_date)
    setups_by_pos = {s["pos"]: s for s in setups}
    swings_5m = find_fractal_swings(df_5m, 2, 2)

    dates = sorted(set(ts.date() for ts in df_5m.index))
    prev_day_hl = {}
    for i, d in enumerate(dates):
        if i == 0:
            continue
        mask = df_5m.index.date == dates[i - 1]
        if mask.any():
            prev_day_hl[d] = (df_5m.loc[mask, "high"].max(), df_5m.loc[mask, "low"].min())

    trades: list[dict] = []
    open_trade = None
    n = len(df_5m)
    highs, lows, opens = df_5m["high"].values, df_5m["low"].values, df_5m["open"].values
    closes = df_5m["close"].values

    for pos in range(5, n):
        ts = df_5m.index[pos]
        d = ts.date()

        if open_trade is not None:
            ot = open_trade
            hit_stop = (lows[pos] <= ot["stop"]) if ot["dir"] == "long" else (highs[pos] >= ot["stop"])
            hit_target = (highs[pos] >= ot["target"]) if ot["dir"] == "long" else (lows[pos] <= ot["target"])
            flatten = (ts.date() != ot["entry_time"].date()) or (ts.timetz().replace(tzinfo=None) >= ot["flatten_by"])
            exit_price = reason = None
            if hit_stop:
                exit_price, reason = ot["stop"], "stop"
            elif hit_target:
                exit_price, reason = ot["target"], "target"
            elif flatten:
                exit_price, reason = closes[pos], "flatten"
            if exit_price is not None:
                pts = (exit_price - ot["entry"]) if ot["dir"] == "long" else (ot["entry"] - exit_price)
                trades.append({**ot, "exit": exit_price, "exit_time": ts, "reason": reason,
                               "points": pts, "pnl": pts * multiplier})
                open_trade = None
            continue

        setup = setups_by_pos.get(pos)
        if setup is None or not in_killzone(ts.timetz().replace(tzinfo=None)):
            continue

        entry_dir = setup["dir"]
        stop = (setup["sweep_extreme"] - 1.0) if entry_dir == "long" else (setup["sweep_extreme"] + 1.0)
        zone_top, zone_bottom = setup["zone_top"], setup["zone_bottom"]

        candidates = []
        if d in prev_day_hl:
            candidates.append(prev_day_hl[d][0] if entry_dir == "long" else prev_day_hl[d][1])
        opp_swing = nearest_swing_before(swings_5m, pos, "H" if entry_dir == "long" else "L")
        if opp_swing:
            candidates.append(opp_swing[2])
        ref_price = zone_top if entry_dir == "long" else zone_bottom
        candidates = [c for c in candidates if (c > ref_price if entry_dir == "long" else c < ref_price)]
        if not candidates:
            continue
        target = min(candidates) if entry_dir == "long" else max(candidates)

        if entry_mode in ("immediate", "on_close"):
            look = pos if entry_mode == "on_close" else pos + 1
            if look < n:
                entry_price = closes[look] if entry_mode == "on_close" else opens[look]
                denom = abs(entry_price - stop)
                rr = abs(target - entry_price) / denom if denom > 1e-9 else 0
                if rr >= 1.0:
                    entry_ts = df_5m.index[look]
                    open_trade = {
                        "symbol": sym, "dir": entry_dir, "entry": entry_price, "entry_time": entry_ts,
                        "stop": stop, "target": target, "bias": setup["bias"], "date": d,
                        "flatten_by": flatten_deadline(entry_ts.timetz().replace(tzinfo=None)),
                    }
        else:
            for look in range(pos + 1, min(pos + retrace_lookahead, n)):
                touched = (zone_bottom <= lows[look] <= zone_top) if entry_dir == "long" else (zone_bottom <= highs[look] <= zone_top)
                crossed = (lows[look] <= zone_bottom) if entry_dir == "long" else (highs[look] >= zone_top)
                if touched or crossed:
                    entry_price = opens[look] if (crossed and not touched) else (zone_top if entry_dir == "long" else zone_bottom)
                    denom = abs(entry_price - stop)
                    rr = abs(target - entry_price) / denom if denom > 1e-9 else 0
                    if rr < 1.0:
                        break
                    entry_ts = df_5m.index[look]
                    open_trade = {
                        "symbol": sym, "dir": entry_dir, "entry": entry_price, "entry_time": entry_ts,
                        "stop": stop, "target": target, "bias": setup["bias"], "date": d,
                        "flatten_by": flatten_deadline(entry_ts.timetz().replace(tzinfo=None)),
                    }
                    break

    return trades, setups


def save_to_sqlite(db_path: str, sym: str, df: pd.DataFrame, setups: list[dict], trades: list[dict]) -> None:
    conn = sqlite3.connect(db_path)
    df.reset_index(names="ts").assign(symbol=sym).to_sql(f"crt_bars_{sym}", conn, if_exists="replace", index=False)
    if setups:
        pd.DataFrame([{"symbol": sym, "dir": s["dir"], "time": str(s["time"]), "bias": s["bias"],
                        "sweep_extreme": s["sweep_extreme"], "range_high": s["range_high"], "range_low": s["range_low"]}
                      for s in setups]).to_sql(f"crt_setups_{sym}", conn, if_exists="replace", index=False)
    if trades:
        pd.DataFrame(trades).to_sql(f"crt_trades_{sym}", conn, if_exists="replace", index=False)
    conn.close()
