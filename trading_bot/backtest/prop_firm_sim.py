"""Prop-firm account simulator: turns per-trade backtest output (from
generic_engine.simulate_trades, which now tracks mae_pts -- the worst
unrealized excursion reached during each trade's life, not just its final
P&L) into a day-by-day account equity simulation against a specific firm's
rules: EOD trailing drawdown enforced in real time, a profit target +
consistency rule for the evaluation phase, and a drawdown lock + payout cap
once funded.

Modeled after Tradeify's Select $25K account, but the rule set is a plain
dataclass -- swap the numbers for another firm/size.

Positions from different symbols can be open at the same time (each
symbol's own "one at a time" constraint in generic_engine is per-symbol,
not account-wide). This is handled as a proper event-driven simulation
(open/close events in chronological order, close-before-open on a tie) so
the account-wide contract cap is a real running total across whatever is
open on any symbol at that instant -- a trade that would push it over the
limit is rejected outright, not silently allowed.

One limitation remains, and is NOT fixed: the real-time drawdown check only
fires at the moment a *new* position opens, using that new trade's own
worst excursion (mae_pts) against the account's closed balance at that
instant. It does not reconstruct true bar-by-bar aggregate unrealized P&L
across every already-open position -- so two positions on different
symbols both hitting their own worst point at the same moment, with no new
trade opening right then to trigger a check, could breach the floor without
this simulator catching it. A fully correct version needs bar-level
mark-to-market across all open positions, not per-trade summaries. Flagged
here rather than assumed away; worth building if results ever hinge on it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class PropFirmRules:
    starting_balance: float = 25_000.0
    trailing_drawdown: float = 1_000.0
    profit_target: float = 1_500.0          # evaluation only
    consistency_pct: float = 0.40           # evaluation only: no day > this fraction of total profit
    eval_max_contracts: int = 10
    funded_max_contracts: int = 20
    lock_buffer: float = 100.0              # floor locks once balance > start + drawdown + this
    payout_threshold_buffer: float = 1_100.0  # payouts allowed once balance > start + this
    max_daily_payout: float = 600.0
    risk_per_trade: float = 150.0           # not a firm rule -- our position-sizing choice, flagged as such


@dataclass
class SimResult:
    outcome: str  # "busted" | "passed_eval_still_funded" | "still_in_eval"
    bust_date: object = None
    bust_reason: str = ""
    eval_passed_date: object = None
    final_balance: float = 0.0
    peak_balance: float = 0.0
    total_payouts: float = 0.0
    floor_locked: bool = False
    locked_floor_value: float = None
    daily_pnl: dict = field(default_factory=dict)
    trade_log: list = field(default_factory=list)


def contracts_for_trade(trade: dict, dollars_per_point: float, risk_per_trade: float, cap: int) -> int:
    stop_pts = abs(trade["entry"] - trade["stop"])
    if stop_pts <= 0:
        return 0
    ideal = math.floor(risk_per_trade / (stop_pts * dollars_per_point))
    return max(1, min(ideal, cap))  # always at least 1 contract if the trade is taken at all


def _build_events(trades_by_symbol: dict[str, list[dict]], dollars_per_point: dict[str, float]) -> list:
    all_trades = []
    for sym, trades in trades_by_symbol.items():
        for i, t in enumerate(trades):
            all_trades.append({**t, "symbol": sym, "dpp": dollars_per_point[sym], "trade_id": f"{sym}-{i}"})

    # Event-driven: "close" events sort before "open" events at the same instant, so a position
    # freeing capacity at the moment another wants to open is available to it -- matches how a
    # real account processes fills, and lets the account-wide contract cap (not a per-symbol one)
    # be tracked correctly across simultaneously-open positions on different symbols.
    events = []
    for t in all_trades:
        events.append((t["entry_time"], 1, t))   # open
        events.append((t["exit_time"], 0, t))    # close
    events.sort(key=lambda e: (e[0], e[1]))
    return events


def _run_one_attempt(events: list, start_idx: int, rules: PropFirmRules) -> tuple[SimResult, int]:
    """Runs a single account attempt starting at events[start_idx]. Returns (result, next_idx):
    next_idx is where a subsequent attempt (after a bust) should resume -- the event right after
    the one that caused the bust, or len(events) if the data ran out with no bust."""
    closed_balance = rules.starting_balance
    highest_eod_balance = rules.starting_balance
    floor = highest_eod_balance - rules.trailing_drawdown
    locked = False

    phase = "eval"
    max_contracts = rules.eval_max_contracts

    open_positions: dict[str, dict] = {}   # trade_id -> {"contracts": int, "dpp": float, "mae_pts": float}
    open_contracts_total = 0

    daily_pnl: dict = {}
    total_payouts = 0.0
    peak_balance = rules.starting_balance
    eval_passed_date = None
    trade_log = []

    current_day = None

    def close_day():
        nonlocal highest_eod_balance, floor, locked
        if not locked:
            if closed_balance > highest_eod_balance:
                highest_eod_balance = closed_balance
            floor = highest_eod_balance - rules.trailing_drawdown
            if highest_eod_balance > rules.starting_balance + rules.trailing_drawdown + rules.lock_buffer:
                locked = True  # floor freezes at its current value from here on

    def make_bust_result(t, worst_equity):
        return SimResult(
            outcome="busted", bust_date=t["entry_time"],
            bust_reason=(f"{t['symbol']} trade opened {t['entry_time']} risked account equity down to "
                          f"${worst_equity:,.2f} (its own worst point, {open_contracts_total} contracts open "
                          f"account-wide at the time), at/below the floor of ${floor:,.2f}"),
            eval_passed_date=eval_passed_date, final_balance=closed_balance, peak_balance=peak_balance,
            total_payouts=total_payouts, floor_locked=locked, locked_floor_value=floor if locked else None,
            daily_pnl=daily_pnl, trade_log=trade_log,
        )

    for idx in range(start_idx, len(events)):
        ts, _kind, t = events[idx]
        day = ts.date()
        if current_day is not None and day != current_day:
            close_day()
            if phase == "funded" and locked and closed_balance > rules.starting_balance + rules.payout_threshold_buffer:
                payout = min(rules.max_daily_payout, closed_balance - (rules.starting_balance + rules.payout_threshold_buffer))
                if payout > 0:
                    closed_balance -= payout
                    total_payouts += payout
        current_day = day

        is_open_event = ts == t["entry_time"]

        if not is_open_event:
            pos = open_positions.pop(t["trade_id"], None)
            if pos is None:
                continue  # was never opened (rejected for lack of capacity)
            open_contracts_total -= pos["contracts"]
            trade_pnl = t["pnl"] * pos["contracts"]
            closed_balance += trade_pnl
            peak_balance = max(peak_balance, closed_balance)
            daily_pnl[day] = daily_pnl.get(day, 0.0) + trade_pnl
            trade_log.append({**t, "contracts": pos["contracts"], "account_pnl": trade_pnl, "balance_after": closed_balance})

            if phase == "eval":
                cumulative_profit = closed_balance - rules.starting_balance
                if cumulative_profit >= rules.profit_target:
                    max_day = max(daily_pnl.values())
                    if max_day <= rules.consistency_pct * cumulative_profit:
                        phase = "funded"
                        max_contracts = rules.funded_max_contracts
                        eval_passed_date = day
            continue

        # open event
        available = max_contracts - open_contracts_total
        if available < 1:
            continue  # account-wide cap already full -- this setup is skipped, not queued
        contracts = contracts_for_trade(t, t["dpp"], rules.risk_per_trade, available)
        if contracts < 1:
            continue

        worst_equity = closed_balance + t["mae_pts"] * contracts * t["dpp"]
        if worst_equity <= floor:
            return make_bust_result(t, worst_equity), idx + 1

        open_positions[t["trade_id"]] = {"contracts": contracts, "dpp": t["dpp"], "mae_pts": t["mae_pts"]}
        open_contracts_total += contracts

    result = SimResult(
        outcome="passed_eval_still_funded" if phase == "funded" else "still_in_eval",
        eval_passed_date=eval_passed_date, final_balance=closed_balance, peak_balance=peak_balance,
        total_payouts=total_payouts, floor_locked=locked, locked_floor_value=floor if locked else None,
        daily_pnl=daily_pnl, trade_log=trade_log,
    )
    return result, len(events)


def simulate_account(trades_by_symbol: dict[str, list[dict]], dollars_per_point: dict[str, float],
                      rules: PropFirmRules = PropFirmRules()) -> SimResult:
    """Single attempt, start to finish (or bust) -- unchanged behavior/signature from before."""
    events = _build_events(trades_by_symbol, dollars_per_point)
    result, _ = _run_one_attempt(events, 0, rules)
    return result


@dataclass
class RepeatedAttemptsResult:
    """The actual prop-firm business question: busting is an accepted cost, not a failure state.
    What matters is total payouts collected minus what you paid to keep re-entering evaluations."""
    attempts: list = field(default_factory=list)   # one SimResult per attempt
    eval_cost: float = 0.0
    num_attempts: int = 0
    num_passed_eval: int = 0
    total_payouts: float = 0.0
    total_eval_fees: float = 0.0
    net_profit: float = 0.0


def simulate_repeated_attempts(trades_by_symbol: dict[str, list[dict]], dollars_per_point: dict[str, float],
                                rules: PropFirmRules = PropFirmRules(), eval_cost: float = 65.0,
                                max_attempts: int = 1000) -> RepeatedAttemptsResult:
    """Restart with a fresh $-cost evaluation attempt immediately after every bust, continuing
    forward through the same trade stream (never replaying trades from before the bust), until
    the data runs out or max_attempts is hit. This is the real economics of the prop-firm model:
    a busted account isn't a loss on its own -- what matters is whether payouts collected across
    however many attempts it takes exceed the fees paid to keep re-entering."""
    events = _build_events(trades_by_symbol, dollars_per_point)
    out = RepeatedAttemptsResult(eval_cost=eval_cost)
    idx = 0
    while idx < len(events) and out.num_attempts < max_attempts:
        result, idx = _run_one_attempt(events, idx, rules)
        out.attempts.append(result)
        out.num_attempts += 1
        out.total_eval_fees += eval_cost
        out.total_payouts += result.total_payouts
        if result.eval_passed_date is not None:
            out.num_passed_eval += 1
    out.net_profit = out.total_payouts - out.total_eval_fees
    return out
