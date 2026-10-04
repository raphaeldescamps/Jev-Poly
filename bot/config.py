import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_SYSTEM_PROMPT = """You are Jev, a short-term Bitcoin price forecaster.
You receive the last 20 closed 5-minute BTC/USD candles (oldest first) as
time,open,high,low,close,volume. Predict whether the close of the NEXT 5-minute
candle will be higher (UP) or lower (DOWN) than its open.
Reply with exactly one word: UP or DOWN."""


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _system_prompt() -> str:
    path = os.getenv("JEV_SYSTEM_PROMPT_FILE")
    if path:
        return Path(path).read_text().strip()
    return os.getenv("JEV_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)


@dataclass
class Config:
    jev_api_key: str
    jev_model: str
    jev_base_url: str | None
    jev_system_prompt: str
    jev_timeout_sec: float

    poly_private_key: str
    poly_signature_type: int
    poly_funder: str | None
    clob_host: str
    gamma_host: str
    chain_id: int
    slug_template: str

    candle_count: int
    bet_usdc: float
    max_price: float
    max_trades_per_day: int
    entry_delay_sec: float
    decision_deadline_sec: float
    dry_run: bool
    trade_log: str = field(default="trades.csv")

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        cfg = cls(
            jev_api_key=os.getenv("JEV_API_KEY", ""),
            jev_model=os.getenv("JEV_MODEL", "gpt-4o"),
            jev_base_url=os.getenv("JEV_BASE_URL") or None,
            jev_system_prompt=_system_prompt(),
            jev_timeout_sec=float(os.getenv("JEV_TIMEOUT_SEC", "30")),
            poly_private_key=os.getenv("POLY_PRIVATE_KEY", ""),
            poly_signature_type=int(os.getenv("POLY_SIGNATURE_TYPE", "0")),
            poly_funder=os.getenv("POLY_FUNDER") or None,
            clob_host=os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com"),
            gamma_host=os.getenv("POLY_GAMMA_HOST", "https://gamma-api.polymarket.com"),
            chain_id=int(os.getenv("POLY_CHAIN_ID", "137")),
            slug_template=os.getenv("POLY_SLUG_TEMPLATE", "btc-updown-5m-{start}"),
            candle_count=int(os.getenv("CANDLE_COUNT", "20")),
            bet_usdc=float(os.getenv("BET_USDC", "5")),
            max_price=float(os.getenv("MAX_PRICE", "0.70")),
            max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "288")),
            entry_delay_sec=float(os.getenv("ENTRY_DELAY_SEC", "2")),
            decision_deadline_sec=float(os.getenv("DECISION_DEADLINE_SEC", "60")),
            dry_run=_bool("DRY_RUN", False),
            trade_log=os.getenv("TRADE_LOG", "trades.csv"),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        missing = [n for n, v in (("JEV_API_KEY", self.jev_api_key),) if not v]
        if not self.dry_run:
            if not self.poly_private_key:
                missing.append("POLY_PRIVATE_KEY")
            if self.poly_signature_type in (1, 2) and not self.poly_funder:
                missing.append("POLY_FUNDER")
        if missing:
            raise SystemExit(f"Missing required settings: {', '.join(missing)}")
        if not 0 < self.max_price < 1:
            raise SystemExit("MAX_PRICE must be between 0 and 1")
        if self.bet_usdc <= 0:
            raise SystemExit("BET_USDC must be positive")
