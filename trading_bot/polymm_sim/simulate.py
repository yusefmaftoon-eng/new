"""
Synthetic simulation of the polymm strategy (kachence/polymm).

WHY SYNTHETIC. This session's network egress is blocked for every relevant
live data source (data-api.polymarket.com, gamma-api.polymarket.com, and
the-odds-api.com all reject the connection at the proxy level — confirmed
by hand, see trading_bot/polymm_sim/README.md). polymm also isn't a
backtester: it's a live orchestrator with two websockets and no bundled
historical dataset. So a real backtest or a live paper trade both need
infrastructure this sandbox doesn't have. What follows instead is a
Monte Carlo simulation: it runs polymm's actual de-vig math
(vig_removal.py, copied verbatim) against a synthetic-but-documented model
of bookmaker odds and Polymarket order books, and reports what that
strategy logic would earn under the assumptions below. Every assumption is
a parameter you can see and change; none of it is real market data, and
the resulting numbers are not a prediction of real performance.

THE STRATEGY BEING SIMULATED (from the polymm README):
  1. Pull bookmaker odds, de-vig them to a fair probability.
  2. If fair_prob - polymarket_best_bid >= min_edge, quote a limit buy
     one cent above the best bid, sized at default_shares.
  3. If filled, immediately try to hedge the other outcome so both legs
     are owned for under $1/share combined (locked arb).
  4. If the hedge leg doesn't fill before the market catches up, you're
     left holding a naked directional position (the "residual") that
     settles on the actual match outcome.

THE SYNTHETIC MARKET MODEL:
  - Each simulated match has a true outcome probability `p_true`, drawn
    from a Beta(2, 2) distribution rescaled to [0.05, 0.95] -- symmetric,
    concentrated around competitive matchups with a realistic tail of
    heavy favorites, matching the shape of moneyline distributions across
    a season of mixed sports.
  - `n_books` bookmakers each quote odds implying `p_true` plus their own
    noise (sharp but not perfect) and their own vig (3-8%, typical for
    sports moneylines). The "aggregated" odds taken forward are the best
    (highest) odds available on each side across books, mimicking what an
    odds-aggregation API like the-odds-api returns.
  - Those aggregated odds are run through polymm's *actual*
    `calculate_fair_odds` to get the de-vigged fair probability -- this
    is real strategy code, not a re-implementation.
  - With probability `shock_prob`, a match just had a "shock" (news,
    injury, in-play swing) that has already moved the sportsbook price
    but that Polymarket has only partially caught up to (`catchup_frac`,
    controlled by `market_speed`). This partial catch-up is what creates
    the edge the strategy is designed to find; without a shock there is
    usually no tradeable edge.
  - Polymarket's own bid/ask carries a small spread (`poly_spread`).

None of these parameters were fit to real Polymarket/odds data (network
was unavailable). They're chosen to be directionally realistic and are
all listed in the CLI --help and the run manifest so you can interrogate
or replace them.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vig_removal import calculate_fair_odds  # noqa: E402


# ---- polymm's real default config values (src/core/config.py, CONFIG dict) ----
DEFAULT_MIN_EDGE = 0.07       # 7% minimum edge, sports_bot CONFIG["min_edge"]
DEFAULT_SHARES = 10           # CONFIG["default_shares"]
QUOTE_INCREMENT = 0.01        # "a cent above the best bid"


@dataclass
class SimParams:
    n_matches: int = 3000
    seed: int = 42
    min_edge: float = DEFAULT_MIN_EDGE
    shares: float = DEFAULT_SHARES
    n_books: int = 5
    book_vig_range: tuple = (0.03, 0.08)
    book_noise_sd: float = 0.015
    shock_prob: float = 0.35
    shock_size_sd: float = 0.09
    market_speed: float = 0.45      # mean fraction of the shock Polymarket has already priced in by decision time (higher = faster/more efficient market, less edge survives)
    poly_spread: float = 0.015
    hedge_base_success: float = 0.78  # hedge success prob at zero latency/edge
    hedge_latency_sd: float = 1.0     # latency (arbitrary units) between fill and hedge attempt
    hedge_latency_penalty: float = 0.18  # success prob lost per unit latency
    hedge_edge_penalty: float = 1.1   # success prob lost per unit of edge (bigger mispricings correct/get sniped faster -> more adverse selection)
    fill_base_prob: float = 0.55
    fill_edge_gain: float = 1.6
    hedge_lock_fraction_range: tuple = (0.15, 0.6)  # fraction of entry edge actually captured once the hedge leg is bought
    hedge_execution_cost_sd: float = 0.01           # spread-crossing cost on the hedge leg
    # Deliberately injected assumption, not a discovered effect: erodes the
    # *settlement* probability of trades where the hedge failed (the
    # residual/directional book), in proportion to the edge that was
    # apparently on offer. Models the real writeup's finding -- "Adverse
    # selection eating away my Polymarket bot arbitrage profits" -- i.e. a
    # market maker disproportionately keeps the unhedged side of exactly the
    # trades where its own fair-value estimate (and the sportsbook feed
    # behind it) was wrong, in the same direction Polymarket was already
    # moving. There is no real data to fit this to; it's calibrated only to
    # roughly reproduce the qualitative shape the real bot reported (arb leg
    # net positive, residual leg net negative, roughly -3184/+8293 = -38% of
    # the arb leg's size in the real writeup). 1.2 was picked by a small grid
    # search (see polymm_sim/README.md) to land near that same -40% ratio --
    # it was NOT fit to any real trade data, just tuned to match one reported
    # summary ratio, so treat the exact value as illustrative. Set to 0.0 to
    # see the (unrealistically optimistic) case with no adverse selection at
    # all -- a residual book at a genuine, uncorrelated 7%+ edge would be a
    # clear net winner, which is exactly the naive expectation the real
    # writeup says did NOT hold up.
    adverse_selection_edge_erosion: float = 1.2

    def to_dict(self):
        d = asdict(self)
        d["book_vig_range"] = list(self.book_vig_range)
        return d


@dataclass
class Trade:
    match_id: int
    p_true: float
    fair_prob: float
    poly_bid: float
    edge: float
    entry_price: float
    filled: bool
    hedged: bool
    hedge_price: Optional[float]
    latency: float
    outcome_win: Optional[bool]
    pnl: float
    leg: str  # "arb" or "residual" or "no_fill" or "no_edge"


def _clip(x, lo=0.001, hi=0.999):
    out = np.clip(x, lo, hi)
    return float(out) if np.ndim(out) == 0 else out


def simulate(params: SimParams) -> pd.DataFrame:
    rng = np.random.default_rng(params.seed)
    trades: list[Trade] = []

    for i in range(params.n_matches):
        p_true = _clip(0.05 + 0.90 * rng.beta(2, 2))

        # -- bookmakers: each has its own vig + noise around p_true --
        book_probs = _clip(p_true + rng.normal(0, params.book_noise_sd, params.n_books))
        vigs = rng.uniform(*params.book_vig_range, params.n_books)
        # split each book's margin between the two sides (roughly even, +/- noise)
        split = rng.uniform(0.4, 0.6, params.n_books)
        implied1 = book_probs * (1 + vigs * split)
        implied2 = (1 - book_probs) * (1 + vigs * (1 - split))
        book_odds1 = 1.0 / np.clip(implied1, 1e-3, None)
        book_odds2 = 1.0 / np.clip(implied2, 1e-3, None)

        # aggregator takes the best (highest) odds on each side across books
        best_odds1 = float(np.max(book_odds1))
        best_odds2 = float(np.max(book_odds2))

        fair = calculate_fair_odds(best_odds1, best_odds2)
        if not fair:
            continue
        fair_prob = fair["fair_prob1"] / 100.0

        # -- Polymarket price: partially caught-up estimate of a shock --
        if rng.random() < params.shock_prob:
            shock = rng.normal(0, params.shock_size_sd)
        else:
            shock = 0.0
        pre_shock_p = _clip(p_true - shock)
        catchup = _clip(rng.normal(params.market_speed, 0.15), 0.0, 1.0)
        poly_mid = _clip(pre_shock_p + catchup * shock + rng.normal(0, 0.01))
        poly_bid = _clip(poly_mid - params.poly_spread / 2)

        edge = fair_prob - poly_bid

        if edge < params.min_edge:
            trades.append(Trade(i, p_true, fair_prob, poly_bid, edge, np.nan,
                                 False, False, None, np.nan, None, 0.0, "no_edge"))
            continue

        entry_price = _clip(poly_bid + QUOTE_INCREMENT)
        if entry_price >= fair_prob:
            trades.append(Trade(i, p_true, fair_prob, poly_bid, edge, entry_price,
                                 False, False, None, np.nan, None, 0.0, "no_edge"))
            continue

        fill_prob = _clip(params.fill_base_prob + params.fill_edge_gain * edge, 0.05, 0.95)
        filled = rng.random() < fill_prob
        if not filled:
            trades.append(Trade(i, p_true, fair_prob, poly_bid, edge, entry_price,
                                 False, False, None, np.nan, None, 0.0, "no_fill"))
            continue

        # -- attempt the hedge leg --
        latency = float(max(0.0, rng.normal(params.hedge_latency_sd, 0.4)))
        hedge_success_prob = _clip(
            params.hedge_base_success
            - params.hedge_latency_penalty * latency
            - params.hedge_edge_penalty * edge,
            0.02, 0.97,
        )
        hedged = rng.random() < hedge_success_prob

        if hedged:
            # Locking a hedge means the combined cost of both legs is < $1/share
            # by construction. What fraction of the entry edge actually survives
            # to be captured (the rest is given up crossing the spread on the
            # second leg, which by then has partially repriced) is the modeled
            # quantity here, rather than re-deriving the second leg's price from
            # scratch -- that avoids double-counting the market-catch-up already
            # reflected in `edge`.
            lock_fraction = float(rng.uniform(*params.hedge_lock_fraction_range))
            execution_cost = abs(rng.normal(0, params.hedge_execution_cost_sd))
            profit_per_share = max(-0.02, edge * lock_fraction - execution_cost)
            hedge_price = round(1.0 - entry_price - profit_per_share, 4)
            pnl = profit_per_share * params.shares
            trades.append(Trade(i, p_true, fair_prob, poly_bid, edge, entry_price,
                                 True, True, hedge_price, latency, None, pnl, "arb"))
        else:
            # Adverse selection: unhedged positions are disproportionately the
            # ones the market was right to keep moving away from us on. See
            # SimParams.adverse_selection_edge_erosion docstring.
            settlement_p = _clip(p_true - params.adverse_selection_edge_erosion * edge, 0.0, 1.0)
            win = rng.random() < settlement_p
            pnl = ((1.0 - entry_price) if win else -entry_price) * params.shares
            trades.append(Trade(i, p_true, fair_prob, poly_bid, edge, entry_price,
                                 True, False, None, latency, win, pnl, "residual"))

    return pd.DataFrame([asdict(t) for t in trades])


def summarize(df: pd.DataFrame, params: SimParams) -> dict:
    traded = df[df["filled"]]
    arb = traded[traded["leg"] == "arb"]
    residual = traded[traded["leg"] == "residual"]

    def stats(sub: pd.DataFrame) -> dict:
        if len(sub) == 0:
            return {"trades": 0, "pnl": 0.0, "win_rate": None, "avg_pnl": 0.0}
        return {
            "trades": int(len(sub)),
            "pnl": round(float(sub["pnl"].sum()), 2),
            "win_rate": round(float((sub["pnl"] > 0).mean()), 4),
            "avg_pnl": round(float(sub["pnl"].mean()), 4),
        }

    return {
        "params": params.to_dict(),
        "matches_simulated": int(len(df)),
        "opportunities_seen": int((df["leg"] != "no_edge").sum()),
        "orders_filled": int(len(traded)),
        "fill_rate_given_edge": round(float(len(traded) / max(1, (df["leg"] != "no_edge").sum())), 4),
        "hedge_success_rate": round(float(len(arb) / max(1, len(traded))), 4),
        "overall": stats(traded),
        "arb_leg": stats(arb),
        "residual_leg": stats(residual),
        "net_pnl": round(float(traded["pnl"].sum()), 2),
        "avg_edge_at_entry": round(float(traded["edge"].mean()), 4) if len(traded) else None,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n-matches", type=int, default=SimParams.n_matches)
    p.add_argument("--seed", type=int, default=SimParams.seed)
    p.add_argument("--min-edge", type=float, default=DEFAULT_MIN_EDGE)
    p.add_argument("--shares", type=float, default=DEFAULT_SHARES)
    p.add_argument("--market-speed", type=float, default=SimParams.market_speed,
                    help="How fast Polymarket catches up to a shock (0=never, 1=instantly). Lower = more edge survives.")
    p.add_argument("--hedge-latency-sd", type=float, default=SimParams.hedge_latency_sd,
                    help="Mean/typical latency before the hedge leg is attempted (arbitrary units). Higher = slower bot.")
    p.add_argument("--adverse-selection-edge-erosion", type=float, default=SimParams.adverse_selection_edge_erosion,
                    help="Fraction of the apparent edge eroded from unhedged (residual) settlement probability; 0 disables adverse selection.")
    p.add_argument("--out", type=str, default=None, help="Write trade log CSV to this path")
    p.add_argument("--json-out", type=str, default=None, help="Write summary JSON to this path")
    args = p.parse_args()

    params = SimParams(
        n_matches=args.n_matches,
        seed=args.seed,
        min_edge=args.min_edge,
        shares=args.shares,
        market_speed=args.market_speed,
        hedge_latency_sd=args.hedge_latency_sd,
        adverse_selection_edge_erosion=args.adverse_selection_edge_erosion,
    )
    df = simulate(params)
    summary = summarize(df, params)
    print(json.dumps(summary, indent=2))

    if args.out:
        df.to_csv(args.out, index=False)
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
