"""Event-based backtest engine for Kalshi binary (YES/NO) markets.

Each resolved market contributes at most one trade: enter at the strategy's
signal price (cut off before resolution to avoid lookahead), hold to
settlement, realize a payoff of (1 - price) if correct or (0 - price) if
wrong (mirrored for 'NO' side), minus Kalshi's real taker fee formula.
"""
from __future__ import annotations

from typing import Callable, Iterable

import pandas as pd

from . import metrics
from ..data.kalshi_fetcher import kalshi_taker_fee


def run_kalshi_backtest(markets: Iterable[dict], get_history: Callable[[dict], list],
                         strategy_fn: Callable[[list], dict | None],
                         stake_per_trade: float = 100.0,
                         initial_capital: float = 10_000.0) -> dict:
    """
    markets:      iterable of settled-market dicts from kalshi_fetcher.fetch_settled_markets
    get_history:  fn(market) -> price history list (cut off before resolution)
    strategy_fn:  fn(history) -> {'t', 'price', 'side'} or None
    stake_per_trade: fixed $ risked per trade (flat sizing, simplest reasonable default)
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
        resolved_yes = market["result"] == "yes"

        if entry_price <= 0 or entry_price >= 1:
            continue

        if side == "YES":
            won = resolved_yes
            payoff_per_contract = (1.0 - entry_price) if won else (0.0 - entry_price)
            fee_price = entry_price
        else:  # NO
            no_entry_price = 1.0 - entry_price
            won = not resolved_yes
            payoff_per_contract = (1.0 - no_entry_price) if won else (0.0 - no_entry_price)
            fee_price = no_entry_price

        contracts = stake_per_trade / entry_price
        gross_pnl = payoff_per_contract * contracts
        fee = kalshi_taker_fee(contracts, fee_price)
        net_pnl = gross_pnl - fee

        equity += net_pnl
        equity_points.append(equity)
        trade_pnls.append(net_pnl)

    equity_series = pd.Series(equity_points)
    trade_pnl_series = pd.Series(trade_pnls)

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
