#!/usr/bin/env python3
"""Run a Kalshi backtest against real resolved-market history.

Example (validated out-of-sample, see README for train/test numbers):
    python -m trading_bot.run_kalshi_backtest \
        --series KXHIGHNY --strategy momentum --lookback 2 --min-move 0.10 \
        --max-markets 300 --hours-before-close 6
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

from trading_bot.backtest.kalshi_engine import run_kalshi_backtest
from trading_bot.data.kalshi_fetcher import candle_price, fetch_candlesticks, fetch_settled_markets
from trading_bot.strategies.kalshi_strategy import STRATEGIES


def _parse_ts(iso_str: str) -> int:
    return int(datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", default="KXHIGHNY",
                         help="Kalshi series ticker, e.g. KXHIGHNY (NYC daily high temp)")
    parser.add_argument("--strategy", default="momentum", choices=list(STRATEGIES))
    parser.add_argument("--entry-threshold", type=float, default=0.90)
    parser.add_argument("--lookback", type=int, default=2)
    parser.add_argument("--min-move", type=float, default=0.10)
    parser.add_argument("--max-markets", type=int, default=300)
    parser.add_argument("--hours-before-close", type=float, default=6.0,
                         help="cut off price history this many hours before market close, "
                              "so the entry can't leak the outcome")
    parser.add_argument("--candle-period-minutes", type=int, default=60, choices=[1, 60, 1440])
    parser.add_argument("--stake", type=float, default=100.0)
    parser.add_argument("--capital", type=float, default=10_000.0)
    args = parser.parse_args()

    print(f"Fetching up to {args.max_markets} settled Kalshi markets for series {args.series!r}...")
    markets = list(fetch_settled_markets(args.series, max_markets=args.max_markets))
    print(f"Loaded {len(markets)} settled markets with a clean yes/no result.")

    def get_history(market: dict) -> list:
        try:
            open_ts = _parse_ts(market["open_time"])
            close_ts = _parse_ts(market["close_time"])
        except (KeyError, ValueError):
            return []
        cutoff_ts = close_ts - int(args.hours_before_close * 3600)
        if cutoff_ts <= open_ts:
            return []
        try:
            candles = fetch_candlesticks(args.series, market["ticker"], open_ts, cutoff_ts,
                                          period_interval=args.candle_period_minutes)
        except Exception as exc:  # a single market hiccup shouldn't kill the whole run
            print(f"  warn: candlestick fetch failed for {market['ticker']}: {exc}")
            return []
        finally:
            time.sleep(0.5)  # stay under Kalshi's public rate limit
        history = []
        for c in candles:
            p = candle_price(c)
            if p is not None:
                history.append({"t": c["end_period_ts"], "p": p})
        return history

    strategy_fn = STRATEGIES[args.strategy]
    if args.strategy == "favorite_longshot":
        strategy = lambda h: strategy_fn(h, entry_threshold=args.entry_threshold)
    else:
        strategy = lambda h: strategy_fn(h, lookback=args.lookback, min_move=args.min_move)

    report = run_kalshi_backtest(
        markets, get_history, strategy,
        stake_per_trade=args.stake, initial_capital=args.capital,
    )
    report.pop("equity_curve")
    print(json.dumps(report, indent=2, default=float))


if __name__ == "__main__":
    main()
