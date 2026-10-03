"""Every 5 minutes: send the last 20 candles to Jev and bet on its answer."""
import csv
import datetime as dt
import logging
import os
import time

from .candles import INTERVAL, fetch_candles
from .config import Config
from .jev import Jev
from .polymarket import Trader, find_market

log = logging.getLogger("bot")
LOG_FIELDS = ["window_start", "slug", "decision", "raw_answer", "price", "usdc", "status", "detail"]


def next_window_start(now: float) -> int:
    return (int(now) // INTERVAL + 1) * INTERVAL


def log_trade(path: str, row: dict) -> None:
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in LOG_FIELDS})


class Bot:
    def __init__(self, cfg: Config, jev: Jev, trader: Trader | None):
        self.cfg = cfg
        self.jev = jev
        self.trader = trader
        self.trades_today = 0
        self.day = dt.datetime.now(dt.timezone.utc).date()

    def run_window(self, start: int) -> dict:
        cfg = self.cfg
        row = {"window_start": start, "slug": cfg.slug_template.format(start=start)}

        today = dt.datetime.now(dt.timezone.utc).date()
        if today != self.day:
            self.day, self.trades_today = today, 0
        if self.trades_today >= cfg.max_trades_per_day:
            return {**row, "status": "skipped", "detail": "daily trade limit reached"}

        market = find_market(cfg.gamma_host, row["slug"])
        if market is None or not market.accepting_orders:
            return {**row, "status": "skipped", "detail": "market not found or not accepting orders"}

        candles = fetch_candles(start, cfg.candle_count)
        decision, raw = self.jev.ask(candles)
        row.update(decision=decision or "", raw_answer=raw[:200])
        if decision is None:
            return {**row, "status": "skipped", "detail": "Jev gave no clear UP/DOWN"}

        late = time.time() - start
        if late > cfg.decision_deadline_sec:
            return {**row, "status": "skipped", "detail": f"decision too late ({late:.0f}s into window)"}

        token = market.tokens[decision]
        if cfg.dry_run:
            return {**row, "usdc": cfg.bet_usdc, "status": "dry_run"}

        price = self.trader.quote(token, cfg.bet_usdc)
        row.update(price=price, usdc=cfg.bet_usdc)
        if price > cfg.max_price:
            return {**row, "status": "skipped", "detail": f"price {price} > MAX_PRICE {cfg.max_price}"}

        resp = self.trader.buy(token, cfg.bet_usdc, price)
        ok = bool(resp.get("success")) if isinstance(resp, dict) else False
        if ok:
            self.trades_today += 1
        return {**row, "status": "filled" if ok else "rejected", "detail": str(resp)[:300]}

    def loop(self) -> None:
        mode = "DRY RUN" if self.cfg.dry_run else "LIVE"
        log.info("Bot started in %s mode: %s USDC per window, max price %s",
                 mode, self.cfg.bet_usdc, self.cfg.max_price)
        while True:
            start = next_window_start(time.time())
            time.sleep(max(0.0, start + self.cfg.entry_delay_sec - time.time()))
            try:
                row = self.run_window(start)
            except Exception as exc:  # noqa: BLE001 - one bad window must not stop the bot
                log.exception("Window %s failed", start)
                row = {"window_start": start, "status": "error", "detail": str(exc)[:300]}
            log.info("%s", row)
            log_trade(self.cfg.trade_log, row)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    jev = Jev(cfg.jev_api_key, cfg.jev_model, cfg.jev_system_prompt,
              cfg.jev_base_url, cfg.jev_timeout_sec)
    trader = None if cfg.dry_run else Trader(
        cfg.clob_host, cfg.chain_id, cfg.poly_private_key,
        cfg.poly_signature_type, cfg.poly_funder)
    Bot(cfg, jev, trader).loop()


if __name__ == "__main__":
    main()
