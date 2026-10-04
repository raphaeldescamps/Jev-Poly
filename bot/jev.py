"""Jev (TypeSafe System One): returns the probability that the next 5m candle closes UP.

API contract taken from the official `typesafe-sdk` package (v0.7.2):
POST {base_url}/v1/systemone, header "Authorization: Bearer <key>",
body {"state", "model", "questions": {name: {"type": "noul", ...}}},
response {"model", "usage", "answers": {name: {"type": "noul", "noul": <P(yes)>}}}.
"""
import datetime as dt
import logging
from dataclasses import dataclass

import requests

from .candles import INTERVAL, Candle

log = logging.getLogger(__name__)

QUESTION_ID = "next_candle_up"
DEFAULT_INSTRUCTIONS = (
    "`candles` holds the last 20 closed 5-minute BTC/USD candles, oldest first. "
    "The next 5-minute candle starts at `next_candle_start`. "
    "Statement: the next 5-minute candle will close higher than it opens."
)
CRITERIA = {
    "true": "The next 5-minute BTC/USD candle closes above its open price (UP).",
    "false": "The next 5-minute BTC/USD candle closes at or below its open price (DOWN).",
}


@dataclass(frozen=True)
class Prediction:
    side: str          # "UP" or "DOWN"
    confidence: float  # probability Jev gives to `side`, 0.5..1
    p_up: float        # raw probability of UP


def build_state(candles: list[Candle]) -> dict:
    def iso(ts: int) -> str:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "symbol": "BTC/USD",
        "interval": "5m",
        "next_candle_start": iso(candles[-1].start + INTERVAL),
        "candles": [
            {"start": iso(c.start), "open": c.open, "high": c.high, "low": c.low,
             "close": c.close, "volume": round(c.volume, 4)}
            for c in candles
        ],
    }


def to_prediction(p_up: float) -> Prediction:
    if not 0.0 <= p_up <= 1.0:
        raise ValueError(f"Jev returned probability outside 0..1: {p_up}")
    if p_up >= 0.5:
        return Prediction("UP", p_up, p_up)
    return Prediction("DOWN", 1.0 - p_up, p_up)


class Jev:
    def __init__(self, api_key: str, model: str = "jev-latest",
                 base_url: str = "https://api.typesafe.ai", timeout: float = 10,
                 instructions: str = DEFAULT_INSTRUCTIONS, session=None):
        self.url = base_url.rstrip("/") + "/v1/systemone"
        self.model = model
        self.timeout = timeout
        self.instructions = instructions
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key.strip()}",
                                     "Accept": "application/json"})

    def ask(self, candles: list[Candle]) -> tuple[Prediction, dict]:
        body = {
            "state": build_state(candles),
            "model": self.model,
            "questions": {QUESTION_ID: {"type": "noul", "instructions": self.instructions,
                                        "criteria": CRITERIA}},
        }
        r = self.session.post(self.url, json=body, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Jev HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        answer = data["answers"][QUESTION_ID]
        if answer.get("type") != "noul":
            raise RuntimeError(f"Unexpected Jev answer type: {answer}")
        return to_prediction(float(answer["noul"])), data
