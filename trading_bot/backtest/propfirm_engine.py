"""Prop-firm-style evaluation overlay on top of the standard time-series engine.

Simulates the pass/fail rules most funded-account challenges use (FTMO-style
defaults given below): a hard max-daily-loss limit, a hard max-total-drawdown
limit, and a profit target. The account is marked as breached the first day
either loss limit is crossed, and marked as passed if it reaches the profit
target before that happens.

Honest caveat: with only daily-close data (no intraday OHLC), "daily loss" is
approximated as the day's close-to-close return -- a real prop firm measures
intraday equity, which can breach a daily-loss limit and recover by the close.
This will systematically UNDER-count daily-loss breaches relative to real
intraday monitoring. Treat "passed" here as an upper bound, not a guarantee
a live funded account would survive.
"""
from __future__ import annotations

import pandas as pd

from . import metrics
from .engine import run_backtest


def evaluate_prop_firm_challenge(
    df: pd.DataFrame, position: pd.Series, periods_per_year: float,
    initial_capital: float = 100_000.0, leverage: float = 1.0,
    fee_bps: float = 2.0, slippage_bps: float = 2.0,
    max_daily_loss_pct: float = 5.0, max_total_drawdown_pct: float = 10.0,
    profit_target_pct: float = 10.0,
) -> dict:
    """
    leverage: scales the strategy's daily returns to simulate a levered/sized
              position, since real prop-firm accounts trade meaningful position
              sizes (unlevered daily FX/futures moves are usually too small to
              ever trip the daily-loss or profit-target thresholds below).
    """
    result = run_backtest(df, position, initial_capital=initial_capital,
                           fee_bps=fee_bps, slippage_bps=slippage_bps)
    raw_returns = result["returns"] * leverage
    equity = (1 + raw_returns).cumprod() * initial_capital

    running_peak = initial_capital
    breach_day = None
    breach_reason = None
    pass_day = None

    for i in range(len(equity)):
        day = equity.index[i]
        eq = equity.iloc[i]
        prev_eq = equity.iloc[i - 1] if i > 0 else initial_capital

        daily_loss_pct = (eq / prev_eq - 1) * 100
        if breach_day is None and daily_loss_pct <= -max_daily_loss_pct:
            breach_day, breach_reason = day, f"daily loss {daily_loss_pct:.2f}% <= -{max_daily_loss_pct}%"

        running_peak = max(running_peak, eq)
        drawdown_pct = (eq / running_peak - 1) * 100
        if breach_day is None and drawdown_pct <= -max_total_drawdown_pct:
            breach_day, breach_reason = day, f"total drawdown {drawdown_pct:.2f}% <= -{max_total_drawdown_pct}%"

        if breach_day is not None:
            break

        total_return_pct = (eq / initial_capital - 1) * 100
        if pass_day is None and total_return_pct >= profit_target_pct:
            pass_day = day

    passed = pass_day is not None and (breach_day is None or pass_day <= breach_day)

    report = metrics.summarize(equity, raw_returns, result["trade_pnls"], periods_per_year)
    report.update({
        "leverage": leverage,
        "max_daily_loss_pct_rule": max_daily_loss_pct,
        "max_total_drawdown_pct_rule": max_total_drawdown_pct,
        "profit_target_pct_rule": profit_target_pct,
        "challenge_passed": bool(passed),
        "breach_date": str(breach_day.date()) if breach_day is not None else None,
        "breach_reason": breach_reason,
        "profit_target_hit_date": str(pass_day.date()) if passed else None,
    })
    report["equity_curve"] = equity
    return report
