import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bot.candles import Candle, fetch_candles
from bot.config import Config
from bot.jev import Jev, Prediction, build_state, to_prediction
from bot.main import Bot, next_window_start
from bot.outcomes import OutcomeTracker
from bot.polymarket import Book, Market, fetch_resolution, fill_price, find_market


def make_cfg(**kw):
    base = dict(
        jev_api_key="k", jev_model="jev-latest", jev_base_url="https://api.typesafe.ai",
        jev_timeout_sec=10, poly_private_key="0x1", poly_signature_type=0, poly_funder=None,
        clob_host="h", gamma_host="g", chain_id=137, slug_template="btc-updown-5m-{start}",
        candle_count=20, bet_usdc=5, max_price=0.7, min_confidence=0.55, min_edge=0.03, max_trades_per_day=10,
        entry_delay_sec=0, decision_deadline_sec=60, dry_run=False, trade_log="/dev/null",
    )
    base.update(kw)
    return Config(**base)


def candles(start, n=20):
    return [Candle(start - 300 * (n - i), 1, 2, 0.5, 1.5, 10) for i in range(n)]


def test_to_prediction():
    assert to_prediction(0.7) == Prediction("UP", 0.7, 0.7)
    p = to_prediction(0.2)
    assert p.side == "DOWN" and abs(p.confidence - 0.8) < 1e-9


def test_next_window_start():
    assert next_window_start(1_000_000_000) == 1_000_000_200
    assert next_window_start(1_000_000_199.9) == 1_000_000_200


def test_jev_request_and_response():
    session = MagicMock()
    session.headers = {}
    session.post.return_value = SimpleNamespace(status_code=200, json=lambda: {
        "model": "jev-latest", "usage": {},
        "answers": {"next_candle_up": {"type": "noul", "noul": 0.31}}})
    pred, _ = Jev(" key ", session=session).ask(candles(3000 * 300))
    assert pred.side == "DOWN" and abs(pred.confidence - 0.69) < 1e-9
    assert session.headers["Authorization"] == "Bearer key"
    url = session.post.call_args.args[0]
    body = session.post.call_args.kwargs["json"]
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert body["model"] == "jev-latest"
    assert body["questions"]["next_candle_up"]["type"] == "noul"
    assert len(body["state"]["candles"]) == 20


def test_build_state_next_start():
    state = build_state(candles(600))
    assert state["next_candle_start"] == "1970-01-01T00:10:00Z"


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


def books(up_asks, down_asks):
    return {"UP": Book([(0.4, 100)], up_asks), "DOWN": Book([(0.4, 100)], down_asks)}


def run(cfg, p_up=0.7, up_asks=((0.5, 100),), down_asks=((0.5, 100),), start=None, trader=True):
    start = start or int(time.time())
    jev = MagicMock(); jev.ask.return_value = (to_prediction(p_up), {})
    t = MagicMock(); t.buy.return_value = {"success": True}
    market = Market("s", "q", {"UP": "111", "DOWN": "222"}, True)
    b = books(list(up_asks), list(down_asks))
    with patch("bot.main.fetch_candles", return_value=candles(start)), \
         patch("bot.main.get_books", return_value={"111": b["UP"], "222": b["DOWN"]}):
        row = Bot(cfg, jev, t if trader else None).run_window(start, market)
    return row, t


def test_buys_side_jev_picks():
    row, t = run(make_cfg(), p_up=0.3, down_asks=[(0.6, 100)])  # DOWN at 0.70 vs ask 0.60
    assert row["status"] == "filled"
    t.buy.assert_called_once_with("222", 5, 0.6)


def test_records_both_sides_odds_even_when_skipped():
    row, t = run(make_cfg(), p_up=0.52, up_asks=[(0.51, 50)], down_asks=[(0.5, 50)])
    assert "MIN_CONFIDENCE" in row["detail"]
    assert row["up_ask"] == 0.51 and row["down_ask"] == 0.5 and row["up_bid"] == 0.4
    assert row["odds_at_sec"] != "" and row["decided_at_sec"] != ""
    t.buy.assert_not_called()


def test_skips_without_edge():
    row, t = run(make_cfg(), p_up=0.62, up_asks=[(0.61, 100)])
    assert row["status"] == "skipped" and "edge" in row["detail"]
    t.buy.assert_not_called()


def test_skips_expensive_side():
    row, t = run(make_cfg(), p_up=0.95, up_asks=[(0.9, 100)])
    assert "MAX_PRICE" in row["detail"]
    t.buy.assert_not_called()


def test_skips_late_decision():
    row, t = run(make_cfg(), start=int(time.time()) - 120)
    assert "too late" in row["detail"]
    t.buy.assert_not_called()


def test_dry_run_places_no_order():
    row, t = run(make_cfg(dry_run=True))
    assert row["status"] == "dry_run" and row["detail"] == "would buy"
    t.buy.assert_not_called()


def test_dry_run_without_wallet():
    row, _ = run(make_cfg(dry_run=True), trader=False)
    assert row["status"] == "dry_run" and row["decision"] == "UP"


def test_fill_price_walks_levels():
    book = Book([], [(0.5, 4), (0.6, 100)])  # 2 USDC at 0.5, rest at 0.6
    avg, worst = fill_price(book, 5)
    assert worst == 0.6 and abs(avg - 5 / (4 + 3 / 0.6)) < 1e-9
    assert fill_price(Book([], [(0.5, 1)]), 5) is None


def resp(data):
    r = MagicMock(); r.json.return_value = data
    return r


def test_fetch_resolution():
    closed = [{"closed": True, "outcomes": '["Up","Down"]', "outcomePrices": '["0","1"]'}]
    with patch("bot.polymarket.requests.get", return_value=resp(closed)):
        assert fetch_resolution("g", "s") == "DOWN"
    with patch("bot.polymarket.requests.get", return_value=resp([{"closed": False}])):
        assert fetch_resolution("g", "s") is None


def test_outcome_tracker_waits_then_records(tmp_path):
    path = tmp_path / "outcomes.csv"
    tr = OutcomeTracker(str(path), "g")
    tr.add(3000, "s")
    with patch("bot.outcomes.fetch_resolution", return_value=None):
        tr.update(now=3000 + 400)
    assert tr.pending and not path.exists()  # not resolved yet, keep waiting
    with patch("bot.outcomes.fetch_resolution", return_value="UP"), \
         patch("bot.outcomes.fetch_candles", return_value=[Candle(3000, 1, 2, 0.5, 1.5, 1)]):
        tr.update(now=3000 + 600)
    assert not tr.pending and "UP" in path.read_text()
    assert OutcomeTracker(str(path), "g").done == {3000}
