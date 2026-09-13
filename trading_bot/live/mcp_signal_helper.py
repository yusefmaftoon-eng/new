"""Signal helper for Robinhood's official Agentic Trading MCP integration.

This module does NOT connect to Robinhood, hold credentials, or place any
order. Robinhood now offers an official, OAuth-based agentic trading path
(see the README's "Robinhood live trading" section) where you connect an
agent to `https://agent.robinhood.com/mcp/trading` and it trades inside a
dedicated, isolated Agentic account using Robinhood's own tools.

That connection has to be made by you, in your own agent session, from a
desktop device with your Robinhood mobile app for verification -- it can't
be done on your behalf from this repo. Once it's connected, use `decide()`
here to turn OHLCV data (fetched however you like -- the MCP's own
market-data tool, this repo's Binance fetcher, etc.) into a position
decision using the same strategies as the crypto backtest, then have the
agent call Robinhood's MCP order tool to act on it. This keeps "decide what
to do" (this repo, backtested) and "actually move money" (Robinhood's
supported, account-isolated MCP path) cleanly separate.
"""
from __future__ import annotations

import pandas as pd

from trading_bot.strategies.crypto_strategy import STRATEGIES


def decide(df: pd.DataFrame, current_quantity: float, target_quantity: float,
           strategy: str = "sma_crossover", **strategy_kwargs) -> dict:
    """
    df:               OHLCV data with a 'close' column, DatetimeIndex.
    current_quantity: shares currently held, e.g. from the MCP's position tool.
    target_quantity:  shares to hold when the strategy signals LONG (0 when FLAT).
    strategy:         one of trading_bot.strategies.crypto_strategy.STRATEGIES.

    Returns {'signal': 'long'|'flat', 'side': 'buy'|'sell'|'hold',
             'quantity': float, 'rationale': str}. Nothing here places an
    order -- the caller decides whether/how to act on it.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}, choose from {list(STRATEGIES)}")

    position = STRATEGIES[strategy](df, **strategy_kwargs)
    signal = "long" if position.iloc[-1] > 0 else "flat"
    desired_quantity = target_quantity if signal == "long" else 0.0
    delta = desired_quantity - current_quantity

    if abs(delta) < 1e-9:
        return {
            "signal": signal, "side": "hold", "quantity": 0.0,
            "rationale": f"{strategy} signals {signal}; already at target position "
                          f"({current_quantity}).",
        }

    side = "buy" if delta > 0 else "sell"
    return {
        "signal": signal, "side": side, "quantity": abs(delta),
        "rationale": f"{strategy} signals {signal}; move from {current_quantity} to "
                      f"{desired_quantity} shares.",
    }
