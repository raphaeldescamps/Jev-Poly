import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    jev_api_key: str
    jev_model: str
    jev_base_url: str
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
    min_confidence: float
    min_edge: float
    max_trades_per_day: int
    entry_delay_sec: float
    decision_deadline_sec: float
    dry_run: bool
    trade_log: str = field(default="trades.csv")
    outcome_log: str = field(default="outcomes.csv")

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        cfg = cls(
            jev_api_key=os.getenv("JEV_API_KEY", "").strip(),
            jev_model=os.getenv("JEV_MODEL", "jev-latest"),
            jev_base_url=os.getenv("JEV_BASE_URL") or "https://api.typesafe.ai",
            jev_timeout_sec=float(os.getenv("JEV_TIMEOUT_SEC", "10")),
            poly_private_key=os.getenv("POLY_PRIVATE_KEY", "").replace("0x...", "").strip(),
            poly_signature_type=int(os.getenv("POLY_SIGNATURE_TYPE", "0")),
            poly_funder=os.getenv("POLY_FUNDER") or None,
            clob_host=os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com"),
            gamma_host=os.getenv("POLY_GAMMA_HOST", "https://gamma-api.polymarket.com"),
            chain_id=int(os.getenv("POLY_CHAIN_ID", "137")),
            slug_template=os.getenv("POLY_SLUG_TEMPLATE", "btc-updown-5m-{start}"),
            candle_count=int(os.getenv("CANDLE_COUNT", "20")),
            bet_usdc=float(os.getenv("BET_USDC", "5")),
            max_price=float(os.getenv("MAX_PRICE", "0.70")),
            min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.55")),
            min_edge=float(os.getenv("MIN_EDGE", "0.03")),
            max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "288")),
            entry_delay_sec=float(os.getenv("ENTRY_DELAY_SEC", "1")),
            decision_deadline_sec=float(os.getenv("DECISION_DEADLINE_SEC", "60")),
            dry_run=_bool("DRY_RUN", False),
            trade_log=os.getenv("TRADE_LOG", "trades.csv"),
            outcome_log=os.getenv("OUTCOME_LOG", "outcomes.csv"),
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
        if not 0.5 <= self.min_confidence < 1:
            raise SystemExit("MIN_CONFIDENCE must be between 0.5 and 1")
        if self.bet_usdc <= 0:
            raise SystemExit("BET_USDC must be positive")
