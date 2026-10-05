import pytest

from bot.candles import Candle
from scripts.backtest import candles_before, price_at


def hist(first, n):
    return [Candle(first + 300 * i, 1, 1, 1, 1, 1) for i in range(n)]


def test_only_candles_closed_before_the_window():
    history = hist(0, 30)  # candles start at 0, 300, ..., 8700
    got = candles_before(history, 3000, 5)
    assert [c.start for c in got] == [1500, 1800, 2100, 2400, 2700]
    assert all(c.start + 300 <= 3000 for c in got)  # the candle starting at 3000 is excluded


def test_missing_history_is_an_error_not_a_shortcut():
    with pytest.raises(ValueError):
        candles_before(hist(0, 5), 3000, 5)  # not enough candles before 3000


def test_price_is_last_point_at_or_before_decision():
    h = [{"t": 900, "p": 0.40}, {"t": 960, "p": 0.45}, {"t": 1020, "p": 0.70}]
    assert price_at(h, 1000) == 0.45  # 1020 is after the decision and must not be used
    assert price_at(h, 800) is None
