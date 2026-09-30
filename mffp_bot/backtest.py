"""Backtest the strategy and estimate the $10K eval pass rate.

python backtest.py --days 60                                  # pulls 5m candles from the MyFundedPerps feed
python backtest.py --csv btc_5m.csv                           # or cached/your own data (t,o,h,l,c,v; t = open ms)
python backtest.py --symbol GOLD --strategy orb --csv data/XAUUSD_5m.csv
"""
import argparse
import bisect
import csv
import os
import statistics
import time
from datetime import datetime

from config import Config
from risk import position_size
from strategy import LOOKBACK_MS, NY, generate, take_profit

BASE = 10_000.0


def load_bars(cfg, days, path):
    if path and os.path.exists(path):
        with open(path) as f:
            return [{k: (int(float(v)) if k == "t" else float(v)) for k, v in r.items()} for r in csv.DictReader(f)]
    from market_data import fetch_range
    end = int(time.time() * 1000)
    bars = fetch_range(cfg.provider, cfg.coin, f"{cfg.interval_min}m", end - days * 86_400_000, end)
    if path:
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["t", "o", "h", "l", "c", "v"])
            w.writeheader()
            w.writerows(bars)
    return bars


def simulate(bars, cfg, start_t=None, end_t=None):
    """Every trade the strategy would take at a fixed $risk_per_trade. Conservative fills:
    next-bar-open entry + slippage (skipped if the next bar isn't contiguous, i.e. the market was
    closed), stop wins if stop and target share a bar, gaps honored, slippage on every exit
    (stop, target and time exit), fees per fill + hourly swap at the market's asset-class rates.
    Signals are only generated for decision bars with start_t <= t < end_t (history before start_t
    is used as warm-up); a trade opened before end_t is followed to its exit."""
    span, slip, R = cfg.interval_min * 60_000, cfg.slippage_bps / 10_000, cfg.risk_per_trade
    look = LOOKBACK_MS.get(cfg.strategy, 300 * span)
    ts = [b["t"] for b in bars]
    first = bisect.bisect_left(ts, max(ts[0] + look, start_t or 0)) if bars else 0
    last = bisect.bisect_left(ts, end_t) if end_t else len(bars)
    trades, i = [], first
    while i < min(last, len(bars) - 1):
        lo = bisect.bisect_left(ts, ts[i] - look, 0, i)
        sig = generate(bars[lo:i + 1], cfg)
        if not sig:
            i += 1
            continue
        long, eb = sig.side == "buy", bars[i + 1]
        if eb["t"] != bars[i]["t"] + span:                 # market closed after the signal bar
            i += 1
            continue
        fill = eb["o"] * (1 + slip if long else 1 - slip)
        if (long and fill <= sig.stop) or (not long and fill >= sig.stop):
            i += 1
            continue
        size = position_size(R, fill, sig.stop, cfg, BASE)
        tp = take_profit(sig, fill, cfg)
        if size <= 0 or (long and tp <= fill) or (not long and tp >= fill):
            i += 1
            continue
        mae, exit_px, how, j = 0.0, None, None, i + 1
        while j < len(bars):
            b = bars[j]
            if sig.exit_by is not None and b["t"] >= sig.exit_by and j > i + 1:
                exit_px, how = b["o"] * (1 - slip if long else 1 + slip), "time"
                mae = max(mae, (fill - exit_px) if long else (exit_px - fill))
                break
            if (long and b["l"] <= sig.stop) or (not long and b["h"] >= sig.stop):
                px = min(b["o"], sig.stop) if long else max(b["o"], sig.stop)
                exit_px, how = px * (1 - slip if long else 1 + slip), "stop"
                mae = max(mae, abs(fill - exit_px))
                break
            mae = max(mae, (fill - b["l"]) if long else (b["h"] - fill))
            if (long and b["h"] >= tp) or (not long and b["l"] <= tp):
                exit_px, how = tp * (1 - slip if long else 1 + slip), "target"
                break
            j += 1
        if exit_px is None:
            break
        exit_t = bars[j]["t"] + (0 if how == "time" else span)
        fees = (fill + exit_px) * size * cfg.fee_rate
        swap = (exit_t // 3_600_000 - eb["t"] // 3_600_000) * fill * size * cfg.swap_daily_rate / 24
        pnl = (exit_px - fill) * size * (1 if long else -1) - fees - swap
        trades.append({"t": eb["t"], "xt": exit_t, "side": sig.side, "pnl": pnl, "r": pnl / R, "exit": how,
                       "symbol": cfg.symbol, "notional": fill * size,
                       "mae": mae * size + fill * size * cfg.fee_rate})
        i = j + 1 + cfg.cooldown_bars
    return trades


def merge(*trade_lists):
    """Combine several markets' trades the way the bot trades them: one position at a time."""
    out, busy_until = [], 0
    for t in sorted((t for tl in trade_lists for t in tl), key=lambda t: t["t"]):
        if t["t"] >= busy_until:
            out.append(t)
            busy_until = t["xt"]
    return out


def stats(trades):
    """Trade-level summary at the fixed $risk_per_trade the trades were simulated with."""
    pnl = [t["pnl"] for t in trades]
    if not pnl:
        return {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "pf": 0.0, "net": 0.0, "max_dd": 0.0}
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    eq = peak = mdd = 0.0
    for p in pnl:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return {"trades": len(pnl), "win_rate": len(wins) / len(pnl),
            "avg_r": statistics.mean(t["r"] for t in trades),
            "pf": sum(wins) / -sum(losses) if losses and sum(losses) else float("inf"),
            "net": sum(pnl), "max_dd": mdd}


def ny_day(ms):
    return datetime.fromtimestamp(ms / 1000, NY).strftime("%Y-%m-%d")


def run_eval(trades, cfg, start_day):
    """Replays trades from start_day with the bot's guardian rules. Returns (outcome, eq, days)."""
    eq, day, day_start, n_today, days = 0.0, None, 0.0, 0, set()
    for t in trades:
        d = ny_day(t["t"])
        if d < start_day:
            continue
        if d != day:
            day, day_start, n_today = d, eq, 0
        if eq - day_start <= -cfg.daily_stop or n_today >= cfg.max_trades_per_day:
            continue
        floor = max(-cfg.max_drawdown, day_start - cfg.daily_loss)
        room = eq - floor
        budget = min(cfg.risk_per_trade, (room - cfg.kill_buffer) * 0.5)
        if room <= cfg.kill_buffer or budget < cfg.min_risk:
            return "halted", eq, len(days)
        scale = budget / cfg.risk_per_trade
        n_today += 1
        days.add(d)
        if eq - t["mae"] * scale <= floor:
            return "FAIL", eq - t["mae"] * scale, len(days)
        eq += t["pnl"] * scale
        if eq >= cfg.profit_target + cfg.target_buffer:
            return "PASS", eq, len(days)
    return "unfinished", eq, len(days)


def eval_rates(trades, cfg):
    """run_eval from every start day that has a trade. Rates are over all start days and over
    finished runs (PASS + FAIL + halted); 'unfinished' runs ran out of data first."""
    starts = sorted({ny_day(t["t"]) for t in trades})
    results = [run_eval(trades, cfg, s) for s in starts]
    counts = {k: sum(1 for r in results if r[0] == k) for k in ("PASS", "FAIL", "halted", "unfinished")}
    done = len(results) - counts["unfinished"]
    pdays = [r[2] for r in results if r[0] == "PASS"]
    return {"runs": len(results), **counts,
            "pass_all": counts["PASS"] / len(results) if results else 0.0,
            "pass_finished": counts["PASS"] / done if done else 0.0,
            "fail_finished": counts["FAIL"] / done if done else 0.0,
            "halted_finished": counts["halted"] / done if done else 0.0,
            "median_days_to_pass": statistics.median(pdays) if pdays else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--csv", default="btc_5m.csv")
    ap.add_argument("--symbol", default="BTC", help="market key from config.MARKETS")
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--rr", type=float)
    ap.add_argument("--risk", type=float)
    a = ap.parse_args()
    cfg = Config()
    if a.strategy:
        cfg.strategy = a.strategy
    cfg = cfg.for_market(a.symbol)
    if a.rr:
        cfg.reward_risk = a.rr
    if a.risk:
        cfg.risk_per_trade = a.risk

    bars = load_bars(cfg, a.days, a.csv)
    if len(bars) < 400:
        raise SystemExit(f"Only {len(bars)} bars - not enough history.")
    span_days = (bars[-1]["t"] - bars[0]["t"]) / 86_400_000
    trades = simulate(bars, cfg)
    print(f"\n{cfg.symbol} ({cfg.asset_class}: fee {cfg.fee_rate:.4%}/fill, swap {cfg.swap_daily_rate:.3%}/day, "
          f"slip {cfg.slippage_bps}bp) | {cfg.strategy} | {len(bars)} x {cfg.interval_min}m bars ({span_days:.1f} days) "
          f"| risk ${cfg.risk_per_trade:.0f}/trade")
    if not trades:
        raise SystemExit("No trades.")

    s = stats(trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    print(f"Trades {s['trades']} | win rate {s['win_rate']:.0%} | "
          f"avg win ${statistics.mean(wins) if wins else 0:.0f} | avg loss ${statistics.mean(losses) if losses else 0:.0f}")
    print(f"Net ${s['net']:.0f} | expectancy ${s['net'] / s['trades']:.2f}/trade ({s['avg_r']:+.3f}R) | "
          f"PF {s['pf']:.2f} | max DD ${s['max_dd']:.0f}")

    e = eval_rates(trades, cfg)
    print(f"\nEval replays (one per start day): {e['runs']}")
    for k in ("PASS", "FAIL", "halted", "unfinished"):
        print(f"  {k:<10} {e[k]:>4}  ({e[k] / e['runs']:.0%})")
    if e["runs"] - e["unfinished"]:
        print(f"  pass rate among finished runs: {e['pass_finished']:.0%}")
    if e["median_days_to_pass"]:
        print(f"  median trading days to pass: {e['median_days_to_pass']:.0f}")
    print("\nZero-edge baseline: a coin-flip walk hits +$900 before -$300 only ~25% of the time.")


if __name__ == "__main__":
    main()
