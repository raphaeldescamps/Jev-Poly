"""Score Jev's logged calls against what BTC actually did.

    python -m scripts.evaluate [trades.csv]

For every logged window with a decision, fetch that window's 5m candle and
check whether the close was above the open. Reports Jev's hit rate overall and
by confidence bucket, plus simulated profit for rows that had a quoted price.
Note: Polymarket settles on Chainlink BTC/USD, so a few close calls may differ.
"""
import csv
import sys

from bot.candles import INTERVAL, fetch_candles


def main(path: str = "trades.csv") -> None:
    rows = [r for r in csv.DictReader(open(path)) if r.get("decision") in ("UP", "DOWN")]
    if not rows:
        sys.exit("No rows with a Jev decision yet.")
    results = []
    for r in rows:
        start = int(r["window_start"])
        try:
            c = fetch_candles(start + INTERVAL, 1)[0]  # the candle that started at `start`
        except Exception as exc:  # noqa: BLE001 - window not finished yet or API error
            print(f"skip {start}: {exc}")
            continue
        actual = "UP" if c.close > c.open else "DOWN"
        results.append((r, actual == r["decision"]))

    def report(label, items):
        if items:
            hits = sum(ok for _, ok in items)
            print(f"{label:28} {hits:4}/{len(items):<4} = {hits / len(items):.1%}")

    print(f"Windows scored: {len(results)}")
    report("All calls", results)
    for lo, hi in ((0.5, 0.55), (0.55, 0.6), (0.6, 0.7), (0.7, 1.01)):
        report(f"Confidence {lo:.2f}-{min(hi, 1):.2f}",
               [(r, ok) for r, ok in results if lo <= float(r["confidence"] or 0) < hi])

    priced = [(r, ok) for r, ok in results if r.get("price") and r.get("status") in ("dry_run", "filled")
              and r.get("detail") != "no price check (no trader)"]
    if priced:
        pnl = sum(float(r["usdc"]) * ((1 / float(r["price"]) - 1) if ok else -1) for r, ok in priced)
        stake = sum(float(r["usdc"]) for r, _ in priced)
        print(f"Bets that passed all checks: {len(priced)}, simulated P&L {pnl:+.2f} USDC "
              f"on {stake:.2f} staked ({pnl / stake:+.1%}), before fees")


if __name__ == "__main__":
    main(*sys.argv[1:])
