"""All tunables in one place. Secrets and mode come from environment variables."""
import os
from dataclasses import dataclass, field, replace


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(int(default))).strip().lower() in ("1", "true", "yes")


# ---- costs per asset class (MyFundedPerps fee page) ----
# stocks, commodities and indices share one schedule ("tradfi").
ASSET_CLASS_COSTS = {
    "crypto": {"fee_rate": 0.0003, "swap_daily_rate": 0.0003},     # 0.03%/fill, 0.03%/day (hourly)
    "tradfi": {"fee_rate": 0.00005, "swap_daily_rate": 0.00015},   # 0.005%/fill, 0.015%/day
    "fx": {"fee_rate": 0.000025, "swap_daily_rate": 0.00005},      # 0.0025%/fill, 0.005%/day
}


@dataclass(frozen=True)
class MarketSpec:
    """One tradable perp, as listed by GET /v1/markets (history/subscriptions use `coin`)."""
    market_id: str
    provider: str
    coin: str
    asset_class: str
    size_decimals: int
    price_decimals: int
    max_leverage: int
    slippage_bps: float      # assumed adverse slippage per fill (measured book impact x ~3-5)


# price_decimals kept to <= 5 significant figures (Hyperliquid tick rules).
# slippage: $30k market order impact measured on the Hyperliquid books 2026-09-30 was ~0.1-0.3 bp
# for GOLD/SP500 and ~2-2.5 bp for EUR/JPY; BTC keeps the original 3 bp.
MARKETS = {
    "BTC": MarketSpec("binance|BTCUSDT", "binance", "BTCUSDT", "crypto", 3, 1, 10, 3.0),
    "GOLD": MarketSpec("hyperliquid|xyz:GOLD", "hyperliquid", "xyz:GOLD", "tradfi", 4, 1, 10, 1.0),
    "SP500": MarketSpec("hyperliquid|xyz:SP500", "hyperliquid", "xyz:SP500", "tradfi", 3, 1, 12, 1.0),
    "EUR": MarketSpec("hyperliquid|xyz:EUR", "hyperliquid", "xyz:EUR", "fx", 1, 5, 50, 3.0),
    "JPY": MarketSpec("hyperliquid|xyz:JPY", "hyperliquid", "xyz:JPY", "fx", 2, 3, 50, 3.0),
}


@dataclass
class Config:
    # ---- connection (env) ----
    api_key: str = os.getenv("FP_API_KEY", "")          # fp_test_... = sandbox, fp_live_... = live
    account_id: str = os.getenv("FP_ACCOUNT_ID", "")
    dry_run: bool = _env_bool("DRY_RUN", True)          # default ON: logs orders, sends nothing

    # ---- markets: the bot scans every symbol, one position at a time across all of them ----
    symbols: tuple = tuple(s for s in os.getenv("FP_SYMBOLS", "BTC").split(",") if s)

    # ---- market (filled in per symbol by for_market) ----
    market_id: str = "binance|BTCUSDT"
    provider: str = "binance"
    coin: str = "BTCUSDT"
    symbol: str = "BTC"
    asset_class: str = "crypto"
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
    max_notional_mult: float = 3.0   # notional cap = 3x starting balance (and never above max leverage)

    # ---- strategy selection (see strategy.STRATEGIES) ----
    strategy: str = os.getenv("FP_STRATEGY", "trend_breakout")
    params: dict = field(default_factory=dict)   # per-strategy tunables; defaults live in strategy.py

    # ---- legacy strategy: 15m trend filter + 5m Donchian breakout, ATR stop, fixed R:R ----
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

    # ---- costs (set from the market's asset class by for_market) ----
    fee_rate: float = 0.0003         # per fill
    swap_daily_rate: float = 0.0003  # per day, charged hourly
    slippage_bps: float = 3.0        # assumed adverse slippage for sizing/backtest

    poll_seconds: int = 10
    state_path: str = "state.json"

    @property
    def base_url(self) -> str:
        if self.api_key.startswith("fp_test_"):
            return "https://sandbox.myfundedperpetuals.com"
        return "https://developers.myfundedperpetuals.com"

    def for_market(self, symbol: str) -> "Config":
        """Copy of this config bound to one market: ids, rounding, leverage cap, per-class costs."""
        m = MARKETS[symbol]
        costs = ASSET_CLASS_COSTS[m.asset_class]
        return replace(
            self, symbol=symbol, market_id=m.market_id, provider=m.provider, coin=m.coin,
            asset_class=m.asset_class, size_decimals=m.size_decimals, price_decimals=m.price_decimals,
            leverage=min(self.leverage, m.max_leverage),
            max_notional_mult=min(self.max_notional_mult, m.max_leverage),
            fee_rate=costs["fee_rate"], swap_daily_rate=costs["swap_daily_rate"],
            slippage_bps=m.slippage_bps,
        )
