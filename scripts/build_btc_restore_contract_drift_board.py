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
INCIDENT_PATH = REPORTS / "btc_restore_supervision_incident_board.json"
REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"

OUTPUT_JSON = REPORTS / "btc_restore_contract_drift_board.json"
OUTPUT_MD = REPORTS / "btc_restore_contract_drift_board.md"

LANE = "shadow_btcusd_m15_warp_restore_v1"
STATE_PATH_DEFAULT = REPORTS / "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json"

PATH_FLAGS = {"--state-path", "--event-path", "--config", "--shared-price-path", "--trade-window-json"}
BOOL_FLAGS = {"--fresh-start", "--direct-live", "--raw-rearm-momentum-gate"}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def age_seconds(value: str | None) -> float | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def normalize_value(flag: str, value: str) -> str:
    text = str(value or "").strip().strip("\"'")
    if flag in PATH_FLAGS:
        return text.replace("\\", "/").lower()
    try:
        return f"{float(text):g}"
    except Exception:
        return text.lower()


def parse_flag_parts(parts: list[str]) -> tuple[dict[str, str], set[str]]:
    values: dict[str, str] = {}
    booleans: set[str] = set()
    idx = 0
    while idx < len(parts):
        token = str(parts[idx] or "")
        if not token.startswith("--"):
            idx += 1
            continue
        if token in BOOL_FLAGS:
            booleans.add(token)
            idx += 1
            continue
        next_idx = idx + 1
        if next_idx < len(parts) and not str(parts[next_idx] or "").startswith("--"):
            values[token] = normalize_value(token, str(parts[next_idx]))
            idx += 2
            continue
        booleans.add(token)
        idx += 1
    return values, booleans


def find_restore_row(payload: dict[str, Any]) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("packet_id") or "") == "btc_restore_comparison_shadow":
            return dict(row)
    return {}


def find_registry_row(payload: dict[str, Any]) -> dict[str, Any]:
    for row in list(payload.get("lanes") or []):
        if str(row.get("name") or "") == LANE:
            return dict(row)
    return {}


def resolve_state_path(restore_row: dict[str, Any]) -> Path:
    for command in list(restore_row.get("command") or []):
        pass
    cmd = [str(part) for part in list(restore_row.get("command") or [])]
    for idx, token in enumerate(cmd):
        if token == "--state-path" and idx + 1 < len(cmd):
            candidate = Path(cmd[idx + 1])
            return candidate if candidate.is_absolute() else ROOT / candidate
    return STATE_PATH_DEFAULT


def compare_contracts(packet_cmd: list[str], registry_args: list[str]) -> dict[str, Any]:
    packet_values, packet_bools = parse_flag_parts([str(part) for part in packet_cmd])
    registry_values, registry_bools = parse_flag_parts([str(part) for part in registry_args])
    mismatches: list[str] = []

    keys = sorted(set(packet_values) | set(registry_values))
    for key in keys:
        if packet_values.get(key) != registry_values.get(key):
            mismatches.append(
                f"value:{key}:packet={packet_values.get(key, '<missing>')}:registry={registry_values.get(key, '<missing>')}"
            )
    for key in sorted(set(packet_bools) | set(registry_bools)):
        if (key in packet_bools) != (key in registry_bools):
            mismatches.append(
                f"bool:{key}:packet={'true' if key in packet_bools else 'false'}:registry={'true' if key in registry_bools else 'false'}"
            )

    return {
        "packet_values": packet_values,
        "packet_bools": sorted(packet_bools),
        "registry_values": registry_values,
        "registry_bools": sorted(registry_bools),
        "mismatches": mismatches,
        "verdict": "aligned" if not mismatches else "packet_registry_drift",
    }


def inspect_artifact_state(state_path: Path, expected_values: dict[str, str], expected_bools: set[str]) -> dict[str, Any]:
    payload = load_json(state_path)
    metadata = dict(payload.get("metadata") or {})
    runner = dict(payload.get("runner") or {})
    sources = {
        "latest_tick_source_last": str(runner.get("latest_tick_source_last") or ""),
        "latest_tick_append_source_last": str(runner.get("latest_tick_append_source_last") or ""),
        "tick_history_source_last": str(runner.get("tick_history_source_last") or ""),
    }
    observed_shared_age_ms = int(metadata.get("shared_price_max_age_ms") or 0)
    expected_shared_age_ms = int(float(expected_values.get("--shared-price-max-age-ms", "0") or 0.0))
    expected_direct_live = "--direct-live" in expected_bools
    observed_direct_live = bool(metadata.get("direct_live"))
    drift_issues: list[str] = []

    if observed_shared_age_ms != expected_shared_age_ms:
        drift_issues.append(
            f"metadata_shared_price_max_age_ms expected={expected_shared_age_ms} observed={observed_shared_age_ms}"
        )
    if observed_direct_live != expected_direct_live:
        drift_issues.append(
            f"metadata_direct_live expected={str(expected_direct_live).lower()} observed={str(observed_direct_live).lower()}"
        )
    if expected_shared_age_ms == 0:
        for key, value in sources.items():
            if value.startswith("shared_"):
                drift_issues.append(f"{key}={value}")

    if not payload:
        verdict = "artifact_missing"
    elif drift_issues:
        verdict = "artifact_residue_mismatch"
    else:
        verdict = "artifact_matches_checked_in_contract"

    return {
        "state_path": str(state_path.relative_to(ROOT) if state_path.is_absolute() and state_path.is_relative_to(ROOT) else state_path),
        "exists": bool(payload),
        "metadata_shared_price_max_age_ms": observed_shared_age_ms,
        "metadata_direct_live": observed_direct_live,
        "runner_started_at": str(runner.get("started_at") or ""),
        "runner_heartbeat_at": str(runner.get("heartbeat_at") or ""),
        "runner_heartbeat_age_seconds": age_seconds(str(runner.get("heartbeat_at") or "")),
        **sources,
        "drift_issues": drift_issues,
        "verdict": verdict,
    }


def build_payload() -> dict[str, Any]:
    overnight = load_json(OVERNIGHT_PACKET_PATH)
    incident = load_json(INCIDENT_PATH)
    registry = load_json(REGISTRY_PATH)

    restore_row = find_restore_row(overnight)
    registry_row = find_registry_row(registry)
    summary = dict(incident.get("summary") or {})

    packet_cmd = [str(part) for part in list(restore_row.get("command") or [])]
    registry_args = [str(part) for part in list(registry_row.get("restart_args") or [])]
    contract_comparison = compare_contracts(packet_cmd, registry_args)
    state_path = resolve_state_path(restore_row)
    artifact_state = inspect_artifact_state(
        state_path,
        expected_values=dict(contract_comparison.get("registry_values") or {}),
        expected_bools=set(contract_comparison.get("registry_bools") or []),
    )
    overnight_action_status = str(restore_row.get("action_status") or "")
    quarantined_until = str(summary.get("quarantined_until") or "")
    quarantine_dt = parse_iso(quarantined_until)
    quarantine_active = bool(quarantine_dt and quarantine_dt > datetime.now(timezone.utc))

    if overnight_action_status == "already_running_monitor_only" and artifact_state["verdict"] == "artifact_matches_checked_in_contract":
        relaunch_gate = "current_contract_running_monitor_only"
    elif quarantine_active and artifact_state["verdict"] == "artifact_residue_mismatch":
        relaunch_gate = "wait_for_quarantine_then_clean_relaunch_on_current_contract"
    elif quarantine_active:
        relaunch_gate = "wait_for_quarantine_expiry"
    elif artifact_state["verdict"] == "artifact_residue_mismatch":
        relaunch_gate = "clean_relaunch_on_current_contract"
    elif contract_comparison["verdict"] != "aligned":
        relaunch_gate = "repair_checked_in_contract_first"
    else:
        relaunch_gate = "checked_in_contract_clean_waiting_runtime_relaunch"

    if artifact_state["verdict"] == "artifact_matches_checked_in_contract":
        artifact_read = (
            "The current restore artifact matches the checked-in contract: state metadata shows "
            f"`shared_price_max_age_ms={artifact_state.get('metadata_shared_price_max_age_ms')}` and latest-tick source "
            f"`{artifact_state.get('latest_tick_source_last') or '-'}`."
        )
    else:
        artifact_read = (
            "The stale artifact is different: current state metadata still shows "
            f"`shared_price_max_age_ms={artifact_state.get('metadata_shared_price_max_age_ms')}` and latest-tick source "
            f"`{artifact_state.get('latest_tick_source_last') or '-'}`."
        )

    if quarantine_active:
        runtime_read = (
            f"Runtime fence remains active: overnight status is `{overnight_action_status}` and incident quarantine is "
            f"`{summary.get('quarantine_reason') or ''}` until `{quarantined_until}`."
        )
    elif quarantined_until:
        runtime_read = (
            f"Incident quarantine memory is expired: overnight status is `{overnight_action_status}`, last recorded quarantine was "
            f"`{summary.get('quarantine_reason') or ''}` until `{quarantined_until}`."
        )
    else:
        runtime_read = (
            f"Current runtime posture is `{overnight_action_status}` with no active incident quarantine recorded on the current snapshot."
        )

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(OVERNIGHT_PACKET_PATH.relative_to(ROOT)),
            str(INCIDENT_PATH.relative_to(ROOT)),
            str(REGISTRY_PATH.relative_to(ROOT)),
            str(state_path.relative_to(ROOT) if state_path.is_absolute() and state_path.is_relative_to(ROOT) else state_path),
        ],
        "summary": {
            "lane": LANE,
            "overnight_action_status": overnight_action_status,
            "incident_quarantine_reason": str(summary.get("quarantine_reason") or ""),
            "incident_quarantined_until": quarantined_until,
            "packet_registry_contract_verdict": str(contract_comparison.get("verdict") or ""),
            "artifact_contract_verdict": str(artifact_state.get("verdict") or ""),
            "artifact_shared_price_max_age_ms": int(artifact_state.get("metadata_shared_price_max_age_ms") or 0),
            "artifact_latest_tick_source_last": str(artifact_state.get("latest_tick_source_last") or ""),
            "relaunch_gate": relaunch_gate,
        },
        "leadership_read": [
            (
                "The checked-in restore contract is not the main drift surface: packet command and registry restart args are "
                f"`{contract_comparison.get('verdict')}`."
            ),
            artifact_read,
            (
                f"That means the current contract posture is `{relaunch_gate}`, not a paper mismatch between operator packet and checked-in registry."
            ),
            runtime_read,
        ],
        "contract_comparison": contract_comparison,
        "artifact_state": artifact_state,
        "notes": [
            "This board is passive. It does not restart or clean the BTC restore lane.",
            "Use it to distinguish checked-in contract drift from stale runtime residue before the next supervised relaunch.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    comparison = dict(payload.get("contract_comparison") or {})
    artifact = dict(payload.get("artifact_state") or {})
    lines = [
        "# BTC Restore Contract Drift Board",
        "",
        "This board answers whether the current BTC restore blocker lives in the checked-in contract, the stale artifact residue, or both.",
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
            f"- overnight_action_status: `{summary.get('overnight_action_status')}`",
            f"- incident_quarantine_reason: `{summary.get('incident_quarantine_reason')}`",
            f"- incident_quarantined_until: `{summary.get('incident_quarantined_until')}`",
            f"- packet_registry_contract_verdict: `{summary.get('packet_registry_contract_verdict')}`",
            f"- artifact_contract_verdict: `{summary.get('artifact_contract_verdict')}`",
            f"- artifact_shared_price_max_age_ms: `{summary.get('artifact_shared_price_max_age_ms')}`",
            f"- artifact_latest_tick_source_last: `{summary.get('artifact_latest_tick_source_last')}`",
            f"- relaunch_gate: `{summary.get('relaunch_gate')}`",
            "",
            "## Packet vs Registry",
            "",
            f"- packet_bools: `{comparison.get('packet_bools')}`",
            f"- registry_bools: `{comparison.get('registry_bools')}`",
            f"- packet_values: `{comparison.get('packet_values')}`",
            f"- registry_values: `{comparison.get('registry_values')}`",
            f"- mismatches: `{comparison.get('mismatches')}`",
            "",
            "## Artifact State",
            "",
            f"- state_path: `{artifact.get('state_path')}`",
            f"- exists: `{artifact.get('exists')}`",
            f"- metadata_shared_price_max_age_ms: `{artifact.get('metadata_shared_price_max_age_ms')}`",
            f"- metadata_direct_live: `{artifact.get('metadata_direct_live')}`",
            f"- runner_started_at: `{artifact.get('runner_started_at')}`",
            f"- runner_heartbeat_at: `{artifact.get('runner_heartbeat_at')}`",
            f"- latest_tick_source_last: `{artifact.get('latest_tick_source_last')}`",
            f"- latest_tick_append_source_last: `{artifact.get('latest_tick_append_source_last')}`",
            f"- tick_history_source_last: `{artifact.get('tick_history_source_last')}`",
            f"- drift_issues: `{artifact.get('drift_issues')}`",
            "",
            "## Notes",
            "",
        ]
    )
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
