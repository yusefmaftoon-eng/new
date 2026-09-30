from helpers import PREV, at, bar, flat, until_signal

from config import Config
from strategy import (SPAN, gap_fade, intraday_momentum, orb, poc, poc_rejection, sweep)


def cfg(strategy, **params):
    return Config(strategy=strategy, params=params).for_market("SP500")


# ------------------------------------------------------------------ ORB

def orb_day(post):
    """Overnight flat at 100, 09:30-09:45 range 99..101, then `post` bars from 09:45."""
    bars = flat(at(5), at(9, 30), 100)
    bars += [bar(at(9, 30), 100, 101, 99, 100.5), bar(at(9, 35), 100.5, 100.8, 99.5, 100),
             bar(at(9, 40), 100, 100.6, 99.2, 100.2)]
    t = at(9, 45)
    for o, h, l, c in post:
        bars.append(bar(t, o, h, l, c))
        t += SPAN
    return bars


def test_orb_break_entry_fires_on_first_close_outside_range():
    bars = orb_day([(100.2, 100.9, 100.1, 100.8), (100.8, 101.6, 100.7, 101.5), (101.5, 102, 101.4, 101.9)])
    i, s = until_signal(bars, orb, cfg("orb", or_min=15, entry="break", stop="mid", rr=2.0))
    assert i == len(bars) - 2 and s.side == "buy"
    assert s.stop == 100.0 and s.rr == 2.0          # range mid
    assert s.exit_by == at(15, 55)


def test_orb_far_stop_and_short_side():
    bars = orb_day([(99.5, 99.6, 98.5, 98.7)])
    _, s = until_signal(bars, orb, cfg("orb", or_min=15, entry="break", stop="far", rr=1.0))
    assert s.side == "sell" and s.stop == 101


def test_orb_no_signal_inside_range_or_during_range():
    bars = orb_day([(100.2, 100.9, 99.1, 100.3)] * 10)
    assert until_signal(bars, orb, cfg("orb", entry="break"))[1] is None


def test_orb_retest_waits_for_touch_of_broken_edge():
    post = [(100.5, 101.6, 100.5, 101.5),      # break (close > 101)
            (101.5, 102.2, 101.4, 102.0),      # no retest
            (102.0, 102.1, 100.9, 101.3)]      # low touches 101, closes above -> retest entry
    bars = orb_day(post)
    i, s = until_signal(bars, orb, cfg("orb", entry="retest", stop="mid", rr=2.0))
    assert i == len(bars) - 1 and s.side == "buy" and s.ref_price == 101.3


def test_orb_retest_invalidated_by_close_through_far_side():
    post = [(100.5, 101.6, 100.5, 101.5), (101.5, 101.5, 98.5, 98.8), (98.8, 101.2, 98.8, 101.1)]
    assert until_signal(orb_day(post), orb, cfg("orb", entry="retest"))[1] is None


def test_orb_fvg_entry_on_pullback_into_gap():
    post = [(100.5, 101.2, 100.4, 101.1),      # a: break bar, high 101.2
            (101.1, 102.5, 101.0, 102.4),      # b: impulse
            (102.4, 103.0, 101.8, 102.9),      # c: low 101.8 > a.high 101.2 -> gap [101.2, 101.8]
            (102.9, 103.1, 102.2, 102.5),      # no touch
            (102.5, 102.6, 101.6, 102.0)]      # dips into gap, closes above 101.2 -> entry
    bars = orb_day(post)
    i, s = until_signal(bars, orb, cfg("orb", entry="fvg", stop="mid", rr=2.0))
    assert i == len(bars) - 1 and s.side == "buy"


def test_orb_only_first_signal_of_the_day():
    post = [(100.5, 101.6, 100.5, 101.5), (101.5, 101.6, 100.5, 100.6), (100.6, 101.8, 100.5, 101.7)]
    bars = orb_day(post)
    c = cfg("orb", entry="break")
    first, _ = until_signal(bars, orb, c)
    assert orb(bars, c) is None and first == len(bars) - 3


# ------------------------------------------------------------------ sweep

def test_sweep_of_london_high_then_structure_shift_sells():
    bars = flat(at(0), at(2), 100)
    bars += flat(at(2), at(5), 100)                       # london range 99.8 .. 100.2
    bars += flat(at(5), at(7), 100)
    t = at(7)
    seq = [(100, 100.1, 99.9, 100.0), (100.0, 100.1, 99.95, 100.05), (100.05, 100.1, 99.97, 100.08),
           (100.1, 100.6, 100.0, 100.5),                  # sweep: high 100.6 > 100.2
           (100.5, 100.55, 100.1, 100.15),
           (100.15, 100.2, 99.8, 99.85)]                  # close < swing low (99.9) and < 100.2 -> sell
    for o, h, l, c in seq:
        bars.append(bar(t, o, h, l, c))
        t += SPAN
    i, s = until_signal(bars, sweep, cfg("sweep", ref="london", swing=3, rr=2.0))
    assert i == len(bars) - 1 and s.side == "sell" and s.stop == 100.6


def test_sweep_opp_target_is_far_side_of_range_and_needs_1r():
    bars = flat(at(0), at(7), 100)
    t = at(7)
    for o, h, l, c in [(100, 100.1, 99.9, 100.0)] * 3 + [(100.1, 100.3, 100.0, 100.25),
                                                         (100.25, 100.26, 99.85, 99.95)]:
        bars.append(bar(t, o, h, l, c))
        t += SPAN
    s = sweep(bars, cfg("sweep", ref="london", swing=3, rr="opp"))
    # risk = 100.3 - 99.95 = 0.35 ; reward to 99.8 = 0.15 < 1R -> skipped
    assert s is None


def test_sweep_outside_window_is_ignored():
    bars = flat(at(0), at(12, 30), 100)
    assert sweep(bars, cfg("sweep", window_end=11)) is None


# ------------------------------------------------------------------ POC

def test_poc_is_most_visited_price():
    bars = [bar(i * SPAN, 100, 100.02, 99.98, 100) for i in range(20)] + [bar(10**9, 100, 110, 100, 105)]
    assert abs(poc(bars) - 100) < 0.2


def test_poc_rejection_long_after_approach_from_above():
    bars = flat(at(9, 30, PREV), at(16, 0, PREV), 100)    # POC ~100
    bars += flat(at(16, 0, PREV), at(9, 30), 103)
    t = at(9, 30)
    for c in [103, 102.5, 102, 101.5, 101, 100.7, 100.4]:
        bars.append(bar(t, c + 0.3, c + 0.4, c - 0.1, c))
        t += SPAN
    bars.append(bar(t, 100.3, 100.4, 99.6, 100.25))       # pierces POC, closes back above
    s = poc_rejection(bars, cfg("poc_rejection", profile="rth", rr=2.0, buffer_atr=0.25, approach_atr=1.0))
    assert s and s.side == "buy" and s.stop < 99.6


# ------------------------------------------------------------------ gap fade

def gap_bars(open_px):
    bars = flat(at(9, 30, PREV), at(16, 0, PREV), 100)
    bars[-1]["c"] = 100.0
    bars += flat(at(16, 0, PREV), at(9, 30), open_px)
    return bars


def test_gap_fade_sells_gap_up_targeting_prior_close():
    s = gap_fade(gap_bars(100.3), cfg("gap_fade", min_gap=0.002, stop_gap=1.0, target="fill"))
    assert s.side == "sell" and s.target == 100.0 and abs(s.stop - 100.6) < 1e-9


def test_gap_fade_half_target_and_filters():
    s = gap_fade(gap_bars(99.6), cfg("gap_fade", min_gap=0.002, stop_gap=0.5, target="half"))
    assert s.side == "buy" and abs(s.target - 99.8) < 1e-9
    assert gap_fade(gap_bars(100.1), cfg("gap_fade", min_gap=0.002)) is None     # too small
    assert gap_fade(gap_bars(101.5), cfg("gap_fade", min_gap=0.002)) is None     # > 1%: runs, don't fade


# ------------------------------------------------------------------ intraday momentum

def mom_bars(px_10, px_15, px_1530):
    bars = flat(at(9, 30, PREV), at(16, 0, PREV), 100)
    bars += flat(at(16, 0, PREV), at(9, 55), 100)
    bars += flat(at(9, 55), at(15, 0), px_10)
    bars[-1]["c"] = px_15
    bars += flat(at(15, 0), at(15, 30), px_1530)
    return bars


def test_momentum_follows_first_half_hour_sign():
    s = intraday_momentum(mom_bars(100.4, 100.4, 100.5), cfg("intraday_momentum", min_r1=0.002, stop_atr=3))
    assert s.side == "buy" and s.exit_by == at(16) and s.stop < 100.5
    s = intraday_momentum(mom_bars(99.6, 99.6, 99.5), cfg("intraday_momentum", min_r1=0.002))
    assert s.side == "sell"


def test_momentum_threshold_and_confirm():
    assert intraday_momentum(mom_bars(100.1, 100.1, 100.1), cfg("intraday_momentum", min_r1=0.002)) is None
    # r1 up but 15:00->15:30 down: confirm filter blocks it
    assert intraday_momentum(mom_bars(100.4, 100.6, 100.3), cfg("intraday_momentum", confirm=True)) is None
