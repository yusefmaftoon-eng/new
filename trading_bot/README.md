# Trading bot backtesting framework (crypto + Polymarket)

A strategy + backtest framework for two markets, built against **public, no-auth
APIs**:

- **Crypto**: Binance's public REST API (`api.binance.com`) for OHLCV candles.
- **Polymarket**: the public Gamma API (`gamma-api.polymarket.com`) for resolved
  markets and the CLOB API (`clob.polymarket.com`) for price history.

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
  run_crypto_backtest.py
  run_polymarket_backtest.py
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
