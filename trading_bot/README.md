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

## Kalshi 15-minute BTC bot (live/paper trading)

`run_kalshi_btc15m_bot.py` trades Kalshi's "BTC price up in next 15 mins?"
markets (series `KXBTC15M`) with one rule: **in the last 2:30 before a market
closes, if YES costs 80c or more, buy YES; if NO costs 80c or more, buy NO.**
One entry per market, sent as an immediate-or-cancel limit order so it never
pays more than ask + `--slippage` (capped at `--max-price`).

```bash
# Paper trading (the default; no API key needed, nothing is sent to Kalshi)
python -m trading_bot.run_kalshi_btc15m_bot

# Kalshi's demo exchange (fake money, needs a demo API key)
python -m trading_bot.run_kalshi_btc15m_bot --demo --live

# Real money
export KALSHI_API_KEY_ID=...                  # kalshi.com -> Account & security -> API Keys
export KALSHI_PRIVATE_KEY_PATH=~/kalshi.pem   # the private key file Kalshi gives you
python -m trading_bot.run_kalshi_btc15m_bot --live --stake 5 --max-daily-loss 25
```

Useful flags: `--threshold 0.80`, `--max-price 0.99`, `--window 150`,
`--stake 10` (dollars per trade), `--max-trades N`, `--max-daily-loss X`.
Every settled trade is appended to `kalshi_btc15m_trades.csv`.

### Backtest of this exact rule

```bash
python -m trading_bot.run_kalshi_btc15m_backtest --max-markets 1500
```

This uses Kalshi's public 1-minute candles. Because they're 1-minute, the
2:30 window is approximated by checking prices at T-2:00 and T-1:00. Result
on 1,500 settled markets (Sep 29 - Oct 14, 2026), $10 stake, taker fees
included, no slippage:

| Entry price | Trades | Win rate | Avg price | Return on staked |
|---|---|---|---|---|
| all | 854 | 93.6% | 0.932 | **-0.11%** |
| 0.80-0.85 | 77 | 84.4% | 0.820 | +1.74% |
| 0.85-0.90 | 123 | 85.4% | 0.870 | -2.83% |
| 0.90-0.95 | 241 | 92.9% | 0.924 | -0.02% |
| 0.95-1.00 | 413 | 98.1% | 0.976 | +0.29% |

**About break-even.** The favorite wins about as often as its price says it
should, so fees and slippage take whatever edge is left. The differences
between buckets are within noise at these sample sizes. Paper trade it before
risking money, and keep in mind that one loss at 95c wipes out the profit
from about 19 wins.

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
  kalshi/
    client.py               # Kalshi API v2: auth signing, markets, candles, V2 orders
    strategy.py             # last-2:30 / 80c rule, fees, tick rounding
  run_crypto_backtest.py
  run_polymarket_backtest.py
  run_kalshi_btc15m_bot.py       # live/paper bot
  run_kalshi_btc15m_backtest.py  # backtest of the same rule on settled markets
```

## Known limitations / next steps

- Polymarket API field names were written from documentation, not verified
  against a live call (this session's egress was blocked) — check the actual
  response shape before trusting results and adjust `data/polymarket_fetcher.py`
  if the API has moved on.
- No walk-forward or out-of-sample split is enforced — if you tune
  `--fast`/`--slow`/`--entry-threshold` against the same window you evaluate
  on, you will overfit. Hold out a test period.
- Live order placement exists only for the Kalshi BTC bot above. The crypto and
  Polymarket strategies are still backtest-only.
