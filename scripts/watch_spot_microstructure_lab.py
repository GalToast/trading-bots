#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ANALYZE_SCRIPT = ROOT / "scripts" / "analyze_spot_microstructure_sync.py"
ALIGNMENT_SCRIPT = ROOT / "scripts" / "analyze_predatory_signal_alignment.py"
EXECUTION_TRUTH_SCRIPT = ROOT / "scripts" / "analyze_rave_v2_execution_truth.py"
EMPIRICAL_SNAPSHOT_SCRIPT = ROOT / "scripts" / "build_empirical_execution_snapshot.py"
DASHBOARD_SCRIPT = ROOT / "scripts" / "build_spot_microstructure_lab_dashboard.py"
DEFAULT_STATE_PATH = ROOT / "reports" / "spot_microstructure_lab_watch_state.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh spot microstructure lab analysis/dashboard on an interval")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    return parser.parse_args()


def write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def run_step(script_path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        "script": script_path.name,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "finished_at": utc_now_iso(),
    }


def refresh_once() -> dict[str, Any]:
    analysis = run_step(ANALYZE_SCRIPT)
    alignment = run_step(ALIGNMENT_SCRIPT)
    execution_truth = run_step(EXECUTION_TRUTH_SCRIPT)
    empirical_snapshot = run_step(EMPIRICAL_SNAPSHOT_SCRIPT)
    dashboard = run_step(DASHBOARD_SCRIPT)
    return {
        "analysis": analysis,
        "alignment": alignment,
        "execution_truth": execution_truth,
        "empirical_snapshot": empirical_snapshot,
        "dashboard": dashboard,
        "refreshed_at": utc_now_iso(),
    }


def main() -> int:
    args = parse_args()
    state_path = Path(args.state_path)
    started_at = utc_now_iso()
    consecutive_exceptions = 0
    last_refresh: dict[str, Any] | None = None
    last_exception_message = ""
    last_exception_at: str | None = None

    while True:
        heartbeat_at = utc_now_iso()
        try:
            last_refresh = refresh_once()
            consecutive_exceptions = 0
            last_exception_message = ""
            last_exception_at = None
        except Exception as exc:
            consecutive_exceptions += 1
            last_exception_message = str(exc)
            last_exception_at = utc_now_iso()

        write_state(
            state_path,
            {
                "runner": {
                    "pid": os.getpid(),
                    "script": Path(__file__).name,
                    "started_at": started_at,
                    "heartbeat_at": heartbeat_at,
                    "interval_seconds": float(args.interval_seconds),
                    "consecutive_exceptions": consecutive_exceptions,
                    "last_exception_at": last_exception_at,
                    "last_exception_message": last_exception_message,
                },
                "watch": {
                    "analysis_script": str(ANALYZE_SCRIPT.relative_to(ROOT)),
                    "alignment_script": str(ALIGNMENT_SCRIPT.relative_to(ROOT)),
                    "execution_truth_script": str(EXECUTION_TRUTH_SCRIPT.relative_to(ROOT)),
                    "empirical_snapshot_script": str(EMPIRICAL_SNAPSHOT_SCRIPT.relative_to(ROOT)),
                    "dashboard_script": str(DASHBOARD_SCRIPT.relative_to(ROOT)),
                    "last_refresh": last_refresh,
                },
                "updated_at": utc_now_iso(),
            },
        )

        if args.once:
            return 0 if consecutive_exceptions == 0 else 1
        time.sleep(max(1.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
