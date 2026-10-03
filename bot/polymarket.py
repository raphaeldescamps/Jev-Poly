"""Find the current 5m BTC Up/Down market and buy one side."""
import json
import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


@dataclass
class Market:
    slug: str
    question: str
    tokens: dict[str, str]  # "UP"/"DOWN" -> CLOB token id
    accepting_orders: bool


def _as_list(value):
    return json.loads(value) if isinstance(value, str) else (value or [])


def find_market(gamma_host: str, slug: str) -> Market | None:
    r = requests.get(f"{gamma_host}/markets", params={"slug": slug}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    m = data[0]
    outcomes = [o.strip().upper() for o in _as_list(m.get("outcomes"))]
    token_ids = _as_list(m.get("clobTokenIds"))
    tokens = dict(zip(outcomes, token_ids))
    if set(tokens) != {"UP", "DOWN"}:
        raise RuntimeError(f"Unexpected outcomes for {slug}: {outcomes}")
    return Market(
        slug=slug,
        question=m.get("question", ""),
        tokens=tokens,
        accepting_orders=bool(m.get("acceptingOrders", True)) and not m.get("closed", False),
    )


class Trader:
    def __init__(self, host: str, chain_id: int, private_key: str,
                 signature_type: int, funder: str | None):
        from py_clob_client.client import ClobClient

        self.client = ClobClient(host, chain_id=chain_id, key=private_key,
                                 signature_type=signature_type, funder=funder)
        self.client.set_api_creds(self.client.create_or_derive_api_creds())

    def quote(self, token_id: str, usdc: float) -> float:
        """Average fill price for a market buy of `usdc` against the current book."""
        from py_clob_client.clob_types import OrderType
        from py_clob_client.order_builder.constants import BUY

        return float(self.client.calculate_market_price(token_id, BUY, usdc, OrderType.FOK))

    def buy(self, token_id: str, usdc: float, price: float) -> dict:
        """Fill-or-kill market buy. `price` is the worst price accepted."""
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        order = self.client.create_market_order(
            MarketOrderArgs(token_id=token_id, amount=usdc, side=BUY, price=price)
        )
        return self.client.post_order(order, OrderType.FOK)
