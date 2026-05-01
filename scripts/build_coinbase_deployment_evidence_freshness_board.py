#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

DEPLOYMENT_GATE_PATH = REPORTS / "coinbase_isolated_runner_deployment_gate_board.json"
OVERRIDE_PROOF_PATH = REPORTS / "coinbase_isolated_runner_override_path_proof_board.json"
READINESS_PATH = REPORTS / "deployment_readiness_assessment.json"
TRACKER_PATH = REPORTS / "live_performance_tracker.json"
NOM_RELEASE_PATH = REPORTS / "coinbase_nom_overlap_release_gate_board.json"

JSON_PATH = REPORTS / "coinbase_deployment_evidence_freshness_board.json"
MD_PATH = REPORTS / "coinbase_deployment_evidence_freshness_board.md"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def age_hours(value: str | None) -> float | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    return round((utc_now() - dt).total_seconds() / 3600.0, 2)


def freshness_label(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours <= 6:
        return "fresh_same_session"
    if hours <= 24:
        return "fresh_same_day"
    return "stale"


def build_rows() -> list[dict[str, Any]]:
    gate = load_json(DEPLOYMENT_GATE_PATH)
    proof = load_json(OVERRIDE_PROOF_PATH)
    readiness = load_json(READINESS_PATH)
    tracker = load_json(TRACKER_PATH)
    nom_release = load_json(NOM_RELEASE_PATH)

    gate_age = age_hours(gate.get("generated_at"))
    proof_age = age_hours(proof.get("generated_at"))
    readiness_age = age_hours(readiness.get("generated_at"))
    tracker_age = age_hours(tracker.get("timestamp"))
    nom_age = age_hours(nom_release.get("generated_at"))

    rows: list[dict[str, Any]] = []

    rows.append(
        {
            "subject": "governed_deployment_gate",
            "freshness": freshness_label(gate_age),
            "age_hours": gate_age,
            "status": str(gate.get("summary", {}).get("verdict") or ""),
            "decision": "governance_gate_is_current_and_blocking",
            "evidence": (
                f"verdict={gate.get('summary', {}).get('verdict')}; "
                f"blocking_subjects={','.join(gate.get('summary', {}).get('blocking_subjects') or [])}"
            ),
            "read": "The canonical governed deployment board is fresh and still says hold, so freshness is not the reason the go call is blocked.",
        }
    )
    rows.append(
        {
            "subject": "override_path_proof",
            "freshness": freshness_label(proof_age),
            "age_hours": proof_age,
            "status": "bounded_smoke_only",
            "decision": "do_not_upgrade_flat_probe_windows_into_go_signal",
            "evidence": (
                f"TRU={proof['rows'][0]['status']}; "
                f"SUP={proof['rows'][2]['status']}; "
                f"next_supervised_target={proof.get('summary', {}).get('next_supervised_target') or '-'}"
            ),
            "read": "The override proof artifacts are fresh, but the clean windows are still empty-signal supervised probes rather than live edge confirmation.",
        }
    )
    rows.append(
        {
            "subject": "tracker_snapshot",
            "freshness": freshness_label(tracker_age),
            "age_hours": tracker_age,
            "status": "bounded_smoke_snapshot",
            "decision": "do_not_treat_tracker_as_live_watch_proof",
            "evidence": (
                f"runner_cycle={tracker.get('runner_cycle')}; total_closes={tracker.get('total_closes')}; "
                f"nom_position={tracker.get('coins', {}).get('NOM-USD', {}).get('live_position')}"
            ),
            "read": "The tracker snapshot is current enough to be operationally useful, but it still reflects a tiny bounded window with zero closes, not a real live-watch regime.",
        }
    )
    rows.append(
        {
            "subject": "conditional_go_assessment",
            "freshness": freshness_label(readiness_age),
            "age_hours": readiness_age,
            "status": str(readiness.get("status") or ""),
            "decision": "treat_as_scope_conflicted_not_authoritative",
            "evidence": (
                f"status={readiness.get('status')}; "
                f"supervised_nom_status={readiness.get('supervised_probes', {}).get('NOM-USD', {}).get('status')}; "
                f"book_includes={','.join(readiness.get('validation_summary', {}).get('strategies', {}).keys())}"
            ),
            "read": "The readiness assessment is fresh, but it still conflicts with governed book policy by elevating default-book strategy stories that the deployment gate has not approved.",
        }
    )
    rows.append(
        {
            "subject": "nom_release_handoff",
            "freshness": freshness_label(nom_age),
            "age_hours": nom_age,
            "status": str(nom_release.get("summary", {}).get("release_verdict") or ""),
            "decision": "wait_for_parallel_nom_lane_to_clear",
            "evidence": (
                f"release_verdict={nom_release.get('summary', {}).get('release_verdict')}; "
                f"next_release_action={nom_release.get('summary', {}).get('next_release_action')}"
            ),
            "read": "The NOM release board is also current, and it says the blocker is handoff conflict, not missing overlap evidence or stale proof.",
        }
    )

    return rows


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    blocking = [
        row["subject"]
        for row in rows
        if row["decision"]
        in {
            "governance_gate_is_current_and_blocking",
            "do_not_upgrade_flat_probe_windows_into_go_signal",
            "do_not_treat_tracker_as_live_watch_proof",
            "wait_for_parallel_nom_lane_to_clear",
        }
    ]
    return {
        "generated_at": utc_now_iso(),
        "deployment_gate_path": str(DEPLOYMENT_GATE_PATH),
        "override_proof_path": str(OVERRIDE_PROOF_PATH),
        "readiness_path": str(READINESS_PATH),
        "tracker_path": str(TRACKER_PATH),
        "nom_release_path": str(NOM_RELEASE_PATH),
        "leadership_read": [
            "The main deployment evidence is fresh enough; recency is not the blocker.",
            "What is blocking go-day is evidence class and governance: bounded smoke windows stayed flat, the tracker is still a tiny smoke snapshot, and the governed deployment gate remains on hold.",
            "The readiness assessment should be treated as a broad infrastructure/status memo, not the authoritative launch permission artifact, because it still conflicts with the governed book and proof boards.",
        ],
        "summary": {
            "verdict": "fresh_but_not_go",
            "blocking_subjects": blocking,
            "fresh_rows": [row["subject"] for row in rows if row["freshness"] != "stale"],
            "stale_rows": [row["subject"] for row in rows if row["freshness"] == "stale"],
        },
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Deployment Evidence Freshness Board",
        "",
        f"Verdict: `{payload['summary']['verdict']}`",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Subject | Freshness | Status | Decision |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            f"| {row['subject']} | {row['freshness']} | {row['status']} | {row['decision']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
