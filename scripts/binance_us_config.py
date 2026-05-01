from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = ROOT / ".env"


def load_env_file(path: Path = DEFAULT_ENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


load_env_file()

API_BASE_URL = os.environ.get("BINANCE_US_API_BASE_URL", "https://api.binance.us").rstrip("/")
WS_BASE_URL = os.environ.get("BINANCE_US_WS_BASE_URL", "wss://stream.binance.us:9443/ws").rstrip("/")
API_KEY = os.environ.get("BINANCE_US_API_KEY", "").strip()
API_SECRET = os.environ.get("BINANCE_US_API_SECRET", "").strip()
RECV_WINDOW_MS = int(os.environ.get("BINANCE_US_RECV_WINDOW_MS", "5000"))
DEFAULT_SYMBOL = os.environ.get("BINANCE_US_DEFAULT_SYMBOL", "BTCUSD").strip().upper()


def has_api_credentials() -> bool:
    return bool(API_KEY and API_SECRET)
