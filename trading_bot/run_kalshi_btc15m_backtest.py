"""Backtest the late-window favorite rule on settled Kalshi 15-min BTC markets.

Uses Kalshi's public 1-minute candlesticks (no API key needed). Because the
candles are 1-minute, the "last 2:30" window is approximated by checking the
quote at T-2:00 and T-1:00 (the closes of the last full candles before the
bell). The first check where a side's ask is in [threshold, max_price] is the
entry, filled at that ask (no slippage modelled), plus Kalshi's taker fee.

    python -m trading_bot.run_kalshi_btc15m_backtest --max-markets 500
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime

from trading_bot.kalshi.client import PROD_BASE_URL, KalshiClient
from trading_bot.kalshi.strategy import StrategyConfig, contracts_for, decide, taker_fee


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def simulate_market(candles: list[dict], close_ts: int, cfg: StrategyConfig):
    by_end = {c["end_period_ts"]: c for c in candles}
    for secs_left in (120, 60):
        c = by_end.get(close_ts - secs_left)
        if not c:
            continue
        yes_bid = _f(c.get("yes_bid", {}).get("close_dollars"))
        yes_ask = _f(c.get("yes_ask", {}).get("close_dollars"))
        sig = decide(yes_bid, yes_ask, secs_left, cfg)
        if sig:
            return sig, secs_left
    return None, None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--series", default="KXBTC15M")
    p.add_argument("--max-markets", type=int, default=500)
    p.add_argument("--threshold", type=float, default=0.80)
    p.add_argument("--max-price", type=float, default=0.99)
    p.add_argument("--stake", type=float, default=10.0)
    p.add_argument("--base-url", default=PROD_BASE_URL)
    p.add_argument("--sleep", type=float, default=0.08, help="pause between API calls (rate limits)")
    args = p.parse_args()

    cfg = StrategyConfig(threshold=args.threshold, max_price=args.max_price,
                         stake_dollars=args.stake, slippage=0.0)
    client = KalshiClient(args.base_url)

    trades = []
    seen = 0
    for m in client.iter_markets(max_markets=args.max_markets, series_ticker=args.series,
                                 status="settled"):
        result = m.get("result")
        if result not in ("yes", "no"):
            continue
        seen += 1
        open_ts, close_ts = _ts(m["open_time"]), _ts(m["close_time"])
        try:
            candles = client.get_candlesticks(args.series, m["ticker"], open_ts, close_ts)
        except Exception as e:  # noqa: BLE001 - skip a bad market, keep the run going
            print(f"  skip {m['ticker']}: {e}")
            continue
        time.sleep(args.sleep)
        sig, secs_left = simulate_market(candles, close_ts, cfg)
        if not sig:
            continue
        n = contracts_for(cfg.stake_dollars, sig.ask)
        if n == 0:
            continue
        cost = n * sig.ask + taker_fee(n, sig.ask)
        payout = n if sig.side == result else 0.0
        trades.append({"ticker": m["ticker"], "side": sig.side, "price": sig.ask,
                       "secs_left": secs_left, "won": sig.side == result,
                       "cost": cost, "pnl": payout - cost})
        if seen % 50 == 0:
            print(f"  scanned {seen} markets, {len(trades)} trades so far")

    print(f"\nMarkets scanned: {seen}   trades taken: {len(trades)}")
    if not trades:
        return
    _report("ALL", trades)
    buckets = [(0.80, 0.85), (0.85, 0.90), (0.90, 0.95), (0.95, 1.00)]
    for lo, hi in buckets:
        sub = [t for t in trades if lo <= t["price"] < hi]
        if sub:
            _report(f"{lo:.2f}-{hi:.2f}", sub)
    print("\nA bucket only makes money if its win rate beats its avg price (plus fees).")


def _report(label: str, trades: list[dict]):
    n = len(trades)
    wins = sum(t["won"] for t in trades)
    staked = sum(t["cost"] for t in trades)
    pnl = sum(t["pnl"] for t in trades)
    avg_px = sum(t["price"] for t in trades) / n
    print(f"  {label:>10}: n={n:4d}  win rate={wins / n:6.1%}  avg price={avg_px:.3f}  "
          f"P&L=${pnl:+9.2f}  return on staked={pnl / staked:+6.2%}")


if __name__ == "__main__":
    main()
