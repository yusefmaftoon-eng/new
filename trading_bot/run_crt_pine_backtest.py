#!/usr/bin/env python3
"""Run the exact strategy from crt_a_plus_mes.pine against real MES/MNQ data.

A faithful Python port -- not the earlier, simpler crt_strategy.py guess.
See strategies/crt_pine_strategy.py and backtest/crt_pine_engine.py for the
rules (MSS confirmation, FVG-after-MSS, London Open + Silver Bullet
killzones, real $-risk position sizing, commission + slippage).

Example:
    python -m trading_bot.run_crt_pine_backtest --symbol MES
"""
from __future__ import annotations

import argparse
import json

from trading_bot.data.futures_fetcher import fetch_micro_future
from trading_bot.backtest.crt_pine_engine import run_crt_pine_backtest, summarize_trades

DOLLARS_PER_POINT = {"MES": 5.0, "MNQ": 2.0}


def resample_1h(df):
    return df.resample("1h").agg({"open": "first", "high": "max", "low": "min",
                                   "close": "last", "volume": "sum"}).dropna()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="MES", choices=["MES", "MNQ", "both"])
    parser.add_argument("--range", default="60d")
    parser.add_argument("--risk-per-trade", type=float, default=200.0)
    parser.add_argument("--max-contracts", type=int, default=20)
    parser.add_argument("--commission", type=float, default=0.47, help="$/contract/side")
    parser.add_argument("--slippage-ticks", type=float, default=1.0)
    parser.add_argument("--no-london", action="store_true")
    parser.add_argument("--no-silver-bullet", action="store_true")
    args = parser.parse_args()

    symbols = ["MES", "MNQ"] if args.symbol == "both" else [args.symbol]
    print(f"Fetching {', '.join(symbols)} 5m bars from Yahoo Finance (range={args.range})...")
    for sym in symbols:
        df_5m = fetch_micro_future(sym, "5m", args.range)
        df_1h = resample_1h(df_5m)
        print(f"  {sym}: {len(df_5m)} bars, {df_5m.index[0]} .. {df_5m.index[-1]}")

        trades = run_crt_pine_backtest(
            sym, df_5m, df_1h, dollars_per_point=DOLLARS_PER_POINT[sym],
            risk_per_trade=args.risk_per_trade, max_contracts=args.max_contracts,
            commission_per_contract_per_side=args.commission, slippage_ticks=args.slippage_ticks,
            use_london=not args.no_london, use_silver_bullet=not args.no_silver_bullet,
        )
        report = summarize_trades(trades)
        print(f"\n=== {sym} (crt_a_plus, ${args.risk_per_trade:.0f} risk/trade) ===")
        print(json.dumps(report, indent=2, default=float))
        for t in trades:
            print(f"  {t['entry_time'].date()} {t['dir']:5s} x{t['contracts']:<2d} entry={t['entry']:.2f} "
                  f"stop={t['stop']:.2f} target={t['target']:.2f} exit={t['exit']:.2f} ({t['reason']}) "
                  f"pnl=${t['pnl']:.2f} (gross ${t['gross_pnl']:.2f} - ${t['commission']:.2f} commission)")


if __name__ == "__main__":
    main()
