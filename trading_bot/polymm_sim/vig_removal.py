"""
Vig Removal Methods for Sports Betting Odds.

Copied verbatim from kachence/polymm (MIT licensed), commit f598cf8,
src/core/vig_removal.py — https://github.com/kachence/polymm
This is real, unmodified strategy code: the same de-vig math the bot used
to turn bookmaker odds into a fair probability before comparing it to the
Polymarket price. Reused here so the simulation exercises the actual
pricing logic rather than a re-implementation of it.

This module provides methods to remove the bookmaker's margin (vig)
from implied probabilities to derive true fair probabilities.

Methods implemented:
- Raw: No vig removal (includes vig, conservative)
- Proportional: Scales probabilities proportionally (simple, default)
"""
from typing import Tuple


def raw_probabilities(odds1: float, odds2: float) -> Tuple[float, float]:
    """
    Return raw implied probabilities without any vig removal.

    This is conservative - probabilities will sum to >100%.
    Use when you want to be cautious about edge estimates.
    """
    if odds1 <= 1 or odds2 <= 1:
        return 0.0, 0.0

    implied1 = 1 / odds1
    implied2 = 1 / odds2
    return implied1, implied2


def proportional_probabilities(odds1: float, odds2: float) -> Tuple[float, float]:
    """
    Remove vig proportionally (basic normalization).

    Each probability is scaled by the same factor.
    Simple but tends to over-correct favorites.

    Formula: p_true = p_implied / sum(p_implied)
    """
    if odds1 <= 1 or odds2 <= 1:
        return 0.0, 0.0

    implied1 = 1 / odds1
    implied2 = 1 / odds2
    total = implied1 + implied2

    return implied1 / total, implied2 / total


def calculate_fair_odds(odds1: float, odds2: float) -> dict:
    """
    Calculate fair probabilities from 2-way decimal odds via proportional
    vig removal.

    Returns:
        Dictionary with fair_prob1, fair_prob2 (as percentages),
        fair_odds1, fair_odds2, and vig. Returns {} for invalid odds
        (odds <= 1 on either side).
    """
    if odds1 <= 1 or odds2 <= 1:
        return {}

    implied1 = 1 / odds1
    implied2 = 1 / odds2
    overround = implied1 + implied2
    vig = (overround - 1) * 100

    fair1, fair2 = proportional_probabilities(odds1, odds2)

    # Calculate fair odds (inverse of fair probability)
    fair_odds1 = 1 / fair1 if fair1 > 0 else 0
    fair_odds2 = 1 / fair2 if fair2 > 0 else 0

    return {
        'fair_prob1': round(fair1 * 100, 1),
        'fair_prob2': round(fair2 * 100, 1),
        'fair_odds1': round(fair_odds1, 2),
        'fair_odds2': round(fair_odds2, 2),
        'vig': round(vig, 1)
    }
