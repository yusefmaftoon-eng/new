"""Event-based backtest engine for Polymarket binary (YES/NO) markets.

Each resolved market contributes at most one trade: enter at the strategy's
signal price, hold to resolution, realize a payoff of (1 - price) if correct
or (0 - price) if wrong (mirrored for 'NO' side), minus a flat fee.
"""
from __future__ import annotations

from typing import Callable, Iterable

import pandas as pd

from . import metrics


def run_polymarket_backtest(markets: Iterable[dict], get_history: Callable[[dict], list],
                             strategy_fn: Callable[[list], dict | None],
                             stake_per_trade: float = 100.0, fee_pct: float = 0.02,
                             initial_capital: float = 10_000.0) -> dict:
    """
    markets:      iterable of resolved-market dicts from polymarket_fetcher.fetch_resolved_markets
    get_history:  fn(market) -> price history list for that market's YES token
    strategy_fn:  fn(history) -> {'t', 'price', 'side'} or None
    stake_per_trade: fixed $ risked per trade (flat sizing, simplest reasonable default)
    fee_pct:      Polymarket effectively charges via spread/gas; approximate as a
                  flat % of stake, tune to taste.
    """
    equity = initial_capital
    equity_points = [equity]
    trade_pnls = []

    for market in markets:
        history = get_history(market)
        if not history:
            continue
        signal = strategy_fn(history)
        if signal is None:
            continue

        entry_price = signal["price"]
        side = signal["side"]
        resolved_yes = market["final_yes_price"] >= 0.5

        if side == "YES":
            won = resolved_yes
            payoff_per_share = (1.0 - entry_price) if won else (0.0 - entry_price)
        else:  # NO
            no_entry_price = 1.0 - entry_price
            won = not resolved_yes
            payoff_per_share = (1.0 - no_entry_price) if won else (0.0 - no_entry_price)

        shares = stake_per_trade / entry_price if entry_price > 0 else 0
        gross_pnl = payoff_per_share * shares
        fee = fee_pct * stake_per_trade
        net_pnl = gross_pnl - fee

        equity += net_pnl
        equity_points.append(equity)
        trade_pnls.append(net_pnl)

    equity_series = pd.Series(equity_points)
    trade_pnl_series = pd.Series(trade_pnls)

    # Time-series metrics like CAGR/Sharpe don't map cleanly onto discrete,
    # resolution-driven trades of varying duration, so report trade-level stats instead.
    report = {
        "num_trades": int(len(trade_pnl_series)),
        "win_rate_pct": float(metrics.win_rate(trade_pnl_series) * 100),
        "total_pnl": float(trade_pnl_series.sum()),
        "roi_pct": (equity_series.iloc[-1] / initial_capital - 1) * 100,
        "max_drawdown_pct": float(metrics.max_drawdown(equity_series) * 100),
        "avg_pnl_per_trade": float(trade_pnl_series.mean()) if len(trade_pnl_series) else 0.0,
        "final_equity": float(equity_series.iloc[-1]),
    }
    report["equity_curve"] = equity_series
    return report
