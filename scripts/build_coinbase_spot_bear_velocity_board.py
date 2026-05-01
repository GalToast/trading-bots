#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
LIVE_RADAR_PATH = REPORTS / "coinbase_spot_live_radar.json"
PULSE_PATH = REPORTS / "coinbase_spot_pulse_board.json"
JSON_PATH = REPORTS / "coinbase_spot_bear_velocity_board.json"
CSV_PATH = REPORTS / "coinbase_spot_bear_velocity_board.csv"
MD_PATH = REPORTS / "coinbase_spot_bear_velocity_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def direct_symbol(currency: str) -> str:
    return f"{currency.upper()}-USD"


def build_payload() -> dict[str, Any]:
    radar = load_json(LIVE_RADAR_PATH)
    pulse = load_json(PULSE_PATH)
    direct_rows = {
        str(row.get("product_id") or ""): row
        for row in radar.get("rows") or []
        if str(row.get("quote_currency") or "") in {"USD", "USDC"}
    }
    direct_dump_rows = []
    for row in direct_rows.values():
        worst_short_bps = min(
            to_float(row.get("move_last_bps")),
            to_float(row.get("ret_30s_bps")),
            to_float(row.get("ret_60s_bps")),
            to_float(row.get("ret_5m_bps")),
        )
        if worst_short_bps <= -25.0:
            copy = dict(row)
            copy["bear_use"] = "avoid_or_rebound_watch"
            copy["worst_short_bps"] = round(worst_short_bps, 6)
            direct_dump_rows.append(copy)
    direct_dump_rows.sort(key=lambda row: to_float(row.get("worst_short_bps")))

    relative_rows = []
    for row in pulse.get("rows") or []:
        product_id = str(row.get("product_id") or "")
        quote = str(row.get("quote_currency") or "").upper()
        base = str(row.get("base_currency") or "").upper()
        if quote in {"USD", "USDC"} or not base or not quote:
            continue
        base_live = direct_rows.get(direct_symbol(base))
        quote_live = direct_rows.get(direct_symbol(quote))
        if not isinstance(base_live, dict) or not isinstance(quote_live, dict):
            continue
        base_move = to_float(base_live.get("ret_60s_bps")) or to_float(base_live.get("move_last_bps"))
        quote_move = to_float(quote_live.get("ret_60s_bps")) or to_float(quote_live.get("move_last_bps"))
        relative_edge_bps = base_move - quote_move
        if quote_move < 0.0 and relative_edge_bps >= 25.0:
            relative_rows.append(
                {
                    "product_id": product_id,
                    "base_currency": base,
                    "quote_currency": quote,
                    "bear_use": "long_base_against_depreciating_quote",
                    "base_usd_move_bps": round(base_move, 6),
                    "quote_usd_move_bps": round(quote_move, 6),
                    "relative_edge_bps": round(relative_edge_bps, 6),
                    "spread_bps": round(to_float(row.get("spread_bps")), 4),
                    "route_note": "spot relative-value only; USD PnL must be checked after base and quote conversion costs",
                }
            )
    relative_rows.sort(key=lambda row: to_float(row.get("relative_edge_bps")), reverse=True)

    rows = []
    for row in direct_dump_rows[:50]:
        rows.append(
            {
                "class": "direct_dump",
                "product_id": row.get("product_id"),
                "bear_use": row.get("bear_use"),
                "worst_short_bps": row.get("worst_short_bps"),
                "spread_bps": row.get("spread_bps"),
                "signal_state": row.get("signal_state"),
                "note": "No spot short: stay in USD or wait for reclaim/rebound proof.",
            }
        )
    for row in relative_rows[:50]:
        rows.append(
            {
                "class": "relative_quote_depreciation",
                "product_id": row.get("product_id"),
                "bear_use": row.get("bear_use"),
                "worst_short_bps": row.get("quote_usd_move_bps"),
                "spread_bps": row.get("spread_bps"),
                "signal_state": "",
                "note": row.get("route_note"),
            }
        )
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_bear_velocity_board",
        "source": {
            "live_radar_path": str(LIVE_RADAR_PATH),
            "pulse_path": str(PULSE_PATH),
        },
        "summary": {
            "direct_dump_rows": len(direct_dump_rows),
            "relative_quote_depreciation_rows": len(relative_rows),
        },
        "leadership_read": [
            "Coinbase spot cannot directly monetize depreciation like a short unless a separate margin/derivatives venue is used.",
            "Bearish velocity is still useful: avoid losers, build rebound watches, or use spot relative-value pairs where the quote asset is weakening against a stronger base.",
            "Relative pair rows are not free money: account-level USD PnL must beat both conversion costs and the pair spread.",
        ],
        "direct_dump_rows": direct_dump_rows,
        "relative_quote_depreciation_rows": relative_rows,
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = ["class", "product_id", "bear_use", "worst_short_bps", "spread_bps", "signal_state", "note"]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({column: row.get(column, "") for column in columns})
    lines = [
        "# Coinbase Spot Bear Velocity Board",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Direct dump rows: `{payload['summary']['direct_dump_rows']}`",
        f"- Relative quote-depreciation rows: `{payload['summary']['relative_quote_depreciation_rows']}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload["leadership_read"]])
    lines.extend(
        [
            "",
            "## Direct Dumps",
            "",
            "| Rank | Product | Worst Short bps | Spread bps | Use |",
            "| ---: | --- | ---: | ---: | --- |",
        ]
    )
    for idx, row in enumerate(payload["direct_dump_rows"][:25], start=1):
        lines.append(
            "| {idx} | {product_id} | {worst_short_bps:.4f} | {spread_bps:.2f} | {bear_use} |".format(
                idx=idx,
                **row,
            )
        )
    lines.extend(
        [
            "",
            "## Relative Quote Depreciation",
            "",
            "| Rank | Product | Base USD bps | Quote USD bps | Relative bps | Spread bps |",
            "| ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, row in enumerate(payload["relative_quote_depreciation_rows"][:25], start=1):
        lines.append(
            "| {idx} | {product_id} | {base_usd_move_bps:.4f} | {quote_usd_move_bps:.4f} | {relative_edge_bps:.4f} | {spread_bps:.2f} |".format(
                idx=idx,
                **row,
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(json.dumps({"json_path": str(JSON_PATH), "md_path": str(MD_PATH), "summary": payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
