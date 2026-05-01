#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oanda_config import get_oanda_config  # noqa: E402


DEFAULT_INSTRUMENTS = ("EUR_USD", "GBP_USD", "USD_JPY", "NZD_USD", "AUD_USD")


def mask(value: str, *, keep: int = 4) -> str:
    text = str(value or "")
    if not text:
        return "-"
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}...{text[-keep:]}"


def request_json(
    method: str,
    path: str,
    *,
    cfg: dict[str, str],
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = requests.request(
        method,
        f"{cfg['api_base_v3']}{path}",
        headers={
            "Authorization": f"Bearer {cfg['api_token']}",
            "Content-Type": cfg["content_type"],
        },
        params=params,
        timeout=20,
    )
    if response.status_code >= 400:
        body = response.text[:500]
        raise RuntimeError(f"OANDA {method} {path} failed: HTTP {response.status_code}: {body}")
    return response.json()


def choose_account_id(cfg: dict[str, str], requested: str | None) -> str:
    if requested:
        return requested
    if cfg.get("account_id"):
        return str(cfg["account_id"])
    payload = request_json("GET", "/accounts", cfg=cfg)
    accounts = payload.get("accounts") or []
    if not accounts:
        raise RuntimeError("Token worked, but OANDA returned no accounts.")
    return str(accounts[0].get("id") or "")


def summarize_account(payload: dict[str, Any]) -> dict[str, Any]:
    account = payload.get("account") or {}
    return {
        "id": mask(str(account.get("id") or "")),
        "alias": account.get("alias") or "",
        "currency": account.get("currency") or "",
        "balance": account.get("balance") or "",
        "NAV": account.get("NAV") or "",
        "marginRate": account.get("marginRate") or "",
        "marginUsed": account.get("marginUsed") or "",
        "marginAvailable": account.get("marginAvailable") or "",
        "openTradeCount": account.get("openTradeCount") or 0,
        "openPositionCount": account.get("openPositionCount") or 0,
        "pendingOrderCount": account.get("pendingOrderCount") or 0,
        "hedgingEnabled": account.get("hedgingEnabled"),
    }


def summarize_instruments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload.get("instruments") or []:
        rows.append(
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "displayName": item.get("displayName"),
                "marginRate": item.get("marginRate"),
                "tradeUnitsPrecision": item.get("tradeUnitsPrecision"),
                "minimumTradeSize": item.get("minimumTradeSize"),
                "maximumOrderUnits": item.get("maximumOrderUnits"),
                "maximumPositionSize": item.get("maximumPositionSize"),
                "pipLocation": item.get("pipLocation"),
                "displayPrecision": item.get("displayPrecision"),
            }
        )
    rows.sort(key=lambda row: str(row.get("name") or ""))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only OANDA account capability probe.")
    parser.add_argument("--account-id", default=None, help="Optional OANDA account ID override.")
    parser.add_argument(
        "--instruments",
        nargs="*",
        default=list(DEFAULT_INSTRUMENTS),
        help="Instrument names to inspect, for example EUR_USD GBP_USD.",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON summary.")
    args = parser.parse_args()

    cfg = get_oanda_config()
    if not cfg.get("api_token"):
        raise SystemExit(
            "Missing OANDA token. Set OANDA_API_TOKEN in .env or the shell. "
            "Optional: OANDA_ACCOUNT_ID and OANDA_ENV=practice."
        )

    account_id = choose_account_id(cfg, args.account_id)
    if not account_id:
        raise SystemExit("Could not resolve an OANDA account ID.")

    summary_payload = request_json("GET", f"/accounts/{account_id}/summary", cfg=cfg)
    instrument_payload = request_json(
        "GET",
        f"/accounts/{account_id}/instruments",
        cfg=cfg,
        params={"instruments": ",".join(args.instruments)} if args.instruments else None,
    )

    output = {
        "environment": cfg["environment"],
        "api_base_v3": cfg["api_base_v3"],
        "account": summarize_account(summary_payload),
        "instruments": summarize_instruments(instrument_payload),
        "notes": [
            "Read-only probe: no orders were placed.",
            "Same-side stacking is feasible only if units/margin caps survive the account gate.",
            "Opposite-side hedge behavior depends on hedgingEnabled and jurisdiction/account rules.",
        ],
    }

    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
        return

    account = output["account"]
    print("OANDA read-only account probe")
    print(f"environment: {output['environment']}")
    print(f"account: {account['id']} {account['alias']}".rstrip())
    print(
        "summary: "
        f"currency={account['currency']} balance={account['balance']} NAV={account['NAV']} "
        f"marginRate={account['marginRate']} marginUsed={account['marginUsed']} "
        f"marginAvailable={account['marginAvailable']} hedgingEnabled={account['hedgingEnabled']}"
    )
    print(
        "counts: "
        f"openTrades={account['openTradeCount']} openPositions={account['openPositionCount']} "
        f"pendingOrders={account['pendingOrderCount']}"
    )
    print("instruments:")
    for row in output["instruments"]:
        print(
            "  "
            f"{row['name']}: type={row['type']} marginRate={row['marginRate']} "
            f"unitsPrecision={row['tradeUnitsPrecision']} minTrade={row['minimumTradeSize']} "
            f"maxOrderUnits={row['maximumOrderUnits']} maxPosition={row['maximumPositionSize']}"
        )


if __name__ == "__main__":
    main()
