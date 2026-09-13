#!/usr/bin/env python3
"""Run a strategy against a LIVE Robinhood account.

LEGACY / FALLBACK PATH: Robinhood now offers an official agentic trading
integration (an OAuth-connected MCP server, isolated to its own dedicated
account) which is the recommended way to do this -- see the README's
"Robinhood live trading" section, and `trading_bot/live/mcp_signal_helper.py`
for using this repo's strategies with it. This script instead logs into your
account directly with your password via the unofficial `robin_stocks`
client, which means your credentials live in your environment and there's no
account isolation from your main portfolio. Keep using it only if you can't
use the official MCP path for some reason.

SAFETY: dry-run by default. No order is ever sent to Robinhood unless you
pass --live *and* have separately set ROBINHOOD_CONFIRM_LIVE_TRADING=yes in
your shell. Credentials come from ROBINHOOD_USERNAME / ROBINHOOD_PASSWORD
(and optionally ROBINHOOD_TOTP_SECRET for automatic 2FA) -- never pass them
as command-line arguments or commit them anywhere. See the README for setup.

Dry run (default -- prints what it WOULD do, places no orders):
    python -m trading_bot.run_robinhood_live --symbol AAPL --quantity 1

Live (only after you've reviewed dry-run output and understand this trades a
real account with real money):
    ROBINHOOD_CONFIRM_LIVE_TRADING=yes python -m trading_bot.run_robinhood_live \
        --symbol AAPL --quantity 1 --live
"""
from __future__ import annotations

import argparse
import os
import sys

from trading_bot.live import robinhood_broker as broker
from trading_bot.strategies.crypto_strategy import STRATEGIES


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", required=True, help="e.g. AAPL")
    parser.add_argument("--interval", default="day",
                         choices=["5minute", "10minute", "hour", "day", "week"])
    parser.add_argument("--span", default="year",
                         choices=["day", "week", "month", "3month", "year", "5year"])
    parser.add_argument("--strategy", default="sma_crossover", choices=list(STRATEGIES))
    parser.add_argument("--fast", type=int, default=20)
    parser.add_argument("--slow", type=int, default=50)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--oversold", type=float, default=30)
    parser.add_argument("--overbought", type=float, default=50)
    parser.add_argument("--quantity", type=float, required=True,
                         help="target shares to hold when the strategy signals LONG")
    parser.add_argument("--max-position-value", type=float, default=None,
                         help="refuse to submit a buy that would push notional exposure "
                              "above this many USD")
    parser.add_argument("--live", action="store_true",
                         help="place real orders (still requires "
                              "ROBINHOOD_CONFIRM_LIVE_TRADING=yes in the environment)")
    args = parser.parse_args()

    dry_run = not args.live
    if not dry_run and os.environ.get("ROBINHOOD_CONFIRM_LIVE_TRADING") != "yes":
        print("Refusing to run with --live: set ROBINHOOD_CONFIRM_LIVE_TRADING=yes "
              "as an explicit environment-level acknowledgement first.", file=sys.stderr)
        sys.exit(1)

    print("Logging in to Robinhood...")
    broker.login()
    try:
        df = broker.get_historicals(args.symbol, interval=args.interval, span=args.span)
        print(f"Loaded {len(df)} candles for {args.symbol}.")

        strategy_fn = STRATEGIES[args.strategy]
        if args.strategy == "sma_crossover":
            position = strategy_fn(df, fast=args.fast, slow=args.slow)
        else:
            position = strategy_fn(df, period=args.rsi_period, oversold=args.oversold,
                                    overbought=args.overbought)

        target_signal = position.iloc[-1]
        current_qty = broker.get_position_quantity(args.symbol)
        target_qty = args.quantity if target_signal > 0 else 0.0
        delta = target_qty - current_qty

        print(f"Signal: {'LONG' if target_signal > 0 else 'FLAT'}  "
              f"current position: {current_qty}  target: {target_qty}")

        if abs(delta) < 1e-9:
            print("No position change needed.")
            return

        side = "buy" if delta > 0 else "sell"
        qty = abs(delta)

        if args.max_position_value is not None and side == "buy":
            quote = broker.get_quote(args.symbol)
            notional = (current_qty + qty) * quote
            if notional > args.max_position_value:
                print(f"Refusing: resulting notional ${notional:,.2f} would exceed "
                      f"--max-position-value ${args.max_position_value:,.2f}", file=sys.stderr)
                sys.exit(1)

        result = broker.place_order(args.symbol, side, qty, dry_run=dry_run)
        print(result)
    finally:
        broker.logout()


if __name__ == "__main__":
    main()
