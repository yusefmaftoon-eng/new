"""Live/paper bot for Kalshi's 15-minute BTC up/down markets.

In the last 2:30 of each market: if YES's ask is >= 80c, buy YES; else if NO's
ask is >= 80c, buy NO. One entry per market, sent as an immediate-or-cancel
limit order so it can never pay more than ask + slippage (capped at --max-price).

PAPER MODE IS THE DEFAULT. Nothing is sent to Kalshi unless you pass --live.

    # paper trade (no keys needed)
    python -m trading_bot.run_kalshi_btc15m_bot

    # real money: 2% of balance per trade, max $20, stop for the day after -10%
    export KALSHI_API_KEY_ID=...            # from kalshi.com -> Account & security -> API Keys
    export KALSHI_PRIVATE_KEY_PATH=~/kalshi.pem
    python -m trading_bot.run_kalshi_btc15m_bot --live --stake-pct 0.02 --max-stake 20 \
        --max-daily-loss-pct 0.10 --stop-below 50

Risk controls (all optional, combine freely):
  --stake-pct F         risk F of your current balance per trade (default 0.02 = 2%)
  --stake D             or a fixed D dollars per trade instead
  --max-stake D         never more than D dollars on one trade
  --max-daily-loss D    stop trading for the day after losing D dollars
  --max-daily-loss-pct F  ... or after losing F of the day's starting balance
  --stop-below D        stop entirely if the balance falls below D
  --max-trades N        stop after N trades
"""
from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import datetime, timezone

from trading_bot.kalshi.client import DEMO_BASE_URL, PROD_BASE_URL, KalshiClient
from trading_bot.kalshi.strategy import (StrategyConfig, contracts_for, decide, order_params,
                                         stake_for, taker_fee)


def _now() -> float:
    return time.time()


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def log(msg: str):
    print(f"{datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)


class Bot:
    def __init__(self, client: KalshiClient, cfg: StrategyConfig, series: str, live: bool,
                 max_trades: int | None, max_daily_loss: float | None, log_path: str,
                 max_daily_loss_pct: float | None = None, stop_below: float | None = None,
                 paper_bankroll: float = 100.0):
        self.client, self.cfg, self.series, self.live = client, cfg, series, live
        self.max_trades, self.max_daily_loss = max_trades, max_daily_loss
        self.max_daily_loss_pct, self.stop_below = max_daily_loss_pct, stop_below
        self.paper_balance = paper_bankroll
        self.day_start_balance: float | None = None
        self.log_path = log_path
        self.traded: set[str] = set()      # tickers we've already entered
        self.open_positions: list[dict] = []
        self.realized_today = 0.0
        self.day = datetime.now(timezone.utc).date()
        self.n_trades = 0

    # -- main loop -------------------------------------------------------------

    def run(self):
        mode = "LIVE" if self.live else "PAPER"
        log(f"Starting in {mode} mode on {self.series}: threshold={self.cfg.threshold:.2f} "
            f"max_price={self.cfg.max_price:.2f} window={self.cfg.window_seconds:.0f}s "
            + (f"stake={self.cfg.stake_pct:.1%} of balance" if self.cfg.stake_pct is not None
               else f"stake=${self.cfg.stake_dollars:.2f}"))
        bal = self.balance()
        self.day_start_balance = bal
        log(f"{'Account' if self.live else 'Paper'} balance: ${bal:.2f}  "
            f"(next stake ${stake_for(bal, self.cfg):.2f})")
        while True:
            try:
                sleep_for = self.tick()
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001 - never let one bad request kill the bot
                log(f"error: {e}")
                sleep_for = 5
            if self.max_trades is not None and self.n_trades >= self.max_trades and not self.open_positions:
                log("Reached --max-trades and all positions settled; stopping.")
                return
            if self.stop_below is not None and not self.open_positions and self.balance() < self.stop_below:
                log(f"Balance below --stop-below ${self.stop_below:.2f}; stopping.")
                return
            time.sleep(sleep_for)

    def tick(self) -> float:
        self._roll_day()
        self._settle_positions()

        markets = self.client.get_markets(series_ticker=self.series, status="open").get("markets", [])
        upcoming = [m for m in markets if _ts(m["close_time"]) > _now()]
        if not upcoming:
            return 15
        m = min(upcoming, key=lambda m: _ts(m["close_time"]))
        secs_left = _ts(m["close_time"]) - _now()

        if secs_left > self.cfg.window_seconds:
            # sleep until just before the window opens (but re-check at least every 30s)
            return max(1.0, min(30.0, secs_left - self.cfg.window_seconds - 1))
        if m["ticker"] in self.traded or not self._can_trade():
            return 2

        yes_bid, yes_ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
        sig = decide(yes_bid, yes_ask, secs_left, self.cfg)
        if sig is None:
            return 1  # inside the window: poll quickly
        self._enter(m, sig, secs_left)
        return 1

    # -- trading -----------------------------------------------------------------

    def balance(self) -> float:
        return self.client.get_balance_dollars() if self.live else self.paper_balance

    def _enter(self, m: dict, sig, secs_left: float):
        stake = stake_for(self.balance(), self.cfg)
        n = contracts_for(stake, sig.limit)
        if n < 1:
            log(f"{m['ticker']}: stake ${stake:.2f} too small for one contract at {sig.limit}")
            self.traded.add(m["ticker"])
            return
        book_side, yes_price = order_params(sig, m.get("price_ranges"))
        log(f"{m['ticker']}: {secs_left:5.1f}s left, {sig.side.upper()} ask={sig.ask:.3f} -> "
            f"buy {n} {sig.side.upper()} (limit {sig.limit:.3f}; V2 side={book_side} price={yes_price})")
        self.traded.add(m["ticker"])

        if self.live:
            resp = self.client.create_order(m["ticker"], book_side, str(n), yes_price)
            filled = float(resp.get("fill_count", 0))
            if filled <= 0:
                log(f"  no fill (IOC cancelled): {resp}")
                return
            avg_yes = _f(resp.get("average_fill_price"))
            price = (avg_yes if sig.side == "yes" else 1 - avg_yes) if avg_yes is not None else sig.ask
            fee = (_f(resp.get("average_fee_paid")) or 0.0) * filled
            log(f"  filled {filled:g} @ {price:.3f}, fees ${fee:.2f}")
        else:
            filled, price = float(n), sig.ask
            fee = taker_fee(filled, price)
            self.paper_balance -= filled * price + fee

        self.n_trades += 1
        pos = {"ticker": m["ticker"], "side": sig.side, "contracts": filled, "price": price,
               "fee": fee, "close_ts": _ts(m["close_time"]), "secs_left": round(secs_left, 1)}
        self.open_positions.append(pos)

    def _settle_positions(self):
        still_open = []
        for pos in self.open_positions:
            if _now() < pos["close_ts"] + 20:
                still_open.append(pos)
                continue
            result = self.client.get_market(pos["ticker"]).get("result")
            if result not in ("yes", "no"):
                still_open.append(pos)
                continue
            cost = pos["contracts"] * pos["price"] + pos["fee"]
            payout = pos["contracts"] if pos["side"] == result else 0.0
            pnl = payout - cost
            self.realized_today += pnl
            if not self.live:
                self.paper_balance += payout
            log(f"SETTLED {pos['ticker']}: result={result.upper()} we had {pos['side'].upper()} "
                f"-> P&L ${pnl:+.2f}  (today ${self.realized_today:+.2f}, balance ${self.balance():.2f})")
            self._write_log({**pos, "result": result, "pnl": round(pnl, 4),
                             "mode": "live" if self.live else "paper"})
        self.open_positions = still_open

    def _can_trade(self) -> bool:
        if self.max_trades is not None and self.n_trades >= self.max_trades:
            return False
        if self.max_daily_loss is not None and self.realized_today <= -self.max_daily_loss:
            return False
        if (self.max_daily_loss_pct is not None and self.day_start_balance
                and self.realized_today <= -self.max_daily_loss_pct * self.day_start_balance):
            return False
        return True

    def _roll_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            self.day, self.realized_today = today, 0.0
            self.day_start_balance = self.balance()

    def _write_log(self, row: dict):
        new = not os.path.exists(self.log_path)
        with open(self.log_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--live", action="store_true", help="send real orders (default: paper trade)")
    p.add_argument("--demo", action="store_true", help="use Kalshi's demo exchange (fake money)")
    p.add_argument("--series", default="KXBTC15M")
    p.add_argument("--threshold", type=float, default=0.80)
    p.add_argument("--max-price", type=float, default=0.99)
    p.add_argument("--window", type=float, default=150, help="seconds before close to start looking")
    p.add_argument("--stake-pct", type=float, default=0.02, help="fraction of balance per trade")
    p.add_argument("--stake", type=float, default=None, help="fixed dollars per trade (overrides --stake-pct)")
    p.add_argument("--max-stake", type=float, default=None, help="cap on dollars per trade")
    p.add_argument("--slippage", type=float, default=0.01)
    p.add_argument("--max-trades", type=int, default=None)
    p.add_argument("--max-daily-loss", type=float, default=None, help="stop for the day after losing this much")
    p.add_argument("--max-daily-loss-pct", type=float, default=None,
                   help="stop for the day after losing this fraction of the day's starting balance")
    p.add_argument("--stop-below", type=float, default=None, help="quit if balance falls below this")
    p.add_argument("--paper-bankroll", type=float, default=100.0, help="starting balance in paper mode")
    p.add_argument("--log", default="kalshi_btc15m_trades.csv")
    args = p.parse_args()

    key_id = os.environ.get("KALSHI_API_KEY_ID")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if args.live and not (key_id and key_path):
        p.error("--live needs KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH set")

    client = KalshiClient(DEMO_BASE_URL if args.demo else PROD_BASE_URL,
                          api_key_id=key_id if args.live else None,
                          private_key_path=os.path.expanduser(key_path) if (args.live and key_path) else None)
    cfg = StrategyConfig(threshold=args.threshold, max_price=args.max_price,
                         window_seconds=args.window, slippage=args.slippage,
                         stake_dollars=args.stake or 0.0,
                         stake_pct=None if args.stake is not None else args.stake_pct,
                         max_stake=args.max_stake)
    bot = Bot(client, cfg, args.series, args.live, args.max_trades, args.max_daily_loss, args.log,
              max_daily_loss_pct=args.max_daily_loss_pct, stop_below=args.stop_below,
              paper_bankroll=args.paper_bankroll)
    try:
        bot.run()
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    main()
