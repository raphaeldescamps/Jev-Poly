"""Score Jev's logged calls against real outcomes.

    python -m scripts.evaluate [trades.csv] [outcomes.csv]

Uses Polymarket's resolution when known, otherwise the Binance candle.
Prints Jev's hit rate overall and by confidence, how often Jev beat the market
price, and simulated profit for the windows where the bot would have bought.
"""
import csv
import os
import sys


def load(trades_path="trades.csv", outcomes_path="outcomes.csv"):
    outcomes = {}
    if os.path.exists(outcomes_path):
        for o in csv.DictReader(open(outcomes_path)):
            actual = o["poly_outcome"] if o["poly_outcome"] in ("UP", "DOWN") else o["binance_outcome"]
            if actual in ("UP", "DOWN"):
                outcomes[o["window_start"]] = (actual, o["poly_outcome"] in ("UP", "DOWN"))
    rows = []
    for t in csv.DictReader(open(trades_path)):
        if t.get("decision") in ("UP", "DOWN") and t["window_start"] in outcomes:
            actual, from_poly = outcomes[t["window_start"]]
            rows.append({**t, "actual": actual, "from_poly": from_poly, "hit": t["decision"] == actual})
    return rows


def pct(hits, n):
    return f"{hits:4}/{n:<4} = {hits / n:6.1%}" if n else "   -"


def main(trades_path="trades.csv", outcomes_path="outcomes.csv") -> None:
    rows = load(trades_path, outcomes_path)
    if not rows:
        sys.exit("No windows with both a Jev decision and a known outcome yet.")
    n = len(rows)
    print(f"Windows scored: {n} ({sum(r['from_poly'] for r in rows)} with Polymarket resolution, "
          f"rest from Binance)")
    print(f"Jev hit rate, all windows:        {pct(sum(r['hit'] for r in rows), n)}")

    print("\nBy Jev confidence           hits           avg confidence")
    for lo, hi in ((0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 1.01)):
        b = [r for r in rows if lo <= float(r["confidence"]) < hi]
        avg = sum(float(r["confidence"]) for r in b) / len(b) if b else 0
        print(f"  {lo:.2f}-{min(hi, 1):.2f}               {pct(sum(r['hit'] for r in b), len(b))}"
              f"   {avg:.3f}" if b else f"  {lo:.2f}-{min(hi, 1):.2f}                  -")

    priced = [r for r in rows if r.get("up_ask") and r.get("down_ask")]
    if priced:
        def ask(r):
            return float(r["up_ask"] if r["decision"] == "UP" else r["down_ask"])
        beat = sum(float(r["confidence"]) > ask(r) for r in priced)
        avg_ask = sum(ask(r) for r in priced) / len(priced)
        pnl_all = sum((1 / ask(r) - 1) if r["hit"] else -1 for r in priced)
        print(f"\nOdds recorded in {len(priced)} windows. Average ask on Jev's side: {avg_ask:.3f}")
        print(f"Jev confidence above that ask:    {pct(beat, len(priced))}")
        print(f"If you bet 1 USDC on Jev's side every window at the ask: {pnl_all:+.2f} USDC "
              f"({pnl_all / len(priced):+.1%} per bet), before fees")

    bets = [r for r in rows if r["status"] in ("dry_run", "filled") and r.get("price")]
    if bets:
        pnl = sum(float(r["usdc"]) * ((1 / float(r["price"]) - 1) if r["hit"] else -1) for r in bets)
        stake = sum(float(r["usdc"]) for r in bets)
        print(f"\nWindows that passed all bot rules: {len(bets)}, "
              f"hit rate {pct(sum(r['hit'] for r in bets), len(bets))}")
        print(f"Simulated P&L: {pnl:+.2f} USDC on {stake:.2f} staked ({pnl / stake:+.1%}), before fees")
    else:
        print("\nNo window passed all bot rules yet.")


if __name__ == "__main__":
    main(*sys.argv[1:])
