# Strategy research for the $10K 1-Step Select eval: results

## Bottom line

**No strategy qualified. Nothing was wired into the bot.**

I tested five candidates on SP500 and gold, over ~24.8 months of 5m data with a locked 30% holdout. All of them had negative expectancy after costs on the holdout (−0.02R to −0.26R per trade, PF 0.45–0.97), and none passed a single eval replay.

The whole-grid check backs this up. I scored every parameter combination on the holdout afterwards: 398 strategy × parameter × market combinations, and zero of them meet the qualifying bar. So the result isn't an artefact of the pick rule.

The bot still runs the original BTC strategy by default. The README warning ("do not run it live as-is") still applies.

## Data: why a proxy, and how it was checked

- **The platform feed is too short.** It keeps only ~5,000 bars per market for the TradFi perps (`hyperliquid|xyz:*`): ~17 days of 5m and ~208 days of 1h. `xyz:SP500` was only listed on 2026-03-18 and `xyz:GOLD` on 2025-12-22. So 12 months of intraday history does not exist on the platform. Only Binance crypto goes back 12+ months.
- **The proxy.** I used 1m bars from histdata.com: XAUUSD, SPXUSD, EURUSD and USDJPY, 2024-09-01 → 2026-09-24, aggregated to 5m and cached under `data/`. Dukascopy rate-limited too hard to use, and Alpha Vantage intraday history is premium-only.
- **A timestamp bug caught during validation.** histdata says its stamps are fixed EST. They're actually UTC−5 / UTC−4 switching on the **EU** DST dates. Read naively, March 2026 was off by an hour; this is now fixed in `research/histdata.py`.
- **Validation** (`research/validate_proxy.py`) compares proxy returns with the platform's own perp bars:

| Proxy vs perp | 5m returns, NY 09:00–16:00 | 1h returns (every month Mar–Sep 2026) | Price basis |
|---|---|---|---|
| XAUUSD vs xyz:GOLD | 0.97 | 0.99–1.00 | +0.04% |
| SPXUSD vs xyz:SP500 | 0.99 | 0.99–1.00 | −0.03% |
| EURUSD vs xyz:EUR | 0.75 | 0.95 | +0.03% |
| USDJPY vs xyz:JPY | 0.78 | 0.96 | −0.03% |

- **Signal agreement.** On the last ~17 days I ran every frozen config on both feeds. They fired on the same days and sides; entry times sometimes differ by one 5m bar. The POC levels differ a little, so POC signals only partly match.
- **The perps trade 24/7.** On weekends they're nearly flat around a frozen oracle, and they jump when the underlying reopens (Sunday 18:00 ET). Every strategy here is intraday and flat by 15:55 ET (momentum: 16:00). No position is held through the 17:00 break or the weekend. The backtester also refuses an entry if the next bar comes after a gap.

## Market pre-screen: FX was dropped before testing

- **Fees vs spreads.** FX fees are the lowest, but on this venue the books aren't. Measured $30k market-order impact on the Hyperliquid books (2026-09-30) was ~0.1–0.3 bp for GOLD and SP500, ~2–2.5 bp for EUR and JPY.
- **xyz:EUR bars.** 43% of its 5m bars are completely flat, and the average 5m range (~2 bp) is smaller than its 2.7 bp spread.
- **Proxy fit.** The proxy tracks FX poorly at 5m (correlation ~0.75).

Intraday FX on this platform is spread-bound, so I didn't spend a candidate slot on it. Crypto wasn't retested: your BTC work already showed fees taking ~13% of R, and the TradFi books are cheaper on both fees and depth.

## Cost model (conservative, applied everywhere)

| Asset class | Fee / fill | Swap / day | Slippage / fill used |
|---|---|---|---|
| crypto | 0.03% | 0.03% (hourly) | 3 bp (unchanged) |
| stocks / commodities / indices | 0.005% | 0.015% | 1 bp (GOLD, SP500): ~3–10x measured impact |
| FX | 0.0025% | 0.005% | 3 bp (EUR, JPY) |

The simulator keeps every existing assumption: next-bar-open entries, stop wins same-bar ties, gaps honoured, fees + swap. It adds two, both pessimistic: slippage now also applies to take-profit and time exits, and there are no entries across closures.

On TradFi, fees + slippage still come to **~0.09R per trade** at $45 risk. Most of that is the slippage assumption, because stops that fit a 5m structure force $15–30k notional.

## Protocol

- **Split:** in-sample 2024-09-01 → 2026-02-10 (70%); holdout 2026-02-10 → 2026-09-24 (~7.5 months). `run_research.py select` physically truncates the data at the split.
- **Walk-forward:** the in-sample period is cut into 6 blocks, with 4 anchored folds (train on blocks 0..k−1, test block k).
- **Pick rule:** at every fold and for the final pick, maximise expectancy (R per trade) averaged over the grid point and its immediate neighbours, with at least 30 trades. A lone peak loses to a good neighbourhood.
- **Markets:** each config is chosen on all its markets traded together (one position at a time, like the bot). A market with negative in-sample expectancy at that config is dropped, unless it's the last one left.
- **Freezing:** the configs were frozen in `research/selected.json` (commit `7dc9eb5`) before `run_research.py holdout` ran once.
- **Grids:** at most 4 parameters per strategy, on coarse grids (182 grid points in total; 290 simulations counting markets).
- **Eval replay:** `run_eval` is unchanged, run from every trade day. "Pass / fail / halted" below are shares of finished runs. **Halted** means the bot's guardian stopped trading at about −$220 (kill buffer, shrinking risk). In practice that's the failure mode, which is why FAIL is always 0%.
- **Baseline:** under these guardian rules, a zero-edge ±1R coin flip passes **19–20%** of runs (not 25%), and only **1%** with 0.1R costs per trade. To clear 35%, a strategy needs about **+0.05R net per trade at a 1:1 payoff**, or +0.10R at 2:1.

## Results per strategy

Stats are at a fixed $45 risk per trade. The max DD is on the full trade sequence, not an eval run.

### 1. NY opening-range breakout (SP500, GOLD)

**Rules:** the range is the 09:30 → 09:30+`or_min` high/low. After a 5m close outside it, enter one of three ways (`entry`):
- **break:** on that close
- **retest:** when a later bar touches the broken edge and closes back outside
- **fvg:** on a pullback into the breakout's 3-bar fair-value gap

The stop is at the range midpoint or the far side (`stop`), and the target is `rr`× risk. First signal of the day only, entries until 12:00, flat at 15:55.

**Frozen config:** `or_min=30, entry=break, stop=far, rr=3`. Both markets were negative in-sample, so only SP500 was kept.

| | Trades | Win | Avg R | PF | Max DD | Eval pass / fail / halted |
|---|---|---|---|---|---|---|
| In-sample | 360 | 45% | −0.019 | 0.96 | $545 | 0% / 0% / 100% |
| Walk-forward OOS | 260 | 39% | −0.052 | 0.90 | $1,103 | — |
| **Holdout** | 151 | 44% | **−0.057** | 0.87 | $710 | 0% / 0% / 100% |

Only 2% of in-sample grid points were positive, and gross expectancy before costs was +0.07R in-sample and +0.03R on the holdout. The best holdout combination in hindsight (fvg, 30 min, mid stop, 3R) made +0.10R with PF 1.22, but still had a 0% pass rate.

**Verdict:** there's a small breakout drift, but it's smaller than the costs. Reject.

### 2. Session liquidity sweep + market-structure shift (SP500, GOLD)

**Rules:** the reference range is Asia (20:00–00:00 NY) or London (02:00–05:00 NY) (`ref`). Between 07:00 and `window_end`, price trades beyond one side of the range. Then a 5m close back inside the range, and through the low (or high) of the `swing` bars before the sweep extreme, triggers a trade back toward the range. The stop is at the sweep extreme. The target is `rr`× risk, or the far side of the range (`rr=opp`, only if that's at least 1R). One trade per day, flat at 15:55.

**Frozen config:** `ref=asia, swing=6, rr=2, window_end=11`. Both markets were negative in-sample; GOLD was kept.

| | Trades | Win | Avg R | PF | Max DD | Eval pass / fail / halted |
|---|---|---|---|---|---|---|
| In-sample | 206 | 38% | −0.024 | 0.95 | $1,403 | 4% / 0% / 96% |
| Walk-forward OOS | 250 | 32% | −0.187 | 0.68 | $2,401 | — |
| **Holdout** | 77 | 32% | **−0.257** | 0.58 | $905 | 0% / 0% / 100% |

No in-sample grid point was positive, and none was positive on the holdout either. Holdout gross expectancy was −0.20R even before costs.

**Verdict:** worst of the five. The "sweep then reverse" pattern didn't happen more often than chance on either market. Reject.

### 3. Volume-profile POC rejection (GOLD)

**Rules:** the POC comes from the previous weekday's session: RTH 09:30–16:00 or the full 18:00–17:00 day (`profile`). It's built from a time-at-price profile, because proxy volume isn't comparable with the perp's volume.

Between 09:30 and 13:00, a trade needs three things: price came from at least `approach_atr` ATR away within the last hour, a 5m bar pierces the POC, and that bar closes back on the side it came from. Trade away from the POC with the stop `buffer_atr` ATR beyond the wick and the target at `rr`× risk. One trade per day.

**Frozen config:** `profile=full, rr=3, buffer_atr=0.75, approach_atr=2`

| | Trades | Win | Avg R | PF | Max DD | Eval pass / fail / halted |
|---|---|---|---|---|---|---|
| In-sample | 60 | 40% | +0.064 | 1.11 | $447 | 0% / 0% / 100% |
| Walk-forward OOS | 38 | 29% | −0.104 | 0.85 | $447 | — |
| **Holdout** | 25 | 32% | **−0.024** | 0.96 | $207 | none finished (25 unfinished) |

This was the best in-sample result, but an isolated one: only 9% of the grid was positive, and 3 of the 4 neighbours were negative. It's also too rare: 25 holdout trades is below the 60-trade minimum on its own.

**Verdict:** noise; its walk-forward folds swung between +0.53R and −0.67R. Reject.

### 4. Mean reversion: overnight-gap fade (SP500)

**Rules:** gap = price at 09:30 vs the previous weekday's 16:00 close. If `min_gap` ≤ |gap| ≤ 1%, fade it at the 09:30 open. The stop is `stop_gap`× the gap size beyond entry, and the target is the prior close (`fill`) or half the gap (`half`). Flat at 15:55.

**Frozen config:** `min_gap=0.5%, stop_gap=0.5, target=fill`

| | Trades | Win | Avg R | PF | Max DD | Eval pass / fail / halted |
|---|---|---|---|---|---|---|
| In-sample | 55 | 42% | +0.094 | 1.18 | $288 | 0% / 0% / 100% (51 of 55 unfinished) |
| Walk-forward OOS | 60 | 35% | −0.092 | 0.83 | $518 | — |
| **Holdout** | 38 | 37% | **−0.021** | 0.96 | $363 | 0% / 0% / 100% (24 unfinished) |

Gross expectancy was +0.20R in-sample and +0.07R on the holdout, so costs account for about 0.09R of the gap between gross and net. Big gaps are rare (38 holdout trades), and 17% of grid points were positive on the holdout.

**Verdict:** the most plausible of the five, but too infrequent and not robust. Reject.

### 5. Own idea: late-day intraday momentum (SP500)

**Why an edge could exist:** Gao, Han, Li & Zhou (2018, *Market intraday momentum*) and Baltussen, Da, Lammers & Martens (2021) show that the return from the prior close to 10:00 predicts the last half hour's return. The proposed reasons are dealers hedging short gamma and leveraged ETFs rebalancing in the direction of the day's move into the close.

**Rules:** at 15:30 NY, go with the sign of the prior-16:00→10:00 return if |r1| ≥ `min_r1`. With `confirm`, the 15:00→15:30 return must agree. The stop is `stop_atr`× the 5m ATR and the TP is 4R; the exit is at 16:00.

**Frozen config:** `min_r1=0, stop_atr=5, confirm=True`

| | Trades | Win | Avg R | PF | Max DD | Eval pass / fail / halted |
|---|---|---|---|---|---|---|
| In-sample | 178 | 42% | −0.086 | 0.58 | $757 | 0% / 0% / 100% |
| Walk-forward OOS | 207 | 42% | −0.066 | 0.67 | $787 | — |
| **Holdout** | 83 | 39% | **−0.112** | 0.45 | $461 | 0% / 0% / 100% |

Gross expectancy was about 0 in-sample (−0.002R) and −0.03R on the holdout, and no grid point was positive in either period. The effect isn't present at all in 2024–2026 SP500 data at this horizon.

**Verdict:** no signal left to pay for. Reject.

## Post-hoc diagnostics (`research/diagnostics.py`, not used for any decision)

| Strategy | IS gross / net R | Holdout gross / net R | Holdout grid: positive share / qualifying |
|---|---|---|---|
| ORB | +0.072 / −0.019 | +0.034 / −0.057 | 7% / 0 of 144 |
| Sweep | +0.080 / −0.024 | −0.204 / −0.257 | 0% / 0 of 180 |
| POC | +0.200 / +0.064 | +0.061 / −0.024 | 0% / 0 of 32 |
| Gap fade | +0.196 / +0.094 | +0.070 / −0.021 | 17% / 0 of 24 |
| Momentum | −0.002 / −0.086 | −0.032 / −0.112 | 0% / 0 of 18 |

The same pattern shows up as in your BTC work. Where there's any gross edge at all (ORB, POC, gap fade), it's under 0.1R, and ~0.09R of costs removes it. The market also has to cooperate for 20R before a ~5R guardian halt, which a near-zero edge can't do reliably.

## What I'd try next (not done: each would be a new candidate, tested on fresh data)

- **Longer holding periods.** Multi-day or higher-timeframe trades on GOLD/SP500 use wider stops, so costs become a smaller share of R. That means modelling weekend gap risk explicitly, since the protocol forbids holding through closures without it.
- **Tighter execution.** Maker/limit entries would help, if the platform's order types allow them, because slippage is most of the TradFi cost.
- **Accumulate platform data.** Run `research/fetch_platform.py` on a schedule so the perp history itself grows past 12 months.

## Code changes

- **`config.py`**
  - Added `ASSET_CLASS_COSTS` (crypto / tradfi / fx) and `MarketSpec` + `MARKETS` (BTC, GOLD, SP500, EUR, JPY), with `market_id`, `provider`, `coin`, size and price decimals, max leverage and per-market slippage, all taken from `GET /v1/markets`.
  - `Config.for_market(symbol)` returns a copy bound to one market: per-class fees and swap, `leverage` and `max_notional_mult` capped at the market's max leverage.
  - Added `strategy` / `params` fields. The defaults are unchanged (BTC, the original strategy), so the bot behaves as before.
- **`strategy.py`**
  - The original signal is kept as `trend_breakout`.
  - Added `orb`, `sweep`, `poc_rejection` (+ `poc()`), `gap_fade` and `intraday_momentum`, dispatched by `generate(bars, cfg)` via `STRATEGIES`.
  - `Signal` gained optional `rr`, `target` and `exit_by` fields, and there's a new `take_profit()` helper.
  - `LOOKBACK_MS` gives each strategy's history needs.
- **`backtest.py`**
  - Per-market costs (via `for_market`) and a time-based warm-up window.
  - No entry if the next bar comes after a closure.
  - Time exits at the open of the first bar at or after `exit_by`.
  - Slippage on take-profit and time exits too.
  - Each trade records its exit time, R and exit type.
  - New `merge()` (one position at a time across markets), `stats()` and `eval_rates()`, plus `--symbol` / `--strategy` flags. `run_eval` is unchanged.
- **`risk.py`**
  - One line in `position_size`: fees and slippage are charged on `max(entry, stop)` instead of `entry`. Before this, a full stop on a short could cost a few cents over the $45 budget; the new sizing test caught it.
  - The kill switch, daily stop, target banking and `Guardian` are untouched.
- **`bot.py`, `client.py`, `market_data.py`:** unchanged. With no qualifying strategy there was nothing to wire in, so no multi-symbol loop or time-exit handling was added. The research strategies can't be selected from the environment.
- **`research/`** (new):
  - `histdata.py`: proxy download plus the timezone fix
  - `fetch_platform.py`: caches platform candles by `coin`
  - `validate_proxy.py`
  - `run_research.py`: `select` / `holdout`
  - `diagnostics.py`
  - `selected.json` (frozen picks), `holdout.json`, `diagnostics.json`
- **`tests/`** (new, 40 tests, `python -m pytest tests`):
  - Signal logic for all five strategies.
  - Simulator conventions: next-bar-open fill, stop-wins ties, gap-through-stop, time exit, no entry after a closure, TP slippage.
  - `merge` and `run_eval`.
  - Sizing for every market / asset class: per-class fees and swap; a full stop including fees and slippage stays ≤ $45 on longs and shorts; the notional cap respects min(3x, max leverage); sizes round down to size decimals.

## Not done, and why

- **DRY_RUN and sandbox runs:** the task makes these conditional on a strategy qualifying, and none did. This environment also has no `fp_test_` key. The sandbox and API hosts (`sandbox.` / `developers.` / `api-stream.myfundedperpetuals.com`) are reachable from here.
- **To reproduce:**

  ```
  python research/histdata.py XAUUSD 2024-09 2026-09   # (and SPXUSD)
  python research/run_research.py select
  python research/run_research.py holdout
  ```
