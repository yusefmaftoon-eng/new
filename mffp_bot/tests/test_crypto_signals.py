import math

from helpers import bar

from config import Config
from strategy import DAY, HOUR, SPAN, donchian_4h, mean_reversion_1h, vol_breakout

T0 = 1_780_000_000_000 // DAY * DAY          # a UTC midnight


def cfg(strategy, **params):
    return Config(strategy=strategy, params=params).for_market("BTC")


def series(n, px=lambda i: 100.0, wiggle=0.5, t0=T0):
    return [bar(t0 + i * SPAN, px(i), px(i) + wiggle, px(i) - wiggle, px(i)) for i in range(n)]


# ------------------------------------------------------------------ 4h Donchian

def test_donchian_long_on_4h_close_above_prior_high_only_on_the_4h_boundary():
    n4 = 48 * 25                                       # 25 4h bars of quiet range 99.5..100.5
    bars = series(n4)
    bars += [bar(T0 + (n4 + i) * SPAN, 100, 103, 99.8, 102.5) for i in range(48)]   # breakout 4h bar
    s = donchian_4h(bars, cfg("donchian_4h", n=20, stop_atr=2.0, rr=3.0))
    assert s and s.side == "buy" and s.ref_price == 102.5 and s.stop < 102.5 and s.rr == 3.0
    assert donchian_4h(bars[:-1], cfg("donchian_4h", n=20)) is None                # mid-4h-bar: nothing


def test_donchian_short_and_no_signal_inside_channel():
    n4 = 48 * 25
    bars = series(n4) + [bar(T0 + (n4 + i) * SPAN, 100, 100.2, 97, 97.5) for i in range(48)]
    assert donchian_4h(bars, cfg("donchian_4h", n=20)).side == "sell"
    assert donchian_4h(series(48 * 26), cfg("donchian_4h", n=20)) is None


# ------------------------------------------------------------------ daily volatility breakout

def vb_day(today_rows):
    bars = series(288, t0=T0 - DAY, wiggle=1.0)          # yesterday: range 99..101 = 2
    t = T0
    for o, h, l, c in today_rows:
        bars.append(bar(t, o, h, l, c))
        t += SPAN
    return bars


def test_vol_breakout_first_close_beyond_open_plus_k_range():
    rows = [(100, 100.5, 99.8, 100.4), (100.4, 100.9, 100.3, 100.8), (100.8, 101.3, 100.7, 101.2)]
    c = cfg("vol_breakout", k=0.5, stop_range=0.5, rr=2.0)
    assert vol_breakout(vb_day(rows[:2]), c) is None                # 100.8 < 100 + 0.5*2
    s = vol_breakout(vb_day(rows), c)
    assert s.side == "buy" and math.isclose(s.stop, 101.2 - 1.0) and s.exit_by == T0 + DAY


def test_vol_breakout_once_per_day():
    rows = [(100, 101.3, 99.8, 101.2), (101.2, 101.3, 100.0, 100.1), (100.1, 101.5, 100, 101.4)]
    c = cfg("vol_breakout", k=0.5)
    assert vol_breakout(vb_day(rows[:1]), c).side == "buy"
    assert vol_breakout(vb_day(rows), c) is None


# ------------------------------------------------------------------ 1h mean reversion

def mr_bars(last_close, trend_px=lambda i: 100.0):
    n = 12 * 215
    bars = [bar(T0 + i * SPAN, trend_px(i), trend_px(i) + 0.3, trend_px(i) - 0.3,
                trend_px(i) + (0.2 if (i // 12) % 2 else -0.2)) for i in range(n)]
    bars += [bar(T0 + (n + i) * SPAN, 100, 100.2, last_close - 0.1, last_close) for i in range(12)]
    return bars


def test_mean_reversion_long_targets_the_mean():
    s = mean_reversion_1h(mr_bars(98.5), cfg("mean_reversion_1h", z=2.0, stop_atr=1.5, trend=False))
    assert s and s.side == "buy" and 99.0 < s.target < 100.1 and s.stop < 98.5


def test_mean_reversion_trend_filter_blocks_counter_trend_long():
    falling = lambda i: 130 - i * 0.01                   # well below its 200h EMA at the end
    bars = mr_bars(falling(12 * 215) - 1.5, falling)
    assert mean_reversion_1h(bars, cfg("mean_reversion_1h", z=2.0, trend=True)) is None


def test_mean_reversion_only_on_hour_close():
    bars = mr_bars(98.5)
    assert mean_reversion_1h(bars[:-1], cfg("mean_reversion_1h", z=2.0, trend=False)) is None
    assert (bars[-1]["t"] + SPAN) % HOUR == 0
