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
3. **Inverse FVG** — a 3-candle fair value gap against bias later gets
   *engulfed*: a single candle whose open is already beyond the gap's far
   edge and whose close clears the near edge, spanning the whole gap in one
   move. It then flips polarity (becomes support/resistance in the bias
   direction). A close that merely grazes past one edge after a slow
   multi-candle grind doesn't count — that's erosion, not the decisive
   reversal candle IFVG entries are built on.
4. **Entry** — only inside the NY AM (09:30–11:00 ET) or NY PM
   (13:30–16:00 ET) killzones, with R:R ≥ 1. Three selectable fill styles
   (`--entry-mode`):
   - `retrace` (default) — wait for price to trade back into the just-inverted
     gap (a "tap") and fill there. Can time out unfilled if price never comes
     back.
   - `immediate` — skip the wait, fill at the next bar's open right when the
     gap inverts. Never misses the setup, but pays whatever price the
     displacement leg already reached instead of a retracement — a larger,
     worse-priced stop distance for the same target, so R:R is measured from
     a worse starting point and more candidates fail the R:R ≥ 1 filter.
   - `on_close` — fill at the engulfing candle's own close, one bar more
     aggressive than `immediate`. A common backtest simplification (real
     fills need a moment after that close prints); in practice nearly
     identical to `immediate` since 5m futures rarely gap far bar-to-bar.
5. **Stop / target** — stop beyond the swept swing (+1pt buffer); target is
   the nearer of prior-day high/low or the nearest opposing session swing.

Yahoo's free 5-minute bars only go back 60 days, so this is still a modest
sample — but with the engulfing-candle inversion rule it's no longer single
digits: 9 trades (`retrace`) to 12 (`immediate`/`on_close`) over the last 60
days, all three modes net positive (56–67% win rate, +$205 to +$521 combined
across MES+MNQ, 1 contract each). Encouraging, but still short of what you'd
want before trusting the edge. For a real read: run against 1–2 years of
intraday data from a paid vendor (Databento, Polygon, IQFeed) or your own
broker/platform export, or loosen the SMT requirement to see how much it's
actually contributing versus just cutting sample size.

Uses `backtest/futures_engine.py` rather than the vectorized `backtest/engine.py`
above, because entries are discrete (a specific tap into a specific gap),
need a second correlated instrument for SMT confirmation, and exit on
whichever of a fixed stop/target is hit first — not a per-bar position
weight applied to the next bar's return.

## CRT backtest (Candle Range Theory, MES / MNQ)

> **Note:** this section (`crt_strategy.py` / `crt_engine.py`) was a from-scratch
> guess at "CRT" for comparison against IFVG, written before the user's actual
> TradingView strategy was shared. It's kept as-is for that original
> IFVG-vs-CRT comparison below, but it is **not** the user's real strategy —
> see "CRT — the actual TradingView strategy" further down for that.

A second, comparable strategy: sweep the prior completed 1H candle's range
and look for a single candle that reverses straight back through the level
(open still beyond it, close back inside) — no second instrument, no SMT
divergence, just the sweep-and-reclaim candle itself. Shares the same HTF
bias, killzones, resting-liquidity targets, R:R gate, and three entry fill
styles as IFVG (`strategies/crt_strategy.py`, `backtest/crt_engine.py`).

```bash
python -m trading_bot.run_crt_backtest --symbol both --db-path crt.sqlite
```

Because it doesn't need SMT confirmation, CRT fires far more often than
IFVG on the same 60-day sample — 43 trades (`retrace`) vs. IFVG's 9, on 23
distinct days vs. 9. All three entry modes land close together: ~30-33% win
rate, net positive (~$320-$410 combined MES+MNQ, 1 contract each) on a
low-win-rate/big-winner profile (MES alone is slightly negative; MNQ alone
carries the total, similar to IFVG).

**IFVG vs CRT are not independent signals.** 7 of IFVG's 9 trading days
also had a CRT trade, and when both fired on the same day/symbol they
agreed on direction 6 of those 7 times — IFVG's setups are largely a
stricter subset of CRT's (both are liquidity-sweep-reversal concepts; IFVG
just adds the SMT + fair-value-gap filter on top). Day-level P&L
correlation is +0.23 — positive but far from 1, since CRT also fires on 16
days IFVG never touches. Running both isn't like adding an uncorrelated
asset (on the days they agree, you're doubling size on the same directional
bet, not diversifying); it's closer to running a looser filter (CRT) and a
stricter one (IFVG) over the same underlying edge. In this sample, combined
max drawdown (-$461.50) came in below CRT alone (-$566.75) and combined P&L
(+$527.50) beat either strategy alone — but at n=9-43 trades per leg, that's
a data point, not a conclusion.

## CRT — the actual TradingView strategy (`crt_a_plus_mes.pine`)

A faithful Python port of the user's real `strategy()` script (not the guess
above): `strategies/crt_pine_strategy.py` + `backtest/crt_pine_engine.py`,
run via `run_crt_pine_backtest.py`. Materially different rules:

- **Killzones**: London Open (02:00–05:00 ET) and NY Silver Bullet
  (10:00–11:00 ET) — not IFVG's AM/PM windows.
- **Confirmation is a state machine**, not a single-candle check: sweep the
  prior 1H candle's range → wait for an MSS (price closes back through the
  most recent 1-bar-fractal swing point, confirming a structure shift) →
  **exactly** 2 bars after that MSS, a one-shot check for a 3-candle FVG in
  the reversal direction. Miss that one bar and the setup is abandoned — no
  search window. Any unresolved setup also dies the moment the current
  hourly range period rolls over.
- **Stop**: the swept extreme, no buffer. **Target**: the opposite side of
  the *same* 1H range (not resting liquidity elsewhere). No R:R minimum.
- **Real position sizing**: `floor($200 risk / (stop_distance × $/point))`,
  capped at 20 contracts — not flat 1-contract.
- **Commission + slippage modeled**: $0.47/contract/side ($0.94 round trip,
  matching Tradovate's Lifetime tier all-in cost) plus 1 tick of adverse
  slippage on market fills (entries, and stops once triggered).

```bash
python -m trading_bot.run_crt_pine_backtest --symbol MES
python -m trading_bot.run_crt_pine_backtest --symbol MNQ
```

Because every stage is strict (narrow killzones, one-shot FVG check, hourly
expiry), this fires far less often than either CRT-guess or IFVG: **4 trades
on MES, 9 on MNQ** over the same 60 days. Both are **net losing** after
commission — MES -$218.77, MNQ -$264.90 — *despite* respectable win rates
(50% MES, 67% MNQ). The reason isn't win rate, it's risk:reward: because
entry only fires after sweep → MSS → a 2-bars-later FVG, price has usually
already retraced most of the way back toward the target (the opposite side
of the range) by the time the trade is taken, while the stop stays anchored
at the original sweep extreme. Measured across all 13 trades taken, **median
R:R was 0.24** (risking roughly 4x the intended reward) — only 2 of 13 trades
had R:R above 1. A strategy needs roughly an 80% win rate to break even at
that R:R; actual win rate was 61.5% combined. This is a structural property
of the entry timing, not a fluke of this particular 60-day window — worth
fixing (an R:R minimum before sizing the trade, or a target further out than
just the near side of the range) before trusting the win rate alone.

**Tested the R:R-filter fix (`--min-rr`) — it made results worse on this
sample, not better.** Filtering to R:R ≥ 0.5 drops the trade count to 2
MES + 2 MNQ, and all 4 surviving trades lost (combined -$635.89, worse than
the unfiltered -$483.67); R:R ≥ 1.0 leaves only 2 MNQ trades, still both
losers; R:R ≥ 2.0 leaves zero trades on either symbol. The reason: 7 of the
8 *winning* trades in the unfiltered 13 had R:R below 0.5 (several near
0.02-0.24 — tiny targets that got tapped almost immediately), so an R:R
floor filters out most of the winners along with the losers, and this
particular 60-day window's few high-R:R trades all happened to lose. That
could be a real signal (low-R:R setups here have a genuinely high hit rate
that a blunt R:R floor can't see) or it could just be n=13 being too small
to draw any conclusion from — there's no way to tell them apart without more
history. **This is the headline finding: at 60 days of data, this strategy's
sample is too thin to fix by filtering; it needs a longer backtest (a paid
intraday data vendor, or your own broker/platform's history export) before
any rule change here — R:R filter included — can be trusted.**

## Three more strategies (MES / MGC, explicitly not ORB)

Three independent, non-ICT-family strategies for contrast, each in its own
`strategies/*.py` module sharing one fill/exit engine
(`backtest/generic_engine.py`: next-bar-open fill, first stop/target touch
wins, fixed-hour flatten):

- **`vwap_reversion_strategy.py`** — session VWAP (resets daily) with a
  rolling-std band; fade a close outside 2 std back toward VWAP, stop at 3
  std, RTH only (09:30–16:00 ET). The one genuinely standard, non-ICT
  strategy in this package.
- **`turtle_soup_strategy.py`** — simplest liquidity-sweep fade: an engulfing
  candle (open beyond, close back inside) through the *prior day's* high or
  low, target the prior day's midpoint. No bias, no killzone, no MSS/FVG.
- **`order_block_strategy.py`** — last opposite-color candle before a
  displacement move (body ≥ 1.5× ATR) becomes a support/resistance zone;
  retest it, target the impulse leg's extreme. No bias, no killzone.

```bash
python -m trading_bot.run_new_strategies_backtest --symbol both
```

Run on ~2.5 months of real 5-minute MES and MGC (Micro Gold, $10/point,
newly added to `futures_fetcher.py`) bars — no commission or slippage
modeled here (unlike `crt_pine_engine.py`), 1 contract flat:

| Strategy | Trades | Win rate | Profit factor | Total P&L |
|---|---|---|---|---|
| VWAP reversion | 243 | 37.9% | 1.47 | **+$2,823.94** |
| Turtle Soup | 226 | 31.9% | 0.93 | -$506.49 |
| Order block | 235 | 41.3% | 0.74 | -$1,164.51 |

**VWAP reversion is the standout** — net positive on both symbols
individually (MES +$369.89, MGC +$2,454.05), with the largest sample of the
three by a comfortable margin. Its edge is the classic mean-reversion
shape: a sub-40% win rate more than compensated by asymmetric R:R (median
~3.2 — target is a 2-std round trip back to VWAP, stop is only 1 std past
entry). Turtle Soup and order-block retest are roughly breakeven-to-negative
and split by symbol (Turtle Soup: MES positive, MGC negative; order block:
negative on both, worst on MES).

Caveats before reading too much into the totals: no commission/slippage
here (VWAP's edge would survive it easily at 243 trades and $0.94/round
trip; order block's already-negative number would only get worse), 1
contract flat (no risk-based sizing), and still one ~70-day window — same
class of caveat as everything else in this file, just with a larger sample
than the ICT strategies get from 60 days of 5-minute data.

## Bug fix: degenerate VWAP stops inflated the earlier numbers above

`vwap_reversion_strategy.py`'s stop is computed from the signal bar's
rolling std; entry fills at the *next* bar's open. When that std was
degenerately small, the stop could end up on the wrong side of the actual
fill (found via the prop-firm sim below flagging a "long" trade with its
stop *above* entry) or merely a fraction of a tick away -- either way,
risk-based position sizing (which divides a fixed dollar risk by the stop
distance) then blows up toward the max contract cap on a nearly-meaningless
stop. `generic_engine.simulate_trades` now takes `min_stop_distance` and
rejects any setup whose stop ends up wrong-side or below that distance from
the actual fill; `run_new_strategies_backtest.py` and
`run_prop_firm_sim.py` pass 4 ticks per instrument.

Corrected VWAP totals (the "Three more strategies" section above used the
buggy numbers): MES +$165.83 (was +$412.92), MNQ +$2,609.57 (was
+$3,550.39), MGC +$1,534.68 (was +$2,454.05) -- about 33% lower combined,
but still positive on all three symbols individually, so the core finding
holds.

## Prop firm account simulation (Tradeify Select $25K, VWAP reversion)

`backtest/prop_firm_sim.py` + `run_prop_firm_sim.py` turn the per-trade
backtest output into a day-by-day account simulation against a specific
firm's rules (a plain dataclass -- swap the numbers for another firm/size):
$1,500 profit target, 40% consistency (eval only), $1,000 EOD trailing
drawdown enforced in real time (not just checked at day-end), floor locks
permanently once balance clears start+drawdown+$100, contracts 10 micro
(eval) -> 20 micro (funded), payouts up to $600/day once past the lock
threshold. `generic_engine.simulate_trades` now also tracks `mae_pts` --
the worst unrealized excursion reached during each trade's life, not just
its final P&L -- so the real-time floor check isn't limited to final
realized outcomes.

```bash
python -m trading_bot.run_prop_firm_sim --risk-per-trade 150
```

Position sizing (`--risk-per-trade`, capped by the phase's contract limit)
is *our* choice, not a firm rule, and it turns out to be the whole game:
running VWAP reversion across MES+MNQ+MGC simultaneously against this
account, **every risk level from $50 to $300 per trade busts the account**
within the ~70-day sample -- typically after comfortably passing the $1,500
eval target first, then blowing through the locked drawdown floor by a
matter of dollars on one real-time excursion. Only $25/trade (2.5% of the
$1,000 drawdown budget) survived the full window: 335 trades, still funded,
no breach. The standalone per-symbol backtests above look good precisely
because they carry no drawdown constraint at all -- against a real prop
account, the same strategy's edge is easily wiped out by position sizing
alone.

Two things this simulator does and does not do, spelled out in
`prop_firm_sim.py`'s docstring: contract caps ARE enforced account-wide via
proper event-driven open/close tracking across all three symbols (a trade
that would push the total over the limit is rejected, not silently
allowed). The real-time drawdown check is NOT a full bar-by-bar aggregate
reconstruction -- it fires when a new position opens, checking that trade's
own worst excursion against the account balance at that instant, so two
positions on different symbols hitting their own worst point simultaneously
with no new trade opening right then could in principle breach the floor
without being caught. Worth tightening before treating a "survives" result
as a guarantee.

## Layout

```
trading_bot/
  data/
    crypto_fetcher.py       # Binance klines
    polymarket_fetcher.py   # Gamma resolved markets + CLOB price history
    futures_fetcher.py      # Yahoo Finance intraday bars (MES/MNQ/MGC)
  strategies/
    crypto_strategy.py
    polymarket_strategy.py
    ifvg_strategy.py         # bias / SMT divergence / FVG detection & inversion
    crt_strategy.py          # bias / 1H range sweep & reclaim (from-scratch guess)
    crt_pine_strategy.py     # port of crt_a_plus_mes.pine: MSS + FVG state machine
    vwap_reversion_strategy.py  # session VWAP + std band fade
    turtle_soup_strategy.py     # prior-day high/low false-breakout fade
    order_block_strategy.py     # displacement candle + retest
  backtest/
    engine.py               # time-series backtest (crypto)
    polymarket_engine.py    # event-based backtest (Polymarket)
    futures_engine.py       # trade-based backtest (IFVG, MES/MNQ)
    crt_engine.py            # trade-based backtest (CRT guess, MES/MNQ)
    crt_pine_engine.py       # trade-based backtest (crt_a_plus_mes.pine port)
    generic_engine.py        # shared engine: vwap/turtle_soup/order_block
    prop_firm_sim.py          # account simulation against firm rules
    metrics.py               # Sharpe, CAGR, max drawdown, win rate
  run_crypto_backtest.py
  run_polymarket_backtest.py
  run_ifvg_backtest.py
  run_crt_backtest.py
  run_crt_pine_backtest.py
  run_new_strategies_backtest.py
  run_prop_firm_sim.py
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
