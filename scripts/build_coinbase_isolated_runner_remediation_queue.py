#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

AUDIT_PATH = REPORTS / "coinbase_isolated_runner_readiness_audit.json"
JSON_PATH = REPORTS / "coinbase_isolated_runner_remediation_queue.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_remediation_queue.md"

FINDING_ORDER = {
    "Crash recovery restores cash only, not live positions": 1,
    "Backfill path cannot reconstruct an open position after restart": 2,
    "Operational paths are hardcoded with no bounded-run controls": 3,
    "Session gate is declared but not enforced": 4,
}


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_rows() -> list[dict[str, Any]]:
    audit = load_json(AUDIT_PATH)
    rows: list[dict[str, Any]] = []
    for finding in list(audit.get("findings") or []):
        title = str(finding.get("title") or "")
        order = FINDING_ORDER.get(title, 99)
        fix_type = "hardening"
        recommended_patch = ""
        acceptance_gate = ""
        validation = ""

        if title == "Crash recovery restores cash only, not live positions":
            fix_type = "recovery_state_restore"
            recommended_patch = "persist full per-ledger position payload and restore it into CoinLedger on startup instead of seeding cash only"
            acceptance_gate = "a restart with an active position preserves cash, position fields, hold bars, and post-restart equity continuity"
            validation = "simulate saved active ledgers and confirm restore creates active positions before the first live fetch"
        elif title == "Backfill path cannot reconstruct an open position after restart":
            fix_type = "deterministic_restart_rebuild"
            recommended_patch = "either hydrate positions directly from saved state or add a deterministic reconstruction mode that can replay entries during restart backfill"
            acceptance_gate = "restart recovery never falls back to flat when the saved lane was active and reconstructable"
            validation = "replay a known open-position scenario through restart and confirm the lane stays active until TP/SL/timeout"
        elif title == "Operational paths are hardcoded with no bounded-run controls":
            fix_type = "ops_cli_controls"
            recommended_patch = "add --state-path, --event-path, --poll-seconds, and --max-loops so proof runs can be isolated and bounded"
            acceptance_gate = "a single-coin smoke run can write to unique files and exit cleanly after a fixed number of loops"
            validation = "run a smoke command with --max-loops 1 and verify isolated state/event files are produced"
        elif title == "Session gate is declared but not enforced":
            fix_type = "session_gate_enforcement"
            recommended_patch = "wire session_open into entry gating so dead-hour candles can manage exits but cannot open new positions"
            acceptance_gate = "dead-hour candles can close an active lane but cannot trigger a fresh open"
            validation = "unit-test a qualifying entry candle during a dead hour and assert no new position opens"

        rows.append(
            {
                "fix_order": order,
                "severity": str(finding.get("severity") or ""),
                "title": title,
                "line": int(finding.get("line") or 0),
                "fix_type": fix_type,
                "recommended_patch": recommended_patch,
                "acceptance_gate": acceptance_gate,
                "validation": validation,
                "governance_action": str(finding.get("governance_action") or ""),
            }
        )

    rows.sort(key=lambda row: (int(row.get("fix_order") or 99), str(row.get("title") or "")))
    return rows


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": [
            "The isolated runner should be fixed in blocker order, not patched opportunistically, because the recovery semantics determine whether any later ops improvements are even meaningful.",
            "Recovery restore and restart reconstruction are the two deploy blockers; until they are fixed, the runner can still lose truth on active lanes across crash or restart.",
            "Operational CLI controls and dead-hour enforcement come next so the runner becomes safely testable and faithful to its own session claims.",
        ],
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Remediation Queue",
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
            "| Order | Severity | Title | Line | Fix Type | Recommended Patch | Acceptance Gate | Validation |",
            "| ---: | --- | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {fix_order} | {severity} | {title} | {line} | {fix_type} | {recommended_patch} | {acceptance_gate} | {validation} |".format(
                **row
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
