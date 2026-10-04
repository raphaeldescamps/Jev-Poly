"""Record what actually happened in each window: Polymarket's resolution and the Binance candle."""
import csv
import logging
import os
import time

from .candles import INTERVAL, fetch_candles
from .polymarket import fetch_resolution

log = logging.getLogger(__name__)
FIELDS = ["window_start", "slug", "poly_outcome", "binance_open", "binance_close", "binance_outcome", "recorded_at"]
GIVE_UP_AFTER = 3 * 3600  # stop waiting for Polymarket's resolution after 3 hours


class OutcomeTracker:
    def __init__(self, path: str, gamma_host: str):
        self.path = path
        self.gamma_host = gamma_host
        self.done = set()
        if os.path.exists(path):
            self.done = {int(r["window_start"]) for r in csv.DictReader(open(path))}
        self.pending: dict[int, str] = {}  # window_start -> slug
        self.warned: set[int] = set()

    def add(self, start: int, slug: str) -> None:
        if start not in self.done:
            self.pending[start] = slug

    def load_pending(self, trade_log: str) -> None:
        """After a restart, queue every logged window that has no outcome yet."""
        if os.path.exists(trade_log):
            for r in csv.DictReader(open(trade_log)):
                if r.get("slug"):
                    self.add(int(r["window_start"]), r["slug"])

    def update(self, now: float | None = None, deadline: float | None = None) -> None:
        now = time.time() if now is None else now
        for start, slug in sorted(self.pending.items()):
            if deadline is not None and time.time() > deadline:
                return  # finish the rest after the next window
            end = start + INTERVAL
            if now < end + 10:
                continue
            try:
                poly = fetch_resolution(self.gamma_host, slug)
            except Exception as exc:  # noqa: BLE001
                log.warning("Resolution lookup failed for %s: %s", slug, exc)
                poly = None
            if poly is None and now < end + GIVE_UP_AFTER:
                if now > end + 900 and start not in self.warned:
                    log.warning("No Polymarket resolution yet for %s, 15+ minutes after it ended", slug)
                    self.warned.add(start)
                continue
            try:
                c = fetch_candles(end, 1)[0]
                b_open, b_close = c.open, c.close
                b_out = "UP" if c.close > c.open else "DOWN"
            except Exception as exc:  # noqa: BLE001
                log.warning("Candle lookup failed for %s: %s", start, exc)
                b_open = b_close = b_out = ""
            self._write({"window_start": start, "slug": slug, "poly_outcome": poly or "unresolved",
                         "binance_open": b_open, "binance_close": b_close, "binance_outcome": b_out,
                         "recorded_at": int(now)})
            del self.pending[start]
            self.done.add(start)

    def _write(self, row: dict) -> None:
        new = not os.path.exists(self.path)
        with open(self.path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
        log.info("Outcome %s", row)
