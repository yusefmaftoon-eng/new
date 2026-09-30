"""Risk guardian: enforces the eval rules with a safety margin, sizes positions.

Floors are computed from the account's own starting balance plus the dollar rules in
config, so a $100K sandbox account behaves like the $10K eval (-$300 / +$900).
On a live account the server's own floors are also respected (the stricter wins).
"""
import json
import math
from dataclasses import dataclass
from datetime import datetime

from strategy import NY


def position_size(risk_dollars, entry, stop, cfg, starting_balance):
    """Size so that a stop-out costs at most risk_dollars including both fees and slippage.
    Fees/slippage are charged on the higher of entry and stop, so shorts (stop above entry) stay inside the budget too."""
    per_unit = abs(entry - stop) + max(entry, stop) * (2 * cfg.fee_rate + cfg.slippage_bps / 10_000)
    if per_unit <= 0 or entry <= 0:
        return 0.0
    size = min(risk_dollars / per_unit, cfg.max_notional_mult * starting_balance / entry)
    f = 10 ** cfg.size_decimals
    return math.floor(size * f) / f


@dataclass
class Decision:
    can_enter: bool
    flatten: bool = False
    stop_bot: bool = False
    reason: str = ""
    risk_budget: float = 0.0


class Guardian:
    def __init__(self, cfg, starting_balance):
        self.cfg = cfg
        self.base = float(starting_balance)
        self.mll_floor = self.base - cfg.max_drawdown
        self.target_equity = self.base + cfg.profit_target
        self.state = self._load()

    def _load(self):
        try:
            with open(self.cfg.state_path) as f:
                s = json.load(f)
            if s.get("base") == self.base:
                return s
        except (OSError, ValueError):
            pass
        return {"base": self.base, "day": None, "day_start_equity": None, "trades_today": 0}

    def _save(self):
        with open(self.cfg.state_path, "w") as f:
            json.dump(self.state, f)

    def _roll_day(self, equity):
        today = datetime.now(NY).strftime("%Y-%m-%d")
        if self.state["day"] != today:
            self.state.update(day=today, day_start_equity=equity, trades_today=0)
            self._save()

    def record_entry(self):
        self.state["trades_today"] += 1
        self._save()

    def evaluate(self, acct, has_position) -> Decision:
        c = self.cfg
        if acct.get("status") != "active":
            return Decision(False, stop_bot=True, reason=f"account status is '{acct.get('status')}'")
        risk = acct.get("risk") or {}
        eq = risk.get("equity")
        if eq is None or risk.get("marks_complete") is False:
            return Decision(False, reason="equity unavailable (marks incomplete) - waiting")
        self._roll_day(eq)

        floor = max(self.mll_floor, self.state["day_start_equity"] - c.daily_loss)
        for k in ("daily_loss_floor", "max_drawdown_floor"):     # server's floors, if stricter
            if risk.get(k) is not None:
                floor = max(floor, float(risk[k]))
        room = eq - floor
        day_pnl = eq - self.state["day_start_equity"]

        if room <= c.kill_buffer:
            return Decision(False, flatten=has_position, stop_bot=True,
                            reason=f"KILL: equity {eq:.2f} is ${room:.0f} above loss floor {floor:.2f}")
        if eq >= self.target_equity + c.target_buffer:
            return Decision(False, flatten=has_position, stop_bot=not has_position,
                            reason=f"TARGET: equity {eq:.2f} >= {self.target_equity:.0f} - banking the pass")
        if has_position:
            return Decision(False, reason="in position")
        if day_pnl <= -c.daily_stop:
            return Decision(False, reason=f"daily stop hit ({day_pnl:+.0f}) - done until midnight NY")
        if self.state["trades_today"] >= c.max_trades_per_day:
            return Decision(False, reason="max trades for today reached")

        budget = min(c.risk_per_trade, (room - c.kill_buffer) * 0.5)
        if budget < c.min_risk:
            return Decision(False, reason=f"only ${room:.0f} of room - not enough to risk a trade")
        return Decision(True, risk_budget=budget,
                        reason=f"ready | eq {eq:.2f} | day {day_pnl:+.0f} | room ${room:.0f} | risk ${budget:.0f}")
