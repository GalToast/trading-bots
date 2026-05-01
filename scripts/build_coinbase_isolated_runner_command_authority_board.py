#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"

DEPLOYMENT_GATE_PATH = REPORTS / "coinbase_isolated_runner_deployment_gate_board.json"
BOOK_GOVERNANCE_PATH = REPORTS / "coinbase_isolated_runner_book_governance_board.json"
SMOKE_QUEUE_PATH = REPORTS / "coinbase_isolated_runner_exact_config_smoke_queue.json"
DRY_PROBE_PATH = REPORTS / "coinbase_isolated_runner_exact_config_dry_probe.json"
OVERRIDE_PROOF_PATH = REPORTS / "coinbase_isolated_runner_override_path_proof_board.json"
NOM_RELEASE_PATH = REPORTS / "coinbase_nom_overlap_release_gate_board.json"

JSON_PATH = REPORTS / "coinbase_isolated_runner_command_authority_board.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_command_authority_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def quote_command(parts: list[str]) -> str:
    quoted: list[str] = []
    for part in parts:
        if " " in part:
            quoted.append(f'"{part}"')
        else:
            quoted.append(part)
    return " ".join(quoted)


def find_row(rows: list[dict[str, Any]], subject: str) -> dict[str, Any]:
    for row in rows:
        if str(row.get("subject") or "") == subject:
            return row
    raise KeyError(subject)


def build_rows() -> list[dict[str, Any]]:
    gate = load_json(DEPLOYMENT_GATE_PATH)
    governance = load_json(BOOK_GOVERNANCE_PATH)
    queue = load_json(SMOKE_QUEUE_PATH)
    dry_probe = load_json(DRY_PROBE_PATH)
    proof = load_json(OVERRIDE_PROOF_PATH)
    nom_release = load_json(NOM_RELEASE_PATH)

    gate_rows = list(gate.get("rows") or [])
    governance_rows = list(governance.get("rows") or [])
    queue_rows = {str(row.get("coin") or ""): row for row in list(queue.get("rows") or [])}
    dry_probe_rows = {str(row.get("coin") or ""): row for row in list(dry_probe.get("rows") or [])}
    proof_rows = {str(row.get("coin") or ""): row for row in list(proof.get("rows") or [])}

    deploy_gate_row = find_row(gate_rows, "strategy_book_governance")
    tracker_gate_row = find_row(gate_rows, "live_monitoring_state")
    full_deploy_claim_row = find_row(governance_rows, "full_deploy_now_command")

    tru = queue_rows["TRU-USD"]
    nom = queue_rows["NOM-USD"]
    sup = queue_rows["SUP-USD"]
    bal = queue_rows["BAL-USD"]

    rows: list[dict[str, Any]] = []

    rows.append(
        {
            "command_name": "default_deploy_helper",
            "status": "blocked",
            "authority": "do_not_launch",
            "command": "python scripts/deploy_isolated_runner.py",
            "evidence": (
                f"gate={gate.get('summary', {}).get('verdict')}; "
                f"book_full_deploy={full_deploy_claim_row.get('status')}"
            ),
            "read": "The deploy helper is infrastructure-ready, but it still points at the unresolved default book and is not authorized as a governed live launch command.",
        }
    )
    rows.append(
        {
            "command_name": "default_runner_direct_live",
            "status": "blocked",
            "authority": "do_not_launch",
            "command": "python scripts/multi_coin_isolated_runner.py --total-cash 48",
            "evidence": (
                f"strategy_book_governance={deploy_gate_row.get('decision')}; "
                f"book_decision={full_deploy_claim_row.get('decision')}"
            ),
            "read": "A raw direct runner launch is explicitly outside the board-approved path while the strategy-book rewrite scope remains unresolved.",
        }
    )
    rows.append(
        {
            "command_name": "exact_config_dry_probe_batch",
            "status": "allowed_probe_only",
            "authority": "run_dry_probe",
            "command": "run_now rows from coinbase_isolated_runner_exact_config_smoke_queue.json",
            "evidence": (
                f"dry_probe={dry_probe.get('summary', {}).get('overall_status')}; "
                f"passing={','.join(dry_probe.get('summary', {}).get('passing_coins') or [])}"
            ),
            "read": "The exact-config dry probe batch is an approved operational check. It proves command-path hygiene only and does not create deployment authority.",
        }
    )
    rows.append(
        {
            "command_name": "tru_supervised_probe",
            "status": "allowed_bounded_probe_only",
            "authority": "run_supervised_probe",
            "command": str(tru.get("supervised_command") or ""),
            "evidence": (
                f"proof_status={proof_rows['TRU-USD'].get('status')}; "
                f"queue_decision={tru.get('queue_decision')}"
            ),
            "read": "TRU supervised runs are allowed as governed bounded probes because the path is clean, but they are still probe-only until a real signal window appears.",
        }
    )
    rows.append(
        {
            "command_name": "sup_supervised_probe",
            "status": "allowed_bounded_probe_only",
            "authority": "run_supervised_probe",
            "command": str(sup.get("supervised_command") or ""),
            "evidence": (
                f"proof_status={proof_rows['SUP-USD'].get('status')}; "
                f"queue_decision={sup.get('queue_decision')}"
            ),
            "read": "SUP supervised runs are also allowed as bounded governed probes, but flat windows still do not count as launch permission.",
        }
    )
    rows.append(
        {
            "command_name": "nom_supervised_probe",
            "status": "deferred",
            "authority": "wait_for_handoff",
            "command": str(nom.get("supervised_command") or ""),
            "evidence": (
                f"proof_status={proof_rows['NOM-USD'].get('status')}; "
                f"release_verdict={nom_release.get('summary', {}).get('release_verdict')}"
            ),
            "read": "The governed NOM probe command exists and is exact-config, but authority is deferred until the parallel alternate NOM lane clears.",
        }
    )
    rows.append(
        {
            "command_name": "bal_exact_probe",
            "status": "blocked",
            "authority": "wait_for_cleanup",
            "command": str(bal.get("smoke_command") or ""),
            "evidence": (
                f"queue_decision={bal.get('queue_decision')}; "
                f"proof_status={proof_rows['BAL-USD'].get('status')}"
            ),
            "read": "BAL exact probe commands stay blocked until the legacy runtime trail is retired, so the queue still treats it as cleanup-gated.",
        }
    )
    rows.append(
        {
            "command_name": "tracker_watch_as_go_proof",
            "status": "blocked",
            "authority": "insufficient_evidence",
            "command": "python scripts/runner_health_check.py --watch",
            "evidence": (
                f"tracker_gate={tracker_gate_row.get('decision')}; "
                f"tracker_status={tracker_gate_row.get('status')}"
            ),
            "read": "Watch-mode monitoring is useful ops support, but it is not by itself a governed proof upgrade while the saved tracker is still a bounded smoke snapshot.",
        }
    )

    return rows


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    return {
        "generated_at": utc_now_iso(),
        "deployment_gate_path": str(DEPLOYMENT_GATE_PATH),
        "book_governance_path": str(BOOK_GOVERNANCE_PATH),
        "smoke_queue_path": str(SMOKE_QUEUE_PATH),
        "dry_probe_path": str(DRY_PROBE_PATH),
        "override_proof_path": str(OVERRIDE_PROOF_PATH),
        "nom_release_path": str(NOM_RELEASE_PATH),
        "leadership_read": [
            "Operational readiness and command authority are different things.",
            "Today the only clearly authorized commands are dry probes and bounded supervised probes on the governed exact-config path.",
            "Default deploy/live commands remain blocked, NOM remains deferred for handoff, and BAL remains blocked for cleanup.",
        ],
        "summary": {
            "verdict": "probe_commands_allowed_live_commands_blocked",
            "allowed_probe_commands": [
                row["command_name"]
                for row in rows
                if str(row.get("authority") or "") in {"run_dry_probe", "run_supervised_probe"}
            ],
            "blocked_commands": [
                row["command_name"]
                for row in rows
                if str(row.get("status") or "") == "blocked"
            ],
            "deferred_commands": [
                row["command_name"]
                for row in rows
                if str(row.get("status") or "") == "deferred"
            ],
        },
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Command Authority Board",
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
            "| Command | Status | Authority |",
            "| --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            f"| {row['command_name']} | {row['status']} | {row['authority']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
