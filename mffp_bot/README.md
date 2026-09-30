# MyFundedPerps $10K Select eval bot

Python bot for the MyFundedPerps (MFFU's perps firm) **$10K 1-Step Select** evaluation:
**+$900 target, $300 static max drawdown, $300 daily loss** (daily loss measured from equity at midnight New York).

> **Read this first.** The included strategy (15m trend + 5m breakout, ATR stop, 2R) **lost money after fees
> over the last 12 months of BTC data** in every variant tested, in and out of sample. The execution,
> risk and backtest layers are solid; the *signal* in `strategy.py` is a placeholder until you find
> one that backtests positive. Do not run it live as-is.

## Files
| File | Role |
|---|---|
| `config.py` | Eval rules, risk limits, strategy params, costs |
| `client.py` | REST client: retries, 429/Retry-After, idempotency keys, flatten-all |
| `market_data.py` | Candles from the public WebSocket feed (`candles.history`) |
| `strategy.py` | Signal logic (shared by bot + backtest), swap this out |
| `risk.py` | Guardian: floors, kill switch, daily stop, target banking, sizing |
| `bot.py` | Live loop |
| `backtest.py` | Trade-level backtest + eval pass-rate replay |

## What the risk guardian enforces
- **Kill switch:** flattens and exits if equity gets within $50 of *either* floor (static $9,700 or today's daily floor). Stops can slip; this buffer is why.
- **Daily stop:** no new trades after -$100 on the day (well inside the firm's $300).
- **Max 4 trades/day**, one position at a time, cooldown after exits.
- **Shrinking risk:** $45/trade normally, auto-reduced as room to the floor shrinks; stops trading below $15.
- **Target banking:** once equity ≥ $10,912, flattens so the profit is *realized* (the target counts realized balance only).
- Every entry carries server-side TP/SL; if none is found after a fill, it flattens immediately.
- Uses the stricter of its own floors and the server's reported floors.

## Setup
```bash
pip install -r requirements.txt
```
1. **Sandbox first.** Settings > API Keys > Create > *Test (Sandbox)* → key starts `fp_test_`.
   Sandbox accounts are $100K, but the guardian applies the $10K dollar rules relative to whatever
   the starting balance is, so behaviour matches the real eval.
2. List accounts: `FP_API_KEY=fp_test_... python bot.py --accounts`
3. Dry run (default, sends nothing):
   ```bash
   export FP_API_KEY=fp_test_... FP_ACCOUNT_ID=...
   python bot.py
   ```
4. Sandbox with real paper orders: `DRY_RUN=0 python bot.py`
5. Live: create a `fp_live_` key, same code. The base URL switches automatically.

## Backtest
```bash
python backtest.py --days 90            # fetches + caches btc_5m.csv
python backtest.py --csv btc_5m.csv --rr 1.5 --risk 40
```
It prints trade stats and replays the eval from every start day with the guardian's rules,
reporting PASS / FAIL / halted / unfinished. Fills are conservative (next-bar open + slippage,
stop assumed first when stop and target share a bar, gaps honoured, fees + hourly swap included).

**Benchmark:** a zero-edge coin flip reaches +$900 before -$300 about 25% of the time (before fees).
A strategy is only worth running if its replayed pass rate clearly beats that.

## Notes
- API is **beta**; field names may change. `client.py` is where to fix breakage.
- MyFundedPerps allows bots/agents but prohibits HFT, latency arbitrage, platform exploitation,
  cross-account coordination, risk-limit circumvention, and unrealistic sizing. Re-check the terms
  before going live.
- Crypto costs: 0.03% per fill + 0.03%/day swap. At $45 risk with tight stops, fees are ~10-15% of R.
  Wider stops / smaller notional cut that drag.
- Ctrl-C leaves open positions protected by their attached TP/SL.
