#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
WATCHDOG = REPORTS / "watchdog"
CONFIGS = ROOT / "configs"

OVERNIGHT_PACKET_PATH = REPORTS / "adaptive_overnight_launch_packet_board.json"
ACCEPTANCE_PATH = REPORTS / "adaptive_harness_acceptance_verdict_board.json"
EXECUTION_PATH = REPORTS / "execution_monitor_report.json"
CRYPTO_WATCHDOG_EVENTS_PATH = WATCHDOG / "crypto_watchdog_events.jsonl"
CRYPTO_WATCHDOG_QUARANTINE_PATH = WATCHDOG / "crypto_watchdog_quarantine_state.json"
CRYPTO_WATCHDOG_LOOP_STATE_PATH = WATCHDOG / "crypto_watchdog_loop_state.json"
REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"

OUTPUT_JSON = REPORTS / "btc_restore_supervision_incident_board.json"
OUTPUT_MD = REPORTS / "btc_restore_supervision_incident_board.md"

LANE = "shadow_btcusd_m15_warp_restore_v1"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_restore_row(payload: dict[str, Any]) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("packet_id") or "") == "btc_restore_comparison_shadow":
            return dict(row)
    return {}


def find_acceptance_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    for row in list(payload.get("candidates") or []):
        if str(row.get("candidate_id") or "") == "btc_restore_comparison_shadow":
            return dict(row)
    return {}


def find_execution_row(payload: dict[str, Any]) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("lane") or "") == LANE:
            return dict(row)
    return {}


def find_registry_row(payload: dict[str, Any]) -> dict[str, Any]:
    for row in list(payload.get("lanes") or []):
        if str(row.get("name") or "") == LANE:
            return dict(row)
    return {}


def find_quarantine_entry(payload: dict[str, Any]) -> dict[str, Any]:
    lanes = payload.get("lanes")
    if isinstance(lanes, dict):
        entry = lanes.get(LANE)
        if isinstance(entry, dict):
            return dict(entry)
    entry = payload.get(LANE)
    return dict(entry) if isinstance(entry, dict) else {}


def build_chronology(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("lane") or event.get("lane_name") or "") != LANE and LANE not in json.dumps(event):
            continue
        action = str(event.get("action") or "")
        if action not in {"watchdog_cleanup", "watchdog_restart", "watchdog_quarantine", "watchdog_startup"}:
            continue
        if action == "watchdog_startup" and str(event.get("event") or "") != "run_watchdog_summary_exit":
            continue
        rows.append(
            {
                "ts_utc": str(event.get("ts_utc") or ""),
                "action": action,
                "status": str(event.get("status") or event.get("prior_status") or ""),
                "reason": str(event.get("reason") or ""),
                "started_pid": int(event.get("started_pid") or 0),
                "stopped_pids": [int(pid) for pid in list(event.get("stopped_pids") or [])],
                "prior_pids": [int(pid) for pid in list(event.get("prior_pids") or [])],
                "prior_reasons": [str(item) for item in list(event.get("prior_reasons") or [])],
                "quarantined_until": str(event.get("quarantined_until") or ""),
                "restart_count_window": int(event.get("restart_count_window") or 0),
            }
        )
    return rows


def build_payload() -> dict[str, Any]:
    overnight = load_json(OVERNIGHT_PACKET_PATH)
    acceptance = load_json(ACCEPTANCE_PATH)
    execution = load_json(EXECUTION_PATH)
    registry = load_json(REGISTRY_PATH)
    quarantine = load_json(CRYPTO_WATCHDOG_QUARANTINE_PATH)
    loop_state = load_json(CRYPTO_WATCHDOG_LOOP_STATE_PATH)
    watchdog_events = load_jsonl(CRYPTO_WATCHDOG_EVENTS_PATH)

    restore_row = find_restore_row(overnight)
    acceptance_row = find_acceptance_candidate(acceptance)
    execution_row = find_execution_row(execution)
    registry_row = find_registry_row(registry)
    quarantine_entry = find_quarantine_entry(quarantine)
    chronology = build_chronology(watchdog_events)
    current_watchdog_lanes = [str(item) for item in list(loop_state.get("lanes") or [])]

    stale_exit_count = sum(1 for row in chronology if row["action"] == "watchdog_startup" and row["status"] == "stale_recurrence")
    restart_count = sum(1 for row in chronology if row["action"] == "watchdog_restart")
    cleanup_count = sum(1 for row in chronology if row["action"] == "watchdog_cleanup")
    quarantine_count = sum(1 for row in chronology if row["action"] == "watchdog_quarantine")
    in_watchdog_set = LANE in current_watchdog_lanes
    runtime_set_phrase = "inside" if in_watchdog_set else "outside"
    runtime_quarantine_read = (
        f"then quarantine `{quarantine_entry.get('reason', '')}` until `{quarantine_entry.get('quarantined_until', '')}`."
        if quarantine_entry
        else "with the historical quarantine now cleared."
    )

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(OVERNIGHT_PACKET_PATH.relative_to(ROOT)),
            str(ACCEPTANCE_PATH.relative_to(ROOT)),
            str(EXECUTION_PATH.relative_to(ROOT)),
            str(CRYPTO_WATCHDOG_EVENTS_PATH.relative_to(ROOT)),
            str(CRYPTO_WATCHDOG_QUARANTINE_PATH.relative_to(ROOT)),
            str(CRYPTO_WATCHDOG_LOOP_STATE_PATH.relative_to(ROOT)),
            str(REGISTRY_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "lane": LANE,
            "acceptance_verdict": str(acceptance_row.get("verdict") or ""),
            "acceptance_queue_status": str(acceptance_row.get("queue_status") or ""),
            "overnight_action_status": str(restore_row.get("action_status") or ""),
            "registry_enabled": bool(registry_row.get("enabled", True)) if registry_row else False,
            "registry_pause_note": str(registry_row.get("pause_note") or ""),
            "currently_in_crypto_watchdog_lane_set": in_watchdog_set,
            "quarantine_reason": str(quarantine_entry.get("reason") or ""),
            "quarantined_until": str(quarantine_entry.get("quarantined_until") or ""),
            "restart_count_window": int(quarantine_entry.get("restart_count_window") or 0),
            "cleanup_count": cleanup_count,
            "restart_count": restart_count,
            "stale_exit_count": stale_exit_count,
            "quarantine_count": quarantine_count,
            "pre_start_state_carry_closes": int(execution_row.get("pre_start_state_carry_closes") or 0),
            "pre_start_state_carry_realized_usd": float(execution_row.get("pre_start_state_carry_realized_usd") or 0.0),
            "current_run_trade_opens": int(restore_row.get("artifact_trade_opens") or 0),
            "current_run_trade_closes": int(restore_row.get("artifact_trade_closes") or 0),
            "first_path_verdict": str(restore_row.get("first_path_verdict") or ""),
        },
        "leadership_read": [
            (
                f"Doctrine still endorses `{LANE}` as the top non-rejected BTC control branch "
                f"(`verdict={acceptance_row.get('verdict', '')}`, `queue_status={acceptance_row.get('queue_status', '')}`)."
            ),
            (
                f"Runtime truth does not: the lane is currently `{restore_row.get('action_status', '')}`, "
                f"registry-enabled=`{bool(registry_row.get('enabled', True)) if registry_row else False}`, "
                f"and {runtime_set_phrase} the current `crypto_watchdog` lane set."
            ),
            (
                f"The failure mode was not a Python traceback but a repeated source-tick stale-recurrence loop: "
                f"`cleanup={cleanup_count}`, `restart={restart_count}`, `stale_exit={stale_exit_count}`, "
                f"{runtime_quarantine_read}"
            ),
            (
                f"Current file residue is not fresh proof: execution still carries pre-start state carry "
                f"`{execution_row.get('pre_start_state_carry_closes', 0)} closes / "
                f"{execution_row.get('pre_start_state_carry_realized_usd', 0.0)}` and the overnight packet reports "
                f"`first_path={restore_row.get('first_path_verdict', '')}` with current-run trades "
                f"`{restore_row.get('artifact_trade_opens', 0)}/{restore_row.get('artifact_trade_closes', 0)}`."
            ),
        ],
        "incident_facts": {
            "acceptance_candidate": acceptance_row,
            "overnight_packet_row": restore_row,
            "execution_row": {
                key: execution_row.get(key)
                for key in [
                    "watchdog_status",
                    "clean_forward_reset_at",
                    "clean_forward_source",
                    "heartbeat_at",
                    "event_last_write_at",
                    "state_last_write_at",
                    "notes",
                    "pre_start_state_carry_closes",
                    "pre_start_state_carry_realized_usd",
                ]
            },
            "registry_row": registry_row,
            "quarantine_entry": quarantine_entry,
            "crypto_watchdog_loop_membership": {
                "currently_in_lane_set": LANE in current_watchdog_lanes,
                "current_lane_count": len(current_watchdog_lanes),
                "loop_updated_at": str(loop_state.get("updated_at") or ""),
                "loop_status_counts": dict(loop_state.get("status_counts") or {}),
            },
        },
        "chronology": chronology,
        "notes": [
            "This board is passive evidence compression for the BTC restore supervision incident.",
            "Use it to support runtime repair decisions; it does not recommend specific watchdog code changes by itself.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# BTC Restore Supervision Incident Board",
        "",
        "This board compresses the current doctrine/runtime split and the watchdog chronology for the BTC restore-comparison lane.",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- lane: `{summary.get('lane')}`",
            f"- acceptance_verdict: `{summary.get('acceptance_verdict')}`",
            f"- acceptance_queue_status: `{summary.get('acceptance_queue_status')}`",
            f"- overnight_action_status: `{summary.get('overnight_action_status')}`",
            f"- registry_enabled: `{summary.get('registry_enabled')}`",
            f"- registry_pause_note: `{summary.get('registry_pause_note')}`",
            f"- currently_in_crypto_watchdog_lane_set: `{summary.get('currently_in_crypto_watchdog_lane_set')}`",
            f"- quarantine_reason: `{summary.get('quarantine_reason')}`",
            f"- quarantined_until: `{summary.get('quarantined_until')}`",
            f"- restart_count_window: `{summary.get('restart_count_window')}`",
            f"- cleanup_count: `{summary.get('cleanup_count')}`",
            f"- restart_count: `{summary.get('restart_count')}`",
            f"- stale_exit_count: `{summary.get('stale_exit_count')}`",
            f"- quarantine_count: `{summary.get('quarantine_count')}`",
            f"- pre_start_state_carry_closes: `{summary.get('pre_start_state_carry_closes')}`",
            f"- pre_start_state_carry_realized_usd: `{summary.get('pre_start_state_carry_realized_usd')}`",
            f"- current_run_trade_opens: `{summary.get('current_run_trade_opens')}`",
            f"- current_run_trade_closes: `{summary.get('current_run_trade_closes')}`",
            f"- first_path_verdict: `{summary.get('first_path_verdict')}`",
            "",
            "## Chronology",
            "",
            "| ts_utc | action | status | reason | started_pid | quarantine |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in list(payload.get("chronology") or []):
        lines.append(
            f"| `{row.get('ts_utc','')}` | `{row.get('action','')}` | `{row.get('status','')}` | "
            f"{', '.join(row.get('prior_reasons') or []) or row.get('reason','')} | "
            f"`{row.get('started_pid',0)}` | `{row.get('quarantined_until','')}` |"
        )
    lines.extend(["", "## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
