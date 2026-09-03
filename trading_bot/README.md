# Trading bot backtesting framework (crypto, Kalshi, prop-firm forex, Polymarket)

A strategy + backtest framework across four markets. Verified working, live,
against real public APIs (no auth/API keys required) from this environment:

- **Crypto**: Coinbase Exchange's public REST API (`api.exchange.coinbase.com`)
  for OHLCV candles. (Binance is geo-blocked from this environment — HTTP 451
  — and Kraken's public OHLC endpoint only retains ~720 recent candles, so
  neither supports a multi-year backtest here.)
- **Kalshi**: the public `trade-api/v2` REST API (`api.elections.kalshi.com`)
  for settled markets and candlestick price history.
- **Prop-firm forex**: the Frankfurter API (`api.frankfurter.dev`), ECB daily
  reference rates, wrapped in a funded-account-challenge rule simulator
  (max daily loss / max drawdown / profit target).
- **Polymarket**: the public Gamma API for resolved markets works live, but
  the CLOB price-history endpoint (`clob.polymarket.com/prices-history`)
  currently returns `400 Bad Request` for every market tried — the request
  shape needs debugging against Polymarket's current API before this one is
  trustworthy. Treat it as unverified/best-effort; the other three are
  confirmed working with real data (see results below).

## Important: this will not hand you a guaranteed-profitable bot

No backtest can promise that. A strategy that looks great on historical data
is easy to produce by accident (overfitting/curve-fitting) and can still lose
money live. What's here is:

- Strategy + backtest engines that are honest about the math (fees, slippage,
  no lookahead bias, a naive baseline for comparison where one applies).
- Simple, well-known demonstration strategies per market (trend-following /
  mean-reversion for crypto and forex; the favorite-longshot bias for
  prediction markets) — chosen because they're grounded in documented, real
  phenomena, not because they're guaranteed winners.
- Run against real historical data, with real numbers below — including the
  runs that lost money or failed the prop-firm rules. Nothing here is cherry-picked.

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
python -m trading_bot.run_kalshi_backtest --series KXHIGHNY --max-markets 250
python -m trading_bot.run_propfirm_backtest --start 2022-01-01 --end 2026-08-01
python -m trading_bot.run_polymarket_backtest --max-markets 200  # unverified, see above
```

## Crypto backtest — real results (BTC-USD, Coinbase, 2021-01-01 .. 2026-08-01, daily)

| Strategy | Total return | CAGR | Sharpe | Max drawdown | Trades | Win rate | Buy & hold |
|---|---|---|---|---|---|---|---|
| `sma_crossover` (20/50) | **-1.3%** | -0.2% | 0.18 | -59.2% | 26 | 30.8% | +113.4% |
| `rsi_mean_reversion` (14, 30/50) | **+88.9%** | 12.1% | 0.52 | -37.4% | 40 | 60.0% | +113.4% |

Both strategies underperformed simple buy-and-hold over this window (BTC was
in a strong multi-year uptrend), though RSI mean-reversion did so with a much
smaller max drawdown (-37% vs. whatever buy-and-hold's drawdown was through
the 2022 bear market). **The honest takeaway: in a strong trending market,
being in cash part of the time costs you more upside than it saves you in
drawdown, unless your timing is very good — and 60% win rate here still
wasn't enough to beat holding.**

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

Both are long-only/flat (no shorting). Supported `--symbol` values map to
Coinbase products (BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT, ADAUSDT,
LTCUSDT, LINKUSDT, or any `XXXUSDT`/native Coinbase `XXX-USD` ticker).

## Memecoin backtest (DOGE-USD, Coinbase, 2021-01-01 .. 2026-08-01, daily)

Same crypto engine and strategies, pointed at DOGE — the only memecoin with
clean, exchange-verified historical OHLCV via a public API with no key; most
newer memecoins only trade on DEXs (Solana/Base/etc.) where honest historical
data requires a paid API (Birdeye, DexScreener Pro) and liquidity is thin
enough that backtests are unreliable anyway. Consider this a proxy for "meme
volatility," not a claim it generalizes to any specific token.

| Strategy | Total return | Max drawdown | Trades | Win rate | Buy & hold |
|---|---|---|---|---|---|
| `sma_crossover` (20/50) | **+15.5%** | -63.5% | 19 | 21.1% | **-82.8%** |
| `rsi_mean_reversion` | **-66.1%** | -69.0% | 40 | 50.0% | -82.8% |

```bash
python -m trading_bot.run_crypto_backtest --symbol DOGEUSDT --interval 1d \
    --start 2021-01-01 --end 2026-08-01 --strategy sma_crossover --fast 20 --slow 50
```

**The honest takeaway**: buy-and-hold DOGE over this window lost **83%** of
its value (it pumped hard in the 2021 mania and never recovered). Trend
following (SMA crossover) turned that into a modest +15.5% gain purely by
being in cash for most of the collapse — a real, useful result. Mean
reversion (buying dips) lost 66% doing the opposite: every "buy the dip" was
followed by a deeper dip. **This is the actual shape of memecoin risk**: the
strategy that wins is the one willing to sit out, not the one trying to catch
the bounce, and even the winning strategy here only avoided a loss — it
didn't produce a strategy you'd want to bet real size on (21% win rate, -63%
drawdown along the way).

## Kalshi backtest

Uses NYC daily-high-temperature markets (`KXHIGHNY`) by default — a series
with many independent, objectively-resolved binary markets and real
intraday-candlestick price history, which makes it a clean testbed for the
favorite-longshot bias. Each market's price history is cut off
`--hours-before-close` hours before resolution so the entry price can't leak
the outcome.

```bash
python -m trading_bot.run_kalshi_backtest \
    --series KXHIGHNY --strategy favorite_longshot \
    --entry-threshold 0.85 --max-markets 250 --hours-before-close 6
```

**Real result** (250 settled `KXHIGHNY` markets, entry threshold 0.85, entries
cut off 6h before close):

| Trades | Win rate | Total P&L | ROI | Max drawdown | Avg P&L/trade |
|---|---|---|---|---|---|
| 47 | **87.2%** | **-$296.56** | -2.97% | -3.4% | -$6.31 |

**The honest takeaway, and the single most important lesson in this whole
repo: an 87% win rate still lost money.** Buying heavy favorites wins often,
but by small amounts (paying $0.85+ for a contract that pays out $1 caps the
per-win upside at ~$0.15), while the rare 13% of losses wipe out most of a
stake in one shot — and Kalshi's real taker fee, which is largest exactly at
these near-certain price levels, tips the already-thin edge negative. This is
the correct, expected behavior of a naive favorite-longshot strategy after
real fees — not a bug in the backtest.

Strategies (`strategies/kalshi_strategy.py`):
- `favorite_longshot` — buys YES the first time price crosses `--entry-threshold`
  in the (pre-cutoff) history, betting on the documented favorite-longshot bias.
- `momentum` — buys YES after a price rise of `--min-move` over the trailing
  `--lookback` candles, while still in a non-extreme price band.

Each resolved market contributes at most one trade. Fees use Kalshi's real
published taker-fee formula (`data/kalshi_fetcher.kalshi_taker_fee`):
`ceil(0.07 * contracts * price * (1-price) * 100) / 100` dollars.

Note on the entry-threshold parameter: with `--hours-before-close` set high
(e.g. 24h), most weather markets haven't converged toward their eventual
price yet, so a 0.90 threshold rarely triggers — that's a real property of
this market (weather uncertainty resolves late), not a bug. Lower the
threshold or the hours-before-close window to get more trades.

## Prop-firm forex backtest

Simulates a typical funded-account challenge (FTMO-style defaults: 5% max
daily loss, 10% max total drawdown, 10% profit target) against real EUR/USD
daily rates.

```bash
python -m trading_bot.run_propfirm_backtest \
    --base EUR --quote USD --start 2022-01-01 --end 2026-08-01 \
    --strategy sma_crossover --fast 10 --slow 30 --leverage 5
```

**Real result** (SMA 10/30 crossover, 5x leverage, 2022-01-01..2026-08-01):
account breached its **5% max-daily-loss limit on 2022-02-24** (a single
-7.98% day at 5x leverage) — challenge failed, never got close to the 10%
profit target. At 15x leverage it breached even faster, on 2022-02-14.

**This is the real, honest lesson prop-firm evaluations teach**: daily-loss
limits are tight relative to normal FX volatility once you apply the leverage
needed to make a meaningful return, which is exactly why most funded-account
challenges fail industry-wide — it's not necessarily that the trading
strategy is bad, it's that the risk-management envelope is unforgiving.
Lower leverage survives longer but also takes far longer (if ever) to hit the
profit target within realistic timeframes — there's a real tension here, not
a free parameter to tune away.

**Caveat**: Frankfurter only provides one official daily close per pair (no
intraday OHLC), so "daily loss" here is close-to-close, which under-counts
real intraday breaches a live funded account would hit. Treat "passed" here
as an optimistic upper bound.

`--strategy` reuses the generic `sma_crossover` / `rsi_mean_reversion`
signal functions from `strategies/crypto_strategy.py` (they only need a
`close` column, so they work unchanged on daily forex data).

## Polymarket backtest (unverified — see caveat above)

```bash
python -m trading_bot.run_polymarket_backtest \
    --strategy favorite_longshot --entry-threshold 0.90 --max-markets 200
```

The Gamma markets API (`fetch_resolved_markets`) is confirmed working live.
The CLOB price-history call (`fetch_price_history`) returns `400 Bad Request`
for every market tried in this environment — needs debugging (check the
`market` query param format against Polymarket's current CLOB API docs)
before trusting any results from this module.

## Layout

```
trading_bot/
  data/
    crypto_fetcher.py       # Coinbase Exchange OHLCV
    kalshi_fetcher.py       # Kalshi settled markets + candlesticks
    forex_fetcher.py        # Frankfurter (ECB) daily FX rates
    polymarket_fetcher.py   # Gamma resolved markets + CLOB price history (unverified)
  strategies/
    crypto_strategy.py      # sma_crossover, rsi_mean_reversion (generic on 'close')
    kalshi_strategy.py      # favorite_longshot, momentum
    polymarket_strategy.py  # favorite_longshot, momentum
  backtest/
    engine.py               # time-series backtest (crypto, forex)
    kalshi_engine.py         # event-based backtest (Kalshi)
    polymarket_engine.py    # event-based backtest (Polymarket)
    propfirm_engine.py      # wraps engine.py with daily-loss/drawdown/target rules
    metrics.py               # Sharpe, CAGR, max drawdown, win rate
  run_crypto_backtest.py
  run_kalshi_backtest.py
  run_propfirm_backtest.py
  run_polymarket_backtest.py
```

## Known limitations / next steps

- Polymarket's CLOB price-history call needs debugging (see above) — don't
  trust `run_polymarket_backtest.py` results until that's fixed.
- No walk-forward or out-of-sample split is enforced anywhere — if you tune
  parameters against the same window you evaluate on, you will overfit. Hold
  out a test period before trusting any of these numbers further.
- Prop-firm daily-loss checks use daily-close data only (see caveat above) —
  a real intraday-monitored funded account would likely breach sooner, not
  later, than this simulation shows.
- No live execution/order-placement is implemented anywhere — this is
  backtesting only. Wiring up real trading (with real exchange/broker API
  keys) is a meaningfully bigger, riskier step; happy to help with that
  separately once you've reviewed real backtest results and decided a
  strategy is worth paper-trading first.
