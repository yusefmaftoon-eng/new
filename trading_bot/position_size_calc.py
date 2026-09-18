#!/usr/bin/env python3
"""Real-time position size calculator for the $90-risk VWAP reversion setup
on the Tradeify Select $25K account (MES/MNQ/MGC).

Not a backtest -- this is meant to be run live, while watching a signal
fire: give it the symbol and your entry/stop prices, it tells you how many
contracts to trade. Same formula as contracts_for_trade() in
backtest/prop_firm_sim.py (floor(risk / (stop_distance * $/point)), capped
by the account-wide contract limit minus whatever you already have open) --
pulled out here as a standalone tool so you don't need to run a backtest to
size a trade.

Examples:
    python -m trading_bot.position_size_calc --symbol MNQ --entry 29500 --stop 29480
    python -m trading_bot.position_size_calc --symbol MES --entry 7650 --stop 7644.5 --open-contracts 3 --funded
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

from trading_bot.data.futures_fetcher import CONTRACT_MULTIPLIER, TICK_SIZE

DEFAULT_RISK_PER_TRADE = 90.0
EVAL_MAX_CONTRACTS = 10   # 10 micro, aggregate across MES+MNQ+MGC -- not per symbol
FUNDED_MAX_CONTRACTS = 20
MIN_STOP_TICKS = 4        # matches generic_engine's min_stop_distance guard -- see its docstring for why


@dataclass
class SizeResult:
    contracts: int
    stop_distance_pts: float
    stop_distance_ticks: float
    dollar_risk: float
    dollars_per_point: float
    capped_by_account: bool
    rejected_reason: str = ""


def calc_position_size(symbol: str, entry: float, stop: float, direction: str,
                        risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
                        open_contracts: int = 0, funded: bool = False) -> SizeResult:
    if symbol not in CONTRACT_MULTIPLIER:
        raise ValueError(f"unsupported symbol {symbol!r}, choose from {list(CONTRACT_MULTIPLIER)}")
    dpp = CONTRACT_MULTIPLIER[symbol]
    tick = TICK_SIZE[symbol]

    invalid = (direction == "long" and stop >= entry) or (direction == "short" and stop <= entry)
    if invalid:
        return SizeResult(0, 0, 0, 0, dpp, False, f"stop is on the wrong side of entry for a {direction} -- not a real stop")

    stop_pts = abs(entry - stop)
    stop_ticks = stop_pts / tick
    if stop_ticks < MIN_STOP_TICKS:
        return SizeResult(0, stop_pts, stop_ticks, 0, dpp, False,
                           f"stop is only {stop_ticks:.1f} ticks away (min {MIN_STOP_TICKS}) -- too tight to size sanely")

    max_contracts = FUNDED_MAX_CONTRACTS if funded else EVAL_MAX_CONTRACTS
    available = max_contracts - open_contracts
    if available < 1:
        return SizeResult(0, stop_pts, stop_ticks, 0, dpp, True,
                           f"account-wide contract cap ({max_contracts}) already used up by open positions")

    ideal = math.floor(risk_per_trade / (stop_pts * dpp))
    contracts = max(1, min(ideal, available))
    capped = contracts == available and ideal > available
    dollar_risk = contracts * stop_pts * dpp

    return SizeResult(contracts, stop_pts, stop_ticks, dollar_risk, dpp, capped)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", required=True, choices=list(CONTRACT_MULTIPLIER))
    parser.add_argument("--entry", type=float, required=True)
    parser.add_argument("--stop", type=float, required=True)
    parser.add_argument("--direction", choices=["long", "short"], default=None,
                         help="inferred from entry vs. stop if omitted")
    parser.add_argument("--risk", type=float, default=DEFAULT_RISK_PER_TRADE, help="$ risk per trade")
    parser.add_argument("--open-contracts", type=int, default=0,
                         help="contracts already open on OTHER symbols right now (account-wide cap is aggregate)")
    parser.add_argument("--funded", action="store_true", help="use the 20-contract funded cap instead of eval's 10")
    args = parser.parse_args()

    direction = args.direction or ("long" if args.entry > args.stop else "short")
    r = calc_position_size(args.symbol, args.entry, args.stop, direction,
                            risk_per_trade=args.risk, open_contracts=args.open_contracts, funded=args.funded)

    print(f"{args.symbol} {direction} @ {args.entry}, stop {args.stop} "
          f"({r.stop_distance_pts:.2f}pt / {r.stop_distance_ticks:.1f} ticks)")
    if r.contracts < 1:
        print(f"NO TRADE: {r.rejected_reason}")
        return
    print(f"-> {r.contracts} contract(s), ${r.dollar_risk:.2f} at risk (target was ${args.risk:.2f})")
    if r.capped_by_account:
        print(f"   (capped by account contract limit, not by the ${args.risk:.0f} risk target)")


if __name__ == "__main__":
    main()
