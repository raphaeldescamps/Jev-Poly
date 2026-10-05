"""Backtest Jev on past 5-minute windows, with no look-ahead.

    python -m scripts.backtest --days 7 [--sets 20,50,100] [--workers 4] [--yes]

For each past window starting at T:
  * Jev sees only candles that CLOSED at or before T (start + 300 <= T). This is
    asserted for every window, so a later candle can never leak in.
  * The price paid is the last Polymarket price recorded at or before T on the
    UP token (from /prices-history), plus HALF_SPREAD as an estimate of the ask.
    DOWN costs 1 - UP price + HALF_SPREAD.
  * The outcome is Polymarket's own resolution of that window's market.

Rows go to backtest_trades.csv / backtest_outcomes.csv in the same format as the
live logs, so `python -m scripts.evaluate backtest_trades.csv backtest_outcomes.csv`
produces the same report. Re-running skips windows already done (no double cost).
"""
import argparse
import csv
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from bot.candles import INTERVAL, Candle
from bot.config import Config
from bot.jev import Jev
from bot.main import log_fields
from bot.outcomes import FIELDS as OUTCOME_FIELDS

log = logging.getLogger("backtest")
HALF_SPREAD = 0.005  # live logs show a 0.01 bid/ask spread at the open
TRADES, OUTCOMES = "backtest_trades.csv", "backtest_outcomes.csv"
TOKENS_PER_CANDLE = 110  # measured: ~2,270 input tokens for 20 candles


def fetch_history(start_ts: int, end_ts: int) -> list[Candle]:
    """All Binance 5m candles that start in [start_ts, end_ts), oldest first."""
    out, cursor = [], start_ts * 1000
    while cursor < end_ts * 1000:
        r = requests.get("https://api.binance.com/api/v3/klines", params={
            "symbol": "BTCUSDT", "interval": "5m", "startTime": cursor,
            "endTime": end_ts * 1000 - 1, "limit": 1000}, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out += [Candle(int(k[0]) // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
                for k in batch]
        cursor = int(batch[-1][0]) + INTERVAL * 1000
    return out


def candles_before(history: list[Candle], window_start: int, n: int) -> list[Candle]:
    """The last n candles that closed at or before window_start. Raises if any later data slips in."""
    closed = [c for c in history if c.start + INTERVAL <= window_start][-n:]
    if len(closed) != n or closed[-1].start != window_start - INTERVAL:
        raise ValueError(f"missing candles before {window_start}")
    assert all(c.start + INTERVAL <= window_start for c in closed), "look-ahead: candle closes after decision"
    return closed


def price_at(history: list[dict], t: int) -> float | None:
    """Last recorded price at or before t."""
    before = [h for h in history if int(h["t"]) <= t]
    return float(max(before, key=lambda h: int(h["t"]))["p"]) if before else None


def market_info(cfg: Config, window_start: int) -> dict | None:
    slug = cfg.slug_template.format(start=window_start)
    r = requests.get(f"{cfg.gamma_host}/markets", params={"slug": slug, "closed": "true"}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    m = data[0]
    outcomes = [o.strip().upper() for o in json.loads(m["outcomes"])]
    tokens = dict(zip(outcomes, json.loads(m["clobTokenIds"])))
    prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
    result = next((o for o, p in zip(outcomes, prices) if p >= 0.99), None)
    return {"slug": slug, "up_token": tokens.get("UP"), "result": result}


def up_price(cfg: Config, token: str, window_start: int) -> float | None:
    r = requests.get(f"{cfg.clob_host}/prices-history", params={
        "market": token, "startTs": window_start - 600, "endTs": window_start, "fidelity": 1}, timeout=15)
    r.raise_for_status()
    return price_at(r.json().get("history", []), window_start)


def run_window(cfg: Config, jev: Jev, history: list[Candle], sets: list[int], ws: int) -> tuple[dict, dict | None]:
    row = {"window_start": ws, "slug": cfg.slug_template.format(start=ws), "status": "backtest"}
    for n in sets:
        pred, _ = jev.ask(candles_before(history, ws, n))
        row[f"p_up_{n}"] = round(pred.p_up, 4)
        if n == cfg.decision_candles:
            row.update(decision=pred.side, decision_candles=n, confidence=round(pred.confidence, 4),
                       p_up=round(pred.p_up, 4))
    info = market_info(cfg, ws)
    if info and info["up_token"]:
        p = up_price(cfg, info["up_token"], ws)
        if p is not None:
            row.update(up_ask=round(min(p + HALF_SPREAD, 0.99), 4), down_ask=round(min(1 - p + HALF_SPREAD, 0.99), 4))
    outcome = None
    if info and info["result"]:
        outcome = {"window_start": ws, "slug": info["slug"], "poly_outcome": info["result"],
                   "recorded_at": int(time.time())}
    return row, outcome


def done_windows() -> set[int]:
    if not os.path.exists(TRADES):
        return set()
    return {int(r["window_start"]) for r in csv.DictReader(open(TRADES))}


def append(path: str, fields: list[str], row: dict) -> None:
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--sets", default=None, help="candle sets, default = CANDLE_SETS")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = Config.from_env()
    sets = [int(x) for x in args.sets.split(",")] if args.sets else cfg.candle_sets
    if cfg.decision_candles not in sets:
        cfg.decision_candles = max(sets)
    now = int(time.time()) // INTERVAL * INTERVAL
    last = now - 2 * INTERVAL  # leave time for the most recent markets to resolve
    first = last - int(args.days * 86400)
    windows = [ws for ws in range(first, last, INTERVAL) if ws not in done_windows()]

    est = len(windows) * sum(sets) * TOKENS_PER_CANDLE
    log.info("Backtest: %d windows to run, sets %s, decision on %s, about %.1fM Jev input tokens",
             len(windows), sets, cfg.decision_candles, est / 1e6)
    if not args.yes:
        if input("Continue? [y/N] ").strip().lower() != "y":
            sys.exit("Cancelled.")

    history = fetch_history(first - max(sets) * INTERVAL - INTERVAL, last + INTERVAL)
    log.info("Loaded %d Binance candles", len(history))
    jev = Jev(cfg.jev_api_key, cfg.jev_model, cfg.jev_base_url, cfg.jev_timeout_sec)
    fields = log_fields(sets)
    if os.path.exists(TRADES):  # keep the existing columns so earlier rows stay aligned
        with open(TRADES) as f:
            existing = f.readline().strip().split(",")
        fields = existing + [k for k in fields if k not in existing]
        if fields != existing:
            sys.exit(f"{TRADES} lacks columns {[k for k in fields if k not in existing]}; move it aside first.")
    done = errors = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(run_window, cfg, jev, history, sets, ws): ws for ws in windows}
        for job in as_completed(jobs):
            try:
                row, outcome = job.result()
                append(TRADES, fields, row)
                if outcome:
                    append(OUTCOMES, OUTCOME_FIELDS, outcome)
                done += 1
            except Exception as exc:  # noqa: BLE001 - one bad window must not stop the run
                errors += 1
                log.warning("Window %s failed: %s", jobs[job], exc)
            if (done + errors) % 100 == 0:
                log.info("Progress: %d/%d done, %d errors", done, len(windows), errors)
    log.info("Backtest finished: %d windows, %d errors", done, errors)


if __name__ == "__main__":
    main()
