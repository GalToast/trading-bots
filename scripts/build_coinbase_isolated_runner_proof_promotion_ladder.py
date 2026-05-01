#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

COMMAND_AUTHORITY_PATH = REPORTS / "coinbase_isolated_runner_command_authority_board.json"
DRY_PROBE_PATH = REPORTS / "coinbase_isolated_runner_exact_config_dry_probe.json"
OVERRIDE_PROOF_PATH = REPORTS / "coinbase_isolated_runner_override_path_proof_board.json"
DEPLOYMENT_GATE_PATH = REPORTS / "coinbase_isolated_runner_deployment_gate_board.json"
NOM_RELEASE_PATH = REPORTS / "coinbase_nom_overlap_release_gate_board.json"
TRACKER_PATH = REPORTS / "live_performance_tracker.json"

JSON_PATH = REPORTS / "coinbase_isolated_runner_proof_promotion_ladder.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_proof_promotion_ladder.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_stage_rules() -> list[dict[str, Any]]:
    return [
        {
            "stage": "blocked_or_deferred",
            "rank": 0,
            "promotion_rule": "cleanup or handoff blocker must clear before any further governed proof",
        },
        {
            "stage": "dry_probe_passed",
            "rank": 1,
            "promotion_rule": "clean exact-config dry probe allows bounded supervised probe, but not live-watch or deploy",
        },
        {
            "stage": "supervised_clean_waiting_for_signal",
            "rank": 2,
            "promotion_rule": "clean bounded supervised windows with zero signals/closes stay probe-only until a real signal window appears",
        },
        {
            "stage": "signal_window_captured",
            "rank": 3,
            "promotion_rule": "at least one governed supervised signal or close upgrades the lane into a live-watch candidate, not full deployment",
        },
        {
            "stage": "live_watch_candidate",
            "rank": 4,
            "promotion_rule": "active live-watch needs non-smoke tracker evidence and governed gate review before any deployable status",
        },
        {
            "stage": "deployable_lane",
            "rank": 5,
            "promotion_rule": "governed deployment gate must reopen and live-watch evidence must exceed bounded-smoke class",
        },
    ]


def build_lane_rows() -> list[dict[str, Any]]:
    command = load_json(COMMAND_AUTHORITY_PATH)
    dry_probe = load_json(DRY_PROBE_PATH)
    proof = load_json(OVERRIDE_PROOF_PATH)
    gate = load_json(DEPLOYMENT_GATE_PATH)
    nom_release = load_json(NOM_RELEASE_PATH)
    tracker = load_json(TRACKER_PATH)

    dry_rows = {str(row.get("coin") or ""): row for row in list(dry_probe.get("rows") or [])}
    proof_rows = {str(row.get("coin") or ""): row for row in list(proof.get("rows") or [])}
    command_rows = {str(row.get("command_name") or ""): row for row in list(command.get("rows") or [])}

    rows: list[dict[str, Any]] = []

    rows.append(
        {
            "coin": "TRU-USD",
            "current_stage": "supervised_clean_waiting_for_signal",
            "rank": 2,
            "authority": command_rows["tru_supervised_probe"]["authority"],
            "evidence": (
                f"dry={dry_rows['TRU-USD']['status']}; "
                f"proof={proof_rows['TRU-USD']['status']}; "
                f"signals=0; closes=0"
            ),
            "next_required_transition": "capture_governed_signal_window",
            "next_authorized_action": "run bounded supervised probe again when a real signal window is expected",
        }
    )
    rows.append(
        {
            "coin": "SUP-USD",
            "current_stage": "supervised_clean_waiting_for_signal",
            "rank": 2,
            "authority": command_rows["sup_supervised_probe"]["authority"],
            "evidence": (
                f"dry={dry_rows['SUP-USD']['status']}; "
                f"proof={proof_rows['SUP-USD']['status']}; "
                f"signals=0; closes=0"
            ),
            "next_required_transition": "capture_governed_signal_window",
            "next_authorized_action": "run bounded supervised probe again when a real signal window is expected",
        }
    )
    rows.append(
        {
            "coin": "NOM-USD",
            "current_stage": "blocked_or_deferred",
            "rank": 0,
            "authority": command_rows["nom_supervised_probe"]["authority"],
            "evidence": (
                f"dry={dry_rows['NOM-USD']['status']}; "
                f"proof={proof_rows['NOM-USD']['status']}; "
                f"release={nom_release.get('summary', {}).get('release_verdict')}"
            ),
            "next_required_transition": "clear_parallel_handoff_conflict",
            "next_authorized_action": "wait for alternate NOM lane to go flat, then run governed supervised probe",
        }
    )
    rows.append(
        {
            "coin": "BAL-USD",
            "current_stage": "blocked_or_deferred",
            "rank": 0,
            "authority": command_rows["bal_exact_probe"]["authority"],
            "evidence": (
                f"proof={proof_rows['BAL-USD']['status']}; "
                f"command_status={command_rows['bal_exact_probe']['status']}"
            ),
            "next_required_transition": "retire_legacy_runtime",
            "next_authorized_action": "keep BAL out of exact probes until cleanup finishes",
        }
    )
    rows.append(
        {
            "coin": "governed_book_global",
            "current_stage": "blocked_or_deferred",
            "rank": 0,
            "authority": "do_not_launch",
            "evidence": (
                f"deployment_gate={gate.get('summary', {}).get('verdict')}; "
                f"tracker_closes={tracker.get('total_closes')}; "
                f"tracker_cycle={tracker.get('runner_cycle')}"
            ),
            "next_required_transition": "reopen_governed_deployment_gate",
            "next_authorized_action": "do not promote any lane to deployable until tracker evidence exceeds smoke class and the gate changes",
        }
    )

    return rows


def build_payload() -> dict[str, Any]:
    rules = build_stage_rules()
    rows = build_lane_rows()
    return {
        "generated_at": utc_now_iso(),
        "command_authority_path": str(COMMAND_AUTHORITY_PATH),
        "dry_probe_path": str(DRY_PROBE_PATH),
        "override_proof_path": str(OVERRIDE_PROOF_PATH),
        "deployment_gate_path": str(DEPLOYMENT_GATE_PATH),
        "nom_release_path": str(NOM_RELEASE_PATH),
        "tracker_path": str(TRACKER_PATH),
        "leadership_read": [
            "The next governed promotion is not from clean infrastructure directly to deployable lane.",
            "TRU and SUP have only reached the supervised-clean-waiting-for-signal stage, which is still probe-only.",
            "NOM and BAL remain blocked for different reasons, and the whole governed book stays non-deployable until the live-watch evidence class improves and the deployment gate reopens.",
        ],
        "summary": {
            "verdict": "no_lane_above_probe_only_yet",
            "highest_current_stage": "supervised_clean_waiting_for_signal",
            "lanes_at_highest_stage": [
                row["coin"] for row in rows if row["current_stage"] == "supervised_clean_waiting_for_signal"
            ],
            "blocked_or_deferred_lanes": [
                row["coin"] for row in rows if row["current_stage"] == "blocked_or_deferred"
            ],
        },
        "stage_rules": rules,
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Proof Promotion Ladder",
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
            "## Stages",
            "",
            "| Stage | Rank | Promotion Rule |",
            "| --- | ---: | --- |",
        ]
    )
    for row in payload["stage_rules"]:
        lines.append(f"| {row['stage']} | {row['rank']} | {row['promotion_rule']} |")
    lines.extend(
        [
            "",
            "## Current Rows",
            "",
            "| Coin | Current Stage | Next Required Transition |",
            "| --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            f"| {row['coin']} | {row['current_stage']} | {row['next_required_transition']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
