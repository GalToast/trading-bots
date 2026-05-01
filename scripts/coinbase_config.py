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


def _normalize_multiline_secret(value: str) -> str:
    return str(value or "").replace("\\n", "\n").strip()


load_env_file()

API_BASE_URL = os.environ.get("COINBASE_API_BASE_URL", "https://api.coinbase.com").rstrip("/")
EXCHANGE_PUBLIC_URL = os.environ.get("COINBASE_EXCHANGE_PUBLIC_URL", "https://api.exchange.coinbase.com").rstrip("/")
SANDBOX_BASE_URL = os.environ.get("COINBASE_SANDBOX_BASE_URL", "https://api-sandbox.coinbase.com").rstrip("/")
WS_MARKET_DATA_URL = os.environ.get("COINBASE_WS_MARKET_DATA_URL", "wss://advanced-trade-ws.coinbase.com").rstrip("/")
WS_USER_URL = os.environ.get("COINBASE_WS_USER_URL", "wss://advanced-trade-ws-user.coinbase.com").rstrip("/")
API_KEY_NAME = os.environ.get("COINBASE_API_KEY_NAME", "").strip()
API_KEY_SECRET = _normalize_multiline_secret(os.environ.get("COINBASE_API_KEY_SECRET", ""))
DEFAULT_PRODUCT_ID = os.environ.get("COINBASE_DEFAULT_PRODUCT_ID", "BTC-USD").strip().upper()
DEFAULT_FUTURES_PRODUCT_ID = os.environ.get("COINBASE_DEFAULT_FUTURES_PRODUCT_ID", "").strip().upper()
TIMEOUT_SECONDS = float(os.environ.get("COINBASE_TIMEOUT_SECONDS", "10"))
JWT_EXPIRES_SECONDS = int(os.environ.get("COINBASE_JWT_EXPIRES_SECONDS", "120"))
HTTP_USER_AGENT = os.environ.get(
    "COINBASE_HTTP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
).strip()


def has_api_credentials() -> bool:
    return bool(API_KEY_NAME and API_KEY_SECRET)
