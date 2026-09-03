"""Causal (no-lookahead) daily up/down probability estimators.

Both signals answer the same question a Kalshi 'will [asset] close above
[prior close] on [date]' market asks, and both only ever use information
available strictly BEFORE the day being predicted -- required for the
backtest in backtest/kalshi_directional_sim.py to be honest.

- vol_model_prob: prices the day as a zero-drift digital/binary option using
  trailing realized volatility (standard quant finance: GBM under a
  real-world zero-drift assumption, not a documented edge from a paper --
  drift is deliberately left at 0 because estimating real-world drift from
  a short trailing window is a classic overfitting trap).
- sma_state_hit_rate: an online, expanding-window estimate of "how often has
  price closed up the day after being above/below its N-day SMA, so far in
  history" -- a real, verifiable statistic, not a documented academic bias
  (unlike the favorite-longshot signal used for Polymarket/Kalshi event
  markets elsewhere in this repo).
"""
from __future__ import annotations

import math

TRADING_DAYS_PER_YEAR = 365  # crypto trades every day; use 365 not 252


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def trailing_realized_vol(closes: list[float], window: int) -> float | None:
    """Annualized realized vol from the trailing `window` daily log returns."""
    if len(closes) < window + 1:
        return None
    tail = closes[-(window + 1):]
    log_returns = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    n = len(log_returns)
    mean = sum(log_returns) / n
    var = sum((r - mean) ** 2 for r in log_returns) / (n - 1) if n > 1 else 0.0
    daily_sigma = math.sqrt(var)
    return daily_sigma * math.sqrt(TRADING_DAYS_PER_YEAR)


def vol_model_prob(closes: list[float], window: int = 30, horizon_days: float = 1.0) -> float | None:
    """P(close_{t+horizon} > close_t) under zero-drift GBM, using only closes[:t+1]."""
    sigma = trailing_realized_vol(closes, window)
    if sigma is None or sigma <= 0:
        return None
    T = horizon_days / TRADING_DAYS_PER_YEAR
    # strike == current price -> ln(S0/K) == 0, so d2 reduces to -0.5*sigma*sqrt(T)
    d2 = -0.5 * sigma * math.sqrt(T)
    return _normal_cdf(d2)


class SmaStateHitRateEstimator:
    """Online estimator: P(up | price is above/below its N-day SMA), updated
    causally one day at a time so the backtest never uses future outcomes."""

    def __init__(self, sma_window: int = 20, prior_up: int = 1, prior_down: int = 1):
        self.sma_window = sma_window
        # Laplace/Beta(1,1) prior so early-history estimates aren't 0% or 100%
        # off a handful of observations.
        self.counts = {
            "above": {"up": prior_up, "down": prior_down},
            "below": {"up": prior_up, "down": prior_down},
        }

    def _state(self, closes: list[float]) -> str | None:
        if len(closes) < self.sma_window:
            return None
        sma = sum(closes[-self.sma_window:]) / self.sma_window
        return "above" if closes[-1] >= sma else "below"

    def predict_prob(self, closes: list[float]) -> float | None:
        state = self._state(closes)
        if state is None:
            return None
        c = self.counts[state]
        return c["up"] / (c["up"] + c["down"])

    def observe(self, closes_before: list[float], went_up: bool) -> None:
        state = self._state(closes_before)
        if state is None:
            return
        self.counts[state]["up" if went_up else "down"] += 1


STRATEGIES = {
    "vol_model": "vol_model_prob (GBM digital option, zero drift, trailing realized vol)",
    "sma_state": "sma_state_hit_rate (online-estimated hit rate conditioned on SMA state)",
}
