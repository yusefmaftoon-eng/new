"""Vectorized long/flat backtest engine for a single time series (e.g. crypto OHLCV)."""
from __future__ import annotations

import pandas as pd

from . import metrics


def run_backtest(df: pd.DataFrame, position: pd.Series, initial_capital: float = 10_000.0,
                  fee_bps: float = 10.0, slippage_bps: float = 5.0) -> dict:
    """
    df:       must contain a 'close' column, DatetimeIndex.
    position: target position in {0, 1} (or any float weight), same index as df.
    fee_bps / slippage_bps: round-trip cost applied on each position CHANGE, in basis points
                             of notional (Binance spot taker fee is ~10 bps).
    """
    position = position.reindex(df.index).fillna(0)
    price_returns = df["close"].pct_change().fillna(0)

    # Position held during period t earns the return realized over that period,
    # using yesterday's (already-decided) position to avoid lookahead.
    held_position = position.shift(1).fillna(0)
    strategy_returns = held_position * price_returns

    position_change = position.diff().abs().fillna(position.abs())
    cost_rate = (fee_bps + slippage_bps) / 10_000.0
    costs = position_change * cost_rate
    net_returns = strategy_returns - costs

    equity = (1 + net_returns).cumprod() * initial_capital

    # Individual trade P&L: from each entry (0->1) to the following exit (1->0).
    trade_pnls = []
    entry_equity = None
    for i in range(len(position)):
        if position.iloc[i] != 0 and entry_equity is None:
            entry_equity = equity.iloc[i - 1] if i > 0 else initial_capital
        elif position.iloc[i] == 0 and entry_equity is not None:
            trade_pnls.append(equity.iloc[i] - entry_equity)
            entry_equity = None
    if entry_equity is not None:
        trade_pnls.append(equity.iloc[-1] - entry_equity)

    return {
        "equity": equity,
        "returns": net_returns,
        "trade_pnls": pd.Series(trade_pnls),
    }


def backtest_report(df: pd.DataFrame, position: pd.Series, periods_per_year: float,
                     initial_capital: float = 10_000.0, fee_bps: float = 10.0,
                     slippage_bps: float = 5.0) -> dict:
    result = run_backtest(df, position, initial_capital, fee_bps, slippage_bps)
    report = metrics.summarize(result["equity"], result["returns"], result["trade_pnls"],
                                periods_per_year)
    report["equity_curve"] = result["equity"]

    # Baseline for comparison: naive buy-and-hold over the same window.
    bh_returns = df["close"].pct_change().fillna(0)
    bh_equity = (1 + bh_returns).cumprod() * initial_capital
    report["buy_and_hold_return_pct"] = (bh_equity.iloc[-1] / bh_equity.iloc[0] - 1) * 100
    return report
