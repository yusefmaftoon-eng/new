#!/usr/bin/env python3
"""Simulate a Tradeify Select $25K account trading the VWAP reversion
strategy across MES, MNQ, and MGC -- evaluation phase, then (if passed)
straight into the funded phase on the same equity curve.

Rules: $1,500 profit target, 40% consistency (eval only), $1,000 EOD
trailing drawdown enforced in real time, floor locks once balance clears
$26,100, contracts 10 micro (eval) -> 20 micro (funded), payouts up to
$600/day once past the lock threshold. See backtest/prop_firm_sim.py for
the exact mechanics and its documented simplifications.

Examples:
    python -m trading_bot.run_prop_firm_sim --risk-per-trade 150
    python -m trading_bot.run_prop_firm_sim --risk-per-trade 150 --repeat --eval-cost 65
"""
from __future__ import annotations

import argparse

import pandas as pd

from trading_bot.data.futures_fetcher import fetch_micro_future, CONTRACT_MULTIPLIER, TICK_SIZE
from trading_bot.backtest.generic_engine import simulate_trades
from trading_bot.strategies import vwap_reversion_strategy
from trading_bot.backtest.prop_firm_sim import PropFirmRules, simulate_account, simulate_repeated_attempts

SYMBOLS = ["MES", "MNQ", "MGC"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--range", default="60d")
    parser.add_argument("--risk-per-trade", type=float, default=150.0,
                         help="our position-sizing choice, not a firm rule -- $ risked per trade before contract caps")
    parser.add_argument("--repeat", action="store_true",
                         help="restart a fresh (paid) evaluation immediately after every bust, continuing through "
                              "the same trade stream, and report total payouts vs. total eval fees")
    parser.add_argument("--eval-cost", type=float, default=65.0, help="$ cost per evaluation attempt (--repeat only)")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD, inclusive -- slice the fetched window "
                                                             "for out-of-sample / sub-period checks")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD, inclusive")
    args = parser.parse_args()

    trades_by_symbol = {}
    dpp = {}
    for sym in SYMBOLS:
        df = fetch_micro_future(sym, "5m", args.range)
        if args.start_date:
            df = df[df.index.date >= pd.Timestamp(args.start_date).date()]
        if args.end_date:
            df = df[df.index.date <= pd.Timestamp(args.end_date).date()]
        setups = vwap_reversion_strategy.detect_setups(df)
        trades = simulate_trades(sym, df, setups, CONTRACT_MULTIPLIER[sym], min_stop_distance=4 * TICK_SIZE[sym])
        trades_by_symbol[sym] = trades
        dpp[sym] = CONTRACT_MULTIPLIER[sym]
        print(f"{sym}: {len(df)} bars, {len(trades)} VWAP trades, {df.index[0].date()} .. {df.index[-1].date()}")

    rules = PropFirmRules(risk_per_trade=args.risk_per_trade)

    if args.repeat:
        rep = simulate_repeated_attempts(trades_by_symbol, dpp, rules, eval_cost=args.eval_cost)
        print(f"\n{'=' * 60}\nRepeated-attempts simulation (${args.eval_cost:.0f}/eval, "
              f"${rules.risk_per_trade:.0f} risk/trade)\n{'=' * 60}")
        print(f"Attempts:        {rep.num_attempts}")
        print(f"Passed eval:     {rep.num_passed_eval}")
        print(f"Total payouts:   ${rep.total_payouts:,.2f}")
        print(f"Total eval fees: ${rep.total_eval_fees:,.2f}")
        print(f"Net profit:      ${rep.net_profit:,.2f}")
        print("\nPer-attempt breakdown:")
        for i, a in enumerate(rep.attempts, 1):
            passed = f"passed {a.eval_passed_date}" if a.eval_passed_date else "never passed eval"
            end = f"busted {a.bust_date}" if a.outcome == "busted" else f"data ran out ({a.outcome})"
            print(f"  #{i}: {passed}, {end}, payouts=${a.total_payouts:,.2f}, trades={len(a.trade_log)}")
        return

    result = simulate_account(trades_by_symbol, dpp, rules)

    print(f"\n{'=' * 60}\nAccount simulation ({rules.starting_balance:,.0f} start, "
          f"${rules.risk_per_trade:.0f} risk/trade)\n{'=' * 60}")
    print(f"Outcome: {result.outcome}")
    if result.outcome == "busted":
        print(f"Busted: {result.bust_date}")
        print(f"Reason: {result.bust_reason}")
    if result.eval_passed_date:
        print(f"Evaluation passed: {result.eval_passed_date}")
    print(f"Final balance: ${result.final_balance:,.2f}")
    print(f"Peak balance:  ${result.peak_balance:,.2f}")
    print(f"Total payouts: ${result.total_payouts:,.2f}")
    print(f"Floor locked:  {result.floor_locked}" + (f" at ${result.locked_floor_value:,.2f}" if result.floor_locked else ""))
    print(f"Trades taken:  {len(result.trade_log)}")

    print("\nTrade log:")
    for t in result.trade_log:
        print(f"  {t['entry_time'].date()} {t['symbol']:4s} {t['dir']:5s} x{t['contracts']:<2d} "
              f"mae={t['mae_pts']:+.2f}pt pnl=${t['account_pnl']:+.2f} balance=${t['balance_after']:,.2f}")

    print("\nDaily P&L:")
    for day, pnl in sorted(result.daily_pnl.items()):
        print(f"  {day}  ${pnl:+.2f}")


if __name__ == "__main__":
    main()
