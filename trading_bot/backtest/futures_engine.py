"""Trade-based backtest engine for the IFVG strategy (strategies/ifvg_strategy.py).

This does not reuse backtest/engine.py because that engine is vectorized over
a per-bar {0,1} position weight on a single series. IFVG entries are discrete
(a specific 5m tap into a specific inverted gap), need a second correlated
instrument for SMT confirmation, and exit on whichever of a fixed stop/target
is hit first (path-dependent, not a weight applied to the next bar's return).
"""
from __future__ import annotations

import math
import sqlite3

import pandas as pd

from ..data.futures_fetcher import CONTRACT_MULTIPLIER
from ..strategies.ifvg_strategy import (
    find_fractal_swings, detect_fvgs, mark_inversions, nearest_swing_before,
    smt_divergence_at, in_killzone, flatten_deadline,
)


def run_ifvg_backtest(sym: str, df_5m: pd.DataFrame, df_5m_other: pd.DataFrame,
                       bias_by_date: dict) -> tuple[list[dict], list[dict]]:
    """Simulate the IFVG strategy for one instrument. `df_5m_other` is the
    correlated instrument used for SMT divergence (e.g. MNQ when sym='MES')."""
    multiplier = CONTRACT_MULTIPLIER[sym]
    swings_5m = find_fractal_swings(df_5m, 2, 2)
    fvgs = mark_inversions(df_5m, detect_fvgs(df_5m))

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
    used_fvg_ids = set()
    n = len(df_5m)
    highs, lows, opens = df_5m["high"].values, df_5m["low"].values, df_5m["open"].values
    closes = df_5m["close"].values

    for pos in range(5, n):
        ts = df_5m.index[pos]
        d = ts.date()
        bias = bias_by_date.get(d)

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

        if bias is None or not in_killzone(ts.timetz().replace(tzinfo=None)):
            continue

        want_kind = bias  # 'bull' or 'bear'
        want_swing_kind = "L" if bias == "bull" else "H"

        for g in fvgs:
            gid = id(g)
            if gid in used_fvg_ids or g["inverted_pos"] != pos:
                continue
            # only an FVG against bias can invert INTO the direction we want to trade
            if not ((g["kind"] == "bear" and want_kind == "bull") or (g["kind"] == "bull" and want_kind == "bear")):
                continue

            pivot = nearest_swing_before(swings_5m, g["formed_pos"], want_swing_kind)
            if pivot is None or not smt_divergence_at(pivot[0], want_swing_kind, swings_5m, df_5m_other):
                continue

            zone_top, zone_bottom = g["top"], g["bottom"]
            entry_dir = "long" if bias == "bull" else "short"
            stop = (pivot[2] - 1.0) if entry_dir == "long" else (pivot[2] + 1.0)

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

            used_fvg_ids.add(gid)
            g["tapped_pos"] = pos

            for look in range(pos + 1, min(pos + 24, n)):
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
                        "stop": stop, "target": target, "bias": bias, "fvg_kind": g["kind"], "date": d,
                        "flatten_by": flatten_deadline(entry_ts.timetz().replace(tzinfo=None)),
                    }
                    break
            break

    return trades, fvgs


def _max_drawdown(pnls: list[float]) -> float:
    equity = peak = mdd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    return mdd


def summarize_trades(trades: list[dict]) -> dict:
    if not trades:
        return {"num_trades": 0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    return {
        "num_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": 100 * len(wins) / len(trades),
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss else math.inf,
        "total_pnl": sum(pnls),
        "max_drawdown": _max_drawdown(pnls),
    }


def save_to_sqlite(db_path: str, sym: str, df: pd.DataFrame, fvgs: list[dict], trades: list[dict]) -> None:
    conn = sqlite3.connect(db_path)
    df.reset_index(names="ts").assign(symbol=sym).to_sql(f"bars_{sym}", conn, if_exists="replace", index=False)
    if fvgs:
        pd.DataFrame([{"symbol": sym, "kind": g["kind"], "top": g["top"], "bottom": g["bottom"],
                        "formed_time": str(g["formed_time"]),
                        "inverted_time": str(g.get("inverted_time")) if g["inverted_pos"] is not None else None,
                        "tapped": g["tapped_pos"] is not None} for g in fvgs]
                      ).to_sql(f"fvgs_{sym}", conn, if_exists="replace", index=False)
    if trades:
        pd.DataFrame(trades).to_sql(f"trades_{sym}", conn, if_exists="replace", index=False)
    conn.close()
