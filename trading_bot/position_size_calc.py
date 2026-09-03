#!/usr/bin/env python3
"""Quick position-size lookup for live use: given the stop distance on a
real setup (in points), prints how many MES micro contracts to trade at
$200 risk/trade -- the sizing confirmed for the A+ CRT strategy
(60m/mss_fvg/opposite, kill-zone-restricted) after backtesting.

Usage:
    python -m trading_bot.position_size_calc 6.5
    python -m trading_bot.position_size_calc --entry 6500.25 --stop 6493.75
"""
from __future__ import annotations

import argparse

from trading_bot.backtest.evaluation_account_sim import size_contracts

MES_DOLLARS_PER_POINT = 5.0
DEFAULT_RISK_PER_TRADE = 200.0
DEFAULT_MAX_CONTRACTS = 20


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stop_points", type=float, nargs="?", default=None,
                         help="stop distance in points (e.g. 6.5)")
    parser.add_argument("--entry", type=float, default=None, help="entry price, use with --stop instead")
    parser.add_argument("--stop", type=float, default=None, help="stop price, use with --entry instead")
    parser.add_argument("--risk", type=float, default=DEFAULT_RISK_PER_TRADE, help="$ risk per trade")
    parser.add_argument("--max-contracts", type=int, default=DEFAULT_MAX_CONTRACTS)
    parser.add_argument("--cushion", type=float, default=None,
                         help="$ of drawdown room actually left right now -- omit to ignore (eval/funded caps still apply live, size DOWN if this is tight)")
    args = parser.parse_args()

    if args.entry is not None and args.stop is not None:
        stop_points = abs(args.entry - args.stop)
    elif args.stop_points is not None:
        stop_points = args.stop_points
    else:
        parser.error("give either a stop-points argument or both --entry and --stop")

    cushion = args.cushion if args.cushion is not None else 1e9  # effectively unbounded; Python's inf//x is nan
    contracts = size_contracts(stop_points, MES_DOLLARS_PER_POINT, args.risk, args.max_contracts, cushion)
    risk_per_contract = stop_points * MES_DOLLARS_PER_POINT
    actual_risk = contracts * risk_per_contract

    print(f"Stop distance: {stop_points:.2f} points  (${risk_per_contract:.2f} risk/contract)")
    print(f"Target risk: ${args.risk:.0f}/trade")
    print(f"-> Contracts: {contracts}")
    print(f"-> Actual $ at risk: ${actual_risk:.2f}")
    if contracts == 0:
        print("!! Stop is too wide for even 1 contract at this risk budget -- skip this setup.")
    if contracts == args.max_contracts:
        print(f"!! Capped at your {args.max_contracts}-contract account max, not the risk budget.")


if __name__ == "__main__":
    main()
