#!/usr/bin/env python3
"""Quick system health check for taskboard/agent coordination.

Outputs a JSON dict with:
- python_process_count: number of running python.exe processes
- watchdog_status: {group_name: {status, lanes, pid, age_seconds}}
- mt5_status: {connected, login, name} or {error: ...}
- disk_free_gb: free disk space on system drive
- timestamp: UTC ISO
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone

import mt5_terminal_guard

ROOT = os.path.dirname(os.path.dirname(__file__))
WATCHDOG_GROUPS_CONFIG = os.path.join(ROOT, "configs", "watchdog_groups.json")
REGISTRY_CONFIG = os.path.join(ROOT, "configs", "penetration_lattice_runner_registry.json")
PYTHON_SCRIPT_RE = re.compile(r'([A-Za-z]:[\\/][^"\r\n]*?\.py|[^"\s]+\.py)', re.IGNORECASE)
KNOWN_EXPECTED_PYTHON_SCRIPTS = {
    "scripts/watch_penetration_lattice_runners.py",
    "scripts/switchboard_cli.py",
    "scripts/system_health_check.py",
    "scripts/build_penetration_lane_scoreboard.py",
    "comms_server.py",
}


def count_python_processes():
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10
        )
        lines = [l for l in result.stdout.strip().split("\n") if l and "python" in l.lower()]
        return len(lines), [l.split(",")[1].strip('"') for l in lines]
    except Exception as e:
        return -1, str(e)


def load_expected_launcher_scripts() -> set[str]:
    expected = {script.lower() for script in KNOWN_EXPECTED_PYTHON_SCRIPTS}
    try:
        with open(REGISTRY_CONFIG, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return expected
    lanes = payload.get("lanes")
    if not isinstance(lanes, list):
        return expected
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        restart_args = lane.get("restart_args")
        if not isinstance(restart_args, list) or not restart_args:
            continue
        script = normalize_script_path(restart_args[0])
        if script:
            expected.add(script)
    return expected


def extract_script_path(command_line: str) -> str:
    text = str(command_line or "").strip()
    if not text:
        return ""
    tokens = [token.strip().strip('"') for token in re.findall(r'"[^"]+"|\S+', text)]
    for candidate in tokens:
        lower = candidate.lower()
        if not lower.endswith(".py"):
            continue
        if lower.endswith("python.exe") or lower == "python":
            continue
        return candidate
    matches = [str(match or "").strip() for match in PYTHON_SCRIPT_RE.findall(text)]
    for candidate in matches:
        lower = candidate.lower()
        if lower.endswith("python.exe") or lower == "python":
            continue
        return candidate
    return ""


def normalize_script_path(script_path: str) -> str:
    text = str(script_path or "").strip().strip('"')
    if not text:
        return ""
    text = text.replace("/", os.sep).replace("\\", os.sep)
    root_norm = os.path.normcase(os.path.normpath(ROOT))
    abs_candidate = os.path.normcase(os.path.normpath(text))
    if os.path.isabs(text) and abs_candidate.startswith(root_norm + os.sep):
        return os.path.relpath(os.path.normpath(text), ROOT).replace("\\", "/").lower()
    parts = [part for part in re.split(r"[\\/]+", text) if part]
    if "trading-bots" in [part.lower() for part in parts]:
        idx = [part.lower() for part in parts].index("trading-bots")
        rel_parts = parts[idx + 1 :]
        if rel_parts:
            return "/".join(rel_parts).lower()
    return text.replace("\\", "/").lower()


def list_python_process_rows() -> list[dict[str, object]]:
    cmd = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Depth 3"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = str(result.stdout or "").strip()
    if not payload:
        return []
    rows = json.loads(payload)
    return rows if isinstance(rows, list) else [rows]


def summarize_python_processes(rows: list[dict[str, object]], *, expected_scripts: set[str] | None = None) -> dict[str, object]:
    expected_scripts = {str(script or "").lower() for script in (expected_scripts or load_expected_launcher_scripts())}
    script_counts: dict[str, int] = {}
    unexpected_rows: list[dict[str, object]] = []
    expected_count = 0
    comms_parent_counts: dict[int, int] = {}
    for row in rows:
        command_line = str(row.get("CommandLine") or "")
        script_raw = extract_script_path(command_line)
        script_norm = normalize_script_path(script_raw) if script_raw else "<unknown>"
        script_counts[script_norm] = int(script_counts.get(script_norm, 0) or 0) + 1
        pid = int(row.get("ProcessId") or 0)
        ppid = int(row.get("ParentProcessId") or 0)
        if script_norm in expected_scripts:
            expected_count += 1
        else:
            unexpected_rows.append({"pid": pid, "parent_pid": ppid, "script": script_norm})
        if script_norm == "comms_server.py":
            comms_parent_counts[ppid] = int(comms_parent_counts.get(ppid, 0) or 0) + 1
    unexpected_examples = [
        f"{row['script']}#{row['pid']}"
        for row in unexpected_rows[:10]
    ]
    same_parent_duplicates = sum(max(count - 1, 0) for count in comms_parent_counts.values() if count > 1)
    top_scripts = [
        {"script": script, "count": count}
        for script, count in sorted(script_counts.items(), key=lambda item: (-item[1], item[0]))[:15]
    ]
    zombie_risk = bool(len(unexpected_rows) >= 3 or same_parent_duplicates > 0)
    return {
        "python_process_count": len(rows),
        "expected_python_process_count": expected_count,
        "unexpected_python_process_count": len(unexpected_rows),
        "unexpected_python_examples": unexpected_examples,
        "top_python_scripts": top_scripts,
        "comms_server_process_count": int(script_counts.get("comms_server.py", 0) or 0),
        "comms_server_same_parent_duplicate_count": same_parent_duplicates,
        "zombie_risk": zombie_risk,
    }


def load_configured_watchdog_groups() -> set[str]:
    try:
        with open(WATCHDOG_GROUPS_CONFIG, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return set()
    groups = payload.get("groups")
    if not isinstance(groups, dict):
        return set()
    return {str(name or "").strip() for name in groups.keys() if str(name or "").strip()}


def is_pid_running(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return False
    output = str(result.stdout or "").strip()
    if not output or "No tasks are running" in output:
        return False
    return f'"{int(pid)}"' in output or f",{int(pid)}," in output


def get_watchdog_status(*, watchdog_dir: str | None = None, configured_groups: set[str] | None = None, now_dt: datetime | None = None, pid_running_fn=None):
    """Read all watchdog loop state files and return status summary."""
    watchdog_dir = watchdog_dir or os.path.join(ROOT, "reports", "watchdog")
    if not os.path.exists(watchdog_dir):
        return {"error": "watchdog directory not found"}
    configured_groups = configured_groups if configured_groups is not None else load_configured_watchdog_groups()
    now_dt = now_dt or datetime.now(timezone.utc)
    pid_running_fn = pid_running_fn or is_pid_running

    result = {}
    for fname in os.listdir(watchdog_dir):
        if fname.endswith("_loop_state.json"):
            group_name = fname.replace("_loop_state.json", "")
            try:
                with open(os.path.join(watchdog_dir, fname)) as f:
                    state = json.load(f)
                updated = state.get("updated_at", "")
                age_seconds = 0
                if updated:
                    try:
                        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        age_seconds = (now_dt - dt).total_seconds()
                    except Exception:
                        pass
                pid = int(state.get("pid", 0) or 0)
                configured_group = str(group_name) in configured_groups
                pid_running = pid_running_fn(pid)
                stale_artifact = bool((not configured_group) and (not pid_running) and age_seconds >= 300)
                result[group_name] = {
                    "status": state.get("status", "unknown"),
                    "lanes": state.get("rows_total", 0),
                    "pid": pid,
                    "pid_running": pid_running,
                    "updated_at": updated,
                    "age_seconds": round(age_seconds, 1),
                    "configured_group": configured_group,
                    "health_included": configured_group,
                    "stale_artifact": stale_artifact,
                }
            except Exception as e:
                result[group_name] = {"error": str(e)}
    return result


def get_mt5_status():
    mt5 = None
    try:
        import MetaTrader5 as mt5

        ok, payload = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
        contract = payload.get("contract") if isinstance(payload, dict) else {}
        if not ok:
            return {
                "connected": False,
                "identity_ok": False,
                "reason": str(payload.get("reason") or "initialize_failed"),
                "binding_mode": str(contract.get("binding_mode") or "account_only"),
                "error": mt5_terminal_guard.failure_summary(payload),
            }

        info = mt5.terminal_info()
        status = {
            "connected": bool(payload.get("connected")),
            "identity_ok": bool(payload.get("identity_ok", True)),
            "binding_mode": str(contract.get("binding_mode") or "account_only"),
            "login": int(payload.get("login") or 0),
            "server": str(payload.get("server") or ""),
            "terminal_path": str(payload.get("terminal_path") or ""),
            "trade_allowed": bool(payload.get("trade_allowed", False)),
            "terminal_connected": bool(payload.get("terminal_connected", False)),
            "trade_mode": getattr(info, "trade_mode", "?"),
        }
        # Version-compatible attribute access
        for attr in ["name", "company"]:
            val = getattr(info, attr, None)
            if val is not None:
                status[attr] = val
        return status
    except ImportError:
        return {"error": "MetaTrader5 module not installed"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if mt5 is not None:
            try:
                mt5.shutdown()
            except Exception:
                pass


def get_disk_free_gb():
    try:
        import shutil
        total, used, free = shutil.disk_usage("C:\\")
        return round(free / (1024**3), 1)
    except Exception:
        return -1


def main():
    py_count, py_pids = count_python_processes()
    python_rows = list_python_process_rows()
    python_summary = summarize_python_processes(python_rows)
    wd_status = get_watchdog_status()
    mt5_status = get_mt5_status()
    disk_free = get_disk_free_gb()

    health = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "python_process_count": int(python_summary.get("python_process_count") or py_count),
        "python_pids": py_pids if isinstance(py_pids, list) else [],
        "expected_python_process_count": int(python_summary.get("expected_python_process_count") or 0),
        "unexpected_python_process_count": int(python_summary.get("unexpected_python_process_count") or 0),
        "unexpected_python_examples": list(python_summary.get("unexpected_python_examples") or []),
        "top_python_scripts": list(python_summary.get("top_python_scripts") or []),
        "comms_server_process_count": int(python_summary.get("comms_server_process_count") or 0),
        "comms_server_same_parent_duplicate_count": int(python_summary.get("comms_server_same_parent_duplicate_count") or 0),
        "zombie_risk": bool(python_summary.get("zombie_risk")),
        "watchdog_status": wd_status,
        "mt5_status": mt5_status,
        "disk_free_gb": disk_free,
    }

    # Write JSON
    report_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
    json_path = os.path.join(report_dir, "system_health_check.json")
    with open(json_path, "w") as f:
        json.dump(health, f, indent=2)

    # Write markdown
    md_path = os.path.join(report_dir, "system_health_check.md")
    with open(md_path, "w") as f:
        f.write(f"# System Health Check\n\nGenerated: `{health['timestamp']}`\n\n")
        f.write(f"| Metric | Value |\n| --- | ---: |\n")
        zombie_flag = " [ZOMBIE RISK]" if health["zombie_risk"] else " [OK]"
        f.write(f"| Python processes | {health['python_process_count']}{zombie_flag} |\n")
        f.write(f"| Expected python processes | {health['expected_python_process_count']} |\n")
        f.write(f"| Unexpected python processes | {health['unexpected_python_process_count']} |\n")
        f.write(f"| comms_server processes | {health['comms_server_process_count']} |\n")
        f.write(f"| Disk free | {disk_free} GB |\n")
        f.write(f"| MT5 | {mt5_status.get('connected', mt5_status.get('error', '?'))} |\n\n")
        if health["unexpected_python_examples"] or health["top_python_scripts"]:
            f.write("## Python Process Census\n\n")
            if health["unexpected_python_examples"]:
                f.write("- Unexpected examples: `" + "`, `".join(health["unexpected_python_examples"]) + "`\n")
            else:
                f.write("- Unexpected examples: none\n")
            f.write("\n| Script | Count |\n| --- | ---: |\n")
            for row in health["top_python_scripts"]:
                f.write(f"| {row['script']} | {row['count']} |\n")
            f.write("\n")
        if mt5_status.get("binding_mode"):
            f.write("## MT5 Identity\n\n")
            f.write("| Metric | Value |\n| --- | --- |\n")
            f.write(f"| Binding mode | {mt5_status.get('binding_mode', '?')} |\n")
            f.write(f"| Identity OK | {mt5_status.get('identity_ok', False)} |\n")
            f.write(f"| Login | {mt5_status.get('login', '?')} |\n")
            f.write(f"| Server | {mt5_status.get('server', '?')} |\n")
            f.write(f"| Terminal path | {mt5_status.get('terminal_path', '?')} |\n\n")
        elif mt5_status.get("error"):
            f.write("## MT5 Identity\n\n")
            f.write(f"- Error: `{mt5_status['error']}`\n\n")
        f.write("## Watchdog Groups\n\n")
        f.write("| Group | Scope | Status | Lanes | PID | Age (s) |\n| --- | --- | --- | ---: | ---: | ---: |\n")
        for name, info in wd_status.items():
            if "error" in info:
                f.write(f"| {name} | unknown | ERROR | - | - | - |\n")
            else:
                flag = " [OK]" if info["status"] == "ok" else " [WARN]"
                scope = "core" if info.get("health_included") else "ad_hoc"
                if info.get("stale_artifact"):
                    scope += " stale_artifact"
                f.write(f"| {name} | {scope} | {info['status']}{flag} | {info['lanes']} | {info['pid']} | {info['age_seconds']} |\n")

    print(json_path)
    print(md_path)
    print(json.dumps({"python_count": health["python_process_count"], "zombie_risk": health["zombie_risk"], "watchdogs": len(wd_status)}))


if __name__ == "__main__":
    main()
