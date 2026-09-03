"""Backtest for a Kalshi-style daily binary market ('does [asset] close above
its prior close'), built directly on REAL historical closes so the market
outcomes are genuine, not simulated.

What's real vs. assumed, explicitly:
  - The underlying price series and the YES/NO outcome of every market: REAL
    historical data (see data/coinmetrics_fetcher.py, data/gold_fetcher.py).
  - The model's predicted probability each day: REAL, computed causally
    (strategies/kalshi_directional_strategy.py never sees future closes).
  - The price you'd actually pay Kalshi for the contract: NOT observed --
    this environment cannot reach Kalshi's API (see README), so no genuine
    historical Kalshi quote exists to backtest against. This module reports
    two kinds of numbers to keep that assumption from hiding in one misleading
    P&L figure:
      1. Calibration/skill metrics (Brier score, log loss, hit-rate by
         probability decile) that do NOT depend on any assumed market price
         -- these say whether the model's probabilities are honest, full
         stop.
      2. A P&L sensitivity table across a range of ASSUMED flat Kalshi entry
         prices, clearly labeled as assumed.
"""
from __future__ import annotations

import math
from typing import Callable


def build_daily_outcomes(prices: list[dict]) -> list[dict]:
    """prices: [{'date','close'}, ...] ascending. Returns one row per day
    from prices[1:] with the real 'up' outcome vs the prior close."""
    rows = []
    for i in range(1, len(prices)):
        rows.append({
            "date": prices[i]["date"],
            "closes": [p["close"] for p in prices[: i + 1]],  # causal history incl. today
            "prior_close": prices[i - 1]["close"],
            "close": prices[i]["close"],
            "up": prices[i]["close"] > prices[i - 1]["close"],
        })
    return rows


def compute_predictions(rows: list[dict], predict_fn: Callable[[list[float]], float | None],
                         update_fn: Callable[[list[float], bool], None] | None = None) -> list[dict]:
    """Single causal pass over history: predict_fn only ever sees
    row['closes'][:-1] (yesterday and earlier), never today's close or
    outcome. update_fn (for stateful estimators like SmaStateHitRateEstimator)
    is called AFTER the prediction, with the real outcome, so state updates
    can't leak into the same day's prediction. Called exactly once per row,
    so this is the only place predict_fn/update_fn run -- both
    evaluate_calibration and pnl_sensitivity consume the result instead of
    re-invoking predict_fn, which would double-count observations for a
    stateful estimator.
    """
    preds = []
    for row in rows:
        closes_before = row["closes"][:-1]
        p = predict_fn(closes_before)
        preds.append({**row, "prob": p})
        if update_fn is not None:
            update_fn(closes_before, row["up"])
    return preds


def evaluate_calibration(preds: list[dict]) -> dict:
    """preds: output of compute_predictions."""
    probs, outcomes = [], []
    for row in preds:
        p = row["prob"]
        if p is None:
            continue
        probs.append(p)
        outcomes.append(1.0 if row["up"] else 0.0)

    n = len(probs)
    if n == 0:
        return {"n": 0}

    brier = sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n
    eps = 1e-9
    log_loss = -sum(o * math.log(max(p, eps)) + (1 - o) * math.log(max(1 - p, eps))
                     for p, o in zip(probs, outcomes)) / n
    base_rate = sum(outcomes) / n

    # Decile calibration: within each predicted-probability bucket, does the
    # realized up-rate actually match the predicted probability?
    buckets: dict[int, list[float]] = {i: [] for i in range(10)}
    for p, o in zip(probs, outcomes):
        idx = min(9, int(p * 10))
        buckets[idx].append(o)
    calibration_by_decile = {
        f"{i/10:.1f}-{(i+1)/10:.1f}": {
            "n": len(v),
            "predicted_mid": round((i + 0.5) / 10, 3),
            "realized_up_rate": round(sum(v) / len(v), 3) if v else None,
        }
        for i, v in buckets.items() if v
    }

    return {
        "n": n,
        "brier_score": brier,
        "brier_score_vs_naive_50_50": 0.25,
        "log_loss": log_loss,
        "base_rate_up": base_rate,
        "naive_log_loss_at_base_rate": -(base_rate * math.log(max(base_rate, eps))
                                          + (1 - base_rate) * math.log(max(1 - base_rate, eps))),
        "calibration_by_decile": calibration_by_decile,
    }


def pnl_sensitivity(preds: list[dict], assumed_prices: list[float], edge_threshold: float,
                     fee_pct: float, stake: float = 100.0) -> list[dict]:
    """preds: output of compute_predictions. For each assumed flat Kalshi YES
    price, trade only when the model's edge over that price exceeds
    edge_threshold, and settle against the real outcome. This is explicitly
    an assumption sweep, not a single number."""
    results = []
    for market_price in assumed_prices:
        pnl_total, wins, n_trades = 0.0, 0, 0
        for row in preds:
            p = row["prob"]
            if p is None:
                continue
            if p - market_price > edge_threshold:
                entry = market_price
                won = row["up"]
            elif market_price - p > edge_threshold:
                entry = 1 - market_price
                won = not row["up"]
            else:
                continue
            shares = stake / entry if entry > 0 else 0
            payoff = (1.0 - entry) if won else (0.0 - entry)
            fee = fee_pct * stake
            pnl = payoff * shares - fee
            pnl_total += pnl
            n_trades += 1
            wins += 1 if won else 0
        results.append({
            "assumed_kalshi_yes_price": market_price,
            "num_trades": n_trades,
            "win_rate_pct": round(100 * wins / n_trades, 2) if n_trades else None,
            "total_pnl": round(pnl_total, 2),
            "roi_pct_on_staked": round(100 * pnl_total / (stake * n_trades), 2) if n_trades else None,
        })
    return results
