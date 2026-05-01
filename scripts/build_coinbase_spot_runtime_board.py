#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

MD_PATH = REPORTS / "coinbase_spot_runtime_board.md"
JSON_PATH = REPORTS / "coinbase_spot_runtime_board.json"

RAVE_LIVE_STATE_PATH = REPORTS / "rave_rsi_mr_live_v2_state.json"
MULTI_COIN_PORTFOLIO_STATE_PATH = REPORTS / "multi_coin_portfolio_state.json"
RSI_SCOREBOARD_PATH = REPORTS / "coinbase_spot_rsi_scoreboard.csv"
PIRANHA_STATE_PATHS = [
    REPORTS / "coinbase_spot_shadow_dogeusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_xrpusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_suiusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_adausd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_solusd_piranha_state.json",
]
STANDALONE_IOTX_PATH = REPORTS / "live_iotx_bb_reversion_state.json"
KEY_LANE_PRIORITY = {
    "rave_rsi_mr_live_v2": 0,
    "multi_coin_portfolio": 1,
    "live_iotx_bb_reversion": 2,
    "coinbase_spot_piranha_shadow": 3,
}


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_iso(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_seconds(raw: str, *, now: datetime) -> float | None:
    dt = parse_iso(raw)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds())


def freshness_status(age: float | None) -> str:
    if age is None:
        return "missing"
    if age <= 300:
        return "active"
    if age <= 3600:
        return "stale"
    return "offline"


def build_rave_live_lane(*, now: datetime) -> dict[str, Any]:
    payload = load_json(RAVE_LIVE_STATE_PATH)
    if not payload:
        return {
            "lane": "rave_rsi_mr_live_v2",
            "product_id": "RAVE-USD",
            "family": "rsi_mean_reversion",
            "status": "missing",
            "action": "restore_live_immediately",
            "realized_net_usd": 0.0,
            "closes": 0,
            "note": "state file missing",
        }
    state = payload.get("state") or {}
    age = age_seconds(str(payload.get("updated_at") or ""), now=now)
    status = freshness_status(age)
    position = state.get("position") or {}
    has_open = bool(position)
    action = "restore_live_immediately" if status != "active" else "monitor_open_position"
    note = (
        f"open_position={has_open}, hold={to_int(position.get('hold'))}, "
        f"tp={to_float(position.get('tp')):.6f}" if has_open else "flat"
    )
    return {
        "lane": "rave_rsi_mr_live_v2",
        "product_id": "RAVE-USD",
        "family": "rsi_mean_reversion",
        "status": status,
        "age_seconds": round(age or 0.0, 1),
        "action": action,
        "realized_net_usd": round(to_float(state.get("realized_net")), 4),
        "closes": to_int(state.get("closes")),
        "note": note,
    }


def build_piranha_lanes(*, now: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in PIRANHA_STATE_PATHS:
        payload = load_json(path)
        metadata = payload.get("metadata") or {}
        runner = payload.get("runner") or {}
        product_id = str(metadata.get("product_id") or "")
        symbol = ((payload.get("symbols") or {}).get(product_id) or {})
        age = age_seconds(str(runner.get("heartbeat_at") or ""), now=now)
        status = freshness_status(age)
        closes = to_int(symbol.get("realized_closes"))
        realized = round(to_float(symbol.get("realized_net_usd")), 4)
        open_lots = len(symbol.get("open_lots") or [])
        if status == "active" and closes == 0:
            action = "keep_probe_running"
        elif status == "stale" and closes == 0 and open_lots > 0:
            action = "verify_probe_health"
        elif status != "active" and realized <= 0.0:
            action = "retire_or_restart_only_if_needed"
        else:
            action = "monitor"
        rows.append(
            {
                "lane": "coinbase_spot_piranha_shadow",
                "product_id": product_id,
                "family": "spot_piranha",
                "status": status,
                "age_seconds": round(age or 0.0, 1),
                "action": action,
                "realized_net_usd": realized,
                "closes": closes,
                "note": f"open_lots={open_lots}, cash={to_float(symbol.get('cash_usd')):.2f}",
            }
        )
    rows.sort(key=lambda row: (row["status"] != "active", -to_float(row["realized_net_usd"]), row["product_id"]))
    return rows


def build_rsi_shadow_queue() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in load_csv(RSI_SCOREBOARD_PATH):
        lane_name = str(row.get("lane_name") or "")
        if lane_name == "TOTAL":
            continue
        realized = to_float(row.get("realized_net_usd"))
        product_id = str(row.get("product_id") or "")
        readiness = str(row.get("readiness_verdict") or "")
        if readiness != "probationary" or realized <= 0.0:
            continue
        status = freshness_status(to_float(row.get("heartbeat_age_seconds")))
        rows.append(
            {
                "lane": lane_name,
                "product_id": product_id,
                "family": "rsi_mean_reversion",
                "status": status,
                "age_seconds": round(to_float(row.get("heartbeat_age_seconds")), 1),
                "action": "promote_small_live" if status == "active" else "verify_then_promote",
                "realized_net_usd": round(realized, 4),
                "closes": to_int(row.get("realized_closes")),
                "note": f"walkforward={row.get('walkforward') or '-'}, baseline72h={to_float(row.get('baseline_72h_net_usd')):.4f}",
            }
        )
    rows.sort(key=lambda row: (-to_float(row["realized_net_usd"]), -to_int(row["closes"]), row["product_id"]))
    return rows


def build_portfolio_lane(*, now: datetime) -> dict[str, Any]:
    payload = load_json(MULTI_COIN_PORTFOLIO_STATE_PATH)
    if not payload:
        return {
            "lane": "multi_coin_portfolio",
            "product_id": "multi-asset",
            "family": "portfolio_orchestrator",
            "status": "missing",
            "action": "verify_runner_before_trusting",
            "realized_net_usd": 0.0,
            "closes": 0,
            "note": "state file missing",
        }
    age = age_seconds(str(payload.get("updated_at") or ""), now=now)
    status = freshness_status(age)
    action = "monitor_not_promote" if status == "active" else "verify_runner_before_trusting"
    return {
        "lane": "multi_coin_portfolio",
        "product_id": "multi-asset",
        "family": "portfolio_orchestrator",
        "status": status,
        "age_seconds": round(age or 0.0, 1),
        "action": action,
        "realized_net_usd": round(to_float(payload.get("portfolio_realized")), 4),
        "closes": to_int(payload.get("portfolio_closes")),
        "note": f"portfolio_wr={to_float(payload.get('portfolio_wr')):.1f}, starting_cash={to_float(payload.get('total_starting_cash')):.2f}",
    }


def build_iotx_standalone(*, now: datetime) -> dict[str, Any]:
    payload = load_json(STANDALONE_IOTX_PATH)
    if not payload:
        return {
            "lane": "live_iotx_bb_reversion",
            "product_id": "IOTX-USD",
            "family": "bb_reversion",
            "status": "missing",
            "action": "do_not_launch_until_reconciled",
            "realized_net_usd": 0.0,
            "closes": 0,
            "note": "no standalone live state file present",
        }
    state = payload.get("state") or {}
    age = age_seconds(str(payload.get("updated_at") or ""), now=now)
    return {
        "lane": "live_iotx_bb_reversion",
        "product_id": "IOTX-USD",
        "family": "bb_reversion",
        "status": freshness_status(age),
        "age_seconds": round(age or 0.0, 1),
        "action": "do_not_launch_until_reconciled",
        "realized_net_usd": round(to_float(state.get("realized_net")), 4),
        "closes": to_int(state.get("closes")),
        "note": "state exists but lane remains reconcile-first",
    }


def build_leadership_read(key_lanes: list[dict[str, Any]]) -> list[str]:
    by_lane = {str(row.get("lane") or ""): row for row in key_lanes}
    rave = by_lane.get("rave_rsi_mr_live_v2") or {}
    portfolio = by_lane.get("multi_coin_portfolio") or {}
    iotx = by_lane.get("live_iotx_bb_reversion") or {}
    piranha_rows = [row for row in key_lanes if row.get("family") == "spot_piranha"]
    active_probes = [str(row["product_id"]) for row in piranha_rows if row.get("status") == "active"]
    stale_probes = [str(row["product_id"]) for row in piranha_rows if row.get("status") == "stale"]

    leadership_read: list[str] = []
    if str(rave.get("status") or "") == "active":
        leadership_read.append(
            "RAVE live is active again, so the job shifts from restore to monitoring the open position honestly."
        )
    else:
        leadership_read.append(
            "RAVE live remains the urgent restore lane because it is stale or offline with live evidence behind it."
        )

    if active_probes:
        leadership_read.append(
            "Active piranha probes are still research lanes, not promotion arguments: "
            + ", ".join(active_probes)
            + "."
        )
    elif stale_probes:
        leadership_read.append(
            "Piranha probes need a health check before they count for anything: " + ", ".join(stale_probes) + "."
        )
    else:
        leadership_read.append("No piranha probe is clean enough yet to argue for promotion.")

    if to_float(portfolio.get("realized_net_usd")) > 0.0:
        leadership_read.append(
            "The multi-coin portfolio is finally positive at runtime, but it still needs cleaner attribution before promotion."
        )
    else:
        leadership_read.append(
            "The multi-coin portfolio is running, but current negative realized PnL means it is still not a promotion argument."
        )

    if str(iotx.get("status") or "") == "missing":
        leadership_read.append(
            "Standalone IOTX remains absent and should stay non-live until the reconciliation gap is closed."
        )
    else:
        leadership_read.append(
            "Standalone IOTX state exists, but the lane should stay non-live until reconciliation and runtime stop disagreeing."
        )
    return leadership_read


def build_payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    key_lanes = [
        build_rave_live_lane(now=now),
        build_portfolio_lane(now=now),
        build_iotx_standalone(now=now),
        *build_piranha_lanes(now=now),
    ]
    key_lanes.sort(
        key=lambda row: (
            KEY_LANE_PRIORITY.get(str(row["lane"]), 99),
            row["action"] != "restore_live_immediately",
            row["status"] != "active",
            row["product_id"],
        )
    )
    return {
        "generated_at": now.isoformat(),
        "leadership_read": build_leadership_read(key_lanes),
        "key_lanes": key_lanes,
        "rsi_shadow_queue": build_rsi_shadow_queue(),
    }


def write_reports(payload: dict[str, Any], *, md_path: Path = MD_PATH, json_path: Path = JSON_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Coinbase Spot Runtime Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Key Lanes",
            "",
            "| Lane | Product | Family | Status | Action | Realized $ | Closes | Note |",
            "| --- | --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in payload["key_lanes"]:
        lines.append(
            "| {lane} | {product_id} | {family} | {status} | {action} | {realized_net_usd:.4f} | {closes} | {note} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## RSI Shadow Promotion Queue",
            "",
            "| Product | Lane | Status | Action | Realized $ | Closes | Note |",
            "| --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in payload["rsi_shadow_queue"]:
        lines.append(
            "| {product_id} | {lane} | {status} | {action} | {realized_net_usd:.4f} | {closes} | {note} |".format(
                **row
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
