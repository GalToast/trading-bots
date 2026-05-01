#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
LIVE_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
PULSE_PATH = REPORTS / "kraken_spot_pulse_board.json"
JSON_PATH = REPORTS / "kraken_spot_bear_velocity_board.json"
CSV_PATH = REPORTS / "kraken_spot_bear_velocity_board.csv"
MD_PATH = REPORTS / "kraken_spot_bear_velocity_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_payload() -> dict[str, Any]:
    radar = load_json(LIVE_RADAR_PATH)
    pulse = load_json(PULSE_PATH)
    
    direct_rows = {
        str(row.get("product_id") or ""): row
        for row in radar.get("rows") or []
        if str(row.get("quote_currency") or "") in {"USD", "USDC", "USDT"}
    }
    
    direct_dump_rows = []
    for row in direct_rows.values():
        worst_short_bps = min(
            to_float(row.get("move_last_bps")),
            to_float(row.get("ret_30s_bps")),
            to_float(row.get("ret_60s_bps")),
            to_float(row.get("ret_5m_bps")),
        )
        # Veto threshold: -20bps instead of Coinbase's -25bps (Kraken is more sensitive due to lower fees)
        if worst_short_bps <= -20.0:
            copy = dict(row)
            copy["bear_use"] = "avoid_or_rebound_watch"
            copy["worst_short_bps"] = round(worst_short_bps, 6)
            direct_dump_rows.append(copy)
            
    direct_dump_rows.sort(key=lambda row: to_float(row.get("worst_short_bps")))

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
                "note": "Kraken Spot Bear Veto: Do not buy falling knives.",
            }
        )
        
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_bear_velocity_board",
        "summary": {
            "direct_dump_rows": len(direct_dump_rows),
        },
        "leadership_read": [
            "Kraken spot bear veto: avoids entries into assets with negative momentum across 30s/60s/5m windows.",
            "Threshold is set to -20bps to catch early-stage dumping.",
            "Crucial for Kraken frontier where wider spreads make 'catch the knife' extremely expensive.",
        ],
        "direct_dump_rows": direct_dump_rows,
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
        "# Kraken Spot Bear Velocity Board",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Direct dump rows: `{payload['summary']['direct_dump_rows']}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in payload["leadership_read"]:
        lines.append(f"- {item}")
        
    lines.extend([
        "",
        "## Direct Dumps (Vetoed Products)",
        "",
        "| Rank | Product | Worst Short bps | Spread bps | Use |",
        "| ---: | --- | ---: | ---: | --- |",
    ])
    for idx, row in enumerate(payload["direct_dump_rows"][:25], start=1):
        lines.append(
            "| {idx} | {product_id} | {worst_short_bps:.4f} | {spread_bps:.2f} | {bear_use} |".format(
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
    main()
