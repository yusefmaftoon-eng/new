import pytest
from helpers import bar

import backtest
from backtest import merge, run_eval, simulate
from config import ASSET_CLASS_COSTS, MARKETS, Config
from risk import position_size
from strategy import SPAN, Signal


# ------------------------------------------------------------------ per-asset-class costs + sizing

@pytest.mark.parametrize("symbol", list(MARKETS))
def test_for_market_uses_asset_class_costs_and_caps(symbol):
    c = Config().for_market(symbol)
    m = MARKETS[symbol]
    assert c.fee_rate == ASSET_CLASS_COSTS[m.asset_class]["fee_rate"]
    assert c.swap_daily_rate == ASSET_CLASS_COSTS[m.asset_class]["swap_daily_rate"]
    assert c.coin == m.coin and c.market_id == m.market_id
    assert c.leverage <= m.max_leverage and c.max_notional_mult <= m.max_leverage


def test_cost_schedule_matches_fee_page():
    assert ASSET_CLASS_COSTS["crypto"] == {"fee_rate": 0.0003, "swap_daily_rate": 0.0003}
    assert ASSET_CLASS_COSTS["tradfi"] == {"fee_rate": 0.00005, "swap_daily_rate": 0.00015}
    assert ASSET_CLASS_COSTS["fx"] == {"fee_rate": 0.000025, "swap_daily_rate": 0.00005}


# typical entry/stop for each market (wide enough that the notional cap does not bind)
CASES = {"BTC": (83000.0, 82500.0), "GOLD": (4160.0, 4140.0), "SP500": (7660.0, 7630.0),
         "EUR": (1.1336, 1.1300), "JPY": (157.30, 156.80)}


@pytest.mark.parametrize("symbol", list(MARKETS))
def test_full_stop_including_fees_and_slippage_costs_at_most_45(symbol):
    c = Config().for_market(symbol)
    entry, stop = CASES[symbol]
    for side_entry, side_stop in ((entry, stop), (stop, entry)):          # long and short
        size = position_size(c.risk_per_trade, side_entry, side_stop, c, 10_000)
        assert size > 0
        slip = c.slippage_bps / 10_000
        worst_exit = side_stop * (1 - slip) if side_entry > side_stop else side_stop * (1 + slip)
        loss = abs(side_entry - worst_exit) * size + (side_entry + worst_exit) * size * c.fee_rate
        assert loss <= 45.0 + 1e-9
        assert loss >= 40.0                          # and it actually uses the budget


@pytest.mark.parametrize("symbol", list(MARKETS))
def test_tight_stop_is_capped_by_notional_and_leverage(symbol):
    c = Config().for_market(symbol)
    entry, _ = CASES[symbol]
    size = position_size(c.risk_per_trade, entry, entry * 0.9999, c, 10_000)
    assert size * entry <= min(3.0, MARKETS[symbol].max_leverage) * 10_000 + 1e-6


def test_size_rounds_down_to_market_decimals():
    c = Config().for_market("GOLD")
    size = position_size(45, 4160.0, 4150.0, c, 10_000)
    assert size == round(size, c.size_decimals) and size * (4160 - 4150) <= 45


# ------------------------------------------------------------------ simulator conventions

def run_one(monkeypatch, bars, sig, **cfg_kw):
    """Simulate a single scripted signal emitted at the close of bars[k]."""
    k = sig.bar_time
    calls = {"n": 0}

    def fake_generate(window, cfg):
        if window[-1]["t"] == k and calls["n"] == 0:
            calls["n"] += 1
            return sig
        return None

    monkeypatch.setattr(backtest, "generate", fake_generate)
    c = Config(strategy="orb", **cfg_kw).for_market("SP500")
    return simulate(bars, c)


def series(rows, t0=10**12):
    return [bar(t0 + i * SPAN, *r) for i, r in enumerate(rows)]


def test_entry_is_next_bar_open_with_slippage(monkeypatch):
    bars = series([(100, 100, 100, 100)] * 60 + [(100, 100.1, 99.9, 100), (101, 103, 100.9, 102.9)])
    sig = Signal("buy", 100, 99, 1, bars[60]["t"], rr=2.0)
    # fill = 101 * (1 + 1bp); tp = fill + 2 * (fill - 99) ~ 105.03 not reached yet: still open, not recorded
    assert run_one(monkeypatch, bars, sig) == []
    tr = run_one(monkeypatch, bars + series([(103, 106, 102.8, 105.5)], bars[-1]["t"] + SPAN), sig)
    assert tr[0]["t"] == bars[61]["t"] and tr[0]["exit"] == "target"
    fill = 101 * 1.0001
    tp = fill + 2 * (fill - 99)
    size = tr[0]["notional"] / fill
    fees = (fill + tp * 0.9999) * size * 0.00005
    assert tr[0]["pnl"] == pytest.approx((tp * 0.9999 - fill) * size - fees, abs=0.05)   # slipped TP, fees, ~0 swap


def test_stop_wins_when_stop_and_target_share_a_bar(monkeypatch):
    bars = series([(100, 100, 100, 100)] * 60 + [(100, 100, 100, 100), (100, 100.2, 99.8, 100),
                                                 (100, 103, 98, 100)])
    sig = Signal("buy", 100, 99, 1, bars[60]["t"], rr=1.0)
    tr = run_one(monkeypatch, bars, sig)
    assert tr[0]["exit"] == "stop" and tr[0]["pnl"] < 0


def test_gap_through_stop_fills_at_the_open(monkeypatch):
    bars = series([(100, 100, 100, 100)] * 60 + [(100, 100, 100, 100), (100, 100.2, 99.8, 100),
                                                 (97, 97.5, 96.5, 97)])
    sig = Signal("buy", 100, 99, 1, bars[60]["t"], rr=2.0)
    tr = run_one(monkeypatch, bars, sig)
    assert tr[0]["exit"] == "stop" and tr[0]["r"] < -1.5          # gap loss worse than 1R


def test_time_exit_at_open_of_first_bar_at_or_after_exit_by(monkeypatch):
    bars = series([(100, 100, 100, 100)] * 60 + [(100, 100, 100, 100)] + [(100, 100.3, 99.7, 100.2)] * 5)
    sig = Signal("buy", 100, 99, 1, bars[60]["t"], rr=5.0, exit_by=bars[64]["t"])
    tr = run_one(monkeypatch, bars, sig)
    assert tr[0]["exit"] == "time" and tr[0]["xt"] == bars[64]["t"]


def test_no_entry_when_next_bar_is_after_a_closure(monkeypatch):
    bars = series([(100, 100, 100, 100)] * 61)
    bars += series([(105, 106, 104, 105)] * 5, bars[-1]["t"] + 3600_000)      # 1h gap
    sig = Signal("buy", 100, 99, 1, bars[60]["t"], rr=2.0)
    assert run_one(monkeypatch, bars, sig) == []


def test_merge_keeps_one_position_at_a_time():
    a = [{"t": 0, "xt": 10}, {"t": 20, "xt": 30}]
    b = [{"t": 5, "xt": 8}, {"t": 12, "xt": 15}]
    assert [t["t"] for t in merge(a, b)] == [0, 12, 20]


def test_run_eval_pass_and_fail_paths():
    c = Config()
    day = 86_400_000
    win = [{"t": 1_780_000_000_000 + i * day, "pnl": 90.0, "mae": 10.0} for i in range(12)]
    assert run_eval(win, c, "2000-01-01")[0] == "PASS"
    loss = [{"t": 1_780_000_000_000 + i * day, "pnl": -45.0, "mae": 45.0} for i in range(12)]
    assert run_eval(loss, c, "2000-01-01")[0] in ("halted", "FAIL")
