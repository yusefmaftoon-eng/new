#!/usr/bin/env python3
"""Run the inverse-fair-value-gap (IFVG) backtest against real MES/MNQ data.

HTF bias (1H swing structure before 09:30 ET) + SMT divergence between MES and
MNQ + a retrace tap into a freshly inverted fair value gap, targeting resting
liquidity (prior day high/low or the nearest opposing swing). See
strategies/ifvg_strategy.py for the exact rules.

Data comes from Yahoo Finance's public chart API; 5-minute bars are capped at
60 days of history there, so a 60-day run is a demonstration that the engine
is wired correctly end to end, not a statistically meaningful sample -- expect
single digits of qualifying trades. See trading_bot/README.md for how to get
a longer, more meaningful backtest.

Example:
    python -m trading_bot.run_ifvg_backtest --symbol both --db-path ifvg.sqlite
"""
from __future__ import annotations

import argparse
import json

from trading_bot.data.futures_fetcher import fetch_micro_future
from trading_bot.strategies.ifvg_strategy import find_fractal_swings, compute_daily_bias
from trading_bot.backtest.futures_engine import run_ifvg_backtest, summarize_trades, save_to_sqlite

PAIR = {"MES": "MNQ", "MNQ": "MES"}


def resample_1h(df):
    return df.resample("1h").agg({"open": "first", "high": "max", "low": "min",
                                   "close": "last", "volume": "sum"}).dropna()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="both", choices=["MES", "MNQ", "both"])
    parser.add_argument("--range", default="60d", help="Yahoo range string, e.g. 30d/60d (5m bars cap at 60d)")
    parser.add_argument("--entry-mode", default="retrace", choices=["retrace", "immediate"],
                         help="'retrace' waits for a tap back into the inverted gap; "
                              "'immediate' fills at the next bar's open right when it inverts")
    parser.add_argument("--db-path", default=None, help="optional SQLite path to persist bars/fvgs/trades")
    args = parser.parse_args()

    symbols = ["MES", "MNQ"] if args.symbol == "both" else [args.symbol]
    print(f"Fetching {', '.join(symbols)} 5m bars from Yahoo Finance (range={args.range})...")
    bars = {sym: fetch_micro_future(sym, "5m", args.range) for sym in set(symbols) | {PAIR[s] for s in symbols}}
    for sym, df in bars.items():
        print(f"  {sym}: {len(df)} bars, {df.index[0]} .. {df.index[-1]}")

    bias_cache = {}
    for sym, df in bars.items():
        df_1h = resample_1h(df)
        bias_cache[sym] = compute_daily_bias(df_1h, find_fractal_swings(df_1h, 2, 2))

    for sym in symbols:
        other = PAIR[sym]
        trades, fvgs = run_ifvg_backtest(sym, bars[sym], bars[other], bias_cache[sym], entry_mode=args.entry_mode)
        report = summarize_trades(trades)
        print(f"\n=== {sym} ===")
        print(json.dumps(report, indent=2, default=float))
        for t in trades:
            print(f"  {t['date']} {t['dir']:5s} entry={t['entry']:.2f} stop={t['stop']:.2f} "
                  f"target={t['target']:.2f} exit={t['exit']:.2f} ({t['reason']}) pnl=${t['pnl']:.2f}")
        if args.db_path:
            save_to_sqlite(args.db_path, sym, bars[sym], fvgs, trades)
    if args.db_path:
        print(f"\nBars/FVGs/trades saved to {args.db_path}")


if __name__ == "__main__":
    main()
