# Trading bot backtesting framework (crypto + Polymarket + Kalshi)

A strategy + backtest framework for three markets, built against **public,
no-auth APIs**:

- **Crypto**: Binance's public REST API (`api.binance.com`) for OHLCV candles.
- **Polymarket**: the public Gamma API (`gamma-api.polymarket.com`) for resolved
  markets and the CLOB API (`clob.polymarket.com`) for price history.
- **Kalshi**: the public v2 trade API (`api.elections.kalshi.com/trade-api/v2`)
  for settled markets and candlestick price history.

`trader-dev` (`mcp.trader.dev`) is not wired in — it needs auth this session
didn't have credentials for, and separately, every session that has worked on
this repo has had its network egress blocked for every external data host
(Binance/CoinGecko/Polymarket/Kalshi/trader-dev/even plain `example.com` all
fail identically with a proxy-level 403 — this looks like the environment's
org network policy denying all non-allowlisted hosts, not anything host-
specific). **None of this code has been run against live data yet.** Once you
run it somewhere with normal internet access (or widen this environment's
egress allowlist to include the relevant API host), swap in trader-dev's data
source at any point if it turns out to be better than the public APIs.

The Kalshi fetcher (`data/kalshi_fetcher.py`) has only been checked to fail at
exactly the network layer (proxy `ProxyError`, not a code exception) — its
endpoint paths and field names are from documentation and are unverified
against a live response, same caveat as the Polymarket fetcher below.

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

## Kalshi backtest

```bash
python -m trading_bot.run_kalshi_backtest \
    --strategy favorite_longshot --entry-threshold 0.90 --max-markets 200
```

Reuses the Polymarket event-based engine and strategies unchanged: both are
0-1-priced binary (YES/NO) markets resolved by buy-and-hold-to-close, so only
the data fetcher (`data/kalshi_fetcher.py`) is Kalshi-specific — it converts
Kalshi's cents-priced (1-99) contracts to the same 0-1 scale on the way in.
Same two strategies as Polymarket (`favorite_longshot`, `momentum`), same
`--fee-pct`, `--stake`, `--capital` flags. Kalshi's real fee schedule is
per-contract and price-dependent (not a flat %), so `--fee-pct` here is a
rougher approximation than for Polymarket — tune it down for contracts near
0.50 and up for contracts near the extremes if you want a closer match.

## Kalshi crypto/gold directional bot — backtested on REAL price data

```bash
python -m trading_bot.run_kalshi_crypto_backtest --asset btc --strategy vol_model
python -m trading_bot.run_kalshi_crypto_backtest --asset eth --strategy sma_state
python -m trading_bot.run_kalshi_crypto_backtest --asset gold --strategy vol_model
```

Backtests a Kalshi-style daily "does [asset] close above its prior close"
market. Unlike the two backtests above, this one runs on **real, verified
historical data reachable from this environment**: CoinMetrics' free daily
BTC/ETH CSVs and the World Bank's monthly gold series (both served from
`raw.githubusercontent.com`, which — unlike Kalshi/Binance/Yahoo/AlphaVantage —
this environment's network policy allows). See `data/coinmetrics_fetcher.py`
and `data/gold_fetcher.py`.

Two causal (no-lookahead) probability models, `strategies/kalshi_directional_strategy.py`:
- `vol_model` — prices the day as a zero-drift digital/binary option using
  trailing realized volatility. Drift is deliberately fixed at 0 rather than
  estimated from history, because trailing-mean-return estimates are a
  classic overfitting trap on this little data.
- `sma_state` — online (expanding-window) estimate of the historical up-rate
  conditional on price being above/below its N-day SMA.

**What the backtest can and can't tell you, reported separately on purpose:**
there is no real historical record of what Kalshi actually charged for these
contracts (its API is unreachable here), so `backtest/kalshi_directional_sim.py`
reports two different things instead of one misleading P&L number:
1. `evaluate_calibration` — Brier score / log loss / realized-vs-predicted
   calibration by decile, which needs no price assumption at all and is the
   real, honest measure of whether either model has genuine predictive skill.
2. `pnl_sensitivity` — P&L swept across a handful of **assumed** flat Kalshi
   YES prices (`--assumed-prices`), clearly labeled as an assumption.

**Actual results as of this writing (BTC/ETH 2010–2026 daily, gold
1960–2026 monthly):** both models come back with a Brier score
statistically indistinguishable from a naive 50/50 coin flip (~0.25 vs.
0.25 baseline) on all three assets — i.e., **neither model has real
predictive skill over the base rate**. The P&L sweep does show "profitable"
rows, but only at assumed prices well below each asset's actual long-run
up-day base rate (BTC ≈53%, ETH ≈51%, gold ≈47%) — that's the sweep
correctly showing "you'd profit if Kalshi priced this market far off the
historical base rate," not a discovered edge. At assumed prices near the
real base rate, PnL is flat-to-negative for both models on both assets.
**This is not a profitable bot as-is** — it's an honest scaffold, and
directional daily price prediction is exactly the kind of signal an
efficient market (crypto or otherwise) is hardest to beat. A more promising
direction, per the calibration numbers actually observed here, is probably
NOT "predict up/down better" but something structurally different — e.g.
applying the favorite-longshot mispricing this repo already models for
Polymarket to Kalshi's far-out-of-the-money strike-ladder crypto/gold
contracts (which behave more like lottery tickets than coin flips) once
real Kalshi quote data is reachable to test it against.

## Layout

```
trading_bot/
  data/
    crypto_fetcher.py       # Binance klines (blocked here, see caveats above)
    polymarket_fetcher.py   # Gamma resolved markets + CLOB price history
    kalshi_fetcher.py       # v2 settled markets + candlestick price history (blocked here)
    coinmetrics_fetcher.py  # REAL daily BTC/ETH prices (reachable here)
    gold_fetcher.py         # REAL monthly gold prices (reachable here)
  strategies/
    crypto_strategy.py
    polymarket_strategy.py       # shared by Kalshi too (both 0-1-priced binary markets)
    kalshi_directional_strategy.py  # causal vol-model / SMA-state probability estimators
  backtest/
    engine.py               # time-series backtest (crypto)
    polymarket_engine.py    # event-based backtest (Polymarket + Kalshi)
    kalshi_directional_sim.py  # real-data directional backtest + calibration metrics
    metrics.py               # Sharpe, CAGR, max drawdown, win rate
  run_crypto_backtest.py
  run_polymarket_backtest.py
  run_kalshi_backtest.py
  run_kalshi_crypto_backtest.py  # the one backtest here that runs on real, verified data
```

## Known limitations / next steps

- Polymarket API field names were written from documentation, not verified
  against a live call (this session's egress was blocked) — check the actual
  response shape before trusting results and adjust `data/polymarket_fetcher.py`
  if the API has moved on.
- No walk-forward or out-of-sample split is enforced — if you tune
  `--fast`/`--slow`/`--entry-threshold` against the same window you evaluate
  on, you will overfit. Hold out a test period.
- No live execution/order-placement is implemented — this is backtesting only.
  Wiring up real trading (with real exchange API keys) is a meaningfully
  bigger, riskier step; happy to help with that separately once you've
  reviewed real backtest results.
