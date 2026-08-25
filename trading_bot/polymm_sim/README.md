# polymm_sim — synthetic simulation of kachence/polymm's strategy

This asked-for deliverable was "run a paper trade or backtest on
[kachence/polymm](https://github.com/kachence/polymm) and show me results."
Neither is actually possible from this environment, and it's worth being
explicit about why before looking at any numbers here:

- **Live paper trading** needs polymm's two live Polymarket websockets plus a
  the-odds-api key. This sandbox's network egress policy rejects
  `data-api.polymarket.com`, `gamma-api.polymarket.com`, and
  `the-odds-api.com` outright — confirmed by hand, each one fails the CONNECT
  at the proxy with a 403.
- **A real backtest** needs historical odds + historical Polymarket order
  book data. polymm doesn't ship either — it's a live orchestrator, not a
  backtester, and the README says the scraping pipeline that fed it isn't
  included.
- Pulling the real wallet's trade history (the public `@b00k13` wallet the
  README reconciles against) also goes through the blocked `data-api`.

So this is a **synthetic Monte Carlo simulation**: it runs polymm's actual,
unmodified de-vig code (`vig_removal.py`, copied verbatim from the repo,
MIT-licensed) against a documented, parameterized model of bookmaker odds
and Polymarket order books, and reports what that pricing/hedging logic
would earn under those modeled assumptions. It is not real market data and
the resulting P&L is not a forecast — see `simulate.py`'s module docstring
for the full model description.

## Files

| File | What it does |
|---|---|
| `vig_removal.py` | polymm's real de-vig math, copied unmodified |
| `simulate.py` | The Monte Carlo engine: one run = one simulated "book" of matches |
| `run_report.py` | Runs `simulate.py` across many seeds + parameter sweeps, writes `report.json` |
| `report.json` | The data behind the results dashboard (200 seeds × 1,500 matches) |
| `dashboard_template.html` | The results page, with a `%%REPORT_JSON%%` placeholder |
| `build_dashboard.py` | Injects `report.json` into the template → `dashboard.html` |

## Running it

```bash
pip install -r ../requirements.txt   # numpy, pandas

# one run, human-readable summary
python3 simulate.py --n-matches 3000 --seed 42

# the full report (200 seeds, ~1-2 min)
python3 run_report.py --n-seeds 200 --n-matches 1500 --out report.json
python3 build_dashboard.py           # -> dashboard.html
```

`simulate.py --help` lists every tunable parameter (min_edge, hedge
latency, market speed, adverse selection, etc). `min_edge` and `shares`
default to polymm's real `src/core/config.py` values (7% / 10 shares);
everything else is a documented, admittedly-arbitrary assumption about a
sports odds market this session couldn't observe.

## Headline result (200 seeds × 1,500 matches, default params)

| | mean | profitable in |
|---|---|---|
| Arb leg (both legs hedged) | +$5.94 | 100% of runs |
| Residual leg (hedge failed) | -$5.58 | 40% of runs |
| Net | +$0.37 (std $20.86) | 49.5% of runs |

Two things worth taking away, both directional matches to the real
writeup rather than exact numbers:

1. **The arb leg is the real, dependable edge.** It's positive in every
   single one of the 200 runs. This is polymm's actual math working as
   designed.
2. **The residual leg is what makes the strategy fragile, not the arb
   math.** Net profitability across runs is close to a coin flip. This
   isn't something the simulation discovered — it's a deliberately
   injected assumption (`adverse_selection_edge_erosion`, see
   `simulate.py`) built to reflect the real writeup's finding
   ("Adverse selection eating away my Polymarket bot arbitrage
   profits" — [kacho.io](https://kacho.io/why-my-polymarket-arbitrage-bot-lost-money)).
   Turn it off and the residual leg looks like a naive, uncorrelated
   positive-edge book that would clearly print money — which is exactly
   the naive expectation the real bot's operator says did not hold up.

The latency sweep in the dashboard reproduces the README's other claim
directly: net P&L in this model degrades monotonically, and eventually
goes negative, as the simulated bot's hedge latency increases — "it got
too slow to defend its edge."

## What would make this real

Run it somewhere with actual network access to Polymarket/the-odds-api,
replace the synthetic odds/order-book generators in `simulate.py` with
real historical pulls, and re-fit `adverse_selection_edge_erosion` (and
the other assumed parameters) against real fill data instead of one
reported summary ratio. Until then, treat every number here as
illustrative of the strategy's mechanics, not its real-world P&L.
