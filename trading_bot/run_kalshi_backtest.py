#!/usr/bin/env python3
"""Run a Kalshi backtest against real settled-market history.

Reuses the Polymarket event-based engine/strategies: both are 0-1-priced
binary (YES/NO) markets with the same buy-and-hold-to-resolution structure,
so the backtest math is identical -- only the data fetcher differs.

Example:
    python -m trading_bot.run_kalshi_backtest \
        --strategy favorite_longshot --entry-threshold 0.9 --max-markets 200
"""
from __future__ import annotations

import argparse
import json
import time

from trading_bot.backtest.polymarket_engine import run_polymarket_backtest
from trading_bot.data.kalshi_fetcher import fetch_price_history, fetch_resolved_markets
from trading_bot.strategies.polymarket_strategy import STRATEGIES

SECONDS_PER_DAY = 86_400


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", default="favorite_longshot", choices=list(STRATEGIES))
    parser.add_argument("--entry-threshold", type=float, default=0.90)
    parser.add_argument("--lookback", type=int, default=5)
    parser.add_argument("--min-move", type=float, default=0.05)
    parser.add_argument("--max-markets", type=int, default=200)
    parser.add_argument("--lookback-days", type=int, default=30,
                         help="how far before close to pull candlestick history")
    parser.add_argument("--stake", type=float, default=100.0)
    parser.add_argument("--fee-pct", type=float, default=0.01,
                         help="Kalshi's real fee schedule is per-contract and price-dependent; "
                              "this flat %% of stake is an approximation, tune to taste")
    parser.add_argument("--capital", type=float, default=10_000.0)
    args = parser.parse_args()

    print(f"Fetching up to {args.max_markets} settled Kalshi markets...")
    markets = list(fetch_resolved_markets(max_markets=args.max_markets))
    print(f"Loaded {len(markets)} settled markets.")

    def get_history(market: dict) -> list:
        try:
            end_ts = int(market["close_time"]) if isinstance(market["close_time"], (int, float)) \
                else int(time.mktime(time.strptime(market["close_time"][:19], "%Y-%m-%dT%H:%M:%S")))
        except (TypeError, ValueError, KeyError):
            return []
        start_ts = end_ts - args.lookback_days * SECONDS_PER_DAY
        try:
            return fetch_price_history(market["series_ticker"], market["ticker"], start_ts, end_ts)
        except Exception as exc:  # public API hiccup for one market shouldn't kill the run
            print(f"  warn: history fetch failed for {market['ticker']!r}: {exc}")
            return []

    strategy_fn = STRATEGIES[args.strategy]
    if args.strategy == "favorite_longshot":
        strategy = lambda h: strategy_fn(h, entry_threshold=args.entry_threshold)
    else:
        strategy = lambda h: strategy_fn(h, lookback=args.lookback, min_move=args.min_move)

    report = run_polymarket_backtest(
        markets, get_history, strategy,
        stake_per_trade=args.stake, fee_pct=args.fee_pct, initial_capital=args.capital,
    )
    report.pop("equity_curve")
    print(json.dumps(report, indent=2, default=float))


if __name__ == "__main__":
    main()
