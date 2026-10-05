"""Score Jev's logged calls against real outcomes.

    python -m scripts.evaluate [trades.csv] [outcomes.csv]

Uses Polymarket's resolution when known, otherwise the Binance candle.
Prints Jev's hit rate overall and by confidence, how often Jev beat the market
price, and simulated profit for the windows where the bot would have bought.
"""
import csv
import glob
import os
import sys


def load(trades_path="trades.csv", outcomes_path="outcomes.csv"):
    outcomes = {}
    if os.path.exists(outcomes_path):
        for o in csv.DictReader(open(outcomes_path)):
            actual = o["poly_outcome"] if o["poly_outcome"] in ("UP", "DOWN") else o["binance_outcome"]
            if actual in ("UP", "DOWN"):
                outcomes[o["window_start"]] = (actual, o["poly_outcome"] in ("UP", "DOWN"))
    rows, seen = [], set()
    # Include archived logs (trades.csv.<time>.old) so column changes do not lose history.
    for path in [trades_path] + sorted(glob.glob(f"{trades_path}.*.old"), reverse=True):
        for t in csv.DictReader(open(path)):
            if t["window_start"] in seen:
                continue
            seen.add(t["window_start"])
            if t.get("decision") not in ("UP", "DOWN") or t["window_start"] not in outcomes:
                continue
            actual, from_poly = outcomes[t["window_start"]]
            rows.append({**t, "actual": actual, "from_poly": from_poly, "hit": t["decision"] == actual})
    return rows


def pct(hits, n):
    return f"{hits:4}/{n:<4} = {hits / n:6.1%}" if n else "   -"


BUCKETS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01))


def print_confidence_bets(rows, sets) -> None:
    """Simulate 1 USDC on Jev's side at the recorded ask, per candle set and 10% confidence band."""
    print("\nBet 1 USDC on Jev's side at the ask, by confidence band (before fees)")
    print("  set   band       bets   hits            avg ask   P&L      per bet   95% range per bet")
    for k in sets:
        for lo, hi in BUCKETS:
            bets = []
            for r in rows:
                p = r.get(f"p_up_{k}")
                if not p or not r.get("up_ask") or not r.get("down_ask"):
                    continue
                p = float(p)
                side = "UP" if p >= 0.5 else "DOWN"
                conf = max(p, 1 - p)
                if not lo <= conf < hi:
                    continue
                ask = float(r["up_ask"] if side == "UP" else r["down_ask"])
                if not 0 < ask < 1:
                    continue
                won = side == r["actual"]
                bets.append((ask, won, (1 / ask - 1) if won else -1.0))
            band = f"{lo:.0%}-{min(hi, 1):.0%}"
            if not bets:
                print(f"  {k:4}  {band:9}     0")
                continue
            n = len(bets)
            pnl = sum(b[2] for b in bets)
            mean = pnl / n
            sd = (sum((b[2] - mean) ** 2 for b in bets) / max(n - 1, 1)) ** 0.5
            half = 1.96 * sd / n ** 0.5
            print(f"  {k:4}  {band:9} {n:5}   {pct(sum(b[1] for b in bets), n)}   {sum(b[0] for b in bets) / n:.3f}"
                  f"   {pnl:+7.2f}  {mean:+6.1%}   {mean - half:+.1%} to {mean + half:+.1%}")


def main(trades_path="trades.csv", outcomes_path="outcomes.csv") -> None:
    rows = load(trades_path, outcomes_path)
    if not rows:
        sys.exit("No windows with both a Jev decision and a known outcome yet.")
    n = len(rows)
    print(f"Windows scored: {n} ({sum(r['from_poly'] for r in rows)} with Polymarket resolution, "
          f"rest from Binance)")
    print(f"Jev hit rate, all windows:        {pct(sum(r['hit'] for r in rows), n)}")

    sets = sorted({int(k[5:]) for r in rows for k in r if k.startswith("p_up_") and k[5:].isdigit()})
    if sets:
        print("\nBy candle set   hit rate               conf>=0.55 hits        avg conf  Brier (0.25 = coin flip)")
        for k in sets:
            sr = [r for r in rows if r.get(f"p_up_{k}")]
            if not sr:
                continue
            p = [float(r[f"p_up_{k}"]) for r in sr]
            hit = [(pi >= 0.5) == (r["actual"] == "UP") for pi, r in zip(p, sr)]
            conf = [max(pi, 1 - pi) for pi in p]
            strong = [h for h, c in zip(hit, conf) if c >= 0.55]
            brier = sum((pi - (r["actual"] == "UP")) ** 2 for pi, r in zip(p, sr)) / len(sr)
            print(f"  {k:3} candles   {pct(sum(hit), len(sr))}   {pct(sum(strong), len(strong))}"
                  f"   {sum(conf) / len(conf):.3f}    {brier:.4f}")

    print_confidence_bets(rows, sets)

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
