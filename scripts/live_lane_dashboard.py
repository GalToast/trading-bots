#!/usr/bin/env python3
"""Live lattice lane dashboard backed by watchdog + execution monitor truth."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
WATCHDOG_GROUPS_CONFIG = ROOT / "configs" / "watchdog_groups.json"
RUNNER_REGISTRY_JSON = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_ROOT_PATH = ROOT / "reports" / "penetration_lattice_runner_watchdog.json"
EXECUTION_MONITOR_JSON = ROOT / "reports" / "execution_monitor_report.json"
OUT_JSON = ROOT / "reports" / "live_lane_dashboard.json"
OUT_MD = ROOT / "reports" / "live_lane_dashboard.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def session_usd_per_hour(started_at: Any, realized_usd: Any, now_dt: datetime | None = None) -> float:
    started_dt = parse_iso(started_at)
    if started_dt is None:
        return 0.0
    if now_dt is None:
        now_dt = utc_now()
    elapsed_seconds = (now_dt - started_dt).total_seconds()
    if elapsed_seconds <= 0:
        return 0.0
    return round(to_float(realized_usd) / (elapsed_seconds / 3600.0), 2)


def monitor_realized_fallback(execution_row: dict[str, Any]) -> float:
    return round(
        to_float(execution_row.get("broker_sync_inherited_realized_usd"))
        + to_float(execution_row.get("pre_start_state_carry_realized_usd"))
        + to_float(execution_row.get("runner_session_trade_realized_usd")),
        2,
    )


def prefer_monitor_realized(scoreboard_realized_usd: float, execution_row: dict[str, Any]) -> float:
    monitor_realized = monitor_realized_fallback(execution_row)
    if abs(scoreboard_realized_usd) <= 1e-9 and abs(monitor_realized) > 1e-9:
        return monitor_realized
    return scoreboard_realized_usd


def merged_watchdog_rows(paths: list[Path]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = load_json(path)
        rows = payload.get("rows") if isinstance(payload, dict) else []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if name and name not in merged:
                merged[name] = row
    return merged


def resolve_state_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = ROOT / path
    return path


def current_runner_pid(watchdog_row: dict[str, Any]) -> int:
    path = resolve_state_path(watchdog_row.get("state_path"))
    if path is None:
        return 0
    payload = load_json(path)
    if not isinstance(payload, dict):
        return 0
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    return to_int(runner.get("pid"))


def watchdog_report_paths() -> list[Path]:
    payload = load_json(WATCHDOG_GROUPS_CONFIG)
    groups = payload.get("groups") if isinstance(payload, dict) else {}
    paths: list[Path] = []
    if isinstance(groups, dict):
        paths.extend(
            ROOT / "reports" / "watchdog" / f"{str(group_name)}_report.json"
            for group_name in sorted(groups.keys())
            if str(group_name or "").strip()
        )
    paths.append(WATCHDOG_ROOT_PATH)
    return paths


def execution_rows_by_lane(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def registry_rows_by_lane(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    rows = payload.get("lanes") if isinstance(payload, dict) else []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("name") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def has_note_fragment(notes: str, fragment: str) -> bool:
    return fragment in notes


def classify_live_lane(row: dict[str, Any]) -> dict[str, str]:
    notes = str(row.get("notes") or "")
    status = str(row.get("status") or "")
    runner_status = str(row.get("runner_status") or "")
    enabled = bool(row.get("enabled", True))
    pause_note = str(row.get("pause_note") or "")
    close_count = to_int(row.get("close_count"))
    managed_open_count = to_int(row.get("managed_open_count"))

    if not enabled or pause_note:
        return {
            "evidence_basis": "decommissioned_or_parked",
            "operator_posture": "leave_paused",
            "evidence_rationale": "registry truth says this live id is paused, parked, or decommissioned",
        }
    if runner_status == "live_contract_friction_invalid" or has_note_fragment(notes, "runner_status=live_contract_friction_invalid"):
        return {
            "evidence_basis": "contract_invalid_live",
            "operator_posture": "fix_contract_before_recycle",
            "evidence_rationale": "runner is live but current contract is not venue-admissible under observed spread friction",
        }
    if (
        runner_status == "positive_only_hold_active"
        and managed_open_count >= 5
        and abs(to_float(row.get("fresh_session_booked_usd"))) <= 1e-9
    ):
        return {
            "evidence_basis": "trapped_hold_live",
            "operator_posture": "manual_review_or_release_capital",
            "evidence_rationale": "runner is in positive-only hold with meaningful managed inventory and no fresh monetization, so the lane is capital-trapped rather than merely waiting",
        }
    if runner_status == "positive_only_hold_active" or has_note_fragment(notes, "runner_status=positive_only_hold_active"):
        return {
            "evidence_basis": "intentional_hold_live",
            "operator_posture": "wait_profitable_unwind",
            "evidence_rationale": "runner intentionally stopped adding risk and is waiting for profitable exits instead of forcing a red unwind",
        }
    if status != "ok":
        return {
            "evidence_basis": "runtime_attention",
            "operator_posture": "repair_runtime_first",
            "evidence_rationale": "lane is not currently runtime-healthy",
        }
    if has_note_fragment(notes, "fx_grad=live progress=graduated"):
        return {
            "evidence_basis": "graduated_live_reference",
            "operator_posture": "keep_live_reference",
            "evidence_rationale": "explicit graduation note marks this lane as the current live reference",
        }
    if has_note_fragment(notes, "pre_start_state_carry="):
        return {
            "evidence_basis": "carry_weighted_live",
            "operator_posture": "require_fresh_forward_sample",
            "evidence_rationale": "net/closes still include pre-start carry and should not be treated as fresh forward proof by themselves",
        }
    if has_note_fragment(notes, "broker_sync_inherited_closes="):
        return {
            "evidence_basis": "inherited_history_live",
            "operator_posture": "separate_fresh_vs_inherited",
            "evidence_rationale": "broker-sync inherited closes are present, so cumulative PnL is not a clean fresh-run read yet",
        }
    if close_count <= 0:
        return {
            "evidence_basis": "thin_live_sample",
            "operator_posture": "wait_more_sample",
            "evidence_rationale": "lane is live, but it has not booked a clean fresh close yet, so the current sample is still too thin to promote confidently",
        }
    return {
        "evidence_basis": "fresh_forward_live",
        "operator_posture": "review_for_scale",
        "evidence_rationale": "lane has live closes without inherited-carry hints in notes",
    }


def displayed_close_count(row: dict[str, Any]) -> int:
    close_count = to_int(row.get("close_count"))
    runner_session_trade_closes = to_int(row.get("runner_session_trade_closes"))
    return max(close_count, runner_session_trade_closes)


def normalize_parked_lane_row(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("evidence_basis") != "decommissioned_or_parked":
        return row
    row["status"] = "paused"
    row["pids"] = []
    row["started_at"] = ""
    row["managed_open_count"] = 0
    row["fresh_session_booked_usd"] = 0.0
    row["fresh_session_usd_per_hour"] = 0.0
    row["runner_status"] = ""
    row["notes"] = ""
    row["display_close_count"] = displayed_close_count(row)
    return row


def build_live_lane_rows(
    watchdog_rows: dict[str, dict[str, Any]],
    execution_rows: dict[str, dict[str, Any]],
    registry_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now_dt = utc_now()
    for lane, watchdog in watchdog_rows.items():
        kind = str(watchdog.get("kind") or "")
        if not kind.startswith("live_"):
            continue
        registry = registry_rows.get(lane, {})
        execution = execution_rows.get(lane, {})
        scoreboard = watchdog.get("scoreboard_total") if isinstance(watchdog.get("scoreboard_total"), dict) else {}
        runner = watchdog.get("runner") if isinstance(watchdog.get("runner"), dict) else {}
        live_pid = current_runner_pid(watchdog)
        pids = [to_int(pid) for pid in (watchdog.get("process_ids") or []) if to_int(pid) > 0]
        if live_pid > 0:
            pids = [live_pid]
        row = {
            "lane": lane,
            "kind": kind,
            "enabled": bool(registry.get("enabled", True)),
            "pause_note": str(registry.get("pause_note") or ""),
            "status": str(watchdog.get("status") or ""),
            "pids": pids,
            "heartbeat_age_seconds": round(to_float(watchdog.get("heartbeat_age_seconds")), 1),
            "heartbeat_at": str(watchdog.get("heartbeat_at") or ""),
            "started_at": str(runner.get("started_at") or ""),
            "broker_open_count": to_int(
                execution.get("broker_magic_open_count"),
                to_int(watchdog.get("open_count")),
            ),
            "managed_open_count": to_int(execution.get("open_count")),
            "outside_scope_open_count": to_int(execution.get("broker_outside_scope_open_count")),
            "close_count": to_int(execution.get("close_count")),
            "runner_session_trade_closes": to_int(execution.get("runner_session_trade_closes")),
            "booked_usd": prefer_monitor_realized(to_float(scoreboard.get("realized_usd")), execution),
            "floating_usd": to_float(scoreboard.get("floating_usd")),
            "net_usd": to_float(scoreboard.get("net_usd")),
            "fresh_session_booked_usd": to_float(execution.get("runner_session_trade_realized_usd")),
            "fresh_session_usd_per_hour": session_usd_per_hour(
                runner.get("started_at"),
                execution.get("runner_session_trade_realized_usd"),
                now_dt=now_dt,
            ),
            "runner_status": str(execution.get("runner_status") or ""),
            "notes": str(execution.get("notes") or ""),
        }
        row["broker_net_usd"] = row["booked_usd"]
        row["display_close_count"] = displayed_close_count(row)
        row.update(classify_live_lane(row))
        normalize_parked_lane_row(row)
        rows.append(row)
    rows.sort(key=lambda row: (row["kind"], row["lane"]))
    return rows


def build_payload() -> dict[str, Any]:
    watchdog_paths = watchdog_report_paths()
    watchdog_rows = merged_watchdog_rows(watchdog_paths)
    execution_rows = execution_rows_by_lane(EXECUTION_MONITOR_JSON)
    registry_rows = registry_rows_by_lane(RUNNER_REGISTRY_JSON)
    rows = build_live_lane_rows(watchdog_rows, execution_rows, registry_rows)
    summary = {
        "total_live_lanes": len(rows),
        "ok_lanes": sum(1 for row in rows if row["status"] == "ok"),
        "non_ok_lanes": sum(1 for row in rows if row["status"] != "ok"),
        "graduated_live_reference_count": sum(1 for row in rows if row["evidence_basis"] == "graduated_live_reference"),
        "trapped_hold_live_count": sum(1 for row in rows if row["evidence_basis"] == "trapped_hold_live"),
        "intentional_hold_live_count": sum(1 for row in rows if row["evidence_basis"] == "intentional_hold_live"),
        "contract_invalid_live_count": sum(1 for row in rows if row["evidence_basis"] == "contract_invalid_live"),
        "carry_weighted_live_count": sum(1 for row in rows if row["evidence_basis"] == "carry_weighted_live"),
        "inherited_history_live_count": sum(1 for row in rows if row["evidence_basis"] == "inherited_history_live"),
        "thin_live_sample_count": sum(1 for row in rows if row["evidence_basis"] == "thin_live_sample"),
        "fresh_forward_live_count": sum(1 for row in rows if row["evidence_basis"] == "fresh_forward_live"),
        "decommissioned_or_parked_count": sum(1 for row in rows if row["evidence_basis"] == "decommissioned_or_parked"),
    }
    return {
        "generated_at": utc_now_iso(),
        "sources": [str(path.relative_to(ROOT)) for path in watchdog_paths] + [str(EXECUTION_MONITOR_JSON.relative_to(ROOT))],
        "summary": summary,
        "rows": rows,
    }


def markdown_from_payload(payload: dict[str, Any]) -> str:
    lines = [
        "# Live Lane Dashboard",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Scope: current live lattice lanes only; sourced from watchdog + execution monitor truth.",
        (
            "- Summary: "
            f"`total={payload.get('summary', {}).get('total_live_lanes', 0)}` "
            f"`ok={payload.get('summary', {}).get('ok_lanes', 0)}` "
            f"`non_ok={payload.get('summary', {}).get('non_ok_lanes', 0)}` "
            f"`graduated={payload.get('summary', {}).get('graduated_live_reference_count', 0)}` "
            f"`trapped={payload.get('summary', {}).get('trapped_hold_live_count', 0)}` "
            f"`hold={payload.get('summary', {}).get('intentional_hold_live_count', 0)}` "
            f"`invalid={payload.get('summary', {}).get('contract_invalid_live_count', 0)}` "
            f"`carry_weighted={payload.get('summary', {}).get('carry_weighted_live_count', 0)}` "
            f"`thin={payload.get('summary', {}).get('thin_live_sample_count', 0)}` "
            f"`parked={payload.get('summary', {}).get('decommissioned_or_parked_count', 0)}`"
        ),
        "",
        "| Lane | Kind | Status | Evidence Basis | Operator Posture | PIDs | Heartbeat Age (s) | Broker Open | Managed Open | Outside Scope | Closes | Fresh Booked USD | Fresh $/hr | Booked USD | Floating USD | Net USD | Notes |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload.get("rows", []):
        display_close_count = row.get("display_close_count", row.get("close_count", 0))
        lines.append(
            f"| {row['lane']} | {row['kind']} | {row['status']} | {row['evidence_basis']} | {row['operator_posture']} | "
            f"{', '.join(str(pid) for pid in row['pids']) or '-'} | "
            f"{row['heartbeat_age_seconds']:.1f} | {row['broker_open_count']} | {row['managed_open_count']} | {row['outside_scope_open_count']} | {display_close_count} | "
            f"{row['fresh_session_booked_usd']:+.2f} | {row['fresh_session_usd_per_hour']:+.2f} | {row['booked_usd']:+.2f} | {row['floating_usd']:+.2f} | {row['net_usd']:+.2f} | {row['notes'] or row['pause_note'] or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `graduated_live_reference` means the lane carries an explicit live-graduation note and is the current live reference rather than a new promotion candidate.",
            "- `trapped_hold_live` means the runner is in positive-only hold with meaningful managed inventory and no fresh monetization, so capital is trapped until the room chooses a release path.",
            "- `intentional_hold_live` means the runner has deliberately stopped adding risk and is waiting for profitable unwind rather than forcing red exits.",
            "- `contract_invalid_live` means the runner is heartbeat-healthy but the current live contract is not admissible under observed venue friction; fix contract or gate before recycling.",
            "- `carry_weighted_live` means cumulative PnL still includes `pre_start_state_carry=...`; do not generalize from those totals as if they were fresh forward proof.",
            "- `inherited_history_live` means broker-sync inherited closes are present without the stronger pre-start carry hint; separate fresh session results from inherited history before retuning.",
            "- `thin_live_sample` means the lane is running but still has zero fresh closes, so treat it as an active probe instead of a proven winner.",
            "- `decommissioned_or_parked` means the registry already says that live id is paused or retired; do not treat it as a repair target just because its watchdog status is non-`ok`.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUT_MD.write_text(markdown_from_payload(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Current live lattice lane dashboard")
    parser.add_argument("--json", action="store_true", help="Print JSON payload instead of Markdown")
    args = parser.parse_args()

    payload = build_payload()
    write_outputs(payload)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(markdown_from_payload(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
