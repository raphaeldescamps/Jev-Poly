# Polymarket 5m BTC bot

Every 5 minutes, at the start of each Polymarket "Bitcoin Up or Down – 5 minute" window, the bot:

1. Fetches the last 20 **closed** 5-minute BTC candles (Binance BTCUSDT, Coinbase BTC-USD as fallback).
2. Sends them as CSV to **Jev**, an LLM on any OpenAI-compatible chat API.
3. Reads Jev's answer (`UP` or `DOWN`) and places a fill-or-kill market buy of `BET_USDC` on that outcome.

Every window is logged to `trades.csv`.

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
git clone <repo> raphaelone && cd raphaelone
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env        # Jev settings, DRY_RUN=true for the first day
.venv/bin/python -m scripts.wallet new
exit
sudo cp /home/bot/raphaelone/deploy/polybot.service /etc/systemd/system/
sudo systemctl enable --now polybot
journalctl -u polybot -f
```

## Safety checks

The bot skips a window (no order) when:

- the market for that window is not found or is closed,
- Jev's answer is not exactly one of UP / DOWN,
- the decision arrives more than `DECISION_DEADLINE_SEC` (default 60) after the window opened,
- the chosen side costs more than `MAX_PRICE` per share,
- `MAX_TRADES_PER_DAY` is reached.

`DRY_RUN=true` runs everything except the order.

## Assumptions to check

- Market slug format is `btc-updown-5m-<window start unix time>`. Change `POLY_SLUG_TEMPLATE` if Polymarket uses another format.
- Polymarket settles on the Chainlink BTC/USD feed, not Binance. The candles Jev sees are close to, but not the same as, the settlement price.
- Binance blocks US IPs; the Coinbase fallback covers that.

## Tests

```bash
pip install pytest && pytest -q
```
