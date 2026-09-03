"""Simulates trading a real, backtested trade sequence through a prop-firm
evaluation account's actual constraints: a profit target, a max-loss/
drawdown limit, and a contract cap -- rather than just reporting raw R or
$/contract, which ignore position sizing and the fact that busting the
drawdown limit ENDS the attempt regardless of what the strategy does after.

drawdown_mode='trailing' models an INTRADAY trailing drawdown specifically
(confirmed as the user's actual rule): the floor can ratchet up mid-trade
from FLOATING (unrealized) profit, not only once a trade closes. Each trade
carries its max-favorable and max-adverse excursion (mfe_points/mae_points)
and which one happened first (mfe_first) -- computed from the real bar path
in candle_range_theory_sim.py, not just entry/exit. That lets this sim
reconstruct, for each trade: if the favorable excursion happened first, the
peak ratchets up BEFORE the adverse dip is checked against the floor (worse
for the trader); if the adverse excursion happened first, the dip is
checked against the OLD, not-yet-ratcheted floor. A breach detected at the
intra-trade extreme ends the attempt right there, at that extreme's equity
-- not wherever the trade would have otherwise closed, since a real
account gets force-flattened the instant it breaches, regardless of what
the position would have gone on to do.

'static' mode (a fixed floor at -max_loss from the starting balance, not
ratcheting with profit at all) is also implemented for comparison, in case
that turns out to be the actual rule for a different plan/stage -- it
still uses the same intra-trade MAE check, since a static floor can still
be breached mid-trade before recovering to a stored win.

Real per-contract commission is modeled as an explicit, separate flat
$/round-trip parameter -- plug in your real number once you have it,
rather than it being silently baked into the P&L.
"""
from __future__ import annotations


def size_contracts(stop_points: float, dollars_per_point: float, risk_per_trade_dollars: float,
                    max_contracts: int, available_cushion: float) -> int:
    """How many contracts to trade this signal, given fixed $-risk-per-trade
    sizing, a hard contract cap, and however much drawdown room is actually
    left before the account would breach. Returns 0 (skip the trade) rather
    than take a size that alone could blow the remaining cushion."""
    if stop_points <= 0:
        return 0
    risk_per_contract = stop_points * dollars_per_point
    by_risk_budget = int(risk_per_trade_dollars // risk_per_contract)
    by_cushion = int(available_cushion // risk_per_contract)
    contracts = max(0, min(max_contracts, by_risk_budget, by_cushion))
    return contracts


def simulate_evaluation(trades: list[dict], dollars_per_point: float,
                         profit_target: float = 1500.0, max_loss: float = 1000.0,
                         max_contracts: int = 20, risk_per_trade_dollars: float = 50.0,
                         drawdown_mode: str = "trailing", round_trip_fee_per_contract: float = 0.0) -> dict:
    """Runs the real trade sequence through repeated evaluation attempts:
    starts a fresh attempt at equity 0, sizes and takes every signal in
    order, and ends that attempt the moment the account either hits
    +profit_target (pass) or breaches its max_loss (fail) -- then starts the
    next attempt on the next trade in the sequence. This is what the strategy
    would ACTUALLY have done across the one real year of data available,
    not a theoretical average.
    """
    attempts = []
    i = 0
    n = len(trades)
    while i < n:
        equity = 0.0
        peak = 0.0
        n_trades_taken = 0
        n_signals_skipped = 0
        start_date = trades[i]["date"]
        outcome = None
        end_date = start_date
        while i < n:
            t = trades[i]
            cushion = (max_loss - (peak - equity)) if drawdown_mode == "trailing" else (max_loss + equity)
            contracts = size_contracts(t["stop_points"], dollars_per_point, risk_per_trade_dollars,
                                        max_contracts, cushion)
            end_date = t["date"]
            i += 1

            if contracts == 0:
                n_signals_skipped += 1
                continue
            n_trades_taken += 1

            mfe_dollars = t["mfe_points"] * dollars_per_point * contracts
            mae_dollars = t["mae_points"] * dollars_per_point * contracts
            realized_pnl = t["pnl_points"] * dollars_per_point * contracts - round_trip_fee_per_contract * contracts

            def floor_for(pk: float) -> float:
                return -max_loss if drawdown_mode == "static" else pk - max_loss

            if t["mfe_first"]:
                peak = max(peak, equity + mfe_dollars)
                worst_equity = equity - mae_dollars
                if worst_equity <= floor_for(peak):
                    equity = worst_equity
                    outcome = "fail"
                    break
            else:
                worst_equity = equity - mae_dollars
                if worst_equity <= floor_for(peak):
                    equity = worst_equity
                    outcome = "fail"
                    break
                peak = max(peak, equity + mfe_dollars)

            equity += realized_pnl
            peak = max(peak, equity)

            # rare gap in the two-checkpoint approximation: a trade that ran up a
            # big favorable excursion (ratcheting the floor) and then gave most
            # of it back to a smaller final close (time_exit) could still land
            # below the now-higher floor even though neither tracked extreme
            # alone triggered it -- catch that here too.
            if equity <= floor_for(peak):
                outcome = "fail"
                break
            if equity >= profit_target:
                outcome = "pass"
                break
        if outcome is None:
            outcome = "incomplete"  # ran out of real historical data mid-attempt
        attempts.append({
            "outcome": outcome, "final_equity": round(equity, 2),
            "n_trades_taken": n_trades_taken, "n_signals_skipped": n_signals_skipped,
            "start_date": str(start_date), "end_date": str(end_date),
        })

    n_pass = sum(1 for a in attempts if a["outcome"] == "pass")
    n_fail = sum(1 for a in attempts if a["outcome"] == "fail")
    n_incomplete = sum(1 for a in attempts if a["outcome"] == "incomplete")
    resolved = [a for a in attempts if a["outcome"] in ("pass", "fail")]
    return {
        "drawdown_mode": drawdown_mode,
        "n_attempts": len(attempts),
        "n_pass": n_pass, "n_fail": n_fail, "n_incomplete": n_incomplete,
        "pass_rate_pct": round(100 * n_pass / len(resolved), 1) if resolved else None,
        "avg_trades_to_resolve": round(sum(a["n_trades_taken"] for a in resolved) / len(resolved), 1) if resolved else None,
        "attempts": attempts,
    }
