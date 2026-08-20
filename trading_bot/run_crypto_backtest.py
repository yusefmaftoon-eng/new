#!/usr/bin/env python3
"""Run a crypto backtest against real Binance historical data.

Example:
    python -m trading_bot.run_crypto_backtest \
        --symbol BTCUSDT --interval 1d --start 2021-01-01 --end 2026-08-01 \
        --strategy sma_crossover --fast 20 --slow 50
"""
from __future__ import annotations

import argparse
import json

from trading_bot.backtest.engine import backtest_report
from trading_bot.data.crypto_fetcher import fetch_binance_klines, periods_per_year
from trading_bot.strategies.crypto_strategy import STRATEGIES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1d", choices=["1m", "5m", "15m", "1h", "4h", "1d"])
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--strategy", default="sma_crossover", choices=list(STRATEGIES))
    parser.add_argument("--fast", type=int, default=20)
    parser.add_argument("--slow", type=int, default=50)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--oversold", type=float, default=30)
    parser.add_argument("--overbought", type=float, default=50)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--fee-bps", type=float, default=10.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--out-csv", default=None, help="optional path to dump the equity curve")
    args = parser.parse_args()

    print(f"Fetching {args.symbol} {args.interval} candles from Binance "
          f"({args.start} .. {args.end})...")
    df = fetch_binance_klines(args.symbol, args.interval, args.start, args.end)
    print(f"Loaded {len(df)} candles.")

    strategy_fn = STRATEGIES[args.strategy]
    if args.strategy == "sma_crossover":
        position = strategy_fn(df, fast=args.fast, slow=args.slow)
    else:
        position = strategy_fn(df, period=args.rsi_period, oversold=args.oversold,
                                overbought=args.overbought)

    report = backtest_report(
        df, position, periods_per_year=periods_per_year(args.interval),
        initial_capital=args.capital, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
    )

    equity_curve = report.pop("equity_curve")
    print(json.dumps(report, indent=2, default=float))

    if args.out_csv:
        equity_curve.to_csv(args.out_csv)
        print(f"Equity curve written to {args.out_csv}")


if __name__ == "__main__":
    main()
