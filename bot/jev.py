"""Jev: an LLM on any OpenAI-compatible chat endpoint that answers UP or DOWN."""
import logging
import re

from openai import OpenAI

from .candles import Candle, to_csv

log = logging.getLogger(__name__)
_ANSWER = re.compile(r"\b(UP|DOWN)\b", re.IGNORECASE)


def parse_answer(text: str) -> str | None:
    """Return "UP" or "DOWN". Return None if the answer is missing or names both."""
    found = {m.upper() for m in _ANSWER.findall(text or "")}
    return found.pop() if len(found) == 1 else None


class Jev:
    def __init__(self, api_key: str, model: str, system_prompt: str,
                 base_url: str | None = None, timeout: float = 30, client=None):
        self.model = model
        self.system_prompt = system_prompt
        self.client = client or OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def ask(self, candles: list[Candle]) -> tuple[str | None, str]:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": to_csv(candles)},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        return parse_answer(raw), raw
