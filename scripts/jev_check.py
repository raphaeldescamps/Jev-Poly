"""One test call to Jev with the latest 20 candles. Places no order.

    python -m scripts.jev_check
"""
import time

from bot.candles import INTERVAL, fetch_candles
from bot.config import Config
from bot.jev import Jev


def main() -> None:
    cfg = Config.from_env()
    start = int(time.time()) // INTERVAL * INTERVAL
    candles = fetch_candles(start, max(cfg.candle_sets))
    jev = Jev(cfg.jev_api_key, cfg.jev_model, cfg.jev_base_url, cfg.jev_timeout_sec)
    print(f"Last close {candles[-1].close:.2f}")
    for n in cfg.candle_sets:
        t0 = time.time()
        pred, raw = jev.ask(candles[-n:])
        print(f"{n:3} candles -> P(UP) = {pred.p_up:.3f} ({pred.side} at {pred.confidence:.1%}), "
              f"{time.time() - t0:.2f}s, usage {raw.get('usage')}")


if __name__ == "__main__":
    main()
