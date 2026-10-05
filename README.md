# Polymarket 5m BTC bot

Every 5 minutes, at the start of each Polymarket "Bitcoin Up or Down – 5 minute" window, the bot:

1. Fetches the last 10, 50 and 100 **closed** 5-minute BTC candles (`CANDLE_SETS`) (Binance BTCUSDT, Coinbase BTC-USD as fallback).
2. Sends each set to **Jev** in a separate, parallel request (TypeSafe System One API) as one yes/no question: "the next candle closes higher than it opens". Jev returns the probability of yes.
3. Uses the `DECISION_CANDLES` set (default 50) to pick the side Jev favours and buys it with a fill-or-kill order, only if Jev's probability beats the share price by `MIN_EDGE`.

Every window is logged to `trades.csv`: Jev's side, confidence and P(UP), the best bid/ask of both
outcomes (read at the same time as the Jev call, about 1 second into the window), timings, and the decision.
After each window resolves, `outcomes.csv` gets Polymarket's result and the Binance candle.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill it in
python -m bot.main
```

## Wallet (run these on the VPS, so the key never leaves it)

```bash
python -m scripts.wallet new       # creates a key in .env (mode 600) and prints the address
# send ~1 POL (gas) and USDC.e (bridged USDC, contract 0x2791...4174) on Polygon to that address
python -m scripts.wallet approve   # approves the Polymarket exchange contracts
python -m scripts.wallet status    # check balances and approvals
```

Back up `.env` offline. If the VPS is lost without a backup, the funds are lost.

## VPS deployment (Ubuntu, Dublin region)

```bash
sudo adduser --disabled-password bot && sudo -iu bot
git clone <repo> jev-poly && cd jev-poly
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env        # Jev settings, DRY_RUN=true for the first day
.venv/bin/python -m scripts.wallet new
exit
sudo cp /home/bot/jev-poly/deploy/polybot.service /etc/systemd/system/
sudo systemctl enable --now polybot
journalctl -u polybot -f
```

## Safety checks

The bot skips a window (no order) when:

- the market for that window is not found or is closed,
- Jev's probability for its side is below `MIN_CONFIDENCE`,
- Jev's probability minus the share price is below `MIN_EDGE`,
- the decision arrives more than `DECISION_DEADLINE_SEC` (default 60) after the window opened,
- the chosen side costs more than `MAX_PRICE` per share,
- `MAX_TRADES_PER_DAY` is reached.

`DRY_RUN=true` runs everything except the order.

## Check Jev and score its calls

```bash
python -m scripts.jev_check     # one live call to Jev, prints P(UP) and latency
python -m scripts.evaluate      # hit rate by confidence and simulated P&L from trades.csv
```

## Assumptions to check

- Market slug format is `btc-updown-5m-<window start unix time>`. Change `POLY_SLUG_TEMPLATE` if Polymarket uses another format.
- Polymarket settles on the Chainlink BTC/USD feed, not Binance. The candles Jev sees are close to, but not the same as, the settlement price.
- Binance blocks US IPs; the Coinbase fallback covers that.

## Tests

```bash
pip install pytest && pytest -q
```

## Remote control from GitHub (deploy, check, status)

The `Server` workflow (Actions tab → Server → Run workflow) logs in to the VPS as `bot` and runs one of:
`status`, `check` (one Jev call), `deploy` (tests, pull, restart), `evaluate`, `recent` (last CSV rows).

One-time setup on the server: `sudo bash /home/bot/jev-poly/deploy/setup_deploy_access.sh`, then add the
repository secrets `DEPLOY_SSH_KEY` and `DEPLOY_HOST` that the script prints. The `bot` user can only
start, restart and read the logs of the `polybot` service. Secrets in `.env` never leave the server.
Note: on a public repo, workflow logs are public.
