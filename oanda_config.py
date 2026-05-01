"""Shared OANDA configuration loader for local bot scripts."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _read_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


@lru_cache(maxsize=1)
def get_oanda_config() -> dict[str, str]:
    env_values = _read_env_file(Path(__file__).resolve().parent / ".env")

    token = os.getenv("OANDA_API_TOKEN") or env_values.get("OANDA_API_TOKEN")
    account_id = os.getenv("OANDA_ACCOUNT_ID") or env_values.get("OANDA_ACCOUNT_ID")
    environment = (os.getenv("OANDA_ENVIRONMENT") or env_values.get("OANDA_ENVIRONMENT") or "practice").lower()

    if not token or not account_id:
        raise RuntimeError(
            "Missing OANDA credentials. Set OANDA_API_TOKEN and OANDA_ACCOUNT_ID in the environment or local .env file."
        )

    host = "https://api-fxtrade.oanda.com" if environment == "live" else "https://api-fxpractice.oanda.com"

    return {
        "api_token": token,
        "account_id": account_id,
        "environment": environment,
        "api_host": host,
        "api_base_v3": f"{host}/v3",
        "content_type": "application/json",
    }
