"""Signal logic, shared by the live bot and the backtester.

15m trend filter (EMA20 > EMA50, EMA50 rising, price above it) + 5m close breaking the
prior 20-bar high/low in the trend direction. Stop = 1.5 ATR, target = 2R from the fill.
"""
from dataclasses import dataclass
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


@dataclass
class Signal:
    side: str          # "buy" | "sell"
    ref_price: float   # signal bar close
    stop: float
    atr: float
    bar_time: int


def ema_series(values, n):
    k, out, e = 2 / (n + 1), [], None
    for v in values:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def atr(bars, n):
    trs = [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])) for p, b in zip(bars, bars[1:])]
    if len(trs) < n:
        return None
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def resample(bars, interval_min, factor):
    """Aggregate closed bars into complete higher-timeframe bars only."""
    span = interval_min * factor * 60_000
    out, cur, count = [], None, 0
    for b in bars:
        key = b["t"] // span * span
        if cur is None or key != cur["t"]:
            if cur is not None and count == factor:
                out.append(cur)
            cur, count = {"t": key, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}, 1
        else:
            cur["h"], cur["l"], cur["c"] = max(cur["h"], b["h"]), min(cur["l"], b["l"]), b["c"]
            count += 1
    if cur is not None and count == factor:
        out.append(cur)
    return out


def in_session(ts_ms, cfg):
    dt = datetime.fromtimestamp(ts_ms / 1000, NY)
    if not cfg.trade_weekends and dt.weekday() >= 5:
        return False
    s = dtime(*map(int, cfg.session_start.split(":")))
    e = dtime(*map(int, cfg.session_end.split(":")))
    return s <= dt.time() < e


def bracket(side, fill, stop, rr):
    """Take-profit price at rr x the actual fill-to-stop distance."""
    return fill + rr * (fill - stop) if side == "buy" else fill - rr * (stop - fill)


def generate(bars, cfg):
    """bars: closed 5m bars, oldest first. Returns Signal or None."""
    if len(bars) < cfg.ema_slow * cfg.trend_factor + 20:
        return None
    last = bars[-1]
    if not in_session(last["t"] + cfg.interval_min * 60_000, cfg):
        return None

    htf = resample(bars, cfg.interval_min, cfg.trend_factor)
    if len(htf) < cfg.ema_slow + 4:
        return None
    closes = [b["c"] for b in htf]
    ef = ema_series(closes, cfg.ema_fast)[-1]
    es = ema_series(closes, cfg.ema_slow)
    up = closes[-1] > es[-1] and ef > es[-1] and es[-1] > es[-4]
    down = closes[-1] < es[-1] and ef < es[-1] and es[-1] < es[-4]

    a = atr(bars[-cfg.atr_len * 4:], cfg.atr_len)
    if not a or a / last["c"] < cfg.min_atr_pct:
        return None

    window = bars[-cfg.breakout_len - 1:-1]
    hh, ll, c = max(b["h"] for b in window), min(b["l"] for b in window), last["c"]
    if up and hh < c <= hh + cfg.max_chase_atr * a:
        return Signal("buy", c, c - cfg.atr_stop_mult * a, a, last["t"])
    if down and ll > c >= ll - cfg.max_chase_atr * a:
        return Signal("sell", c, c + cfg.atr_stop_mult * a, a, last["t"])
    return None
