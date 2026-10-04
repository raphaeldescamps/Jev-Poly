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

    def buy(self, token_id: str, usdc: float, price: float) -> dict:
        """Fill-or-kill market buy. `price` is the worst price accepted."""
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        order = self.client.create_market_order(
            MarketOrderArgs(token_id=token_id, amount=usdc, side=BUY, price=price)
        )
        return self.client.post_order(order, OrderType.FOK)


@dataclass
class Book:
    bids: list[tuple[float, float]]  # (price, size), best first
    asks: list[tuple[float, float]]

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None


def get_books(clob_host: str, token_ids: list[str]) -> dict[str, Book]:
    """Order books for several tokens in one public call (no wallet needed)."""
    r = requests.post(f"{clob_host}/books", json=[{"token_id": t} for t in token_ids], timeout=5)
    r.raise_for_status()
    books = {}
    for raw in r.json():
        bids = sorted(((float(b["price"]), float(b["size"])) for b in raw.get("bids", [])), reverse=True)
        asks = sorted((float(a["price"]), float(a["size"])) for a in raw.get("asks", []))
        books[raw["asset_id"]] = Book(bids, asks)
    return books


def fill_price(book: Book, usdc: float) -> tuple[float, float] | None:
    """Walk the asks for a `usdc` market buy. Return (average price, worst price), or None if too thin."""
    spent = shares = 0.0
    for price, size in book.asks:
        take = min(size * price, usdc - spent)
        spent += take
        shares += take / price
        if spent >= usdc - 1e-9:
            return spent / shares, price
    return None


def fetch_resolution(gamma_host: str, slug: str) -> str | None:
    """Return "UP" or "DOWN" once Polymarket has resolved the market, else None."""
    r = requests.get(f"{gamma_host}/markets", params={"slug": slug}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data or not data[0].get("closed"):
        return None
    m = data[0]
    outcomes = [o.strip().upper() for o in _as_list(m.get("outcomes"))]
    prices = [float(p) for p in _as_list(m.get("outcomePrices"))]
    for outcome, price in zip(outcomes, prices):
        if price >= 0.99 and outcome in ("UP", "DOWN"):
            return outcome
    return None
