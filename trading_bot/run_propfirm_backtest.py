#!/usr/bin/env python3
"""Run a prop-firm-style funded-account evaluation against real forex data.

Example:
    python -m trading_bot.run_propfirm_backtest \
        --base EUR --quote USD --start 2022-01-01 --end 2026-08-01 \
        --strategy sma_crossover --fast 10 --slow 30 --leverage 15

Rules default to a typical FTMO-style challenge: 5% max daily loss, 10% max
total drawdown, 10% profit target. See backtest/propfirm_engine.py for the
important caveat about daily-close-only data.
"""
from __future__ import annotations

import argparse
import json

from trading_bot.backtest.propfirm_engine import evaluate_prop_firm_challenge
from trading_bot.data.forex_fetcher import fetch_forex_daily, periods_per_year
from trading_bot.strategies.crypto_strategy import STRATEGIES  # generic 'close'-column signals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="EUR")
    parser.add_argument("--quote", default="USD")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--strategy", default="sma_crossover", choices=list(STRATEGIES))
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--oversold", type=float, default=30)
    parser.add_argument("--overbought", type=float, default=50)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--leverage", type=float, default=15.0,
                         help="scales daily strategy returns to simulate real position sizing")
    parser.add_argument("--fee-bps", type=float, default=2.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--max-daily-loss-pct", type=float, default=5.0)
    parser.add_argument("--max-drawdown-pct", type=float, default=10.0)
    parser.add_argument("--profit-target-pct", type=float, default=10.0)
    args = parser.parse_args()

    pair = f"{args.base}/{args.quote}"
    print(f"Fetching {pair} daily rates from Frankfurter (ECB) ({args.start} .. {args.end})...")
    df = fetch_forex_daily(args.base, args.quote, args.start, args.end)
    print(f"Loaded {len(df)} daily observations.")

    strategy_fn = STRATEGIES[args.strategy]
    if args.strategy == "sma_crossover":
        position = strategy_fn(df, fast=args.fast, slow=args.slow)
    else:
        position = strategy_fn(df, period=args.rsi_period, oversold=args.oversold,
                                overbought=args.overbought)

    report = evaluate_prop_firm_challenge(
        df, position, periods_per_year=periods_per_year(),
        initial_capital=args.capital, leverage=args.leverage,
        fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
        max_daily_loss_pct=args.max_daily_loss_pct,
        max_total_drawdown_pct=args.max_drawdown_pct,
        profit_target_pct=args.profit_target_pct,
    )
    report.pop("equity_curve")
    print(json.dumps(report, indent=2, default=float))


if __name__ == "__main__":
    main()
