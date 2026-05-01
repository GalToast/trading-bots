from __future__ import annotations

import os
from pathlib import Path

import psutil


def _cmdline_mentions_script(cmdline: list[str] | None, script_path: Path) -> bool:
    if not cmdline:
        return False
    target = str(script_path.resolve()).lower().replace("/", "\\")
    for token in cmdline:
        token_text = str(token or "").strip().strip("\"'").lower().replace("/", "\\")
        if token_text == target or token_text.endswith("\\comms_server.py"):
            return True
    joined = " ".join(str(token or "") for token in cmdline).lower().replace("/", "\\")
    return target in joined


def _safe_proc_attr(proc: object, getter_name: str, default):
    info = getattr(proc, "info", None)
    if isinstance(info, dict) and getter_name in info:
        value = info.get(getter_name)
        return default if value is None else value
    try:
        getter = getattr(proc, getter_name, None)
        if callable(getter):
            return getter()
    except Exception:
        return default
    return default


def server_code_mtime(script_path: Path) -> float:
    root = script_path.resolve().parent
    candidates = [
        script_path,
        root / "archive" / "war-room" / "comms_server.py",
        root / "switchboard_server_cleanup.py",
    ]
    mtimes: list[float] = []
    for candidate in candidates:
        try:
            mtimes.append(float(candidate.stat().st_mtime or 0.0))
        except OSError:
            continue
    return max(mtimes, default=0.0)


def should_terminate_duplicate_siblings() -> bool:
    value = str(os.environ.get("SWITCHBOARD_TERMINATE_DUPLICATE_SIBLINGS", "0") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def should_terminate_outdated_servers() -> bool:
    value = str(os.environ.get("SWITCHBOARD_TERMINATE_OUTDATED_SERVERS", "0") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def should_run_startup_cleanup() -> bool:
    value = str(os.environ.get("SWITCHBOARD_ENABLE_STARTUP_CLEANUP", "1") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def find_duplicate_server_pids(
    processes: list[object],
    *,
    current_pid: int,
    parent_pid: int,
    script_path: Path,
) -> list[int]:
    candidates: list[tuple[float, int]] = []
    for proc in processes:
        pid = int(getattr(proc, "pid", 0) or 0)
        if pid <= 0 or pid == current_pid:
            continue
        proc_parent = int(_safe_proc_attr(proc, "ppid", 0) or 0)
        if proc_parent != parent_pid:
            continue
        cmdline = _safe_proc_attr(proc, "cmdline", [])
        if not _cmdline_mentions_script(cmdline, script_path):
            continue
        created = float(_safe_proc_attr(proc, "create_time", 0.0) or 0.0)
        candidates.append((created, pid))
    return [pid for _, pid in sorted(candidates)]


def find_orphaned_server_pids(
    processes: list[object],
    *,
    current_pid: int,
    script_path: Path,
) -> list[int]:
    candidates: list[tuple[float, int]] = []
    for proc in processes:
        pid = int(getattr(proc, "pid", 0) or 0)
        if pid <= 0 or pid == current_pid:
            continue
        cmdline = _safe_proc_attr(proc, "cmdline", [])
        if not _cmdline_mentions_script(cmdline, script_path):
            continue
        proc_parent = int(_safe_proc_attr(proc, "ppid", 0) or 0)
        if proc_parent > 0 and psutil.pid_exists(proc_parent):
            continue
        created = float(_safe_proc_attr(proc, "create_time", 0.0) or 0.0)
        candidates.append((created, pid))
    return [pid for _, pid in sorted(candidates)]


def list_server_processes(script_path: Path) -> list[psutil.Process]:
    matches: list[psutil.Process] = []
    # On Windows, asking process_iter for parent metadata across the full process
    # table can stall bad or trigger WinError 1455 (paging file too small).
    # Wrap in try/except and fall back to empty list on OOM.
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            cmdline = _safe_proc_attr(proc, "cmdline", [])
            if _cmdline_mentions_script(cmdline, script_path):
                matches.append(proc)
    except Exception:
        # WinError 1455 or MemoryError — skip cleanup safely
        pass
    return matches


def find_same_parent_duplicate_server_pids(
    processes: list[object],
    *,
    script_path: Path,
) -> list[int]:
    grouped: dict[int, list[tuple[float, int]]] = {}
    for proc in processes:
        pid = int(getattr(proc, "pid", 0) or 0)
        if pid <= 0:
            continue
        cmdline = _safe_proc_attr(proc, "cmdline", [])
        if not _cmdline_mentions_script(cmdline, script_path):
            continue
        proc_parent = int(_safe_proc_attr(proc, "ppid", 0) or 0)
        if proc_parent <= 0:
            continue
        created = float(_safe_proc_attr(proc, "create_time", 0.0) or 0.0)
        grouped.setdefault(proc_parent, []).append((created, pid))

    duplicates: list[int] = []
    for items in grouped.values():
        if len(items) <= 1:
            continue
        # Keep the newest child per parent and mark older siblings as duplicates.
        for _, pid in sorted(items)[:-1]:
            duplicates.append(pid)
    return sorted(duplicates)


def find_outdated_server_pids(
    processes: list[object],
    *,
    current_pid: int,
    script_path: Path,
) -> list[int]:
    script_mtime = server_code_mtime(script_path)
    if script_mtime <= 0:
        return []

    candidates: list[tuple[float, int]] = []
    for proc in processes:
        pid = int(getattr(proc, "pid", 0) or 0)
        if pid <= 0 or pid == current_pid:
            continue
        cmdline = _safe_proc_attr(proc, "cmdline", [])
        if not _cmdline_mentions_script(cmdline, script_path):
            continue
        created = float(_safe_proc_attr(proc, "create_time", 0.0) or 0.0)
        if created > 0 and created < script_mtime:
            candidates.append((created, pid))
    return [pid for _, pid in sorted(candidates)]


def build_startup_cleanup_plan(
    processes: list[object],
    *,
    current_pid: int,
    script_path: Path,
    terminate_duplicate_siblings: bool | None = None,
    terminate_outdated_servers: bool | None = None,
) -> dict[str, object]:
    if terminate_duplicate_siblings is None:
        terminate_duplicate_siblings = should_terminate_duplicate_siblings()
    if terminate_outdated_servers is None:
        terminate_outdated_servers = should_terminate_outdated_servers()
    orphaned = find_orphaned_server_pids(processes, current_pid=current_pid, script_path=script_path)
    same_parent_duplicates = find_same_parent_duplicate_server_pids(processes, script_path=script_path)
    outdated = find_outdated_server_pids(processes, current_pid=current_pid, script_path=script_path)
    exit_current = terminate_duplicate_siblings and current_pid in same_parent_duplicates
    targets: list[int] = []
    if not exit_current:
        targets = sorted(
            set(orphaned)
            | ({pid for pid in same_parent_duplicates if pid != current_pid} if terminate_duplicate_siblings else set())
            | ({pid for pid in outdated if pid != current_pid} if terminate_outdated_servers else set())
        )
    return {
        "current_pid": current_pid,
        "process_count": len(processes),
        "orphaned_pids": orphaned,
        "same_parent_duplicate_pids": same_parent_duplicates,
        "outdated_pids": outdated,
        "exit_current": exit_current,
        "targets": targets,
    }


def run_startup_cleanup(
    script_path: Path,
    *,
    current_pid: int | None = None,
) -> dict[str, object]:
    pid = int(current_pid or os.getpid() or 0)
    if not should_run_startup_cleanup():
        return {
            "enabled": False,
            "current_pid": pid,
            "process_count": 0,
            "orphaned_pids": [],
            "same_parent_duplicate_pids": [],
            "outdated_pids": [],
            "exit_current": False,
            "targets": [],
            "actions": [],
        }
    processes = list_server_processes(script_path)
    plan = build_startup_cleanup_plan(
        processes,
        current_pid=pid,
        script_path=script_path,
    )
    actions: list[dict[str, object]] = []
    targets = list(plan.get("targets") or [])
    if targets:
        actions = terminate_processes(targets)
    return {
        "enabled": True,
        **plan,
        "actions": actions,
    }


def snapshot_server_processes(processes: list[object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for proc in processes:
        pid = int(getattr(proc, "pid", 0) or 0)
        if pid <= 0:
            continue
        ppid = int(_safe_proc_attr(proc, "ppid", 0) or 0)
        parent_alive = ppid > 0 and psutil.pid_exists(ppid)
        parent_name = ""
        if parent_alive:
            try:
                parent_name = str(psutil.Process(ppid).name() or "")
            except Exception:
                parent_name = ""
        rows.append(
            {
                "pid": pid,
                "ppid": ppid,
                "parent_alive": parent_alive,
                "parent_name": parent_name,
                "created_at": float(_safe_proc_attr(proc, "create_time", 0.0) or 0.0),
                "cmdline": list(_safe_proc_attr(proc, "cmdline", []) or []),
            }
        )
    return sorted(rows, key=lambda row: (int(row["ppid"]), int(row["pid"])))


def terminate_processes(pids: list[int]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for pid in sorted({int(pid) for pid in pids if int(pid) > 0}):
        try:
            proc = psutil.Process(pid)
        except Exception:
            results.append({"pid": pid, "status": "missing"})
            continue
        try:
            proc.terminate()
            proc.wait(timeout=3)
            results.append({"pid": pid, "status": "terminated"})
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=3)
                results.append({"pid": pid, "status": "killed"})
            except Exception as exc:
                results.append({"pid": pid, "status": "failed", "error": str(exc)})
    return results
