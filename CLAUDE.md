# Jev-Poly

Polymarket 5-minute BTC Up/Down bot. Jev (TypeSafe System One, `POST https://api.typesafe.ai/v1/systemone`)
gives P(UP); the bot bets only when that beats the share price (see README).

## Server control rules
- The VPS (AWS Lightsail, Dublin) is controlled only through the `Server` GitHub Actions workflow
  (`.github/workflows/deploy.yml`), triggered with `run_workflow`, `workflow_id: deploy.yml`.
- Run `action: deploy` ONLY when the user's message contains the word **DEPLOY** in capitals.
- `status`, `check` and `evaluate` are read-only and can run whenever they help answer the user.
- Never ask for, print or commit secrets. `.env` lives only on the server; the user edits it.
- Never change `DRY_RUN` to `false`. Going live is the user's decision and edit.
