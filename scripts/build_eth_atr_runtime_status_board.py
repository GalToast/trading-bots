#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

EXECUTION_MONITOR_JSON = REPORTS / "execution_monitor_report.json"
RUNNER_REGISTRY_JSON = CONFIGS / "penetration_lattice_runner_registry.json"
OUTPUT_JSON = REPORTS / "eth_atr_runtime_status_board.json"
OUTPUT_MD = REPORTS / "eth_atr_runtime_status_board.md"

ACTIVE_LANES = (
    "shadow_ethusd_m5_atr_optimized",
    "shadow_ethusd_m15_atr_optimized",
    "shadow_ethusd_m15_asymmetric",
)

LEGACY_ETH_LANE_NAMES = (
    "live_ethusd_m15_warp_graduation_941782",
    "live_ethusd_m5_warp_941784",
    "shadow_ethusd_exc2_tight",
    "shadow_ethusd_m15_warp",
    "shadow_ethusd_m5_warp",
    "shadow_ethusd_m5_warp_5",
    "shadow_ethusd_m5_warp_wide",
    "hungry_hippo_ethusd_m5_step3p0_retuned_shadow",
    "hungry_hippo_ethusd_m5_step14_control",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            payload, _ = json.JSONDecoder().raw_decode(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def registry_rows_by_lane(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("lanes") if isinstance(payload, dict) else []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("name") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def execution_rows_by_lane(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload, dict) else []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def state_snapshot(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    symbol_row = next(iter(symbols.values()), {}) if symbols else {}
    symbol_row = symbol_row if isinstance(symbol_row, dict) else {}
    mt5_connection = metadata.get("mt5_connection") if isinstance(metadata.get("mt5_connection"), dict) else {}
    return {
        "state_exists": path.exists(),
        "updated_at": payload.get("updated_at"),
        "step": metadata.get("step"),
        "timeframe": metadata.get("timeframe"),
        "raw_close_alpha": metadata.get("raw_close_alpha"),
        "direct_live": bool(metadata.get("direct_live", False)),
        "mt5_identity_ok": bool(mt5_connection.get("identity_ok", False)),
        "mt5_contract_reason": str(mt5_connection.get("reason") or ""),
        "runner_pid": runner.get("pid"),
        "runner_heartbeat_at": runner.get("heartbeat_at"),
        "runner_started_at": runner.get("started_at"),
        "open_count": len(symbol_row.get("open_tickets") or []),
        "realized_closes": int(symbol_row.get("realized_closes") or 0),
        "realized_net_usd": float(symbol_row.get("realized_net_usd") or 0.0),
        "anchor_resets": int(symbol_row.get("anchor_resets") or 0),
        "symbol": str(symbol_row.get("symbol") or ""),
        "live_ticket_count": sum(1 for ticket in symbol_row.get("open_tickets") or [] if int(ticket.get("live_ticket") or 0) != 0),
    }


def build_payload() -> dict[str, Any]:
    registry_rows = registry_rows_by_lane(load_json(RUNNER_REGISTRY_JSON))
    execution_rows = execution_rows_by_lane(load_json(EXECUTION_MONITOR_JSON))

    active_rows: list[dict[str, Any]] = []
    for lane in ACTIVE_LANES:
        registry_row = registry_rows.get(lane, {})
        state_rel = str(registry_row.get("state_path") or "")
        snapshot = state_snapshot(ROOT / state_rel) if state_rel else {}
        execution_row = execution_rows.get(lane, {})
        active_rows.append(
            {
                "lane": lane,
                "enabled": bool(registry_row.get("enabled", False)),
                "watchdog_group": str(registry_row.get("watchdog_group") or ""),
                "state_path": state_rel,
                "event_path": str(registry_row.get("event_path") or ""),
                "watchdog_status": str(execution_row.get("watchdog_status") or ""),
                "execution_open_count": int(execution_row.get("open_count") or 0),
                "execution_close_count": int(execution_row.get("close_count") or 0),
                "execution_last_seen": execution_row.get("heartbeat_at"),
                "shadow_only": not bool(snapshot.get("direct_live", False)),
                "mt5_visibility_expectation": "not_expected_in_mt5_shadow_only",
                "mt5_visibility_reason": "runner writes shadow state/events only; it should not open user-visible MT5 positions",
                **snapshot,
            }
        )

    legacy_rows: list[dict[str, Any]] = []
    for lane in LEGACY_ETH_LANE_NAMES:
        registry_row = registry_rows.get(lane)
        if not registry_row:
            continue
        legacy_rows.append(
            {
                "lane": lane,
                "enabled": bool(registry_row.get("enabled", False)),
                "pause_note": str(registry_row.get("pause_note") or ""),
                "watchdog_group": str(registry_row.get("watchdog_group") or ""),
                "kind": str(registry_row.get("kind") or ""),
            }
        )
    legacy_rows.sort(key=lambda row: row["lane"])

    summary = {
        "active_shadow_lane_count": len(active_rows),
        "enabled_shadow_lane_count": sum(1 for row in active_rows if row["enabled"]),
        "healthy_shadow_lane_count": sum(
            1
            for row in active_rows
            if row["enabled"] and row["mt5_identity_ok"] and str(row["runner_heartbeat_at"] or "").strip()
        ),
        "total_open_shadow_positions": sum(int(row["open_count"]) for row in active_rows),
        "total_realized_shadow_closes": sum(int(row["realized_closes"]) for row in active_rows),
        "mt5_visible_lane_count": sum(
            1 for row in active_rows if row["mt5_visibility_expectation"] != "not_expected_in_mt5_shadow_only"
        ),
        "legacy_paused_eth_lane_count": sum(1 for row in legacy_rows if not row["enabled"]),
    }

    return {
        "generated_at": utc_now_iso(),
        "summary": summary,
        "active_rows": active_rows,
        "legacy_rows": legacy_rows,
        "operator_read": (
            "ETH ATR-optimized runtime is currently a shadow-only sample-building pack. "
            "It is healthy, but it should not appear as live positions in the user-facing MT5 terminal."
        ),
        "historical_context": (
            "Use this board for current optimized ETH runtime truth. "
            "Use reports/eth_decommission_packet.md only as historical context for the toxic lanes it retired."
        ),
    }


def build_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    active_rows = payload.get("active_rows") if isinstance(payload.get("active_rows"), list) else []
    legacy_rows = payload.get("legacy_rows") if isinstance(payload.get("legacy_rows"), list) else []

    lines = [
        "# ETH ATR Runtime Status Board",
        "",
        "> Current runtime generated board.",
        "",
        f"Generated: `{payload.get('generated_at', '-')}`",
        "",
        "## Operator Read",
        "",
        str(payload.get("operator_read") or "-"),
        "",
        "## Summary",
        "",
        f"- Active optimized ETH shadow lanes: `{summary.get('active_shadow_lane_count', 0)}`",
        f"- Healthy optimized ETH shadow lanes: `{summary.get('healthy_shadow_lane_count', 0)}`",
        f"- Open shadow positions across the pack: `{summary.get('total_open_shadow_positions', 0)}`",
        f"- Realized closes since launch across the pack: `{summary.get('total_realized_shadow_closes', 0)}`",
        f"- Lanes expected to show as live MT5 positions now: `{summary.get('mt5_visible_lane_count', 0)}`",
        f"- Legacy paused ETH rows tracked here: `{summary.get('legacy_paused_eth_lane_count', 0)}`",
        "",
        "## MT5 Implication",
        "",
        "- These optimized ETH lanes are `shadow_only` and currently use `direct_live=false`.",
        "- They update state, events, and watchdog surfaces, but they should not create user-visible MT5 positions.",
        "- If ETH does appear in the user-facing MT5 terminal, treat that as separate live or detached inventory and inspect `reports/mt5_user_visibility_board.md` plus detached-inventory surfaces.",
        "",
        "## Active Optimized ETH Shadow Lanes",
        "",
        "| Lane | Enabled | TF | Step | Alpha | PID | Heartbeat | Opens | Closes | Net USD | MT5 expectation |",
        "|---|---|---|---:|---:|---:|---|---:|---:|---:|---|",
    ]

    for row in active_rows:
        net_value = float(row.get("realized_net_usd") or 0.0)
        lines.append(
            "| `{lane}` | {enabled} | {timeframe} | {step} | {alpha} | {pid} | {heartbeat} | {opens} | {closes} | {net:+.2f} | `{mt5}` |".format(
                lane=row.get("lane", "-"),
                enabled="yes" if row.get("enabled") else "no",
                timeframe=row.get("timeframe") or "-",
                step=row.get("step") if row.get("step") is not None else "-",
                alpha=row.get("raw_close_alpha") if row.get("raw_close_alpha") is not None else "-",
                pid=row.get("runner_pid") if row.get("runner_pid") is not None else "-",
                heartbeat=row.get("runner_heartbeat_at") or "-",
                opens=int(row.get("open_count") or 0),
                closes=int(row.get("realized_closes") or 0),
                net=net_value,
                mt5=row.get("mt5_visibility_expectation") or "-",
            )
        )

    lines.extend(
        [
            "",
            "## Paused Or Replaced ETH Rows",
            "",
            "| Lane | Enabled | Kind | Watchdog group | Pause note |",
            "|---|---|---|---|---|",
        ]
    )

    for row in legacy_rows:
        lines.append(
            "| `{lane}` | {enabled} | `{kind}` | `{watchdog}` | `{pause_note}` |".format(
                lane=row.get("lane", "-"),
                enabled="yes" if row.get("enabled") else "no",
                kind=row.get("kind") or "-",
                watchdog=row.get("watchdog_group") or "-",
                pause_note=row.get("pause_note") or "-",
            )
        )

    lines.extend(
        [
            "",
            "## Historical Context",
            "",
            str(payload.get("historical_context") or "-"),
            "",
            "- `reports/eth_decommission_packet.md` remains useful for the toxic ETH lanes that were stopped or paused.",
            "- It is not the current authority surface for the active optimized ETH shadow pack.",
        ]
    )

    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_payload()
    write_json(OUTPUT_JSON, payload)
    write_text(OUTPUT_MD, build_markdown(payload))
    print(json.dumps({"ok": True, "output_json": str(OUTPUT_JSON), "output_md": str(OUTPUT_MD)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
