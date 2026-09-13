# Trading bot: crypto + Polymarket backtesting, and live Robinhood trading

A strategy + backtest framework for two markets, built against **public, no-auth
APIs**:

- **Crypto**: Binance's public REST API (`api.binance.com`) for OHLCV candles.
- **Polymarket**: the public Gamma API (`gamma-api.polymarket.com`) for resolved
  markets and the CLOB API (`clob.polymarket.com`) for price history.

There's also **live trading** support for Robinhood (`trading_bot/live/`),
preferably through Robinhood's own official agentic-trading MCP integration
(with `mcp_signal_helper.py` bridging in this repo's strategies), or via a
legacy direct-login fallback script. Read the "Robinhood live trading"
section below in full before using either — unlike the two backtests above,
this moves real money.

`trader-dev` (`mcp.trader.dev`) is not wired in — it needs auth this session
didn't have credentials for, and separately, this session's network egress
policy blocked every external data host (Binance/CoinGecko/Polymarket/trader-dev
all failed identically). None of this code has been run against live data yet.
Once you run it somewhere with normal internet access, swap in trader-dev's
data source at any point if it turns out to be better than the public APIs.

## Important: this will not hand you a guaranteed-profitable bot

No backtest can promise that. A strategy that looks great on historical data is
easy to produce by accident (overfitting/curve-fitting) and can still lose
money live. What's here is:

- A strategy + backtest engine that is honest about the math (fees, slippage,
  no lookahead bias, buy-and-hold baseline for comparison).
- Two demonstration strategies per market, chosen because they're grounded in
  documented, real phenomena (trend-following for crypto; the
  favorite-longshot bias for prediction markets) — not because they're
  guaranteed winners.
- Sanity-tested against synthetic data to confirm the engine's math is correct
  (see below) — **not** a claim about real-world profitability.

Treat any results as a starting point for further research/paper trading, not
as a signal to deploy real capital.

## Setup

```bash
cd trading_bot
pip install -r requirements.txt
```

Run everything from the **parent** directory (so `trading_bot` is importable
as a package):

```bash
cd ..
python -m trading_bot.run_crypto_backtest --start 2021-01-01 --end 2026-08-01
python -m trading_bot.run_polymarket_backtest --max-markets 200
```

## Crypto backtest

```bash
python -m trading_bot.run_crypto_backtest \
    --symbol BTCUSDT --interval 1d --start 2021-01-01 --end 2026-08-01 \
    --strategy sma_crossover --fast 20 --slow 50 \
    --fee-bps 10 --slippage-bps 5
```

Strategies (`strategies/crypto_strategy.py`):
- `sma_crossover` — trend-following, long while the fast SMA is above the slow SMA.
- `rsi_mean_reversion` — buy when RSI drops below `--oversold`, exit once it
  recovers above `--overbought`.

Both are long-only/flat (no shorting), and both report an equity curve, CAGR,
Sharpe ratio, max drawdown, win rate, and a buy-and-hold baseline for the same
window so you can tell whether the strategy actually added anything.

## Polymarket backtest

```bash
python -m trading_bot.run_polymarket_backtest \
    --strategy favorite_longshot --entry-threshold 0.90 --max-markets 200
```

Strategies (`strategies/polymarket_strategy.py`):
- `favorite_longshot` — buys YES the first time price crosses `--entry-threshold`,
  betting on the documented favorite-longshot bias (heavy favorites are
  historically slightly underpriced in prediction/betting markets).
- `momentum` — buys YES after a price rise of `--min-move` over the trailing
  `--lookback` snapshots, while still in a non-extreme price band.

Each resolved market contributes at most one trade (buy-and-hold-to-resolution,
matching how most Polymarket positions are actually taken). Fees are modeled
as a flat `--fee-pct` of stake — tune to match real spread/gas costs.

## Robinhood live trading

This acts on the present with real money, not a historical simulation like
the two backtests above. There are two ways to do it — prefer the first.

### Recommended: Robinhood's official Agentic Trading (MCP)

As of May 2026, Robinhood runs an official, OAuth-connected MCP server for
AI-agent trading at `https://agent.robinhood.com/mcp/trading`, supported by
Claude Code, Claude Desktop, ChatGPT, Cursor, Grok, and other MCP-capable
clients. This is the better path: no passwords or TOTP secrets stored
anywhere, and trades are confined to a **dedicated Agentic account** isolated
from your main portfolio (one of up to 10 individual accounts Robinhood lets
you hold).

Setup, from your own desktop (this step needs your physical device and
Robinhood mobile app for verification — it can't be done for you from a repo
or a cloud session):

```bash
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
```

Then finish the on-screen OAuth/onboarding flow, which also opens the
Agentic account if you don't already have one. You'll need an existing
primary Robinhood account in good standing first.

Once connected, a Claude session with that MCP loaded can query your
Agentic account's positions/balances/history directly through Robinhood's
own tools, and place orders through them too — those calls go through
Robinhood's supported, reviewed path (trade previews / confirmation by
default; an explicit "autonomous mode" removes that if you turn it on).

Use `trading_bot/live/mcp_signal_helper.py` to bring this repo's strategies
into that flow without giving up any of Robinhood's safety controls: feed it
OHLCV data (from the MCP's own market-data tool, or this repo's fetchers)
plus your current/target position, and `decide()` returns a plain
`{signal, side, quantity, rationale}` dict — the agent then decides whether
to hand that to Robinhood's own order-placement tool. This repo never touches
your Robinhood credentials or places the order itself in this path.

Known gaps in Robinhood's public documentation as of writing: no documented
hard spend cap or per-order limit beyond your account balance, and rate
limits/session lifetime/token revocation aren't specified — read Robinhood's
own docs at `robinhood.com/us/en/support/agentic-trading` before relying on
autonomous mode for anything beyond a small account balance.

### Legacy / fallback: direct `robin_stocks` script

`trading_bot/live/robinhood_broker.py` and `run_robinhood_live.py` log into a
**real Robinhood account** directly (via the unofficial `robin_stocks`
client, using your actual username/password — Robinhood has no public API
for this) and can submit **real market orders with real money**, with no
account isolation from your main portfolio. Prefer the official MCP path
above; use this only if connecting an MCP-capable agent isn't an option for
your setup.

#### Safety model

- **Dry-run by default.** Without `--live`, the script fetches live data,
  computes the signal, and prints/logs what it *would* trade — it never calls
  Robinhood's order endpoints.
- **Two independent gates before any real order.** Even with `--live`, the
  code refuses to submit unless the environment variable
  `ROBINHOOD_CONFIRM_LIVE_TRADING=yes` is also set. This is deliberately an
  env var rather than a flag, so a real order can't fire just because someone
  reused a command line.
- **Every order (dry-run or real) is appended to `trading_bot/logs/robinhood_orders.csv`**
  (gitignored) for an audit trail.
- **Credentials are read from the environment only** — `ROBINHOOD_USERNAME`,
  `ROBINHOOD_PASSWORD`, and optionally `ROBINHOOD_TOTP_SECRET` for automatic
  2FA codes (via `pyotp`) instead of typing a code each run. Never pass these
  as CLI arguments or commit them; `.env` is gitignored if you use one with
  something like `direnv` or `python-dotenv` to load it.
- **`--max-position-value`** lets you cap the notional a buy is allowed to
  push you to, as a second line of defense against a bad signal.

#### Setup

```bash
pip install -r requirements.txt
export ROBINHOOD_USERNAME="you@example.com"
export ROBINHOOD_PASSWORD="..."
export ROBINHOOD_TOTP_SECRET="..."   # optional: skip typing 2FA codes each run
```

#### Usage

```bash
# Dry run: shows what it would do, places nothing.
python -m trading_bot.run_robinhood_live \
    --symbol AAPL --strategy sma_crossover --fast 20 --slow 50 --quantity 1

# Live: only after you've reviewed dry-run output and accept this trades
# real money. Note the separate env var, set in the same command here for
# clarity — in practice set it in your shell so it isn't in your history.
ROBINHOOD_CONFIRM_LIVE_TRADING=yes python -m trading_bot.run_robinhood_live \
    --symbol AAPL --strategy sma_crossover --quantity 1 \
    --max-position-value 500 --live
```

This is a one-shot "compute signal, adjust position toward it" script, not a
daemon — run it on a schedule (cron, etc.) if you want it to check
periodically. It reuses the same `sma_crossover` / `rsi_mean_reversion`
signals as the crypto backtest (see above), computed on Robinhood's own
historical candles for the symbol.

#### What this doesn't do

- No options, no shorting, no fractional-share sizing beyond what you pass in
  `--quantity` — long/flat market orders on whole (or Robinhood-fractional)
  share counts only.
- No portfolio-level risk management beyond `--max-position-value` — it does
  not know about your other positions or overall account risk.
- Never backtested with this exact code path against live data before you run
  it — the strategies were validated in the crypto backtest, not against
  Robinhood's historicals specifically. Run in dry-run mode for a while and
  compare its logged signals against what you'd expect before trusting it
  with `--live`.

## Layout

```
trading_bot/
  data/
    crypto_fetcher.py       # Binance klines
    polymarket_fetcher.py   # Gamma resolved markets + CLOB price history
  strategies/
    crypto_strategy.py
    polymarket_strategy.py
  backtest/
    engine.py               # time-series backtest (crypto)
    polymarket_engine.py    # event-based backtest (Polymarket)
    metrics.py               # Sharpe, CAGR, max drawdown, win rate
  live/
    mcp_signal_helper.py    # turns OHLCV data into a decision for the official MCP path
    robinhood_broker.py     # legacy fallback: login, historicals, positions, order placement
  run_crypto_backtest.py
  run_polymarket_backtest.py
  run_robinhood_live.py     # legacy fallback -- LIVE trading, real orders, real money
```

## Known limitations / next steps

- Polymarket API field names were written from documentation, not verified
  against a live call (this session's egress was blocked) — check the actual
  response shape before trusting results and adjust `data/polymarket_fetcher.py`
  if the API has moved on.
- No walk-forward or out-of-sample split is enforced — if you tune
  `--fast`/`--slow`/`--entry-threshold` against the same window you evaluate
  on, you will overfit. Hold out a test period.
- Live execution exists for Robinhood only (official MCP path recommended,
  direct-login script as fallback — see "Robinhood live trading" above).
  Crypto and Polymarket are backtesting only; wiring up real order placement
  for either would need real exchange/wallet credentials, a meaningfully
  bigger and riskier step than what's here.
- Robinhood's official MCP path has undocumented rate limits, session
  lifetime, and token-revocation behavior, and no documented hard spend cap
  beyond your account balance — read Robinhood's own docs before turning on
  its "autonomous mode" (no per-trade confirmation) for anything beyond a
  small account balance.
