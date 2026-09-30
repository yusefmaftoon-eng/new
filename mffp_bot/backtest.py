"""Backtest the strategy and estimate the $10K eval pass rate.

python backtest.py --days 60             # pulls 5m candles from the MyFundedPerps feed
python backtest.py --csv btc_5m.csv      # or use cached/your own data (t,o,h,l,c,v; t = open ms)
"""
import argparse
import csv
import os
import statistics
import time
from datetime import datetime

from config import Config
from risk import position_size
from strategy import NY, bracket, generate

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


def simulate(bars, cfg):
    """Every trade the strategy would take at a fixed $risk_per_trade. Conservative fills:
    next-bar-open entry + slippage, stop wins if stop and target share a bar, gaps honored."""
    span, slip, R = cfg.interval_min * 60_000, cfg.slippage_bps / 10_000, cfg.risk_per_trade
    trades, i, look = [], 300, 300
    while i < len(bars) - 1:
        sig = generate(bars[i - look:i + 1], cfg)
        if not sig:
            i += 1
            continue
        long, eb = sig.side == "buy", bars[i + 1]
        fill = eb["o"] * (1 + slip if long else 1 - slip)
        if (long and fill <= sig.stop) or (not long and fill >= sig.stop):
            i += 1
            continue
        size = position_size(R, fill, sig.stop, cfg, BASE)
        if size <= 0:
            i += 1
            continue
        tp = bracket(sig.side, fill, sig.stop, cfg.reward_risk)
        mae, exit_px, j = 0.0, None, i + 1
        while j < len(bars):
            b = bars[j]
            if (long and b["l"] <= sig.stop) or (not long and b["h"] >= sig.stop):
                px = min(b["o"], sig.stop) if long else max(b["o"], sig.stop)
                exit_px = px * (1 - slip if long else 1 + slip)
                mae = max(mae, abs(fill - exit_px))
                break
            mae = max(mae, (fill - b["l"]) if long else (b["h"] - fill))
            if (long and b["h"] >= tp) or (not long and b["l"] <= tp):
                exit_px = tp
                break
            j += 1
        if exit_px is None:
            break
        exit_t = bars[j]["t"] + span
        fees = (fill + exit_px) * size * cfg.fee_rate
        swap = (exit_t // 3_600_000 - eb["t"] // 3_600_000) * fill * size * cfg.swap_daily_rate / 24
        pnl = (exit_px - fill) * size * (1 if long else -1) - fees - swap
        trades.append({"t": eb["t"], "side": sig.side, "pnl": pnl, "mae": mae * size + fill * size * cfg.fee_rate})
        i = j + 1 + cfg.cooldown_bars
    return trades


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--csv", default="btc_5m.csv")
    ap.add_argument("--rr", type=float)
    ap.add_argument("--risk", type=float)
    a = ap.parse_args()
    cfg = Config()
    if a.rr:
        cfg.reward_risk = a.rr
    if a.risk:
        cfg.risk_per_trade = a.risk

    bars = load_bars(cfg, a.days, a.csv)
    if len(bars) < 400:
        raise SystemExit(f"Only {len(bars)} bars - not enough history.")
    span_days = (bars[-1]["t"] - bars[0]["t"]) / 86_400_000
    trades = simulate(bars, cfg)
    print(f"\nData: {len(bars)} x {cfg.interval_min}m bars ({span_days:.1f} days) | "
          f"risk ${cfg.risk_per_trade:.0f}/trade | {cfg.reward_risk}R target")
    if not trades:
        raise SystemExit("No trades.")

    pnl = [t["pnl"] for t in trades]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    eq = peak = mdd = 0.0
    for p in pnl:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    pf = sum(wins) / -sum(losses) if losses and sum(losses) else float("inf")
    print(f"Trades {len(trades)} | win rate {len(wins) / len(trades):.0%} | "
          f"avg win ${statistics.mean(wins) if wins else 0:.0f} | avg loss ${statistics.mean(losses) if losses else 0:.0f}")
    print(f"Net ${sum(pnl):.0f} | expectancy ${statistics.mean(pnl):.2f}/trade | PF {pf:.2f} | max DD ${mdd:.0f}")

    starts = sorted({ny_day(t["t"]) for t in trades})
    results = [run_eval(trades, cfg, s) for s in starts]
    counts = {k: sum(1 for r in results if r[0] == k) for k in ("PASS", "FAIL", "halted", "unfinished")}
    done = len(results) - counts["unfinished"]
    print(f"\nEval replays (one per start day): {len(results)}")
    for k, v in counts.items():
        print(f"  {k:<10} {v:>4}  ({v / len(results):.0%})")
    if done:
        print(f"  pass rate among finished runs: {counts['PASS'] / done:.0%}")
    pdays = [r[2] for r in results if r[0] == "PASS"]
    if pdays:
        print(f"  median trading days to pass: {statistics.median(pdays):.0f}")
    print("\nZero-edge baseline: a coin-flip walk hits +$900 before -$300 only ~25% of the time.")


if __name__ == "__main__":
    main()
