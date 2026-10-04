"""Fetch closed 5-minute BTC/USD candles. Binance first, Coinbase as fallback."""
import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)
INTERVAL = 300


@dataclass(frozen=True)
class Candle:
    start: int  # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float


def _binance(end_ts: int, count: int) -> list[Candle]:
    r = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={
            "symbol": "BTCUSDT",
            "interval": "5m",
            "endTime": end_ts * 1000 - 1,
            "limit": count,
        },
        timeout=10,
    )
    r.raise_for_status()
    return [
        Candle(int(k[0]) // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
        for k in r.json()
    ]


def _coinbase(end_ts: int, count: int) -> list[Candle]:
    r = requests.get(
        "https://api.exchange.coinbase.com/products/BTC-USD/candles",
        params={"granularity": INTERVAL, "start": end_ts - INTERVAL * count, "end": end_ts - 1},
        timeout=10,
    )
    r.raise_for_status()
    # Coinbase row: [time, low, high, open, close, volume], newest first
    rows = sorted(r.json(), key=lambda k: k[0])
    return [Candle(int(k[0]), float(k[3]), float(k[2]), float(k[1]), float(k[4]), float(k[5])) for k in rows]


def fetch_candles(window_start: int, count: int = 20) -> list[Candle]:
    """Return the `count` candles that end exactly at `window_start`, oldest first.

    Called after `window_start`, all candles are closed. Called shortly before it, the
    last candle is the one still in progress (both sources filter by candle start time),
    which lets the bot ask Jev before the window opens.
    """
    errors = []
    for name, source in (("binance", _binance), ("coinbase", _coinbase)):
        try:
            candles = [c for c in source(window_start, count) if c.start + INTERVAL <= window_start]
            candles = candles[-count:]
            if len(candles) == count and candles[-1].start == window_start - INTERVAL:
                return candles
            errors.append(f"{name}: got {len(candles)} usable candles")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    raise RuntimeError("No candle source succeeded: " + "; ".join(errors))


def to_csv(candles: list[Candle]) -> str:
    lines = ["time,open,high,low,close,volume"]
    for c in candles:
        lines.append(f"{c.start},{c.open:.2f},{c.high:.2f},{c.low:.2f},{c.close:.2f},{c.volume:.4f}")
    return "\n".join(lines)
