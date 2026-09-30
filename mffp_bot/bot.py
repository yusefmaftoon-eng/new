"""MyFundedPerps $10K Select eval bot. Run: python bot.py  (DRY_RUN=1 by default)."""
import logging
import sys
import time

from client import APIError, FPClient
from config import Config
from market_data import fetch_candles
from risk import Guardian, position_size
from strategy import bracket, generate

log = logging.getLogger("bot")


def closed_bars(cfg):
    span = cfg.interval_min * 60_000
    bars = fetch_candles(cfg.provider, cfg.coin, f"{cfg.interval_min}m", limit=400)
    now_ms = time.time() * 1000
    return [b for b in bars if b["t"] + span <= now_ms]


def last_closed_open(cfg):
    span = cfg.interval_min * 60_000
    return int(time.time() * 1000) // span * span - span


def execute(client, cfg, guardian, sig, budget, base):
    side = sig.side
    probe = position_size(budget, sig.ref_price, sig.stop, cfg, base)
    if probe <= 0:
        log.info("signal %s skipped: size rounds to 0", side)
        return
    q = client.quote(cfg.market_id, side, probe)
    fill = float(q.get("estimated_fill_price") or q.get("mid"))
    if (side == "buy" and fill <= sig.stop) or (side == "sell" and fill >= sig.stop):
        log.info("signal %s skipped: price already through stop", side)
        return
    size = position_size(budget, fill, sig.stop, cfg, base)
    if size <= 0:
        return
    tp = round(bracket(side, fill, sig.stop, cfg.reward_risk), cfg.price_decimals)
    sl = round(sig.stop, cfg.price_decimals)
    risk_est = size * (abs(fill - sl) + fill * 2 * cfg.fee_rate)
    log.info("SIGNAL %s %s size=%s ~fill=%.1f SL=%.1f TP=%.1f risk~$%.0f",
             side.upper(), cfg.coin, size, fill, sl, tp, risk_est)
    if cfg.dry_run:
        log.info("[DRY RUN] order not sent")
        guardian.record_entry()
        return

    order = client.place_market(account_id=cfg.account_id, market_id=cfg.market_id, side=side, size=size,
                                expected_price=float(q.get("mid") or fill), leverage=cfg.leverage,
                                margin_mode=cfg.margin_mode, take_profit=tp, stop_loss=sl,
                                client_order_id=f"bot-{sig.bar_time}-{side}")
    guardian.record_entry()
    final = client.wait_order(order["id"])
    log.info("order %s -> %s %s", order["id"], final.get("status"), final.get("reject_reason") or "")
    if final.get("status") != "filled":
        return
    time.sleep(1)
    if not client.working_orders(cfg.account_id):      # never sit in a position without a stop
        log.error("No protective TP/SL found after fill - flattening")
        client.close_all(cfg.account_id)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")])
    cfg = Config()
    if not cfg.api_key or not cfg.account_id:
        sys.exit("Set FP_API_KEY and FP_ACCOUNT_ID (run `python bot.py --accounts` to list IDs).")
    client = FPClient(cfg.api_key, cfg.base_url)

    if "--accounts" in sys.argv:
        for a in client.accounts():
            print(a.get("id"), a.get("name"), a.get("stage"), a.get("status"), a.get("starting_balance"))
        return

    acct = client.account(cfg.account_id)
    base = float(acct["starting_balance"])
    guardian = Guardian(cfg, base)
    log.info("Start | %s | %s | base %.0f | floor %.0f | target %.0f | DRY_RUN=%s",
             cfg.base_url.split("//")[1], acct.get("name"), base, guardian.mll_floor,
             guardian.target_equity, cfg.dry_run)

    last_bar, cooldown_until, last_reason, had_pos = None, 0, None, False
    span = cfg.interval_min * 60_000
    while True:
        try:
            acct = client.account(cfg.account_id)
            has_pos = bool(client.positions(cfg.account_id))
            if had_pos and not has_pos:
                cooldown_until = time.time() * 1000 + cfg.cooldown_bars * span
                log.info("position closed | balance %.2f", float(acct.get("balance", 0)))
            had_pos = has_pos

            d = guardian.evaluate(acct, has_pos)
            if d.reason != last_reason:
                log.info(d.reason)
                last_reason = d.reason
            if d.flatten:
                log.warning("FLATTENING: %s", d.reason)
                if not cfg.dry_run:
                    client.close_all(cfg.account_id)
                time.sleep(3)
                continue
            if d.stop_bot:
                log.warning("Bot stopping: %s", d.reason)
                break

            if d.can_enter and time.time() * 1000 >= cooldown_until:
                expected = last_closed_open(cfg)
                if expected != last_bar:
                    bars = closed_bars(cfg)
                    if bars and bars[-1]["t"] >= expected:       # feed has caught up
                        last_bar = bars[-1]["t"]
                        sig = generate(bars, cfg)
                        if sig:
                            execute(client, cfg, guardian, sig, d.risk_budget, base)
            time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            log.info("Stopped by user. Open positions keep their attached TP/SL on the server.")
            break
        except APIError as e:
            log.error("API error: %s", e)
            time.sleep(15)
        except Exception:
            log.exception("Unexpected error")
            time.sleep(15)


if __name__ == "__main__":
    main()
