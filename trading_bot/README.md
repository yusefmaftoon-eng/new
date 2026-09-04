# Trading bot backtesting framework (crypto + Polymarket + futures)

A strategy + backtest framework for three markets, built against **public,
no-auth APIs**:

- **Crypto**: Binance's public REST API (`api.binance.com`) for OHLCV candles.
- **Polymarket**: the public Gamma API (`gamma-api.polymarket.com`) for resolved
  markets and the CLOB API (`clob.polymarket.com`) for price history.
- **Futures (MES/MNQ)**: Yahoo Finance's public chart API for 5-minute bars.
  Marketstack (the equities/FX/crypto provider some earlier scaffolding in
  this repo referenced) does not carry CME futures data, so it isn't used here.

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

## IFVG backtest (MES / MNQ micro futures)

An ICT/SMT-style strategy: higher-timeframe bias before the NY open, SMT
divergence between MES and MNQ, and an entry on the retrace into a freshly
inverted fair value gap, targeting resting liquidity.

```bash
python -m trading_bot.run_ifvg_backtest --symbol both --db-path ifvg.sqlite
```

Rules (`strategies/ifvg_strategy.py`), all non-discretionary:
1. **Bias** — 1H confirmed swing structure (2-bar fractals) as of 09:30 ET.
   Higher-high + higher-low → bullish; lower-high + lower-low → bearish;
   anything mixed → no trade that day.
2. **SMT divergence** — at a 5m swing pivot behind the setup, MES and MNQ are
   compared in a matched time window: one instrument confirms a new
   high/low, the other doesn't.
3. **Inverse FVG** — a 3-candle fair value gap against bias that later gets
   closed through flips polarity (becomes support/resistance in the bias
   direction).
4. **Entry** — only inside the NY AM (09:30–11:00 ET) or NY PM
   (13:30–16:00 ET) killzones, with R:R ≥ 1. Two selectable fill styles
   (`--entry-mode`):
   - `retrace` (default) — wait for price to trade back into the just-inverted
     gap (a "tap") and fill there. Can time out unfilled if price never comes
     back.
   - `immediate` — skip the wait, fill at the next bar's open right when the
     gap inverts. Never misses the setup, but pays whatever price the
     displacement leg already reached instead of a retracement — a larger,
     worse-priced stop distance for the same target, so R:R is measured from
     a worse starting point and more candidates fail the R:R ≥ 1 filter.
5. **Stop / target** — stop beyond the swept swing (+1pt buffer); target is
   the nearer of prior-day high/low or the nearest opposing session swing.

This is deliberately a rare, high-confluence setup, and Yahoo's free 5-minute
bars only go back 60 days — a 60-day run typically produces single digits of
qualifying trades per symbol. That's enough to confirm the engine (bias, SMT,
FVG detection/inversion, entry/stop/target simulation, SQLite export) is
wired correctly end to end; it is **not** a statistically meaningful sample.
For a real read on the edge: run against 1–2 years of intraday data from a
paid vendor (Databento, Polygon, IQFeed) or your own broker/platform export,
or loosen one filter at a time (drop the SMT requirement, widen the
killzones) to see how much each condition is actually contributing.

Uses `backtest/futures_engine.py` rather than the vectorized `backtest/engine.py`
above, because entries are discrete (a specific tap into a specific gap),
need a second correlated instrument for SMT confirmation, and exit on
whichever of a fixed stop/target is hit first — not a per-bar position
weight applied to the next bar's return.

## Layout

```
trading_bot/
  data/
    crypto_fetcher.py       # Binance klines
    polymarket_fetcher.py   # Gamma resolved markets + CLOB price history
    futures_fetcher.py      # Yahoo Finance intraday bars (MES/MNQ)
  strategies/
    crypto_strategy.py
    polymarket_strategy.py
    ifvg_strategy.py         # bias / SMT divergence / FVG detection & inversion
  backtest/
    engine.py               # time-series backtest (crypto)
    polymarket_engine.py    # event-based backtest (Polymarket)
    futures_engine.py       # trade-based backtest (IFVG, MES/MNQ)
    metrics.py               # Sharpe, CAGR, max drawdown, win rate
  run_crypto_backtest.py
  run_polymarket_backtest.py
  run_ifvg_backtest.py
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
