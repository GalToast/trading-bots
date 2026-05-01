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

API_BASE_URL = os.environ.get("KRAKEN_API_BASE_URL", "https://api.kraken.com").rstrip("/")
WS_PUBLIC_URL = os.environ.get("KRAKEN_WS_PUBLIC_URL", "wss://ws.kraken.com/v2").rstrip("/")
API_KEY = os.environ.get("KRAKEN_API_KEY", "").strip()
API_SECRET = os.environ.get("KRAKEN_API_SECRET", "").strip()
TIMEOUT_SECONDS = float(os.environ.get("KRAKEN_TIMEOUT_SECONDS", "10"))
HTTP_USER_AGENT = os.environ.get(
    "KRAKEN_HTTP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
).strip()
DEFAULT_MAKER_FEE_BPS = float(os.environ.get("KRAKEN_DEFAULT_MAKER_FEE_BPS", "25"))
DEFAULT_TAKER_FEE_BPS = float(os.environ.get("KRAKEN_DEFAULT_TAKER_FEE_BPS", "40"))


def has_api_credentials() -> bool:
    return bool(API_KEY and API_SECRET)
