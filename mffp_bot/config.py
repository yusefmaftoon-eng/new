"""All tunables in one place. Secrets and mode come from environment variables."""
import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(int(default))).strip().lower() in ("1", "true", "yes")


@dataclass
class Config:
    # ---- connection (env) ----
    api_key: str = os.getenv("FP_API_KEY", "")          # fp_test_... = sandbox, fp_live_... = live
    account_id: str = os.getenv("FP_ACCOUNT_ID", "")
    dry_run: bool = _env_bool("DRY_RUN", True)          # default ON: logs orders, sends nothing

    # ---- market ----
    market_id: str = "binance|BTCUSDT"
    provider: str = "binance"
    coin: str = "BTCUSDT"
    size_decimals: int = 3
    price_decimals: int = 1
    leverage: int = 5
    margin_mode: str = "cross"

    # ---- evaluation rules: $10K Select (dollars, relative to starting balance) ----
    profit_target: float = 900.0
    max_drawdown: float = 300.0      # static floor = start - 300
    daily_loss: float = 300.0        # from start-of-day equity, midnight New York

    # ---- bot risk (deliberately stricter than the firm) ----
    risk_per_trade: float = 45.0     # $ lost if the stop is hit, incl. fees + slippage
    min_risk: float = 15.0           # below this budget the bot won't trade
    daily_stop: float = 100.0        # bot stops for the day after losing this much
    max_trades_per_day: int = 4
    kill_buffer: float = 50.0        # flatten + stop if equity gets this close to any floor
    target_buffer: float = 12.0      # bank the pass once equity >= target + this
    max_notional_mult: float = 3.0   # notional cap = 3x starting balance

    # ---- strategy: 15m trend filter + 5m Donchian breakout, ATR stop, fixed R:R ----
    interval_min: int = 5
    trend_factor: int = 3            # 3 x 5m = 15m trend bars
    ema_fast: int = 20
    ema_slow: int = 50
    breakout_len: int = 20
    atr_len: int = 14
    atr_stop_mult: float = 1.5
    reward_risk: float = 2.0
    max_chase_atr: float = 0.5       # skip if close is already > 0.5 ATR past the breakout level
    min_atr_pct: float = 0.0008      # skip dead markets
    session_start: str = "09:30"     # New York time
    session_end: str = "16:00"
    trade_weekends: bool = False
    cooldown_bars: int = 3

    # ---- costs (crypto, per MyFundedPerps fee page) ----
    fee_rate: float = 0.0003         # 0.03% per fill
    swap_daily_rate: float = 0.0003  # 0.03%/day, charged hourly
    slippage_bps: float = 3.0        # assumed adverse slippage for sizing/backtest

    poll_seconds: int = 10
    state_path: str = "state.json"

    @property
    def base_url(self) -> str:
        if self.api_key.startswith("fp_test_"):
            return "https://sandbox.myfundedperpetuals.com"
        return "https://developers.myfundedperpetuals.com"
