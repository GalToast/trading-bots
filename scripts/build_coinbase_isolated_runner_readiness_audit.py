#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"

TARGET_PATH = SCRIPTS / "multi_coin_isolated_runner.py"
JSON_PATH = REPORTS / "coinbase_isolated_runner_readiness_audit.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_readiness_audit.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def source_lines() -> list[str]:
    return TARGET_PATH.read_text(encoding="utf-8").splitlines()


def find_line(lines: list[str], pattern: str) -> int:
    for idx, line in enumerate(lines, start=1):
        if pattern in line:
            return idx
    return 0


def build_findings() -> list[dict[str, Any]]:
    lines = source_lines()
    findings: list[dict[str, Any]] = []

    findings.append(
        {
            "severity": "high",
            "title": "Crash recovery restores cash only, not live positions",
            "file": str(TARGET_PATH),
            "line": find_line(lines, 'starting = prev.get("cash", per_coin_cash)'),
            "evidence": "previous state is only used to seed `starting` cash for each ledger; no active position, hold bars, fees, or history are restored into the new ledger object",
            "impact": "a restart can orphan deployed capital and reopen the same coin from the wrong state, which is the exact failure class the isolated runner claims to solve",
            "governance_action": "block deployment until per-ledger position recovery or deterministic reconstruction exists",
        }
    )
    findings.append(
        {
            "severity": "high",
            "title": "Backfill path cannot reconstruct an open position after restart",
            "file": str(TARGET_PATH),
            "line": find_line(lines, "if not backfill and self.position is None and self.cash >= MIN_CASH_PER_POSITION:"),
            "evidence": "entry logic is explicitly disabled during backfill, so historical candles only advance exits and indicators; they can never recreate the active position that was live before a crash",
            "impact": "restart recovery falls back to flat even when the pre-crash lane was active, so saved state and real exposure diverge immediately",
            "governance_action": "block deployment until restart can restore or rebuild an active lane honestly",
        }
    )
    findings.append(
        {
            "severity": "medium",
            "title": "Session gate is declared but not enforced",
            "file": str(TARGET_PATH),
            "line": find_line(lines, "session_open = hour not in SESSION_DEAD_HOURS"),
            "evidence": "`session_open` is computed but never used in entry gating",
            "impact": "the runner can trade through dead hours even though the design claims dead-hour suppression",
            "governance_action": "treat session-gated results as unproven until the gate is actually wired into entry decisions",
        }
    )
    findings.append(
        {
            "severity": "medium",
            "title": "Operational paths are hardcoded with no bounded-run controls",
            "file": str(TARGET_PATH),
            "line": find_line(lines, 'STATE_PATH = ROOT / "reports" / "multi_coin_isolated_state.json"'),
            "evidence": "state and event paths are fixed globals, and the CLI exposes only `--total-cash` and `--coins`; there are no `--state-path`, `--event-path`, `--poll-seconds`, or `--max-loops` controls",
            "impact": "proof runs cannot be safely isolated from production artifacts, and supervised smoke runs cannot be bounded without editing source",
            "governance_action": "treat the runner as not yet ops-ready for controlled deployment or parallel proof lanes",
        }
    )
    return findings


def build_payload() -> dict[str, Any]:
    findings = build_findings()
    return {
        "generated_at": utc_now_iso(),
        "target": str(TARGET_PATH),
        "readiness_verdict": "block_deploy_until_recovery_and_ops_gaps_close",
        "leadership_read": [
            "The isolated runner is directionally correct on bankroll architecture, but its current implementation still fails the same class of crash-truth test the shared runner already failed.",
            "Recovery is the blocking issue: the source restores cash only, disables entry reconstruction during backfill, and therefore cannot honestly preserve an active lane across restart.",
            "Operationally it is also not yet proof-run friendly because state/event outputs are hardcoded and the live loop has no bounded-run controls.",
        ],
        "findings": findings,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Readiness Audit",
        "",
        f"Readiness verdict: `{payload['readiness_verdict']}`",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Findings",
            "",
            "| Severity | Title | File | Line | Impact | Governance Action |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for finding in payload["findings"]:
        lines.append(
            "| {severity} | {title} | {file} | {line} | {impact} | {governance_action} |".format(
                **finding
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
