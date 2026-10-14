"""Minimal Kalshi Trade API v2 client (market data + V2 order placement).

Field names and endpoints were checked against the live API and
docs.kalshi.com in Oct 2026:
  - Prices come back as fixed-point dollar strings (`yes_ask_dollars`, ...).
  - Orders go to `POST /portfolio/events/orders` (the V2 shape). `side` is
    quoted from the YES leg only: `bid` = buy YES at `price`, `ask` = sell YES
    at `price`, which is economically the same as buying NO at `1 - price`.
  - Auth: sign `timestamp_ms + METHOD + path` (path without query string)
    with your API private key (RSA-PSS/SHA-256, or Ed25519).
"""
from __future__ import annotations

import base64
import time
import uuid
from typing import Iterator

import requests

PROD_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"


def _load_private_key(path: str):
    from cryptography.hazmat.primitives import serialization

    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


class KalshiClient:
    def __init__(self, base_url: str = PROD_BASE_URL, api_key_id: str | None = None,
                 private_key_path: str | None = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key_id = api_key_id
        self.private_key = _load_private_key(private_key_path) if private_key_path else None
        self.timeout = timeout
        self.session = requests.Session()

    @property
    def authenticated(self) -> bool:
        return bool(self.api_key_id and self.private_key)

    # -- auth ---------------------------------------------------------------

    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        message = f"{timestamp_ms}{method}{path.split('?')[0]}".encode()
        if isinstance(self.private_key, Ed25519PrivateKey):
            sig = self.private_key.sign(message)
        else:
            sig = self.private_key.sign(
                message,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                            salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256(),
            )
        return base64.b64encode(sig).decode()

    def _request(self, method: str, endpoint: str, *, params: dict | None = None,
                 json: dict | None = None, auth: bool = False) -> dict:
        for attempt in range(5):
            resp = self._send(method, endpoint, params, json, auth)
            if resp.status_code != 429:
                break
            time.sleep(0.5 * 2 ** attempt)  # rate limited: back off and retry
        if resp.status_code >= 400:
            raise requests.HTTPError(f"{method} {endpoint} -> {resp.status_code}: {resp.text}",
                                     response=resp)
        return resp.json()

    def _send(self, method: str, endpoint: str, params: dict | None, json: dict | None,
              auth: bool) -> requests.Response:
        url = self.base_url + endpoint
        headers = {}
        if auth:
            if not self.authenticated:
                raise RuntimeError("This call needs KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH")
            ts = str(int(time.time() * 1000))
            path = requests.utils.urlparse(url).path
            headers = {
                "KALSHI-ACCESS-KEY": self.api_key_id,
                "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": self._sign(ts, method, path),
            }
        return self.session.request(method, url, params=params, json=json,
                                    headers=headers, timeout=self.timeout)

    # -- market data (public) -------------------------------------------------

    def get_markets(self, **params) -> dict:
        return self._request("GET", "/markets", params=params)

    def iter_markets(self, max_markets: int | None = None, **params) -> Iterator[dict]:
        cursor, n = None, 0
        while True:
            page = self.get_markets(limit=200, cursor=cursor, **params) if cursor else \
                self.get_markets(limit=200, **params)
            for m in page.get("markets", []):
                yield m
                n += 1
                if max_markets and n >= max_markets:
                    return
            cursor = page.get("cursor")
            if not cursor:
                return

    def get_market(self, ticker: str) -> dict:
        return self._request("GET", f"/markets/{ticker}")["market"]

    def get_candlesticks(self, series: str, ticker: str, start_ts: int, end_ts: int,
                         period_minutes: int = 1) -> list[dict]:
        data = self._request("GET", f"/series/{series}/markets/{ticker}/candlesticks",
                             params={"start_ts": start_ts, "end_ts": end_ts,
                                     "period_interval": period_minutes})
        return data.get("candlesticks", [])

    # -- portfolio (authenticated) ---------------------------------------------

    def get_balance_dollars(self) -> float:
        data = self._request("GET", "/portfolio/balance", auth=True)
        return data["balance"] / 100.0  # balance is reported in cents

    def create_order(self, ticker: str, book_side: str, count: str, price: str,
                     time_in_force: str = "immediate_or_cancel") -> dict:
        """Place a V2 order. book_side: 'bid' (buy YES) or 'ask' (sell YES == buy NO)."""
        body = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": book_side,
            "count": count,
            "price": price,
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
        }
        return self._request("POST", "/portfolio/events/orders", json=body, auth=True)
