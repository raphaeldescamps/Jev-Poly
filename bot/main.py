"""At second 1 of every 5-minute window: ask Jev, read both sides' odds, decide, log.

Each window is written to TRADE_LOG. The real result (Polymarket resolution and
Binance candle) is written to OUTCOME_LOG once the window has ended and resolved.
"""
import csv
import datetime as dt
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from .candles import INTERVAL, fetch_candles
from .config import Config
from .jev import Jev
from .outcomes import OutcomeTracker
from .polymarket import Market, Trader, fill_price, find_market, get_books

log = logging.getLogger("bot")
BASE_FIELDS = ["window_start", "slug", "decision", "decision_candles", "confidence", "p_up",
               "up_ask", "up_bid", "down_ask", "down_bid", "candles_at_sec", "odds_at_sec", "decided_at_sec",
               "price", "edge", "usdc", "status", "detail"]


def log_fields(candle_sets: list[int]) -> list[str]:
    """Base columns plus one P(UP) column per candle set, e.g. p_up_10, p_up_20, p_up_50."""
    return BASE_FIELDS + [f"p_up_{n}" for n in candle_sets]
PREFETCH_SEC = 20  # look up the next window's market this long before it opens


def next_window_start(now: float) -> int:
    return (int(now) // INTERVAL + 1) * INTERVAL


def log_trade(path: str, row: dict, fields: list[str]) -> None:
    if os.path.exists(path):
        with open(path) as f:
            header = f.readline().strip().split(",")
        if header != fields:  # columns changed: keep the old file, start a new one
            os.rename(path, f"{path}.{int(time.time())}.old")
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def _r(x):
    return "" if x is None else round(x, 4)


class Bot:
    def __init__(self, cfg: Config, jev: Jev, trader: Trader | None):
        self.cfg = cfg
        self.jev = jev
        self.trader = trader
        self.trades_today = 0
        self.day = dt.datetime.now(dt.timezone.utc).date()
        self.pool = ThreadPoolExecutor(max_workers=1 + len(cfg.candle_sets))

    def _read_odds(self, market: Market, start: int) -> tuple[dict, dict]:
        books = get_books(self.cfg.clob_host, [market.tokens["UP"], market.tokens["DOWN"]])
        at = time.time() - start
        up, down = books[market.tokens["UP"]], books[market.tokens["DOWN"]]
        odds = {"up_ask": _r(up.best_ask), "up_bid": _r(up.best_bid),
                "down_ask": _r(down.best_ask), "down_bid": _r(down.best_bid), "odds_at_sec": round(at, 2)}
        return odds, {"UP": up, "DOWN": down}

    def ask_jev(self, start: int) -> tuple[float, dict]:
        """Snapshot the candles and send every set to Jev in the background. Returns (snapshot time, jobs)."""
        candles = fetch_candles(start, max(self.cfg.candle_sets))
        at = round(time.time() - start, 2)
        return at, {n: self.pool.submit(self.jev.ask, candles[-n:]) for n in self.cfg.candle_sets}

    def run_window(self, start: int, market: Market | None = None, jev_jobs=None) -> dict:
        cfg = self.cfg
        row = {"window_start": start, "slug": cfg.slug_template.format(start=start)}

        today = dt.datetime.now(dt.timezone.utc).date()
        if today != self.day:
            self.day, self.trades_today = today, 0

        if market is None:
            market = find_market(cfg.gamma_host, row["slug"])
        if market is None or not market.accepting_orders:
            return {**row, "status": "skipped", "detail": "market not found or not accepting orders"}

        # Jev normally started before the window opened (see loop); otherwise ask now.
        odds_job = self.pool.submit(self._read_odds, market, start)
        candles_at, jobs = jev_jobs if jev_jobs else self.ask_jev(start)
        row["candles_at_sec"] = candles_at
        preds = {}
        for n, job in jobs.items():
            try:
                preds[n] = job.result(timeout=cfg.jev_timeout_sec + 5)[0]
                row[f"p_up_{n}"] = round(preds[n].p_up, 4)
            except Exception as exc:  # noqa: BLE001 - other sets are still useful
                log.warning("Jev failed for %s candles: %s", n, exc)
        try:
            odds, books = odds_job.result(timeout=10)
            row.update(odds)
        except Exception as exc:  # noqa: BLE001
            return {**row, "status": "error", "detail": f"order book: {exc}"[:300]}
        if cfg.decision_candles not in preds:
            return {**row, "status": "error", "detail": f"Jev failed for the {cfg.decision_candles}-candle set"}
        pred = preds[cfg.decision_candles]
        row.update(decision=pred.side, decision_candles=cfg.decision_candles,
                   confidence=round(pred.confidence, 4), p_up=round(pred.p_up, 4),
                   decided_at_sec=round(time.time() - start, 2))

        if self.trades_today >= cfg.max_trades_per_day:
            return {**row, "status": "skipped", "detail": "daily trade limit reached"}
        late = time.time() - start
        if late > cfg.decision_deadline_sec:
            return {**row, "status": "skipped", "detail": f"decision too late ({late:.0f}s into window)"}
        if pred.confidence < cfg.min_confidence:
            return {**row, "status": "skipped",
                    "detail": f"confidence {pred.confidence:.3f} < MIN_CONFIDENCE {cfg.min_confidence}"}

        fill = fill_price(books[pred.side], cfg.bet_usdc)
        if fill is None:
            return {**row, "status": "skipped", "detail": "not enough liquidity for the bet size"}
        price, worst = fill
        edge = pred.confidence - price
        row.update(price=round(price, 4), edge=round(edge, 4), usdc=cfg.bet_usdc)
        if price > cfg.max_price:
            return {**row, "status": "skipped", "detail": f"price {price:.3f} > MAX_PRICE {cfg.max_price}"}
        if edge < cfg.min_edge:
            return {**row, "status": "skipped", "detail": f"edge {edge:.3f} < MIN_EDGE {cfg.min_edge}"}
        if cfg.dry_run or self.trader is None:
            return {**row, "status": "dry_run", "detail": "would buy"}

        resp = self.trader.buy(market.tokens[pred.side], cfg.bet_usdc, worst)
        ok = bool(resp.get("success")) if isinstance(resp, dict) else False
        if ok:
            self.trades_today += 1
        return {**row, "status": "filled" if ok else "rejected", "detail": str(resp)[:300]}

    def loop(self) -> None:
        cfg = self.cfg
        mode = "DRY RUN" if cfg.dry_run else "LIVE"
        log.info("Bot started in %s mode: Jev asked at -%ss, entry at +%ss, candle sets %s (decides on %s), %s USDC per window, "
                 "min confidence %s, min edge %s, max price %s", mode, cfg.jev_lead_sec, cfg.entry_delay_sec, cfg.candle_sets,
                 cfg.decision_candles, cfg.bet_usdc, cfg.min_confidence, cfg.min_edge, cfg.max_price)
        fields = log_fields(cfg.candle_sets)
        tracker = OutcomeTracker(cfg.outcome_log, cfg.gamma_host)
        tracker.load_pending(cfg.trade_log)
        while True:
            start = next_window_start(time.time())
            slug = cfg.slug_template.format(start=start)

            # Use the quiet time before the window to record outcomes and find the next market.
            try:
                tracker.update(deadline=start - PREFETCH_SEC - 5)
            except Exception:  # noqa: BLE001
                log.exception("Outcome update failed")
            time.sleep(max(0.0, start - PREFETCH_SEC - time.time()))
            try:
                market = find_market(cfg.gamma_host, slug)
            except Exception as exc:  # noqa: BLE001
                log.warning("Prefetch of %s failed: %s", slug, exc)
                market = None

            # Ask Jev just before the window opens, so the answer is ready at +ENTRY_DELAY_SEC.
            time.sleep(max(0.0, start - cfg.jev_lead_sec - time.time()))
            try:
                jev_jobs = self.ask_jev(start)
            except Exception as exc:  # noqa: BLE001 - run_window retries after the open
                log.warning("Early Jev call for %s failed: %s", slug, exc)
                jev_jobs = None

            time.sleep(max(0.0, start + cfg.entry_delay_sec - time.time()))
            try:
                row = self.run_window(start, market, jev_jobs)
            except Exception as exc:  # noqa: BLE001 - one bad window must not stop the bot
                log.exception("Window %s failed", start)
                row = {"window_start": start, "slug": slug, "status": "error", "detail": str(exc)[:300]}
            log.info("%s", row)
            log_trade(cfg.trade_log, row, fields)
            tracker.add(start, slug)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    jev = Jev(cfg.jev_api_key, cfg.jev_model, cfg.jev_base_url, cfg.jev_timeout_sec)
    trader = None if cfg.dry_run else Trader(cfg.clob_host, cfg.chain_id, cfg.poly_private_key,
                                             cfg.poly_signature_type, cfg.poly_funder)
    Bot(cfg, jev, trader).loop()


if __name__ == "__main__":
    main()
