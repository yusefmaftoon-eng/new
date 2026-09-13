"""Live Robinhood brokerage client (real account, real money).

This is NOT a backtest sandbox: `place_order` can submit a real market order
against a real Robinhood account. Two independent things must both be true
before that happens:

1. The caller passes `dry_run=False` explicitly.
2. The environment variable ROBINHOOD_CONFIRM_LIVE_TRADING is set to "yes".

That confirmation is deliberately an environment variable and not a CLI flag
alone, so a real order can never be triggered by a copy-pasted command line
without the person running it having separately set up their shell for it.

Credentials (ROBINHOOD_USERNAME / ROBINHOOD_PASSWORD, and optionally
ROBINHOOD_TOTP_SECRET for automatic 2FA) are read from the environment only.
Never pass them as function arguments from a script that hardcodes them, and
never commit them anywhere.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    import robin_stocks.robinhood as rh
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "robin_stocks is required for live trading: pip install robin_stocks pyotp"
    ) from exc

ORDER_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "robinhood_orders.csv"
ORDER_LOG_FIELDS = ["time", "symbol", "side", "quantity", "order_type", "dry_run",
                     "status", "detail"]

VALID_INTERVALS = {"5minute", "10minute", "hour", "day", "week"}
VALID_SPANS = {"day", "week", "month", "3month", "year", "5year"}


def login(username: str | None = None, password: str | None = None,
          mfa_code: str | None = None, totp_secret: str | None = None):
    """Log in to Robinhood. Credentials default to environment variables."""
    username = username or os.environ.get("ROBINHOOD_USERNAME")
    password = password or os.environ.get("ROBINHOOD_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "Set ROBINHOOD_USERNAME and ROBINHOOD_PASSWORD environment variables "
            "before logging in. Do not pass credentials on the command line."
        )
    totp_secret = totp_secret or os.environ.get("ROBINHOOD_TOTP_SECRET")
    if totp_secret and not mfa_code:
        import pyotp
        mfa_code = pyotp.TOTP(totp_secret).now()
    return rh.login(username=username, password=password, mfa_code=mfa_code)


def logout() -> None:
    rh.logout()


def get_historicals(symbol: str, interval: str = "day", span: str = "year") -> pd.DataFrame:
    """Fetch historical OHLCV candles for `symbol`, shaped like the crypto fetcher's output."""
    if interval not in VALID_INTERVALS:
        raise ValueError(f"unsupported interval {interval!r}, choose from {sorted(VALID_INTERVALS)}")
    if span not in VALID_SPANS:
        raise ValueError(f"unsupported span {span!r}, choose from {sorted(VALID_SPANS)}")

    raw = rh.get_stock_historicals(symbol, interval=interval, span=span, bounds="regular")
    if not raw:
        raise RuntimeError(f"no historical data returned for {symbol} ({interval}/{span})")

    df = pd.DataFrame(raw)
    df["begins_at"] = pd.to_datetime(df["begins_at"], utc=True)
    for col in ("open_price", "high_price", "low_price", "close_price", "volume"):
        df[col] = df[col].astype(float)
    df = df.rename(columns={
        "open_price": "open", "high_price": "high",
        "low_price": "low", "close_price": "close",
    })
    df = df.set_index("begins_at")[["open", "high", "low", "close", "volume"]]
    return df[~df.index.duplicated(keep="first")]


def get_quote(symbol: str) -> float:
    price = rh.get_latest_price(symbol)[0]
    if price is None:
        raise RuntimeError(f"no quote available for {symbol}")
    return float(price)


def get_position_quantity(symbol: str) -> float:
    """Currently held shares of `symbol` (0.0 if none)."""
    positions = rh.get_open_stock_positions()
    for pos in positions:
        instrument = rh.get_instrument_by_url(pos["instrument"])
        if instrument.get("symbol", "").upper() == symbol.upper():
            return float(pos["quantity"])
    return 0.0


def get_buying_power() -> float:
    profile = rh.load_account_profile()
    return float(profile["buying_power"])


def place_order(symbol: str, side: str, quantity: float, dry_run: bool = True,
                 order_type: str = "market") -> dict:
    """
    Place (or simulate) a market order. side must be 'buy' or 'sell'.

    Refuses to submit a real order unless dry_run=False AND
    ROBINHOOD_CONFIRM_LIVE_TRADING=yes is set in the environment.
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol, "side": side, "quantity": quantity,
        "order_type": order_type, "dry_run": dry_run,
    }

    if quantity <= 0:
        record["status"], record["detail"] = "skipped", "non-positive quantity"
        _log_order(record)
        return record

    if dry_run:
        record["status"] = "dry_run"
        record["detail"] = f"would {side} {quantity} {symbol} ({order_type})"
        _log_order(record)
        return record

    if os.environ.get("ROBINHOOD_CONFIRM_LIVE_TRADING") != "yes":
        raise RuntimeError(
            "Refusing to place a live order: set ROBINHOOD_CONFIRM_LIVE_TRADING=yes "
            "as an explicit, separate acknowledgement that this submits a real "
            "order against a real account with real money."
        )

    if side == "buy":
        response = rh.order_buy_market(symbol, quantity)
    else:
        response = rh.order_sell_market(symbol, quantity)

    record["status"] = "submitted"
    record["detail"] = str(response)
    _log_order(record)
    return record


def _log_order(record: dict) -> None:
    ORDER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not ORDER_LOG_PATH.exists()
    row = {field: record.get(field, "") for field in ORDER_LOG_FIELDS}
    with open(ORDER_LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ORDER_LOG_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
