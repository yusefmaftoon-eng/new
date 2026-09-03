#!/usr/bin/env python3
"""Run the MES 60m/either/opposite CRT strategy (the one result this session
that survived robustness checks) through a real MyFundedFutures-style
evaluation: $1500 profit target, $1000 max loss, up to 20 micro contracts,
on the real full-year 5m data already fetched from Dukascopy.

Position sizing is fixed-$-risk-per-trade (--risk-per-trade), capped by
--max-contracts and by whatever drawdown cushion is actually left -- see
backtest/evaluation_account_sim.py for exactly how, and for the two things
this can't verify from here (trailing vs static drawdown, real commission).
"""
from __future__ import annotations

import argparse
import json

from trading_bot.backtest.candle_range_theory_sim import run_backtest
from trading_bot.backtest.evaluation_account_sim import simulate_evaluation
from trading_bot.data.dukascopy_fetcher import fetch_range
from datetime import date, timedelta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duka-symbol", default="USA500IDXUSD", help="MES proxy; USATECHIDXUSD for MNQ, XAUUSD for MGC")
    parser.add_argument("--dollars-per-point", type=float, default=5.0)
    parser.add_argument("--range-minutes", type=int, default=60)
    parser.add_argument("--entry-style", default="either", choices=["candle3_open", "ltf_confirm", "either"])
    parser.add_argument("--target-mode", default="opposite", choices=["opposite", "halfback"])
    parser.add_argument("--profit-target", type=float, default=1500.0)
    parser.add_argument("--max-loss", type=float, default=1000.0)
    parser.add_argument("--max-contracts", type=int, default=20)
    parser.add_argument("--risk-per-trade", type=float, default=50.0,
                         help="$ risked per trade before sizing hits --max-contracts or the cushion cap")
    parser.add_argument("--drawdown-mode", default="trailing", choices=["trailing", "static"])
    parser.add_argument("--round-trip-fee", type=float, default=0.0,
                         help="$ per contract per round trip; 0 = gross, no fees modeled")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=args.days)
    print(f"Fetching real 1-min {args.duka_symbol} data {start} to {end}...")
    bars_1m, failed = fetch_range(args.duka_symbol, start, end, pause=0.8, retries=6)
    print(f"Got {len(bars_1m)} bars, {len(failed)} failed days.")

    bars_1m.sort(key=lambda b: b["t"])
    bars_5m, bucket, bucket_start = [], None, None
    for b in bars_1m:
        bt = (b["t"] // 300) * 300
        if bucket is None or bt != bucket_start:
            if bucket is not None:
                bars_5m.append(bucket)
            bucket_start = bt
            bucket = {"t": bt, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            bucket["h"] = max(bucket["h"], b["h"])
            bucket["l"] = min(bucket["l"], b["l"])
            bucket["c"] = b["c"]
    if bucket is not None:
        bars_5m.append(bucket)

    report = run_backtest(bars_5m, range_minutes=args.range_minutes,
                           entry_style=args.entry_style, target_mode=args.target_mode)
    trades = report["trades"]
    print(f"\nStrategy generated {len(trades)} real signals over the period.")

    eval_report = simulate_evaluation(
        trades, dollars_per_point=args.dollars_per_point, profit_target=args.profit_target,
        max_loss=args.max_loss, max_contracts=args.max_contracts,
        risk_per_trade_dollars=args.risk_per_trade, drawdown_mode=args.drawdown_mode,
        round_trip_fee_per_contract=args.round_trip_fee,
    )
    attempts = eval_report.pop("attempts")
    print(json.dumps(eval_report, indent=2, default=float))
    print("\n--- attempts ---")
    for a in attempts:
        print(f"  {a['start_date']} -> {a['end_date']}: {a['outcome']:10s} "
              f"equity=${a['final_equity']:>9.2f} trades_taken={a['n_trades_taken']:3d} skipped={a['n_signals_skipped']}")


if __name__ == "__main__":
    main()
