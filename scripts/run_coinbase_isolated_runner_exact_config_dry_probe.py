#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"

QUEUE_PATH = REPORTS / "coinbase_isolated_runner_exact_config_smoke_queue.json"
CONFIG_PATH = REPORTS / "coinbase_isolated_runner_sleeve_book_config.json"
RUNNER_PATH = SCRIPTS / "multi_coin_isolated_runner.py"

JSON_PATH = REPORTS / "coinbase_isolated_runner_exact_config_dry_probe.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_exact_config_dry_probe.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def windows_no_window_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def build_command(coin: str, state_path: Path, event_path: Path) -> list[str]:
    return [
        sys.executable,
        str(RUNNER_PATH),
        "--config-path",
        str(CONFIG_PATH),
        "--total-cash",
        "48",
        "--coins",
        coin,
        "--state-path",
        str(state_path),
        "--event-path",
        str(event_path),
        "--dry-run",
    ]


def run_probe(coin: str) -> dict[str, Any]:
    stem = coin.lower().replace("-", "").replace("_", "")
    state_path = REPORTS / f"probe_exact_config_{stem}_state.json"
    event_path = REPORTS / f"probe_exact_config_{stem}_events.jsonl"
    command = build_command(coin, state_path, event_path)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
        creationflags=windows_no_window_creationflags(),
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    status = "probe_pass"
    if result.returncode != 0:
        status = "probe_fail"
    elif "BACKFILL ERROR" in stdout or "Traceback" in stdout or "Traceback" in stderr:
        status = "probe_fail"

    return {
        "coin": coin,
        "status": status,
        "return_code": result.returncode,
        "state_path": str(state_path),
        "event_path": str(event_path),
        "event_path_exists": event_path.exists(),
        "dry_run_complete": "DRY RUN complete" in stdout,
        "stdout_tail": stdout[-1200:],
        "stderr_tail": stderr[-1200:],
        "command": command,
    }


def build_payload() -> dict[str, Any]:
    queue = load_json(QUEUE_PATH)
    exact_rows = [
        row
        for row in list(queue.get("rows") or [])
        if str(row.get("proof_class") or "") == "exact_config_smoke"
        and str(row.get("queue_decision") or "") == "run_now"
    ]
    probe_rows = [run_probe(str(row.get("coin") or "")) for row in exact_rows]
    passing = [row["coin"] for row in probe_rows if row["status"] == "probe_pass"]
    failing = [row["coin"] for row in probe_rows if row["status"] != "probe_pass"]

    leadership_read = [
        "This dry probe checks whether the exact-config override path actually executes cleanly against the approved sleeve book without opening live positions.",
        "Only `run_now` exact rows are included here; deferred rows like BAL-USD stay out until their governance blocker clears.",
        "A dry-probe pass means the command path is operationally usable for supervised proof work, not that the lane is already live-validated.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "queue_path": str(QUEUE_PATH),
        "config_path": str(CONFIG_PATH),
        "runner_path": str(RUNNER_PATH),
        "leadership_read": leadership_read,
        "summary": {
            "probes_run": len(probe_rows),
            "passing_coins": passing,
            "failing_coins": failing,
            "overall_status": "all_pass" if not failing else "has_failures",
        },
        "rows": probe_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Exact Config Dry Probe",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Probes run: `{payload['summary']['probes_run']}`",
            f"- Overall status: `{payload['summary']['overall_status']}`",
            f"- Passing coins: `{', '.join(payload['summary']['passing_coins']) or 'none'}`",
            f"- Failing coins: `{', '.join(payload['summary']['failing_coins']) or 'none'}`",
            "",
            "## Rows",
            "",
            "| Coin | Status | Return Code | Dry Run Complete | Event File |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            f"| {row['coin']} | {row['status']} | {row['return_code']} | {row['dry_run_complete']} | {row['event_path_exists']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
