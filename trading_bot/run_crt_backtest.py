#!/usr/bin/env python3
"""Run the Candle Range Theory (CRT) backtest against real MES/MNQ data.

Same HTF bias, killzones, resting-liquidity targets, and R:R gate as the
IFVG backtest (run_ifvg_backtest.py), so the two are comparable -- what
differs is the core signal: CRT sweeps the prior completed 1H candle's
range and looks for a single candle that reverses straight back through
the level (no second instrument, no SMT divergence). See
strategies/crt_strategy.py for the exact rules.

Example:
    python -m trading_bot.run_crt_backtest --symbol both --db-path crt.sqlite
"""
from __future__ import annotations

import argparse
import json

from trading_bot.data.futures_fetcher import fetch_micro_future
from trading_bot.strategies.ifvg_strategy import find_fractal_swings, compute_daily_bias
from trading_bot.backtest.crt_engine import run_crt_backtest, summarize_trades, save_to_sqlite


def resample_1h(df):
    return df.resample("1h").agg({"open": "first", "high": "max", "low": "min",
                                   "close": "last", "volume": "sum"}).dropna()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="both", choices=["MES", "MNQ", "both"])
    parser.add_argument("--range", default="60d", help="Yahoo range string, e.g. 30d/60d (5m bars cap at 60d)")
    parser.add_argument("--entry-mode", default="retrace", choices=["retrace", "immediate", "on_close"])
    parser.add_argument("--db-path", default=None, help="optional SQLite path to persist bars/setups/trades")
    args = parser.parse_args()

    symbols = ["MES", "MNQ"] if args.symbol == "both" else [args.symbol]
    print(f"Fetching {', '.join(symbols)} 5m bars from Yahoo Finance (range={args.range})...")
    bars = {sym: fetch_micro_future(sym, "5m", args.range) for sym in symbols}
    for sym, df in bars.items():
        print(f"  {sym}: {len(df)} bars, {df.index[0]} .. {df.index[-1]}")

    for sym in symbols:
        df_1h = resample_1h(bars[sym])
        bias = compute_daily_bias(df_1h, find_fractal_swings(df_1h, 2, 2))
        trades, setups = run_crt_backtest(sym, bars[sym], df_1h, bias, entry_mode=args.entry_mode)
        report = summarize_trades(trades)
        print(f"\n=== {sym} ===")
        print(json.dumps(report, indent=2, default=float))
        for t in trades:
            print(f"  {t['date']} {t['dir']:5s} entry={t['entry']:.2f} stop={t['stop']:.2f} "
                  f"target={t['target']:.2f} exit={t['exit']:.2f} ({t['reason']}) pnl=${t['pnl']:.2f}")
        if args.db_path:
            save_to_sqlite(args.db_path, sym, bars[sym], setups, trades)
    if args.db_path:
        print(f"\nBars/setups/trades saved to {args.db_path}")


if __name__ == "__main__":
    main()
