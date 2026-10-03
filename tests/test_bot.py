import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bot.candles import Candle, fetch_candles
from bot.config import Config, DEFAULT_SYSTEM_PROMPT
from bot.jev import Jev, parse_answer
from bot.main import Bot, next_window_start
from bot.polymarket import Market, find_market


def make_cfg(**kw):
    base = dict(
        jev_api_key="k", jev_model="m", jev_base_url=None, jev_system_prompt=DEFAULT_SYSTEM_PROMPT,
        jev_timeout_sec=30, poly_private_key="0x1", poly_signature_type=0, poly_funder=None,
        clob_host="h", gamma_host="g", chain_id=137, slug_template="btc-updown-5m-{start}",
        candle_count=20, bet_usdc=5, max_price=0.7, max_trades_per_day=10,
        entry_delay_sec=0, decision_deadline_sec=60, dry_run=False, trade_log="/dev/null",
    )
    base.update(kw)
    return Config(**base)


def candles(start, n=20):
    return [Candle(start - 300 * (n - i), 1, 2, 0.5, 1.5, 10) for i in range(n)]


def test_parse_answer():
    assert parse_answer("UP") == "UP"
    assert parse_answer("  down.") == "DOWN"
    assert parse_answer("I think UP") == "UP"
    assert parse_answer("UP or DOWN?") is None
    assert parse_answer("") is None
    assert parse_answer("upward") is None


def test_next_window_start():
    assert next_window_start(1_000_000_000) == 1_000_000_200
    assert next_window_start(1_000_000_199.9) == 1_000_000_200


def test_jev_sends_candles():
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="DOWN"))])
    decision, raw = Jev("k", "m", "sys", client=client).ask(candles(3000))
    assert decision == "DOWN"
    msgs = client.chat.completions.create.call_args.kwargs["messages"]
    assert msgs[1]["content"].count("\n") == 20  # header + 20 rows


def test_fetch_candles_falls_back_to_coinbase():
    start = 300 * 1000
    with patch("bot.candles._binance", side_effect=RuntimeError("451")), \
         patch("bot.candles._coinbase", return_value=candles(start)):
        got = fetch_candles(start)
    assert len(got) == 20 and got[-1].start == start - 300


def test_find_market_maps_outcomes():
    resp = MagicMock()
    resp.json.return_value = [{"question": "q", "outcomes": '["Up", "Down"]',
                               "clobTokenIds": '["111", "222"]', "acceptingOrders": True}]
    with patch("bot.polymarket.requests.get", return_value=resp):
        m = find_market("g", "s")
    assert m.tokens == {"UP": "111", "DOWN": "222"} and m.accepting_orders


def run(cfg, answer="UP", price=0.5, start=None):
    start = start or int(time.time())
    jev = MagicMock(); jev.ask.return_value = (parse_answer(answer), answer)
    trader = MagicMock(); trader.quote.return_value = price
    trader.buy.return_value = {"success": True}
    market = Market("s", "q", {"UP": "111", "DOWN": "222"}, True)
    with patch("bot.main.find_market", return_value=market), \
         patch("bot.main.fetch_candles", return_value=candles(start)):
        row = Bot(cfg, jev, trader).run_window(start)
    return row, trader


def test_buys_side_jev_picks():
    row, trader = run(make_cfg(), "DOWN")
    assert row["status"] == "filled"
    trader.buy.assert_called_once_with("222", 5, 0.5)


def test_skips_expensive_side():
    row, trader = run(make_cfg(), price=0.9)
    assert row["status"] == "skipped"
    trader.buy.assert_not_called()


def test_skips_unclear_answer():
    row, trader = run(make_cfg(), answer="maybe")
    assert row["status"] == "skipped"
    trader.buy.assert_not_called()


def test_skips_late_decision():
    row, trader = run(make_cfg(), start=int(time.time()) - 120)
    assert "too late" in row["detail"]
    trader.buy.assert_not_called()


def test_dry_run_places_no_order():
    row, trader = run(make_cfg(dry_run=True))
    assert row["status"] == "dry_run"
    trader.quote.assert_not_called()
