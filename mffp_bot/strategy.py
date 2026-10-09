"""Signal logic, shared by the live bot and the backtester.

generate(bars, cfg) -> Signal | None dispatches on cfg.strategy. bars are closed 5m bars, oldest
first; the decision is made at the close of bars[-1] and filled at the next bar's open.

Every signal here is stateless: it looks at the bars it is given and fires only on the bar where
its trigger first appears that day, so the bot and the backtester take exactly the same trades.
All intraday strategies are flat by `exit_by` (15:55 New York), before the underlying's daily
break and the weekend, so no position is held through a closure.
"""
from dataclasses import dataclass
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SPAN = 300_000          # 5m in ms
HOUR = 3_600_000


@dataclass
class Signal:
    side: str          # "buy" | "sell"
    ref_price: float   # signal bar close
    stop: float
    atr: float
    bar_time: int
    rr: float = None       # take-profit at rr x fill-to-stop distance (None -> cfg.reward_risk)
    target: float = None   # absolute take-profit price (overrides rr)
    exit_by: int = None    # epoch ms: be flat by this time (time exit before a closure)


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


def take_profit(sig, fill, cfg):
    """TP for a signal filled at `fill`: its absolute target if it has one, else rr x risk."""
    if sig.target is not None:
        return sig.target
    return bracket(sig.side, fill, sig.stop, sig.rr if sig.rr is not None else cfg.reward_risk)


# --------------------------------------------------------------------------- time helpers

def ny_ms(day, hh, mm=0):
    """Epoch ms of HH:MM New York time on calendar date `day`."""
    return int(datetime(day.year, day.month, day.day, hh, mm, tzinfo=NY).timestamp() * 1000)


def _now(bars):
    """Decision time = close of the last bar, as (ms, NY datetime)."""
    now = bars[-1]["t"] + SPAN
    return now, datetime.fromtimestamp(now / 1000, NY)


def _between(bars, t0, t1):
    """Bars whose open time is in [t0, t1). Scans from the end: windows are always recent."""
    out = []
    for b in reversed(bars):
        if b["t"] < t0:
            break
        if b["t"] < t1:
            out.append(b)
    out.reverse()
    return out


def _complete(bs, t0, t1, frac=0.8):
    return len(bs) >= frac * (t1 - t0) / SPAN


def _prev_weekday_session(bars, day, hh0, mm0, hh1, mm1):
    """Bars of the most recent weekday before `day` between hh0:mm0 and hh1:mm1 NY that has data."""
    for back in range(1, 5):
        d = datetime.fromordinal(day.toordinal() - back).date()
        if d.weekday() >= 5:
            continue
        t0, t1 = ny_ms(d, hh0, mm0), ny_ms(d, hh1, mm1)
        bs = _between(bars, t0, t1)
        if _complete(bs, t0, t1):
            return bs
        return None
    return None


def _p(cfg, defaults):
    return {**defaults, **(cfg.params or {})}


# --------------------------------------------------------------------------- 0. legacy BTC breakout

def trend_breakout(bars, cfg):
    """15m trend filter (EMA20 > EMA50, EMA50 rising, price above it) + 5m close breaking the
    prior 20-bar high/low in the trend direction. Stop = 1.5 ATR, target = 2R from the fill."""
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


# --------------------------------------------------------------------------- 1. NY opening range breakout

ORB_DEFAULTS = {"or_min": 15, "entry": "retest", "stop": "mid", "rr": 2.0}


def _orb_trigger(post, hi, lo, entry):
    """Index of the first entry trigger in `post` (bars after the range) and its side, or None."""
    side, k_break, fvg = None, None, None
    for k, b in enumerate(post):
        if side is None:
            if b["c"] > hi:
                side, k_break = "buy", k
            elif b["c"] < lo:
                side, k_break = "sell", k
            else:
                continue
            if entry == "break":
                return k, side
            continue
        long = side == "buy"
        if (long and b["c"] < lo) or (not long and b["c"] > hi):
            return None                                   # range failed the other way: done for the day
        if entry == "retest":
            if long and b["l"] <= hi < b["c"]:
                return k, side
            if not long and b["h"] >= lo > b["c"]:
                return k, side
        elif entry == "fvg":
            if fvg is None:
                if k - 2 >= max(0, k_break - 1):
                    a, c = post[k - 2], b
                    if long and c["l"] > a["h"]:
                        fvg = (a["h"], c["l"])            # bullish gap: [a.high, c.low]
                    elif not long and c["h"] < a["l"]:
                        fvg = (c["h"], a["l"])            # bearish gap: [c.high, a.low]
                continue
            g_lo, g_hi = fvg
            if long:
                if b["c"] < g_lo:
                    return None                           # gap filled through: setup dead
                if b["l"] <= g_hi and b["c"] > g_lo:
                    return k, side
            else:
                if b["c"] > g_hi:
                    return None
                if b["h"] >= g_lo and b["c"] < g_hi:
                    return k, side
    return None


def orb(bars, cfg):
    """Range = 09:30 -> 09:30+or_min NY. After a 5m close outside it, enter on the break itself,
    on a retest of the broken edge, or on a pullback into the breakout's fair-value gap.
    Stop at the range midpoint or far side; target rr x risk; entries until 12:00; flat 15:55."""
    p = _p(cfg, ORB_DEFAULTS)
    now, d = _now(bars)
    if d.weekday() >= 5:
        return None
    day = d.date()
    t_open = ny_ms(day, 9, 30)
    t_or = t_open + p["or_min"] * 60_000
    if not (t_or < now <= ny_ms(day, 12)):
        return None
    today = _between(bars, t_open, now)
    rng = [b for b in today if b["t"] < t_or]
    if len(rng) != p["or_min"] // 5:
        return None
    hi, lo = max(b["h"] for b in rng), min(b["l"] for b in rng)
    post = [b for b in today if b["t"] >= t_or]
    trig = _orb_trigger(post, hi, lo, p["entry"])
    if not trig or trig[0] != len(post) - 1:
        return None
    side, c = trig[1], bars[-1]["c"]
    if p["stop"] == "mid":
        stop = (hi + lo) / 2
    else:
        stop = lo if side == "buy" else hi
    if (side == "buy" and stop >= c) or (side == "sell" and stop <= c):
        return None
    return Signal(side, c, stop, hi - lo, bars[-1]["t"], rr=p["rr"], exit_by=ny_ms(day, 15, 55))


# --------------------------------------------------------------------------- 2. session liquidity sweep

SWEEP_DEFAULTS = {"ref": "london", "swing": 3, "rr": 2.0, "window_end": 11}


def sweep(bars, cfg):
    """Reference range = Asia (20:00-00:00 NY) or London (02:00-05:00 NY). Between 07:00 and
    window_end NY, price trades beyond one side of it (liquidity sweep), then a 5m close back inside
    the range AND through the low (high) of the `swing` bars before the sweep extreme
    (market-structure shift). Trade back into the range: stop at the sweep extreme, target rr x
    risk, or the far side of the range when rr == 'opp' (skipped if that is < 1R)."""
    p = _p(cfg, SWEEP_DEFAULTS)
    now, d = _now(bars)
    if d.weekday() >= 5:
        return None
    day = d.date()
    w0, w1 = ny_ms(day, 7), ny_ms(day, p["window_end"])
    if not (w0 < now <= w1):
        return None
    if p["ref"] == "asia":
        r0, r1 = ny_ms(day, 0) - 4 * HOUR, ny_ms(day, 0)
    else:
        r0, r1 = ny_ms(day, 2), ny_ms(day, 5)
    ref = _between(bars, r0, r1)
    if not _complete(ref, r0, r1):
        return None
    rh, rl = max(b["h"] for b in ref), min(b["l"] for b in ref)

    hist = _between(bars, r0, now)                       # bars used for swing points
    start = next(i for i, b in enumerate(hist) if b["t"] >= w0) if hist[-1]["t"] >= w0 else None
    if start is None:
        return None
    k = p["swing"]
    hi_ext = lo_ext = None                                # (price, index) of sweep extremes
    trig = None
    for i in range(start, len(hist)):
        b = hist[i]
        if b["h"] > rh and (hi_ext is None or b["h"] > hi_ext[0]):
            hi_ext = (b["h"], i)
        if b["l"] < rl and (lo_ext is None or b["l"] < lo_ext[0]):
            lo_ext = (b["l"], i)
        if hi_ext and i > hi_ext[1] and hi_ext[1] >= k:
            swing_lo = min(x["l"] for x in hist[hi_ext[1] - k:hi_ext[1]])
            if b["c"] < swing_lo and b["c"] < rh:
                trig = (i, "sell", hi_ext[0])
                break
        if lo_ext and i > lo_ext[1] and lo_ext[1] >= k:
            swing_hi = max(x["h"] for x in hist[lo_ext[1] - k:lo_ext[1]])
            if b["c"] > swing_hi and b["c"] > rl:
                trig = (i, "buy", lo_ext[0])
                break
    if not trig or trig[0] != len(hist) - 1:
        return None
    _, side, stop = trig
    c = bars[-1]["c"]
    risk = abs(c - stop)
    if risk <= 0:
        return None
    exit_by = ny_ms(day, 15, 55)
    if p["rr"] == "opp":
        tgt = rl if side == "sell" else rh
        if abs(tgt - c) < risk or (side == "sell" and tgt >= c) or (side == "buy" and tgt <= c):
            return None
        return Signal(side, c, stop, rh - rl, bars[-1]["t"], target=tgt, exit_by=exit_by)
    return Signal(side, c, stop, rh - rl, bars[-1]["t"], rr=p["rr"], exit_by=exit_by)


# --------------------------------------------------------------------------- 3. volume-profile POC rejection

POC_DEFAULTS = {"profile": "rth", "rr": 2.0, "buffer_atr": 0.25, "approach_atr": 1.0}
POC_BIN = 0.0005          # profile bin width, fraction of price (~$2 on gold)


def poc(bars):
    """Point of control of a price-at-time profile: each bar spreads one unit evenly over the bins
    it spans. (Bar volume isn't comparable between the proxy data and the perp feed, so time at
    price - the classic TPO profile - stands in for volume.)"""
    if not bars:
        return None
    w = bars[-1]["c"] * POC_BIN
    prof = {}
    for b in bars:
        lo_i, hi_i = int(b["l"] // w), int(b["h"] // w)
        share = 1.0 / (hi_i - lo_i + 1)
        for i in range(lo_i, hi_i + 1):
            prof[i] = prof.get(i, 0.0) + share
    top = max(prof.values())
    best = [i for i, v in prof.items() if v == top]
    mid = sum(best) / len(best)
    return (mid + 0.5) * w


def poc_rejection(bars, cfg):
    """POC of the previous weekday's session (09:30-16:00 NY for 'rth', 18:00-17:00 for 'full').
    Between 09:30 and 13:00, price arrives at the POC from at least approach_atr ATR away within
    the last hour, pierces it, and the 5m bar closes back on the side it came from (rejection).
    Trade away from the POC: stop beyond the rejection wick + buffer_atr ATR, target rr x risk."""
    p = _p(cfg, POC_DEFAULTS)
    now, d = _now(bars)
    if d.weekday() >= 5:
        return None
    day = d.date()
    if not (ny_ms(day, 9, 30) < now <= ny_ms(day, 13)):
        return None
    if p["profile"] == "rth":
        prof = _prev_weekday_session(bars, day, 9, 30, 16, 0)
    else:
        prof = None
        for back in range(1, 5):
            pd = datetime.fromordinal(day.toordinal() - back).date()
            if pd.weekday() >= 5:
                continue
            t0, t1 = ny_ms(pd, 17) - 23 * HOUR, ny_ms(pd, 17)
            bs = _between(bars, t0, t1)
            prof = bs if _complete(bs, t0, t1, 0.7) else None
            break
    if not prof:
        return None
    level = poc(prof)
    recent = bars[-13:]
    a = atr(bars[-60:], 14)
    if not a or len(recent) < 13:
        return None
    b, prev = bars[-1], bars[-2]
    side = None
    if prev["c"] > level and b["o"] > level and b["l"] < level < b["c"]:
        if max(x["c"] for x in recent[:-1]) >= level + p["approach_atr"] * a:
            side, stop = "buy", b["l"] - p["buffer_atr"] * a
    elif prev["c"] < level and b["o"] < level and b["h"] > level > b["c"]:
        if min(x["c"] for x in recent[:-1]) <= level - p["approach_atr"] * a:
            side, stop = "sell", b["h"] + p["buffer_atr"] * a
    if not side:
        return None
    # one trade per day: skip if an earlier bar today already gave a rejection signal
    t_first = ny_ms(day, 9, 30)
    earlier = _between(bars, t_first, b["t"])
    for j in range(len(bars) - 1 - len(earlier), len(bars) - 1):
        if j >= 60 and _poc_hit(bars[:j + 1], level, p):
            return None
    return Signal(side, b["c"], stop, a, b["t"], rr=p["rr"], exit_by=ny_ms(day, 15, 55))


def _poc_hit(bars, level, p):
    a = atr(bars[-60:], 14)
    b, prev, recent = bars[-1], bars[-2], bars[-13:]
    if not a:
        return False
    if prev["c"] > level and b["o"] > level and b["l"] < level < b["c"]:
        return max(x["c"] for x in recent[:-1]) >= level + p["approach_atr"] * a
    if prev["c"] < level and b["o"] < level and b["h"] > level > b["c"]:
        return min(x["c"] for x in recent[:-1]) <= level - p["approach_atr"] * a
    return False


# --------------------------------------------------------------------------- 4. mean reversion: gap fade

GAP_DEFAULTS = {"min_gap": 0.002, "stop_gap": 1.0, "target": "fill"}
MAX_GAP = 0.01            # gaps above 1% tend to run, not fill: not traded


def gap_fade(bars, cfg):
    """Overnight gap = price at 09:30 NY vs the previous weekday's 16:00 close. If
    min_gap <= |gap| <= 1%, fade it at the 09:30 open: stop stop_gap x the gap size beyond the
    entry, target the previous close ('fill') or half the gap ('half'). Flat 15:55."""
    p = _p(cfg, GAP_DEFAULTS)
    now, d = _now(bars)
    if d.weekday() >= 5:
        return None
    day = d.date()
    if now != ny_ms(day, 9, 30):
        return None
    prev = _prev_weekday_session(bars, day, 9, 30, 16, 0)
    if not prev or prev[-1]["t"] != _last_bar_before(prev, 16):
        return None
    pc, c = prev[-1]["c"], bars[-1]["c"]
    gap = c / pc - 1
    if not (p["min_gap"] <= abs(gap) <= MAX_GAP):
        return None
    size = abs(c - pc)
    side = "sell" if gap > 0 else "buy"
    stop = c + p["stop_gap"] * size if side == "sell" else c - p["stop_gap"] * size
    tgt = pc if p["target"] == "fill" else (c + pc) / 2
    return Signal(side, c, stop, size, bars[-1]["t"], target=tgt, exit_by=ny_ms(day, 15, 55))


def _last_bar_before(bs, hh):
    d = datetime.fromtimestamp(bs[-1]["t"] / 1000, NY).date()
    return ny_ms(d, hh) - SPAN


# --------------------------------------------------------------------------- 5. late-day intraday momentum

MOM_DEFAULTS = {"min_r1": 0.0, "stop_atr": 3.0, "confirm": False}


def intraday_momentum(bars, cfg):
    """Gao, Han, Li & Zhou (2018, JFS) 'Market intraday momentum': the return from the previous
    16:00 close to 10:00 predicts the 15:30-16:00 return (dealer gamma hedging and leveraged-ETF
    rebalancing trade in the direction of the day's move into the close; Baltussen et al. 2021).
    At 15:30 NY go with the sign of that first-half-hour return if |r1| >= min_r1 (and, with
    confirm, if the 15:00-15:30 return agrees). Stop stop_atr x 5m ATR, far TP (4R), flat at 16:00."""
    p = _p(cfg, MOM_DEFAULTS)
    now, d = _now(bars)
    if d.weekday() >= 5:
        return None
    day = d.date()
    if now != ny_ms(day, 15, 30):
        return None
    prev = _prev_weekday_session(bars, day, 9, 30, 16, 0)
    if not prev or prev[-1]["t"] != _last_bar_before(prev, 16):
        return None
    at10 = _between(bars, ny_ms(day, 9, 55), ny_ms(day, 10))
    at15 = _between(bars, ny_ms(day, 14, 55), ny_ms(day, 15))
    if not at10 or not at15:
        return None
    r1 = at10[0]["c"] / prev[-1]["c"] - 1
    r12 = bars[-1]["c"] / at15[0]["c"] - 1
    if abs(r1) < p["min_r1"] or r1 == 0:
        return None
    side = "buy" if r1 > 0 else "sell"
    if p["confirm"] and (r12 > 0) != (r1 > 0):
        return None
    a = atr(bars[-60:], 14)
    if not a:
        return None
    c = bars[-1]["c"]
    stop = c - p["stop_atr"] * a if side == "buy" else c + p["stop_atr"] * a
    return Signal(side, c, stop, a, bars[-1]["t"], rr=4.0, exit_by=ny_ms(day, 16))


# --------------------------------------------------------------------------- crypto (24/7, higher timeframes)
# Crypto costs ~12 bp per round trip (0.03%/fill + slippage), so stops have to be wide (~1%+) for costs
# to stay a small slice of R: these work on 1h / 4h / daily structure built from the 5m feed.
DAY = 24 * HOUR


def _htf(bars, minutes):
    """Complete higher-timeframe bars (UTC-aligned) from 5m bars."""
    return resample(bars, 5, minutes // 5)


DONCHIAN_DEFAULTS = {"n": 20, "stop_atr": 3.0, "rr": 3.0}


def donchian_4h(bars, cfg):
    """On each 4h close (UTC grid): close above the highest high of the previous n 4h bars -> long,
    below the lowest low -> short. Stop stop_atr x ATR(14, 4h) from the close, target rr x risk."""
    p = _p(cfg, DONCHIAN_DEFAULTS)
    now = bars[-1]["t"] + SPAN
    if now % (4 * HOUR):
        return None
    h4 = _htf(bars, 240)
    n = p["n"]
    if len(h4) < max(n, 15) + 1 or h4[-1]["t"] + 4 * HOUR != now:
        return None
    a = atr(h4[-60:], 14)
    if not a:
        return None
    prev, c = h4[-n - 1:-1], h4[-1]["c"]
    hh, ll = max(b["h"] for b in prev), min(b["l"] for b in prev)
    if c > hh:
        return Signal("buy", c, c - p["stop_atr"] * a, a, bars[-1]["t"], rr=p["rr"])
    if c < ll:
        return Signal("sell", c, c + p["stop_atr"] * a, a, bars[-1]["t"], rr=p["rr"])
    return None


VOLBO_DEFAULTS = {"k": 0.5, "stop_range": 0.5, "rr": 2.0}


def vol_breakout(bars, cfg):
    """UTC day. Trigger level = today's open +/- k x yesterday's range. The first 5m close beyond
    either level (once per day) is traded in that direction; stop stop_range x yesterday's range
    from the close; target rr x risk; flat at the end of the UTC day."""
    p = _p(cfg, VOLBO_DEFAULTS)
    now = bars[-1]["t"] + SPAN
    d0 = now // DAY * DAY
    if now == d0:
        return None
    today = _between(bars, d0, now)
    yday = _between(bars, d0 - DAY, d0)
    if not today or today[0]["t"] != d0 or not _complete(yday, d0 - DAY, d0, 0.95):
        return None
    rng = max(b["h"] for b in yday) - min(b["l"] for b in yday)
    o = today[0]["o"]
    up, dn = o + p["k"] * rng, o - p["k"] * rng
    first = None
    for i, b in enumerate(today):
        if b["c"] > up:
            first = (i, "buy")
            break
        if b["c"] < dn:
            first = (i, "sell")
            break
    if not first or first[0] != len(today) - 1:
        return None
    side, c = first[1], bars[-1]["c"]
    stop = c - p["stop_range"] * rng if side == "buy" else c + p["stop_range"] * rng
    return Signal(side, c, stop, rng, bars[-1]["t"], rr=p["rr"], exit_by=d0 + DAY)


MR_DEFAULTS = {"z": 2.0, "stop_atr": 2.0, "trend": True}


def mean_reversion_1h(bars, cfg):
    """On each 1h close: z = (close - SMA20) / stdev20 on 1h closes. z <= -z_entry -> long,
    z >= +z_entry -> short (with trend: only longs above / shorts below the 200h EMA). Target the
    SMA20, stop stop_atr x ATR(14, 1h); skipped if the target is less than 0.5R away."""
    p = _p(cfg, MR_DEFAULTS)
    now = bars[-1]["t"] + SPAN
    if now % HOUR:
        return None
    h1 = _htf(bars, 60)
    if len(h1) < 210 or h1[-1]["t"] + HOUR != now:
        return None
    closes = [b["c"] for b in h1]
    w = closes[-20:]
    mean = sum(w) / 20
    sd = (sum((x - mean) ** 2 for x in w) / 20) ** 0.5
    a = atr(h1[-60:], 14)
    if not sd or not a:
        return None
    c = closes[-1]
    z = (c - mean) / sd
    ema = ema_series(closes[-210:], 200)[-1]
    side = None
    if z <= -p["z"] and (not p["trend"] or c > ema):
        side, stop = "buy", c - p["stop_atr"] * a
    elif z >= p["z"] and (not p["trend"] or c < ema):
        side, stop = "sell", c + p["stop_atr"] * a
    if not side or abs(mean - c) < 0.5 * abs(c - stop):
        return None
    return Signal(side, c, stop, a, bars[-1]["t"], target=mean)


STRATEGIES = {
    "trend_breakout": trend_breakout,
    "orb": orb,
    "sweep": sweep,
    "poc_rejection": poc_rejection,
    "gap_fade": gap_fade,
    "intraday_momentum": intraday_momentum,
    "donchian_4h": donchian_4h,
    "vol_breakout": vol_breakout,
    "mean_reversion_1h": mean_reversion_1h,
}

# how much history (ms) each strategy needs before the decision bar
LOOKBACK_MS = {
    "trend_breakout": 300 * SPAN,
    "orb": 4 * HOUR,
    "sweep": 16 * HOUR,
    "poc_rejection": 4 * 24 * HOUR,
    "gap_fade": 4 * 24 * HOUR,
    "intraday_momentum": 4 * 24 * HOUR,
    "donchian_4h": 62 * 4 * HOUR,
    "vol_breakout": 2 * DAY,
    "mean_reversion_1h": 215 * HOUR,
}

# strategies that can only fire on these boundaries (lets the backtester skip other bars cheaply)
STEP_MS = {"donchian_4h": 4 * HOUR, "mean_reversion_1h": HOUR}


def generate(bars, cfg):
    """bars: closed 5m bars, oldest first. Returns Signal or None."""
    if len(bars) < 3:
        return None
    return STRATEGIES[cfg.strategy](bars, cfg)
