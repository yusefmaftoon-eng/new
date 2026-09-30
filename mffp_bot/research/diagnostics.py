"""Post-hoc diagnostics, run only AFTER the holdout was scored. Nothing here feeds back into selection.

1. gross vs net: the frozen configs re-run with zero fees/swap/slippage, to separate "costs ate the
   edge" from "there was no edge".
2. whole-grid holdout scan: how every grid point did on the holdout, to check the pick rule didn't
   simply miss a qualifying config.
3. zero-edge baseline under the bot's own guardian rules (run_eval), with and without costs.
"""
import json
import os
import random
import sys
from dataclasses import replace
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
from backtest import eval_rates, merge, run_eval, simulate, stats  # noqa: E402
from config import Config  # noqa: E402
from run_research import GRIDS, MARKETS, SELECTED, SPLIT, bars_for, cfg_for, points  # noqa: E402


def _ho(job):
    s, pt, m = job
    return job, simulate(bars_for(m, holdout=True), cfg_for(s, pt, m), start_t=SPLIT)


def gross_vs_net(sel):
    print("1. gross (no fees/swap/slippage) vs net, frozen configs")
    out = {}
    for s, r in sel["strategies"].items():
        pt = tuple(r["params"].items())
        row = {}
        for label, holdout in (("is", False), ("ho", True)):
            net, gross = [], []
            for m in r["markets"]:
                c = cfg_for(s, pt, m)
                g = replace(c, fee_rate=0.0, swap_daily_rate=0.0, slippage_bps=0.0)
                kw = {"start_t": SPLIT} if holdout else {}
                net.append(simulate(bars_for(m, holdout), c, **kw))
                gross.append(simulate(bars_for(m, holdout), g, **kw))
            row[label] = {"net": stats(merge(*net))["avg_r"], "gross": stats(merge(*gross))["avg_r"]}
        out[s] = row
        print(f"   {s:18s} IS gross {row['is']['gross']:+.3f}R net {row['is']['net']:+.3f}R | "
              f"holdout gross {row['ho']['gross']:+.3f}R net {row['ho']['net']:+.3f}R")
    return out


def grid_scan():
    print("2. every grid point on the holdout (diagnostic only)")
    jobs = [(s, pt, m) for s in GRIDS for pt in points(s) for m in MARKETS[s]]
    with Pool(4) as pool:
        res = dict(pool.imap_unordered(_ho, jobs, chunksize=4))
    out = {}
    for s in GRIDS:
        rows = []
        for pt in points(s):
            for mk in [[m] for m in MARKETS[s]] + ([MARKETS[s]] if len(MARKETS[s]) > 1 else []):
                tr = merge(*[res[(s, pt, m)] for m in mk])
                st = stats(tr)
                ev = eval_rates(tr, cfg_for(s, pt, mk[0])) if tr else {"pass_finished": 0, "fail_finished": 0,
                                                                         "halted_finished": 0}
                qual = (st["trades"] >= 60 and st["avg_r"] > 0 and st["pf"] >= 1.2 and ev["pass_finished"] > 0.35
                        and ev["fail_finished"] < ev["pass_finished"])
                rows.append((st["avg_r"], st["pf"], st["trades"], ev["pass_finished"], qual, pt, mk))
        pos = sum(1 for r in rows if r[0] > 0 and r[2] >= 30) / len(rows)
        best = max(rows, key=lambda r: r[0] if r[2] >= 30 else -9)
        nq = sum(r[4] for r in rows)
        out[s] = {"combos": len(rows), "positive_share": pos, "qualifying": nq,
                  "best": {"avg_r": best[0], "pf": best[1], "trades": best[2], "pass": best[3],
                           "params": dict(best[5]), "markets": best[6]}}
        print(f"   {s:18s} {len(rows):3d} combos | avgR>0 (n>=30): {pos:.0%} | qualifying: {nq} | best "
              f"{best[0]:+.3f}R PF {best[1]:.2f} n={best[2]} pass {best[3]:.0%} {dict(best[5])} {best[6]}")
    return out


def baseline(n_runs=4000, cost_r=0.0, seed=1):
    """Zero-edge +/-1R coin flips (minus cost_r per trade), 2 trades/day, replayed with run_eval."""
    rnd = random.Random(seed)
    c = Config()
    counts = {"PASS": 0, "FAIL": 0, "halted": 0, "unfinished": 0}
    for _ in range(n_runs):
        trades = []
        for k in range(1500):
            pnl = (45.0 if rnd.random() < 0.5 else -45.0) - cost_r * 45
            trades.append({"t": 1_780_000_000_000 + (k // 2) * 86_400_000 + k % 2,
                           "pnl": pnl, "mae": 45.0 if pnl < 0 else 10.0})
        counts[run_eval(trades, c, "2000-01-01")[0]] += 1
    done = n_runs - counts["unfinished"]
    return {k: v / done for k, v in counts.items() if k != "unfinished"}


def main():
    with open(SELECTED) as f:
        sel = json.load(f)
    out = {"gross_vs_net": gross_vs_net(sel)}
    out["grid_holdout"] = grid_scan()
    print("3. zero-edge baseline with the bot's guardian (daily stop, kill buffer, shrinking risk)")
    out["baseline"] = {}
    for cost in (0.0, 0.10):
        b = baseline(cost_r=cost)
        out["baseline"][f"cost_{cost:.2f}R"] = b
        print(f"   +/-1R coin flip, costs {cost:.2f}R/trade: pass {b['PASS']:.0%} fail {b['FAIL']:.0%} "
              f"halted {b['halted']:.0%}")
    with open(os.path.join(HERE, "diagnostics.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)


if __name__ == "__main__":
    main()
