"""Thin MyFundedPerps REST client (API is beta - field names may change)."""
import time
import uuid
from urllib.parse import quote

import requests


class APIError(Exception):
    def __init__(self, status, code, message, body=None):
        super().__init__(f"[{status}] {code}: {message}")
        self.status, self.code, self.body = status, code, body


def _data(body):
    return body.get("data", body) if isinstance(body, dict) else body


class FPClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 10):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        self.last_status = None

    def _req(self, method, path, *, params=None, json=None, idem=None, retries=5):
        headers = {"Idempotency-Key": idem} if idem else {}
        err = None
        for attempt in range(retries):
            try:
                r = self.s.request(method, self.base + path, params=params, json=json,
                                   headers=headers, timeout=self.timeout)
            except requests.RequestException as e:          # network blip: retry, same idem key
                err = e
                time.sleep(min(2 ** attempt, 10))
                continue
            self.last_status = r.status_code
            body = r.json() if r.content else {}
            if r.status_code in (429, 503) or (r.status_code == 409 and
                                                 (body.get("error") or {}).get("code") == "operation_in_progress"):
                time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            if r.status_code >= 500:
                time.sleep(min(2 ** attempt, 10))
                continue
            if r.status_code >= 400:
                e = body.get("error") or {}
                raise APIError(r.status_code, e.get("code"), e.get("message"), body)
            return body
        raise APIError(self.last_status, "retries_exhausted", str(err), None)

    # ---- accounts ----
    def accounts(self):
        return _data(self._req("GET", "/v1/accounts"))

    def account(self, account_id):
        return _data(self._req("GET", f"/v1/accounts/{account_id}"))

    def positions(self, account_id):
        return _data(self._req("GET", "/v1/positions", params={"account_id": account_id})) or []

    def working_orders(self, account_id):
        return _data(self._req("GET", "/v1/orders", params={"account_id": account_id, "status": "working"})) or []

    # ---- markets ----
    def quote(self, market_id, side, size):
        return _data(self._req("GET", f"/v1/markets/{quote(market_id, safe='')}/quote",
                               params={"side": side, "size": size}))

    # ---- orders ----
    def place_market(self, *, account_id, market_id, side, size, expected_price,
                     leverage, margin_mode, take_profit, stop_loss, client_order_id):
        body = {
            "type": "market", "client_order_id": client_order_id, "account_id": account_id,
            "market_id": market_id, "side": side, "size": size, "expected_price": expected_price,
            "leverage": leverage, "margin_mode": margin_mode,
            "take_profit_price": take_profit, "stop_loss_price": stop_loss,
        }
        return _data(self._req("POST", "/v1/orders", json=body, idem=f"{client_order_id}-{uuid.uuid4().hex[:8]}"))

    def order(self, order_id):
        return _data(self._req("GET", f"/v1/orders/{order_id}"))

    def wait_order(self, order_id, timeout=30):
        deadline, delay = time.time() + timeout, 0.5
        while time.time() < deadline:
            o = self.order(order_id)
            if o.get("status") in ("filled", "rejected", "canceled", "cancelled", "expired"):
                return o
            time.sleep(delay)
            delay = min(delay * 1.5, 3)
        return self.order(order_id)

    def close_all(self, account_id, max_rounds=40):
        """Flatten the account. Repeats the same idempotency key until the server reports completed."""
        key = f"flatten-{uuid.uuid4()}"
        for _ in range(max_rounds):
            body = _data(self._req("POST", f"/v1/accounts/{account_id}/close-all-positions", idem=key))
            if self.last_status == 200 or str(body.get("status", "")).lower() == "completed":
                return body
            time.sleep(1)
        raise APIError(self.last_status, "flatten_incomplete", "close-all did not complete", body)
