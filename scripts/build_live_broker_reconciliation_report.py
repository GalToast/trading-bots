#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCOREBOARD_CSV = ROOT / "reports" / "penetration_lattice_lane_scoreboard.csv"
REGISTRY_JSON = ROOT / "configs" / "penetration_lattice_runner_registry.json"
OUT_CSV = ROOT / "reports" / "live_broker_reconciliation.csv"
OUT_MD = ROOT / "reports" / "live_broker_reconciliation.md"


def load_registry_thresholds() -> dict[str, float]:
    if not REGISTRY_JSON.exists():
        return {}
    payload = json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for lane in payload.get("lanes") or []:
        if not isinstance(lane, dict):
            continue
        name = str(lane.get("name", "") or "")
        if not name:
            continue
        try:
            out[name] = float(lane.get("broker_gap_alert_usd") or 0.0)
        except Exception:
            out[name] = 0.0
    return out


def classify(row: dict[str, str], threshold: float) -> str:
    try:
        gap = abs(float(row.get("realized_gap_usd") or 0.0))
        net = float(row.get("net_usd") or 0.0)
    except Exception:
        return "unknown"
    if threshold > 0.0 and gap >= threshold:
        return "alert"
    if net < 0.0:
        return "warn"
    return "ok"


def main() -> int:
    if not SCOREBOARD_CSV.exists():
        print(f"Missing {SCOREBOARD_CSV}")
        return 1
    thresholds = load_registry_thresholds()
    rows: list[dict[str, str]] = []
    with SCOREBOARD_CSV.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("lane_type") != "live":
                continue
            lane_id = str(row.get("lane_id") or "")
            threshold = thresholds.get(lane_id, 0.0)
            row["gap_alert_threshold_usd"] = f"{threshold:.2f}"
            row["status"] = classify(row, threshold)
            rows.append(row)
    rows.sort(key=lambda row: (row["lane_id"], row["symbol"]))

    fields = list(rows[0].keys()) if rows else [
        "lane_id",
        "lane_type",
        "symbol",
        "updated_at",
        "session_started_at",
        "realized_basis",
        "realized_usd",
        "modeled_realized_usd",
        "realized_gap_usd",
        "floating_usd",
        "net_usd",
        "closes",
        "open_count",
        "avg_usd_per_close",
        "gap_alert_threshold_usd",
        "status",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Live Broker Reconciliation",
        "",
        "| Lane | Symbol | Status | Broker Realized USD | Modeled Realized USD | Gap USD | Floating USD | Net USD | Closes | Open | Threshold USD | Updated |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['lane_id']}` | `{row['symbol']}` | `{row['status']}` | "
            f"{float(row['realized_usd']):.3f} | {float(row['modeled_realized_usd']):.3f} | "
            f"{float(row['realized_gap_usd']):.3f} | {float(row['floating_usd']):.3f} | {float(row['net_usd']):.3f} | "
            f"{row['closes']} | {row['open_count']} | {float(row['gap_alert_threshold_usd']):.2f} | {row['updated_at']} |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

