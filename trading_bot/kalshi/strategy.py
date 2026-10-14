"""Late-window favorite strategy for Kalshi's 15-minute BTC up/down markets.

Rule: in the final `window_seconds` (default 150s = 2:30) before a market
closes, if YES costs >= `threshold` (default $0.80) buy YES; else if NO costs
>= `threshold` buy NO. At most one entry per market.

"Costs" means the ask you would actually pay: YES ask for YES, and
NO ask = 1 - YES bid for NO.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


@dataclass
class StrategyConfig:
    threshold: float = 0.80       # enter when a side's ask is at least this
    max_price: float = 0.99       # never pay more than this per contract
    window_seconds: float = 150   # only trade in the last 2:30
    min_seconds_left: float = 3   # too close to the bell to get a fill
    stake_dollars: float = 10.0   # dollars per trade (used when stake_pct is None)
    stake_pct: float | None = None  # or: this fraction of current balance per trade
    max_stake: float | None = None  # hard cap on dollars per trade
    slippage: float = 0.01        # pay up to this much above the ask (capped at max_price)


@dataclass
class Signal:
    side: str          # "yes" or "no"
    ask: float         # the side's current ask
    limit: float       # worst price we'll accept for that side


def decide(yes_bid: float | None, yes_ask: float | None, seconds_left: float,
           cfg: StrategyConfig) -> Signal | None:
    if not (cfg.min_seconds_left <= seconds_left <= cfg.window_seconds):
        return None
    candidates = []
    if yes_ask is not None and 0 < yes_ask < 1:
        candidates.append(("yes", yes_ask))
    if yes_bid is not None and 0 < yes_bid < 1:
        candidates.append(("no", round(1 - yes_bid, 6)))
    for side, ask in candidates:
        if cfg.threshold <= ask <= cfg.max_price:
            return Signal(side=side, ask=ask, limit=min(ask + cfg.slippage, cfg.max_price))
    return None


def taker_fee(contracts: float, price: float) -> float:
    """Kalshi's general taker fee: 0.07 * C * P * (1 - P), rounded up to the cent."""
    return math.ceil(0.07 * contracts * price * (1 - price) * 100 - 1e-9) / 100


def stake_for(balance: float | None, cfg: StrategyConfig) -> float:
    """Dollars to risk on the next trade: a fixed amount, or a % of balance, capped."""
    if cfg.stake_pct is not None and balance is not None:
        stake = balance * cfg.stake_pct
    else:
        stake = cfg.stake_dollars
    if cfg.max_stake is not None:
        stake = min(stake, cfg.max_stake)
    if balance is not None:
        stake = min(stake, balance)
    return max(stake, 0.0)


def contracts_for(stake_dollars: float, price: float) -> int:
    return int(stake_dollars // price) if price > 0 else 0


def _tick_at(price: Decimal, price_ranges: list[dict] | None) -> Decimal:
    for r in price_ranges or []:
        if Decimal(r["start"]) <= price < Decimal(r["end"]):
            return Decimal(r["step"])
    return Decimal("0.01")


def to_tick(price: float, price_ranges: list[dict] | None, up: bool) -> str:
    """Snap a YES price to the market's tick grid; returns a 4-dp dollar string."""
    p = Decimal(str(price))
    step = _tick_at(p, price_ranges)
    n = (p / step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return str((n * step).quantize(Decimal("0.0001")))


def order_params(signal: Signal, price_ranges: list[dict] | None) -> tuple[str, str]:
    """Translate a signal into a V2 (book_side, yes_price) pair.

    Buying YES at <= L  -> bid at L (rounded down).
    Buying NO at <= L   -> sell YES at >= 1 - L (rounded up), which caps NO's cost at L.
    """
    if signal.side == "yes":
        return "bid", to_tick(signal.limit, price_ranges, up=False)
    return "ask", to_tick(1 - signal.limit, price_ranges, up=True)
