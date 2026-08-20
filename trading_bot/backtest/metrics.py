"""Performance metrics for equity curves."""
from __future__ import annotations

import numpy as np
import pandas as pd


def cagr(equity: pd.Series, periods_per_year: float) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    n_periods = len(equity) - 1
    total_return = equity.iloc[-1] / equity.iloc[0]
    years = n_periods / periods_per_year
    if years <= 0:
        return 0.0
    return total_return ** (1 / years) - 1


def sharpe_ratio(returns: pd.Series, periods_per_year: float, risk_free: float = 0.0) -> float:
    excess = returns - risk_free / periods_per_year
    std = excess.std()
    if std == 0 or np.isnan(std):
        return 0.0
    return (excess.mean() / std) * np.sqrt(periods_per_year)


def max_drawdown(equity: pd.Series) -> float:
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    return drawdown.min()


def win_rate(trade_pnls: pd.Series) -> float:
    if len(trade_pnls) == 0:
        return 0.0
    return (trade_pnls > 0).mean()


def summarize(equity: pd.Series, returns: pd.Series, trade_pnls: pd.Series,
              periods_per_year: float) -> dict:
    return {
        "total_return_pct": (equity.iloc[-1] / equity.iloc[0] - 1) * 100 if len(equity) else 0.0,
        "cagr_pct": cagr(equity, periods_per_year) * 100,
        "sharpe": sharpe_ratio(returns, periods_per_year),
        "max_drawdown_pct": max_drawdown(equity) * 100,
        "num_trades": int(len(trade_pnls)),
        "win_rate_pct": win_rate(trade_pnls) * 100,
        "final_equity": float(equity.iloc[-1]) if len(equity) else 0.0,
    }
