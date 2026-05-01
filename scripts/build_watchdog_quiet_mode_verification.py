#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SUPERVISOR_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "TradingBotsSupervisor"

JSON_PATH = REPORTS / "watchdog_quiet_mode_verification.json"
MD_PATH = REPORTS / "watchdog_quiet_mode_verification.md"

EXPECTED_TASKS = [
    "TradingBots-CryptoWatchdog-Ensure",
    "TradingBots-CryptoWatchdog-Guard",
    "TradingBots-FXWatchdog-Ensure",
    "TradingBots-FXWatchdog-Guard",
    "TradingBots-ShadowWatchdog-Ensure",
    "TradingBots-ShadowWatchdog-Guard",
    "TradingBots-SupervisorWatchdogBoard-Refresh",
]

EXPECTED_GUARDS = [
    SUPERVISOR_DIR / "watch_crypto_watchdog.ps1",
    SUPERVISOR_DIR / "watch_fx_watchdog.ps1",
    SUPERVISOR_DIR / "watch_shadow_watchdog.ps1",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_powershell(cmd: str) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    return completed.stdout


def load_task_rows() -> list[dict[str, Any]]:
    cmd = (
        "Get-ScheduledTask | "
        "Where-Object { $_.TaskName -like 'TradingBots*' } | "
        "ForEach-Object { [pscustomobject]@{ "
        "TaskName=$_.TaskName; Hidden=$_.Settings.Hidden; State=$_.State; Execute=$_.Actions.Execute; Arguments=$_.Actions.Arguments } } | "
        "Sort-Object TaskName | ConvertTo-Json -Depth 4"
    )
    raw = run_powershell(cmd).strip()
    rows = json.loads(raw) if raw else []
    if isinstance(rows, dict):
        rows = [rows]
    return rows


def inspect_guard_script(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    uses_hidden_start = "Start-Process -FilePath $PowerShellExe -ArgumentList @(" in text
    uses_direct_ensure = "& $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $EnsureScriptPath" in text
    desktop_alerts_opt_in = "TRADING_BOTS_ENABLE_DESKTOP_ALERTS" in text
    return {
        "path": str(path),
        "exists": path.exists(),
        "uses_hidden_start_process": uses_hidden_start,
        "uses_direct_ensure_invocation": uses_direct_ensure,
        "desktop_alerts_opt_in": desktop_alerts_opt_in,
    }


def build_payload() -> dict[str, Any]:
    task_rows = load_task_rows()
    task_by_name = {str(row.get("TaskName") or ""): row for row in task_rows}
    guard_rows = [inspect_guard_script(path) for path in EXPECTED_GUARDS]

    missing_tasks = [name for name in EXPECTED_TASKS if name not in task_by_name]
    non_hidden_tasks = [
        name for name in EXPECTED_TASKS if name in task_by_name and not bool(task_by_name[name].get("Hidden"))
    ]
    non_wscript_tasks = [
        name
        for name in EXPECTED_TASKS
        if name in task_by_name and "wscript.exe" not in str(task_by_name[name].get("Execute") or "").lower()
    ]
    direct_guard_scripts = [
        row["path"] for row in guard_rows if row["exists"] and row["uses_direct_ensure_invocation"]
    ]
    non_hidden_guard_scripts = [
        row["path"] for row in guard_rows if row["exists"] and not row["uses_hidden_start_process"]
    ]
    popup_enabled_guard_scripts = [
        row["path"] for row in guard_rows if row["exists"] and not row["desktop_alerts_opt_in"]
    ]

    verdict = "quiet_mode_verified"
    if (
        missing_tasks
        or non_hidden_tasks
        or non_wscript_tasks
        or direct_guard_scripts
        or non_hidden_guard_scripts
        or popup_enabled_guard_scripts
    ):
        verdict = "quiet_mode_regressed"

    return {
        "generated_at": utc_now_iso(),
        "verdict": verdict,
        "leadership_read": [
            "This verification is intentionally narrow: installed TradingBots scheduled-task visibility plus the live external guard recovery invocation mode.",
            "If these rows stay green, the main watchdog-driven visible-window burst path should stay suppressed even during stale-loop or missed-trade recovery waves.",
            "If the user reports bursts again, re-run this artifact first before guessing whether the repo regressed or the source is external shell churn.",
        ],
        "summary": {
            "expected_task_count": len(EXPECTED_TASKS),
            "observed_task_count": len(task_rows),
            "missing_tasks": missing_tasks,
            "non_hidden_tasks": non_hidden_tasks,
            "non_wscript_tasks": non_wscript_tasks,
            "guard_scripts_with_direct_ensure": direct_guard_scripts,
            "guard_scripts_missing_hidden_start": non_hidden_guard_scripts,
            "guard_scripts_missing_opt_in_desktop_alerts": popup_enabled_guard_scripts,
        },
        "task_rows": task_rows,
        "guard_rows": guard_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Watchdog Quiet Mode Verification",
        "",
        f"Verdict: `{payload['verdict']}`",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Tasks",
            "",
            "| Task | Hidden | State | Execute |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in payload["task_rows"]:
        lines.append(f"| {row['TaskName']} | {row['Hidden']} | {row['State']} | {row['Execute']} |")
    lines.extend(
        [
            "",
            "## Guard Scripts",
            "",
            "| Path | Hidden Start-Process | Direct Ensure Invocation | Desktop Alerts Opt-In |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in payload["guard_rows"]:
        lines.append(
            f"| {Path(row['path']).name} | {row['uses_hidden_start_process']} | {row['uses_direct_ensure_invocation']} | {row['desktop_alerts_opt_in']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
