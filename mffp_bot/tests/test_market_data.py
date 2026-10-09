import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from websockets.exceptions import ConnectionClosedError  # noqa: E402

import market_data  # noqa: E402


def test_fetch_range_retries_dropped_websocket(monkeypatch):
    calls = {"n": 0}

    def flaky(provider, symbol, interval, limit, start_ms, end_ms):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionClosedError(None, None)
        return [{"t": start_ms, "o": 1, "h": 1, "l": 1, "c": 1, "v": 0}]

    monkeypatch.setattr(market_data, "fetch_candles", flaky)
    monkeypatch.setattr(market_data.time, "sleep", lambda s: None)
    out = market_data.fetch_range("binance", "BTCUSDT", "5m", 0, 300_000 * 10, page=500)
    assert calls["n"] == 2 and len(out) == 1
