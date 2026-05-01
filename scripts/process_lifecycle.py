#!/usr/bin/env python3
"""Process lifecycle tracking for watchdog supervision.

Tracks launched child processes, detects orphans, and ensures clean lifecycle
management to prevent zombie/stale process buildup.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
WATCHDOG_PROCESS_NEEDLES = (
    "watch_penetration_lattice_runners.py",
    "start_watchdog_group_loop.ps1",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_watchdog_process_command(cmdline: str) -> bool:
    lower = str(cmdline or "").lower()
    return any(needle.lower() in lower for needle in WATCHDOG_PROCESS_NEEDLES)


def _commandline_has_flag_value(cmdline: str, flag: str, value: str | int | None) -> bool:
    rendered_value = str(value or "").strip()
    if not rendered_value:
        return False
    pattern = re.compile(
        rf"(?i)(?:^|\s){re.escape(flag)}(?:\s+|=)(?:\"{re.escape(rendered_value)}\"|'{re.escape(rendered_value)}'|{re.escape(rendered_value)})(?=\s|$)"
    )
    return bool(pattern.search(str(cmdline or "")))


def _commandline_has_any_flag_value(cmdline: str, flags: tuple[str, ...], value: str | int | None) -> bool:
    return any(_commandline_has_flag_value(cmdline, flag, value) for flag in flags)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def process_alive(pid: int) -> bool:
    """Check if a process is still running."""
    if pid <= 0:
        return False
    if psutil is not None:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False
    return True


def load_process_tracker(state_path: Path) -> dict[str, Any]:
    """Load the process tracking table from watchdog loop state."""
    tracker_path = state_path.parent / "process_tracker.json"
    if not tracker_path.exists():
        return {"lanes": {}, "updated_at": ""}
    try:
        return json.loads(tracker_path.read_text(encoding="utf-8"))
    except Exception:
        return {"lanes": {}, "updated_at": ""}


def save_process_tracker(state_path: Path, tracker: dict[str, Any]) -> None:
    """Save the process tracking table."""
    tracker_path = state_path.parent / "process_tracker.json"
    tracker["updated_at"] = utc_now_iso()
    tracker_path.parent.mkdir(parents=True, exist_ok=True)
    tracker_path.write_text(json.dumps(tracker, indent=2, sort_keys=True), encoding="utf-8")


def record_lane_launch(
    tracker: dict[str, Any],
    lane_name: str,
    pid: int,
    magic: int | None = None,
    state_path: str | None = None,
) -> None:
    """Record a new lane launch in the process tracker."""
    tracker["lanes"][lane_name] = {
        "pid": pid,
        "launched_at": utc_now_iso(),
        "watchdog_pid": os.getpid(),
        "magic": magic,
        "state_path": state_path,
    }


def remove_lane(tracker: dict[str, Any], lane_name: str) -> None:
    """Remove a lane from the process tracker."""
    tracker["lanes"].pop(lane_name, None)


def find_processes_by_magic(magic: int) -> list[dict[str, Any]]:
    """Find all Python processes running with a specific MT5 magic number."""
    matches = []
    try:
        if os.name == "nt":
            # Windows: use PowerShell to get Python processes
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
                ],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                try:
                    data = json.loads(result.stdout)
                    if isinstance(data, dict):
                        data = [data]
                    for proc in data:
                        cmdline = proc.get("CommandLine", "") or ""
                        if _is_watchdog_process_command(cmdline):
                            continue
                        if _commandline_has_any_flag_value(cmdline, ("--live-magic", "--magic"), magic):
                            matches.append({
                                "pid": proc.get("ProcessId"),
                                "cmdline": cmdline,
                            })
                except json.JSONDecodeError:
                    pass
        else:
            # Linux/Mac fallback using psutil
            if psutil is not None:
                for proc in psutil.process_iter(['pid', 'cmdline']):
                    try:
                        cmdline = " ".join(proc.info.get('cmdline') or [])
                        if _is_watchdog_process_command(cmdline):
                            continue
                        if _commandline_has_any_flag_value(cmdline, ("--live-magic", "--magic"), magic):
                            matches.append({
                                "pid": proc.info['pid'],
                                "cmdline": cmdline,
                            })
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
    except Exception:
        pass
    return matches


def find_processes_by_state_path(state_path: str) -> list[dict[str, Any]]:
    """Find all Python processes running with a specific state file path."""
    matches = []
    try:
        if os.name == "nt":
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
                ],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                try:
                    data = json.loads(result.stdout)
                    if isinstance(data, dict):
                        data = [data]
                    for proc in data:
                        cmdline = proc.get("CommandLine", "") or ""
                        if _is_watchdog_process_command(cmdline):
                            continue
                        if _commandline_has_any_flag_value(
                            cmdline,
                            ("--state-path", "--direct-exec-state-path"),
                            state_path,
                        ):
                            matches.append({
                                "pid": proc.get("ProcessId"),
                                "cmdline": cmdline,
                            })
                except json.JSONDecodeError:
                    pass
        else:
            if psutil is not None:
                for proc in psutil.process_iter(['pid', 'cmdline']):
                    try:
                        cmdline = " ".join(proc.info.get('cmdline') or [])
                        if _is_watchdog_process_command(cmdline):
                            continue
                        if _commandline_has_any_flag_value(
                            cmdline,
                            ("--state-path", "--direct-exec-state-path"),
                            state_path,
                        ):
                            matches.append({
                                "pid": proc.info['pid'],
                                "cmdline": cmdline,
                            })
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
    except Exception:
        pass
    return matches


def sweep_lane_processes(
    lane_name: str,
    magic: int | None = None,
    state_path: str | None = None,
    expected_pid: int | None = None,
) -> list[dict[str, Any]]:
    """Sweep for all processes matching a lane, excluding the expected PID.
    
    Returns list of {pid, cmdline} for orphan/stale processes.
    """
    orphans = []
    
    # Find by magic number (most reliable)
    if magic is not None:
        by_magic = find_processes_by_magic(magic)
        for proc in by_magic:
            if proc["pid"] != expected_pid:
                orphans.append(proc)
    
    # Find by state path (fallback/additional check)
    if state_path is not None:
        by_state = find_processes_by_state_path(state_path)
        for proc in by_state:
            # Deduplicate: skip if already found by magic
            if proc["pid"] != expected_pid and not any(o["pid"] == proc["pid"] for o in orphans):
                orphans.append(proc)
    
    return orphans


def reconcile_on_startup(
    tracker: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile process tracker with actual running processes on watchdog startup.
    
    Returns dict with:
    - orphaned_lanes: processes the caller should terminate immediately
    - stale_entries: tracker entries for processes no longer running
    - reconciled_tracker: cleaned tracker

    The process tracker is loop-local and only records lane child PIDs. Peer
    watchdog supervisors are not tracker-managed children, so startup
    reconciliation must not classify other watchdog loops as killable orphans.
    Cross-loop supervisor cleanup is handled by the wrapper/lock layer instead.
    """
    stale_entries = []
    orphaned_lanes: list[dict[str, Any]] = []
    
    # Check tracker entries: mark stale if process not running
    for lane_name, entry in list(tracker.get("lanes", {}).items()):
        pid = entry.get("pid")
        if pid and not process_alive(int(pid)):
            stale_entries.append({
                "lane": lane_name,
                "stale_pid": pid,
                "launched_at": entry.get("launched_at"),
            })
    
    # Remove stale entries from tracker
    for entry in stale_entries:
        tracker["lanes"].pop(entry["lane"], None)
    
    return {
        "stale_entries": stale_entries,
        "orphaned_lanes": orphaned_lanes,
        "reconciled_tracker": tracker,
    }


def stop_process_forceful(pid: int) -> bool:
    """Force-stop a process. Returns True if successful."""
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        else:
            os.kill(pid, 9)  # SIGKILL
            return True
    except Exception:
        return False
