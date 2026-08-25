"""
Aggregate the polymm synthetic simulation across many random seeds and
produce a single JSON report used to build the results dashboard.

A single seed is not a result -- it's a sample. Everything reported here is
averaged (with spread) across `--n-seeds` independent runs so the numbers
reflect the strategy's expected behavior under the modeled assumptions,
not one lucky/unlucky draw.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simulate import SimParams, simulate, summarize  # noqa: E402


def run_many(n_seeds: int, n_matches: int, seed0: int = 0, **overrides) -> list[dict]:
    out = []
    for i in range(n_seeds):
        params = SimParams(n_matches=n_matches, seed=seed0 + i, **overrides)
        df = simulate(params)
        out.append(summarize(df, params))
    return out


def agg(summaries: list[dict], key_path: list[str]) -> dict:
    vals = []
    for s in summaries:
        v = s
        for k in key_path:
            v = v[k]
        vals.append(v if v is not None else 0.0)
    arr = np.array(vals, dtype=float)
    return {
        "mean": round(float(arr.mean()), 2),
        "std": round(float(arr.std()), 2),
        "p10": round(float(np.percentile(arr, 10)), 2),
        "p90": round(float(np.percentile(arr, 90)), 2),
        "pct_positive": round(float((arr > 0).mean()), 3),
        "values": [round(float(x), 2) for x in arr],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n-seeds", type=int, default=200)
    p.add_argument("--n-matches", type=int, default=1500)
    p.add_argument("--out", type=str, default="report.json")
    args = p.parse_args()

    baseline = run_many(args.n_seeds, args.n_matches)

    # Adverse selection on/off comparison
    no_adverse = run_many(args.n_seeds, args.n_matches, adverse_selection_edge_erosion=0.0)

    # Latency sweep: how much does net P&L erode as the bot's hedge latency grows?
    # (Directly analogous to the real README's "it got too slow to defend its edge.")
    latency_grid = [0.3, 0.6, 1.0, 1.5, 2.2, 3.0]
    latency_sweep = []
    for lat in latency_grid:
        runs = run_many(60, args.n_matches, hedge_latency_sd=lat)
        latency_sweep.append({
            "hedge_latency_sd": lat,
            "net_pnl_mean": agg(runs, ["net_pnl"])["mean"],
            "hedge_success_rate_mean": round(float(np.mean([r["hedge_success_rate"] for r in runs])), 3),
        })

    # min_edge sweep: does a tighter/looser edge threshold help?
    edge_grid = [0.03, 0.05, 0.07, 0.10, 0.13, 0.17]
    edge_sweep = []
    for me in edge_grid:
        runs = run_many(60, args.n_matches, min_edge=me)
        edge_sweep.append({
            "min_edge": me,
            "net_pnl_mean": agg(runs, ["net_pnl"])["mean"],
            "trades_mean": round(float(np.mean([r["orders_filled"] for r in runs])), 1),
        })

    # One representative single run (median-net-pnl seed) for the trade-level charts
    seeds_net = [s["net_pnl"] for s in baseline]
    median_idx = int(np.argsort(seeds_net)[len(seeds_net) // 2])
    rep_params = SimParams(n_matches=args.n_matches, seed=median_idx)
    rep_df = simulate(rep_params)
    rep_df = rep_df.sort_values("match_id")
    traded = rep_df[rep_df["filled"]].copy()
    traded["cum_pnl"] = traded["pnl"].cumsum()
    traded["cum_pnl_arb"] = traded["pnl"].where(traded["leg"] == "arb", 0).cumsum()
    traded["cum_pnl_residual"] = traded["pnl"].where(traded["leg"] == "residual", 0).cumsum()

    report = {
        "default_params": SimParams().to_dict(),
        "n_seeds": args.n_seeds,
        "n_matches_per_seed": args.n_matches,
        "baseline": {
            "net_pnl": agg(baseline, ["net_pnl"]),
            "arb_pnl": agg(baseline, ["arb_leg", "pnl"]),
            "residual_pnl": agg(baseline, ["residual_leg", "pnl"]),
            "trades": agg(baseline, ["orders_filled"]),
            "hedge_success_rate": agg(baseline, ["hedge_success_rate"]),
        },
        "no_adverse_selection": {
            "net_pnl": agg(no_adverse, ["net_pnl"]),
            "residual_pnl": agg(no_adverse, ["residual_leg", "pnl"]),
        },
        "latency_sweep": latency_sweep,
        "edge_sweep": edge_sweep,
        "representative_run": {
            "seed": median_idx,
            "match_id": traded["match_id"].tolist(),
            "cum_pnl": [round(x, 2) for x in traded["cum_pnl"].tolist()],
            "cum_pnl_arb": [round(x, 2) for x in traded["cum_pnl_arb"].tolist()],
            "cum_pnl_residual": [round(x, 2) for x in traded["cum_pnl_residual"].tolist()],
            "leg": traded["leg"].tolist(),
            "pnl": [round(x, 2) for x in traded["pnl"].tolist()],
            "edge": [round(x, 4) for x in traded["edge"].tolist()],
        },
    }

    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"wrote {args.out}")
    print(json.dumps({
        "baseline_net_pnl_mean": report["baseline"]["net_pnl"]["mean"],
        "baseline_net_pnl_std": report["baseline"]["net_pnl"]["std"],
        "pct_seeds_profitable": report["baseline"]["net_pnl"]["pct_positive"],
        "arb_pnl_mean": report["baseline"]["arb_pnl"]["mean"],
        "residual_pnl_mean": report["baseline"]["residual_pnl"]["mean"],
    }, indent=2))


if __name__ == "__main__":
    main()
