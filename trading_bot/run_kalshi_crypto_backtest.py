#!/usr/bin/env python3
"""Backtest a Kalshi-style daily directional bot (crypto or gold) against
REAL historical prices.

Real vs. assumed -- read this before trusting any number below:
  - Price history and daily up/down outcomes: real (CoinMetrics for crypto,
    World Bank/'datasets' gold-prices for gold -- see data/*_fetcher.py).
  - Model probabilities: real, computed causally (no lookahead).
  - The price Kalshi would have charged for the contract: NOT observed (this
    environment cannot reach Kalshi's API -- see README) -- swept across
    --assumed-prices instead of asserted as one number.

Examples:
    python -m trading_bot.run_kalshi_crypto_backtest --asset btc --strategy vol_model
    python -m trading_bot.run_kalshi_crypto_backtest --asset eth --strategy sma_state
    python -m trading_bot.run_kalshi_crypto_backtest --asset gold --strategy vol_model
"""
from __future__ import annotations

import argparse
import json

from trading_bot.backtest.kalshi_directional_sim import (
    build_daily_outcomes, compute_predictions, evaluate_calibration, pnl_sensitivity,
)
from trading_bot.data.coinmetrics_fetcher import fetch_daily_closes
from trading_bot.data.gold_fetcher import fetch_monthly_closes
from trading_bot.strategies.kalshi_directional_strategy import (
    SmaStateHitRateEstimator, vol_model_prob,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--asset", default="btc", choices=["btc", "eth", "gold"])
    parser.add_argument("--strategy", default="vol_model", choices=["vol_model", "sma_state"])
    parser.add_argument("--vol-window", type=int, default=30,
                         help="trailing days of returns used for realized vol (vol_model)")
    parser.add_argument("--sma-window", type=int, default=20,
                         help="SMA window in periods (sma_state)")
    parser.add_argument("--edge-threshold", type=float, default=0.03,
                         help="only trade when model prob differs from assumed price by more")
    parser.add_argument("--assumed-prices", type=float, nargs="+",
                         default=[0.45, 0.48, 0.50, 0.52, 0.55],
                         help="sweep of hypothetical flat Kalshi YES prices to test P&L against")
    parser.add_argument("--fee-pct", type=float, default=0.01)
    parser.add_argument("--stake", type=float, default=100.0)
    parser.add_argument("--start-date", default=None,
                         help="drop price points before this ISO date (e.g. gold data pre-1960 "
                              "repeats the annual average for every month, which is not a real "
                              "monthly close and will silently distort results if left in)")
    args = parser.parse_args()

    if args.asset == "gold":
        print("Fetching real monthly gold prices (World Bank via datasets/gold-prices)...")
        prices = fetch_monthly_closes()
        if args.start_date is None:
            args.start_date = "1960-01-01"  # see --start-date help: pre-1960 rows aren't real monthly data
            print(f"Defaulting --start-date to {args.start_date} (pre-1960 gold rows are "
                  f"annual averages repeated 12x/year, not real monthly closes -- including them "
                  f"fabricates a 'flat month = down' pattern that doesn't reflect real gold "
                  f"price behavior).")
        horizon_days = 30  # monthly cadence -- see data/gold_fetcher.py caveat
        print(f"NOTE: gold data here is MONTHLY, not Kalshi's real daily/hourly cadence -- "
              f"treat this run as a coarse directional sanity check, not a faithful sim.")
    else:
        print(f"Fetching real daily {args.asset.upper()} prices (CoinMetrics)...")
        prices = fetch_daily_closes(args.asset)
        horizon_days = 1

    if args.start_date is not None:
        prices = [p for p in prices if p["date"] >= args.start_date]
    print(f"Loaded {len(prices)} price points, {prices[0]['date']} to {prices[-1]['date']}.")

    rows = build_daily_outcomes(prices)

    if args.strategy == "vol_model":
        predict_fn = lambda closes: vol_model_prob(closes, window=args.vol_window,
                                                     horizon_days=horizon_days)
        update_fn = None
    else:
        estimator = SmaStateHitRateEstimator(sma_window=args.sma_window)
        predict_fn = estimator.predict_prob
        update_fn = estimator.observe

    preds = compute_predictions(rows, predict_fn, update_fn)

    calibration = evaluate_calibration(preds)
    pnl_table = pnl_sensitivity(preds, args.assumed_prices, args.edge_threshold,
                                 args.fee_pct, args.stake)

    print("\n=== Calibration / skill (real data, no price assumption) ===")
    print(json.dumps(calibration, indent=2, default=float))
    print("\n=== P&L sensitivity across ASSUMED Kalshi YES prices (not observed) ===")
    print(json.dumps(pnl_table, indent=2, default=float))


if __name__ == "__main__":
    main()
