#!/usr/bin/env python3
"""Backtest VWAP mean-reversion, Turtle Soup, and Order Block retest on
real MES/MGC 5-minute data (explicitly not ORB, per request).

All three share backtest/generic_engine.py for fill/exit/flatten mechanics
(next-bar-open fill, first stop/target touch wins, flatten at a fixed ET
hour) and differ only in setup detection -- see each strategy module's
docstring for its exact rules and what's deliberately NOT shared with the
ICT-style strategies elsewhere in this package (no bias filter, no
killzones, no SMT).

Example:
    python -m trading_bot.run_new_strategies_backtest --symbol both
"""
from __future__ import annotations

import argparse
import json

from trading_bot.data.futures_fetcher import fetch_micro_future, CONTRACT_MULTIPLIER, TICK_SIZE
from trading_bot.backtest.generic_engine import simulate_trades, summarize_trades
from trading_bot.strategies import vwap_reversion_strategy, turtle_soup_strategy, order_block_strategy

STRATEGIES = {
    "vwap": lambda df: vwap_reversion_strategy.detect_setups(df),
    "turtle_soup": lambda df: turtle_soup_strategy.detect_setups(df),
    "order_block": lambda df: order_block_strategy.detect_setups(df),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="both",
                         help="'both' (MES+MGC), 'all' (MES+MGC+MNQ), or a comma-separated list e.g. MNQ or MES,MNQ")
    parser.add_argument("--strategy", default="all", choices=["all", *STRATEGIES])
    parser.add_argument("--range", default="60d")
    args = parser.parse_args()

    if args.symbol == "both":
        symbols = ["MES", "MGC"]
    elif args.symbol == "all":
        symbols = ["MES", "MGC", "MNQ"]
    else:
        symbols = args.symbol.split(",")
    strategies = list(STRATEGIES) if args.strategy == "all" else [args.strategy]

    bars = {}
    for sym in symbols:
        df = fetch_micro_future(sym, "5m", args.range)
        bars[sym] = df
        print(f"{sym}: {len(df)} bars, {df.index[0]} .. {df.index[-1]}")

    for strat_name in strategies:
        print(f"\n{'=' * 60}\n{strat_name}\n{'=' * 60}")
        combined = []
        for sym in symbols:
            setups = STRATEGIES[strat_name](bars[sym])
            trades = simulate_trades(sym, bars[sym], setups, CONTRACT_MULTIPLIER[sym],
                                      min_stop_distance=4 * TICK_SIZE[sym])
            combined += trades
            report = summarize_trades(trades)
            print(f"\n--- {sym} ---")
            print(json.dumps(report, indent=2, default=float))
            for t in trades:
                print(f"  {t['entry_time'].date()} {t['dir']:5s} entry={t['entry']:.2f} stop={t['stop']:.2f} "
                      f"target={t['target']:.2f} exit={t['exit']:.2f} ({t['reason']}) "
                      f"R:R={t['rr_at_entry']:.2f} pnl=${t['pnl']:.2f}")
        if len(symbols) > 1:
            print(f"\n--- combined ({'+'.join(symbols)}) ---")
            print(json.dumps(summarize_trades(combined), indent=2, default=float))


if __name__ == "__main__":
    main()
