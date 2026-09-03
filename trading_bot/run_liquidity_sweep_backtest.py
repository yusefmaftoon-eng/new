#!/usr/bin/env python3
"""Backtest the liquidity-sweep + inverse-FVG strategy on real Yahoo Finance
futures data (continuous NQ=F/ES=F/GC=F -- price-identical to MNQ/MES/MGC,
only the $/point multiplier differs).

Yahoo's free 5m history only goes back ~60 days -- that's the real, honest
sample size available here, not a choice. See backtest/liquidity_sweep_ifvg_sim.py
for the exact, explicit definitions of every rule (sessions, sweep, iFVG,
entry/stop/target) -- read those before trusting any number below.
"""
from __future__ import annotations

import argparse
import json

from trading_bot.backtest.liquidity_sweep_ifvg_sim import run_backtest
from trading_bot.data.yahoo_futures_fetcher import fetch_intraday

INSTRUMENTS = {
    "MNQ": {"yahoo_symbol": "NQ=F", "dollars_per_point": 2.0},
    "MES": {"yahoo_symbol": "ES=F", "dollars_per_point": 5.0},
    "MGC": {"yahoo_symbol": "GC=F", "dollars_per_point": 10.0},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", required=True, choices=list(INSTRUMENTS))
    parser.add_argument("--stop-points", type=float, required=True)
    parser.add_argument("--range", default="60d")
    parser.add_argument("--confirmation", default="fvg", choices=["fvg", "cisd", "bos_fvg"])
    parser.add_argument("--sweep-start-hour", type=float, default=None,
                         help="ET hour, e.g. 10 for 10am -- only sweeps in [start,end) can trigger a setup")
    parser.add_argument("--sweep-end-hour", type=float, default=None)
    args = parser.parse_args()

    spec = INSTRUMENTS[args.instrument]
    print(f"Fetching real 5m {args.instrument} ({spec['yahoo_symbol']}) data, range={args.range}...")
    bars = fetch_intraday(spec["yahoo_symbol"], interval="5m", range_=args.range)
    print(f"Loaded {len(bars)} real 5-minute bars.")

    sweep_window = None
    if args.sweep_start_hour is not None and args.sweep_end_hour is not None:
        sweep_window = (args.sweep_start_hour, args.sweep_end_hour)

    report = run_backtest(bars, stop_points=args.stop_points, confirmation=args.confirmation,
                           sweep_hour_window=sweep_window)
    trades = report.pop("trades")

    dpp = spec["dollars_per_point"]
    total_dollars_per_contract = sum(t["pnl_points"] for t in trades) * dpp if trades else 0.0
    report["dollars_per_point"] = dpp
    report["total_pnl_per_contract_usd"] = round(total_dollars_per_contract, 2)
    report["stop_points"] = args.stop_points
    report["stop_dollars_per_contract"] = round(args.stop_points * dpp, 2)

    print(json.dumps(report, indent=2, default=float))

    print("\n--- individual trades ---")
    for t in trades:
        print(f"  {t['level']:12s} {t['sweep_type']:13s} {t['direction']:5s} "
              f"entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} "
              f"{t['result']:4s} R={t['r_multiple']:+.2f}")


if __name__ == "__main__":
    main()
