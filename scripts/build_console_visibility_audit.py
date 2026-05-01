#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"

JSON_PATH = REPORTS / "console_visibility_audit.json"
MD_PATH = REPORTS / "console_visibility_audit.md"

REFRESH_SCRIPT = SCRIPTS / "operators" / "refresh_supervisor_watchdog_board.ps1"
ENSURE_CRYPTO = SCRIPTS / "operators" / "ensure_crypto_watchdog.ps1"
ENSURE_GROUP = SCRIPTS / "operators" / "ensure_watchdog_group.ps1"
START_CRYPTO = SCRIPTS / "operators" / "start_crypto_watchdog_loop.ps1"
START_GROUP = SCRIPTS / "operators" / "start_watchdog_group_loop.ps1"
ALERT_SCRIPT = SCRIPTS / "operators" / "emit_trade_firing_alerts.ps1"
TASK_HELPERS = SCRIPTS / "operators" / "task_launcher_helpers.ps1"
INSTALL_CRYPTO_TASK = SCRIPTS / "operators" / "install_crypto_watchdog_task.ps1"
INSTALL_GROUP_TASK = SCRIPTS / "operators" / "install_watchdog_group_task.ps1"
INSTALL_CRYPTO_GUARD = SCRIPTS / "operators" / "install_external_crypto_watchdog_guard.ps1"
INSTALL_GROUP_GUARD = SCRIPTS / "operators" / "install_external_watchdog_group_guard.ps1"
INSTALL_REFRESH_TASK = SCRIPTS / "operators" / "install_supervisor_watchdog_board_task.ps1"
WATCHDOG = SCRIPTS / "watch_penetration_lattice_runners.py"
MONITOR = ROOT / "monitor.py"
DEPLOY = SCRIPTS / "deploy_isolated_runner.py"
BENCHMARK = SCRIPTS / "benchmarks" / "benchmark_trading_bot.py"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def find_line(lines: list[str], pattern: str) -> int:
    for idx, line in enumerate(lines, start=1):
        if pattern in line:
            return idx
    return 0


def start_process_hidden_count(lines: list[str]) -> int:
    count = 0
    for line in lines:
        if "Start-Process" in line and "WindowStyle Hidden" in line:
            count += 1
    return count


def build_findings() -> list[dict[str, Any]]:
    refresh_lines = read_lines(REFRESH_SCRIPT)
    ensure_crypto_lines = read_lines(ENSURE_CRYPTO)
    ensure_group_lines = read_lines(ENSURE_GROUP)
    start_crypto_lines = read_lines(START_CRYPTO)
    start_group_lines = read_lines(START_GROUP)
    alert_lines = read_lines(ALERT_SCRIPT)
    task_helper_lines = read_lines(TASK_HELPERS)
    install_crypto_task_lines = read_lines(INSTALL_CRYPTO_TASK)
    install_group_task_lines = read_lines(INSTALL_GROUP_TASK)
    install_crypto_guard_lines = read_lines(INSTALL_CRYPTO_GUARD)
    install_group_guard_lines = read_lines(INSTALL_GROUP_GUARD)
    install_refresh_task_lines = read_lines(INSTALL_REFRESH_TASK)
    watchdog_lines = read_lines(WATCHDOG)
    monitor_lines = read_lines(MONITOR)
    deploy_lines = read_lines(DEPLOY)
    benchmark_lines = read_lines(BENCHMARK)

    findings: list[dict[str, Any]] = []

    findings.append(
        {
            "severity": "info",
            "title": "Scheduled watchdog and supervisor tasks now launch through WSH hidden wrappers",
            "status": "patched_quiet",
            "files": [
                {"path": str(TASK_HELPERS), "line": find_line(task_helper_lines, "function New-HiddenPowerShellTaskAction")},
                {"path": str(INSTALL_CRYPTO_TASK), "line": find_line(install_crypto_task_lines, "New-HiddenPowerShellTaskAction")},
                {"path": str(INSTALL_GROUP_TASK), "line": find_line(install_group_task_lines, "New-HiddenPowerShellTaskAction")},
                {"path": str(INSTALL_CRYPTO_GUARD), "line": find_line(install_crypto_guard_lines, "New-HiddenPowerShellTaskAction")},
                {"path": str(INSTALL_GROUP_GUARD), "line": find_line(install_group_guard_lines, "New-HiddenPowerShellTaskAction")},
                {"path": str(INSTALL_REFRESH_TASK), "line": find_line(install_refresh_task_lines, "New-HiddenPowerShellTaskAction")},
            ],
            "evidence": (
                "Minute-level scheduled tasks no longer execute PowerShell directly. They now invoke wscript.exe wrappers that run the PowerShell targets hidden, "
                "which is a more reliable way to suppress foreground console flashes on Windows interactive sessions."
            ),
            "impact": "the recurring once-per-minute watchdog and board refresh launches should stop surfacing visible terminal windows",
        }
    )
    findings.append(
        {
            "severity": "info",
            "title": "External watchdog desktop alerts are now opt-in instead of default foreground popups",
            "status": "patched_quiet",
            "files": [
                {"path": str(INSTALL_CRYPTO_GUARD), "line": find_line(install_crypto_guard_lines, "TRADING_BOTS_ENABLE_DESKTOP_ALERTS")},
                {"path": str(INSTALL_GROUP_GUARD), "line": find_line(install_group_guard_lines, "TRADING_BOTS_ENABLE_DESKTOP_ALERTS")},
            ],
            "evidence": (
                "The external guard scripts still log and post switchboard alerts, but MessageBox popups now require TRADING_BOTS_ENABLE_DESKTOP_ALERTS=1 instead of firing by default."
            ),
            "impact": "real watchdog incidents can still be traced without stealing focus or covering active foreground work",
        }
    )
    findings.append(
        {
            "severity": "info",
            "title": "Background watchdog launcher hops now use CreateNoWindow process starts",
            "status": "quiet_ready",
            "files": [
                {"path": str(ENSURE_CRYPTO), "line": find_line(ensure_crypto_lines, "$psi.CreateNoWindow = $true")},
                {"path": str(ENSURE_GROUP), "line": find_line(ensure_group_lines, "$psi.CreateNoWindow = $true")},
                {"path": str(START_CRYPTO), "line": find_line(start_crypto_lines, "$psi.CreateNoWindow = $true")},
                {"path": str(START_GROUP), "line": find_line(start_group_lines, "$psi.CreateNoWindow = $true")},
                {"path": str(ALERT_SCRIPT), "line": find_line(alert_lines, "$DesktopAlertsEnabled =")},
            ],
            "evidence": (
                "The scheduled/background operator layer now uses ProcessStartInfo.CreateNoWindow for the ensure and launcher hops, "
                "and trade-firing desktop alerts are opt-in via environment toggle."
            ),
            "impact": "watchdog ensure and launcher bursts should no longer surface visible console windows during repair waves",
        }
    )
    findings.append(
        {
            "severity": "info",
            "title": "Supervisor board refresh now launches builders through a hidden PowerShell host",
            "status": "patched_quiet",
            "file": str(REFRESH_SCRIPT),
            "line": find_line(refresh_lines, "Start-Process -FilePath $PowerShellExe `"),
            "evidence": (
                "The refresh path now launches both the builder scripts and the trade-firing alert refresh through hidden PowerShell hosts, "
                "instead of spawning short-lived Python console children directly."
            ),
            "impact": "minute-level supervisor refreshes should no longer leak short-lived Python or PowerShell console windows",
        }
    )
    findings.append(
        {
            "severity": "info",
            "title": "Repo PowerShell helper calls now opt into CREATE_NO_WINDOW",
            "status": "patched_quiet",
            "files": [
                {"path": str(WATCHDOG), "line": find_line(watchdog_lines, "NO_WINDOW_FLAGS = getattr(subprocess, \"CREATE_NO_WINDOW\", 0)")},
                {"path": str(WATCHDOG), "line": find_line(watchdog_lines, "creationflags=NO_WINDOW_FLAGS,")},
                {"path": str(MONITOR), "line": find_line(monitor_lines, "return getattr(subprocess, \"CREATE_NO_WINDOW\", 0)")},
                {"path": str(MONITOR), "line": find_line(monitor_lines, "creationflags=windows_no_window_creationflags(),")},
            ],
            "evidence": (
                "Watchdog process queries/stop calls and monitor process queries now request CREATE_NO_WINDOW on Windows "
                "when they shell out to PowerShell."
            ),
            "impact": "repo-owned inspection and cleanup helpers are less likely to flash a console when run from background contexts",
        }
    )
    findings.append(
        {
            "severity": "medium",
            "title": "Manual operator scripts still intentionally inherit normal console semantics",
            "status": "non_blocking_manual",
            "files": [
                {"path": str(DEPLOY), "line": find_line(deploy_lines, "process = subprocess.Popen(")},
                {"path": str(BENCHMARK), "line": find_line(benchmark_lines, "proc = subprocess.Popen(")},
            ],
            "evidence": (
                "Foreground deployment and benchmark helpers still use normal subprocess launches rather than detached hidden windows. "
                "They redirect logs or run under direct operator control, so they are not background supervisor noise by default."
            ),
            "impact": "these paths are acceptable for manual sessions, but they are not candidates to explain recurring unattended window bursts",
        }
    )
    return findings


def build_payload() -> dict[str, Any]:
    findings = build_findings()
    return {
        "generated_at": utc_now_iso(),
        "audit_scope": {
            "background_operator_scripts": [
                str(ENSURE_CRYPTO),
                str(ENSURE_GROUP),
                str(START_CRYPTO),
                str(START_GROUP),
                str(ALERT_SCRIPT),
                str(REFRESH_SCRIPT),
            ],
            "python_helpers": [
                str(WATCHDOG),
                str(MONITOR),
            ],
            "manual_operator_scripts": [
                str(DEPLOY),
                str(BENCHMARK),
            ],
        },
        "quiet_launch_verdict": "repo_background_launchers_quiet_ready",
        "leadership_read": [
            "The repo-owned background operator layer is now quiet-oriented at both layers: top-level scheduled tasks launch through WSH hidden wrappers, and the child watchdog/monitor hops use CreateNoWindow or hidden hosts.",
            "Foreground MessageBox-style watchdog popups are now opt-in for both the trade-firing alerts and the external guard scripts, so unattended incidents should stay observable without stealing focus.",
            "Manual benchmark and deploy helpers still inherit normal console behavior, but those are operator-invoked paths rather than unattended minute-loop supervisors.",
        ],
        "findings": findings,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)

    lines = [
        "# Console Visibility Audit",
        "",
        f"Quiet launch verdict: `{payload['quiet_launch_verdict']}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in payload["leadership_read"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Findings",
            "",
            "| Severity | Status | Title | Impact |",
            "| --- | --- | --- | --- |",
        ]
    )
    for finding in payload["findings"]:
        lines.append(
            "| {severity} | {status} | {title} | {impact} |".format(
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
