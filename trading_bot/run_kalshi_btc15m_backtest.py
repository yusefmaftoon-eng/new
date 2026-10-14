"""Backtest the late-window favorite rule on settled Kalshi 15-min BTC markets.

Uses Kalshi's public 1-minute candlesticks (no API key needed). Because the
candles are 1-minute, the "last 2:30" window is approximated by checking the
quote at T-2:00 and T-1:00 (the closes of the last full candles before the
bell). The first check where a side's ask is in [threshold, max_price] is the
entry, filled at that ask (no slippage modelled), plus Kalshi's taker fee.

Trades are replayed oldest-first against a starting --bankroll, so you can see
what percent-of-balance sizing (--stake-pct) does to drawdowns.

    python -m trading_bot.run_kalshi_btc15m_backtest --max-markets 1500 --threshold 0.70 \\
        --bankroll 100 --stake-pct 0.02
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

from trading_bot.kalshi.client import PROD_BASE_URL, KalshiClient
from trading_bot.kalshi.strategy import (StrategyConfig, contracts_for, decide, stake_for,
                                         taker_fee)


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_markets(client: KalshiClient, series: str, max_markets: int, cache: str | None,
                 sleep: float) -> list[dict]:
    """Settled markets with the quotes at T-2:00 and T-1:00, oldest first."""
    if cache and os.path.exists(cache):
        with open(cache) as f:
            data = json.load(f)
        if data.get("series") == series and data.get("requested", 0) >= max_markets:
            return data["rows"][-max_markets:]
    rows = []
    for m in client.iter_markets(max_markets=max_markets, series_ticker=series, status="settled"):
        if m.get("result") not in ("yes", "no"):
            continue
        open_ts, close_ts = _ts(m["open_time"]), _ts(m["close_time"])
        try:
            candles = client.get_candlesticks(series, m["ticker"], open_ts, close_ts)
        except Exception as e:  # noqa: BLE001 - skip a bad market, keep the run going
            print(f"  skip {m['ticker']}: {e}")
            continue
        time.sleep(sleep)
        by_end = {c["end_period_ts"]: c for c in candles}
        quotes = {}
        for secs_left in (120, 60):
            c = by_end.get(close_ts - secs_left)
            if c:
                quotes[secs_left] = (_f(c.get("yes_bid", {}).get("close_dollars")),
                                     _f(c.get("yes_ask", {}).get("close_dollars")))
        rows.append({"ticker": m["ticker"], "close_ts": close_ts, "result": m["result"],
                     "quotes": {str(k): v for k, v in quotes.items()}})
        if len(rows) % 100 == 0:
            print(f"  downloaded {len(rows)} markets")
    rows.sort(key=lambda r: r["close_ts"])
    if cache:
        with open(cache, "w") as f:
            json.dump({"series": series, "requested": max_markets, "rows": rows}, f)
    return rows


def run(rows: list[dict], cfg: StrategyConfig, bankroll: float | None) -> tuple[list[dict], dict]:
    trades = []
    balance = bankroll
    peak = bankroll or 0.0
    max_dd = 0.0
    for r in rows:
        sig = None
        for secs_left in (120, 60):
            q = r["quotes"].get(str(secs_left))
            if q:
                sig = decide(q[0], q[1], secs_left, cfg)
                if sig:
                    break
        if not sig:
            continue
        n = contracts_for(stake_for(balance, cfg), sig.ask)
        if n == 0:
            continue
        cost = n * sig.ask + taker_fee(n, sig.ask)
        if balance is not None and cost > balance:
            continue
        payout = n if sig.side == r["result"] else 0.0
        pnl = payout - cost
        trades.append({"ticker": r["ticker"], "price": sig.ask, "won": sig.side == r["result"],
                       "cost": cost, "pnl": pnl})
        if balance is not None:
            balance += pnl
            peak = max(peak, balance)
            max_dd = max(max_dd, (peak - balance) / peak if peak else 0.0)
    return trades, {"final": balance, "max_dd": max_dd}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--series", default="KXBTC15M")
    p.add_argument("--max-markets", type=int, default=1500)
    p.add_argument("--threshold", type=float, default=0.80)
    p.add_argument("--max-price", type=float, default=0.99)
    p.add_argument("--stake", type=float, default=10.0, help="fixed dollars per trade")
    p.add_argument("--bankroll", type=float, default=None, help="starting balance to simulate")
    p.add_argument("--stake-pct", type=float, default=None, help="fraction of balance per trade, e.g. 0.02")
    p.add_argument("--max-stake", type=float, default=None)
    p.add_argument("--cache", default="kalshi_btc15m_cache.json",
                   help="where to cache downloaded candles ('' to disable)")
    p.add_argument("--base-url", default=PROD_BASE_URL)
    p.add_argument("--sleep", type=float, default=0.15, help="pause between API calls (rate limits)")
    args = p.parse_args()
    if args.stake_pct is not None and args.bankroll is None:
        p.error("--stake-pct needs --bankroll")

    rows = load_markets(KalshiClient(args.base_url), args.series, args.max_markets,
                        args.cache or None, args.sleep)
    cfg = StrategyConfig(threshold=args.threshold, max_price=args.max_price,
                         stake_dollars=args.stake, stake_pct=args.stake_pct,
                         max_stake=args.max_stake, slippage=0.0)
    trades, acct = run(rows, cfg, args.bankroll)

    span = ""
    if rows:
        fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
        span = f" ({fmt(rows[0]['close_ts'])} to {fmt(rows[-1]['close_ts'])})"
    print(f"\nMarkets: {len(rows)}{span}   trades taken: {len(trades)}")
    if not trades:
        return
    _report("ALL", trades)
    edges = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00]
    for lo, hi in zip(edges, edges[1:]):
        sub = [t for t in trades if lo <= t["price"] < hi]
        if sub:
            _report(f"{lo:.2f}-{hi:.2f}", sub)
    if args.bankroll is not None:
        print(f"\nBankroll ${args.bankroll:.2f} -> ${acct['final']:.2f}   "
              f"max drawdown {acct['max_dd']:.1%}")
    print("\nA bucket only makes money if its win rate beats its avg price (plus fees).")


def _report(label: str, trades: list[dict]):
    n = len(trades)
    wins = sum(t["won"] for t in trades)
    staked = sum(t["cost"] for t in trades)
    pnl = sum(t["pnl"] for t in trades)
    avg_px = sum(t["price"] for t in trades) / n
    streak = worst = 0
    for t in trades:
        streak = 0 if t["won"] else streak + 1
        worst = max(worst, streak)
    print(f"  {label:>10}: n={n:4d}  win rate={wins / n:6.1%}  avg price={avg_px:.3f}  "
          f"P&L=${pnl:+9.2f}  return on staked={pnl / staked:+6.2%}  worst losing streak={worst}")


if __name__ == "__main__":
    main()
