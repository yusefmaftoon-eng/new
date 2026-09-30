"""Strategy research with a locked holdout.

python research/run_research.py select     # in-sample only: grid + walk-forward, writes selected.json
python research/run_research.py holdout    # runs ONLY the configs frozen in selected.json on the holdout

Protocol
- Data: ~24.8 months of 5m bars (histdata proxy, validated against the platform's own bars).
- Split: everything before SPLIT (70% of the span) is in-sample; the rest is the holdout.
  `select` truncates the data at SPLIT before doing anything, so it cannot see the holdout.
- Walk-forward inside the in-sample: 6 equal blocks; 4 anchored folds (train blocks 0..k-1,
  test block k for k = 2..5). At every fold the config is picked on train only.
- Pick rule (same in every fold and for the final pick): maximise expectancy in R per trade,
  smoothed over the grid point and its immediate neighbours (min 30 train trades), so a lone
  peak loses to a good neighbourhood.
- Markets: a strategy's config is chosen on all its markets together (one position at a time,
  like the bot); afterwards a market is dropped if its own in-sample expectancy at that config is
  negative (unless it is the only one left).
"""
import itertools
import json
import os
import statistics
import sys
from datetime import date, datetime
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from backtest import eval_rates, load_bars, merge, simulate, stats  # noqa: E402
from config import Config  # noqa: E402
from strategy import NY, ny_ms  # noqa: E402

DATA = {"GOLD": "XAUUSD_5m.csv", "SP500": "SPXUSD_5m.csv"}
SPLIT = ny_ms(date(2026, 2, 10), 0)          # 70% of 2024-09-01 .. 2026-09-24
SELECTED = os.path.join(HERE, "selected.json")
MIN_TRADES = 30

# (param name, values, ordered?) - at most 4 tunables per strategy, coarse steps
GRIDS = {
    "orb": [("or_min", [15, 30], True), ("entry", ["break", "retest", "fvg"], False),
            ("stop", ["mid", "far"], False), ("rr", [1.0, 1.5, 2.0, 3.0], True)],
    "sweep": [("ref", ["asia", "london"], False), ("swing", [3, 6], True),
              ("rr", [1.0, 1.5, 2.0, 3.0, "opp"], True), ("window_end", [10, 11, 12], True)],
    "poc_rejection": [("profile", ["rth", "full"], False), ("rr", [1.0, 1.5, 2.0, 3.0], True),
                      ("buffer_atr", [0.25, 0.75], True), ("approach_atr", [1.0, 2.0], True)],
    "gap_fade": [("min_gap", [0.001, 0.002, 0.003, 0.005], True), ("stop_gap", [0.5, 1.0, 1.5], True),
                 ("target", ["fill", "half"], False)],
    "intraday_momentum": [("min_r1", [0.0, 0.002, 0.005], True), ("stop_atr", [2.0, 3.0, 5.0], True),
                          ("confirm", [False, True], False)],
}
MARKETS = {"orb": ["SP500", "GOLD"], "sweep": ["SP500", "GOLD"], "poc_rejection": ["GOLD"],
           "gap_fade": ["SP500"], "intraday_momentum": ["SP500"]}

_BARS = {}


def bars_for(market, holdout=False):
    key = (market, holdout)
    if key not in _BARS:
        bs = load_bars(None, 0, os.path.join(HERE, "..", "data", DATA[market]))
        _BARS[key] = bs if holdout else [b for b in bs if b["t"] < SPLIT]
    return _BARS[key]


def cfg_for(strategy, params, market):
    return Config(strategy=strategy, params=dict(params)).for_market(market)


def _sim(job):
    strategy, params, market = job
    return job, simulate(bars_for(market), cfg_for(strategy, params, market))


def points(strategy):
    g = GRIDS[strategy]
    return [tuple(zip([n for n, _, _ in g], combo)) for combo in itertools.product(*[v for _, v, _ in g])]


def neighbours(strategy, pt):
    g, out = GRIDS[strategy], []
    for d, (name, vals, ordered) in enumerate(g):
        i = vals.index(pt[d][1])
        alts = [i - 1, i + 1] if ordered else [j for j in range(len(vals)) if j != i]
        for j in alts:
            if 0 <= j < len(vals):
                q = list(pt)
                q[d] = (name, vals[j])
                out.append(tuple(q))
    return out


def window(trades, t0, t1):
    return [t for t in trades if t0 <= t["t"] < t1]


def score(trades):
    return statistics.mean(t["r"] for t in trades) if len(trades) >= MIN_TRADES else None


def pick(strategy, results, t0, t1, markets):
    """Best smoothed expectancy on [t0, t1) using the given markets."""
    raw = {pt: score(window(merge(*[results[(pt, m)] for m in markets]), t0, t1)) for pt in points(strategy)}
    smooth = {}
    for pt, s in raw.items():
        if s is None:
            continue
        vals = [s] + [raw[q] for q in neighbours(strategy, pt) if raw.get(q) is not None]
        smooth[pt] = statistics.mean(vals)
    best = max(smooth, key=smooth.get)
    return best, raw, smooth


def fmt_pt(pt):
    return ", ".join(f"{k}={v}" for k, v in pt)


def select():
    t_start = min(bars_for(m)[0]["t"] for m in DATA)
    edges = [t_start + (SPLIT - t_start) * k // 6 for k in range(7)]
    jobs = [(s, pt, m) for s in GRIDS for pt in points(s) for m in MARKETS[s]]
    print(f"in-sample {day(t_start)} .. {day(SPLIT)} | {len(jobs)} simulations", flush=True)
    with Pool(4) as pool:
        res = {}
        for (s, pt, m), tr in pool.imap_unordered(_sim, jobs, chunksize=4):
            res.setdefault(s, {})[(pt, m)] = tr
    report = {}
    for s in GRIDS:
        results, mk = res[s], MARKETS[s]
        # walk-forward
        folds, oos = [], []
        for k in range(2, 6):
            best, _, _ = pick(s, results, edges[0], edges[k], mk)
            test = window(merge(*[results[(best, m)] for m in mk]), edges[k], edges[k + 1])
            oos += test
            st = stats(test)
            folds.append({"train": [day(edges[0]), day(edges[k])], "test": [day(edges[k]), day(edges[k + 1])],
                          "pick": fmt_pt(best), "test_trades": st["trades"], "test_avg_r": st["avg_r"],
                          "test_pf": st["pf"]})
        # final pick on the whole in-sample
        best, raw, smooth = pick(s, results, edges[0], SPLIT, mk)
        per_mkt = {m: stats(results[(best, m)]) for m in mk}
        keep = [m for m in mk if per_mkt[m]["avg_r"] >= 0] or [max(mk, key=lambda m: per_mkt[m]["avg_r"])]
        is_trades = merge(*[results[(best, m)] for m in keep])
        nb = {fmt_pt(q): raw.get(q) for q in neighbours(s, best)}
        nb_vals = [v for v in nb.values() if v is not None]
        cfg = cfg_for(s, best, keep[0])
        report[s] = {
            "params": dict(best), "markets": keep, "markets_tested": mk,
            "per_market_is": per_mkt,
            "is": stats(is_trades), "is_eval": eval_rates(is_trades, cfg),
            "wf_oos": stats(oos), "wf_folds": folds,
            "raw_avg_r": raw.get(best), "smoothed_avg_r": smooth[best],
            "neighbours": nb,
            "stable": bool(smooth[best] > 0 and nb_vals and sum(v > 0 for v in nb_vals) >= len(nb_vals) / 2),
            "grid_size": len(points(s)),
            "grid_positive_share": sum(1 for v in raw.values() if v is not None and v > 0) / len(raw),
        }
        print_is(s, report[s])
    with open(SELECTED, "w") as f:
        json.dump({"split": day(SPLIT), "frozen_at": datetime.now().isoformat(timespec="seconds"),
                   "strategies": report}, f, indent=1, default=str)
    print(f"\nfrozen -> {SELECTED}")


def print_is(s, r):
    i, w, e = r["is"], r["wf_oos"], r["is_eval"]
    print(f"\n== {s} | pick {r['params']} | markets {r['markets']} (tested {r['markets_tested']})")
    print(f"   IS : n={i['trades']} win {i['win_rate']:.0%} avgR {i['avg_r']:+.3f} PF {i['pf']:.2f} maxDD ${i['max_dd']:.0f}"
          f" | eval pass {e['pass_finished']:.0%} fail {e['fail_finished']:.0%} halted {e['halted_finished']:.0%}")
    print(f"   WF OOS (4 folds): n={w['trades']} win {w['win_rate']:.0%} avgR {w['avg_r']:+.3f} PF {w['pf']:.2f}")
    for f in r["wf_folds"]:
        print(f"      test {f['test'][0]}..{f['test'][1]} pick [{f['pick']}] n={f['test_trades']} avgR {f['test_avg_r']:+.3f}")
    print(f"   smoothed {r['smoothed_avg_r']:+.3f} raw {r['raw_avg_r']:+.3f} stable={r['stable']} "
          f"| grid points with avgR>0: {r['grid_positive_share']:.0%}")


def holdout():
    with open(SELECTED) as f:
        sel = json.load(f)
    out = {}
    for s, r in sel["strategies"].items():
        params = tuple(r["params"].items())
        per = {}
        for m in r["markets_tested"]:
            per[m] = simulate(bars_for(m, holdout=True), cfg_for(s, params, m), start_t=SPLIT)
        trades = merge(*[per[m] for m in r["markets"]])
        cfg = cfg_for(s, params, r["markets"][0])
        out[s] = {"params": r["params"], "markets": r["markets"], "ho": stats(trades),
                  "ho_eval": eval_rates(trades, cfg),
                  "ho_per_market": {m: stats(per[m]) for m in r["markets_tested"]}}
        h, e = out[s]["ho"], out[s]["ho_eval"]
        ok = (h["trades"] >= 60 and h["avg_r"] > 0 and h["pf"] >= 1.2 and e["pass_finished"] > 0.35
              and e["fail_finished"] < e["pass_finished"])
        out[s]["qualifies"] = ok
        print(f"\n== {s} {r['params']} {r['markets']}")
        print(f"   HOLDOUT: n={h['trades']} win {h['win_rate']:.0%} avgR {h['avg_r']:+.3f} PF {h['pf']:.2f} "
              f"maxDD ${h['max_dd']:.0f} net ${h['net']:.0f}")
        print(f"   eval: runs {e['runs']} PASS {e['PASS']} FAIL {e['FAIL']} halted {e['halted']} unfinished {e['unfinished']}"
              f" | finished: pass {e['pass_finished']:.0%} fail {e['fail_finished']:.0%} halted {e['halted_finished']:.0%}")
        for m, st in out[s]["ho_per_market"].items():
            print(f"   {m}: n={st['trades']} avgR {st['avg_r']:+.3f} PF {st['pf']:.2f}")
        print(f"   QUALIFIES: {ok}")
    with open(os.path.join(HERE, "holdout.json"), "w") as f:
        json.dump({"split": sel["split"], "frozen_at": sel["frozen_at"], "strategies": out}, f, indent=1, default=str)


def day(ms):
    return datetime.fromtimestamp(ms / 1000, NY).strftime("%Y-%m-%d")


if __name__ == "__main__":
    {"select": select, "holdout": holdout}[sys.argv[1]]()
