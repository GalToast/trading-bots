from __future__ import annotations

import atexit
import contextlib
import importlib.util
import json
import logging
import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Any

import switchboard_server_cleanup

ROOT_DIR = Path(__file__).resolve().parent
ARCHIVE_SERVER = ROOT_DIR / "archive" / "war-room" / "comms_server.py"
LOG_DIR = ROOT_DIR / "reports" / "switchboard"
LIFECYCLE_LOG = LOG_DIR / "switchboard_server_lifecycle.log"
ERROR_LOG = LOG_DIR / "switchboard_server_errors.log"
HEARTBEAT_FILE = LOG_DIR / "switchboard_heartbeat.txt"
SERVER_INSTANCE_ID = f"{os.getpid()}-{int(time.time() * 1000)}"

spec = importlib.util.spec_from_file_location("_archive_switchboard_server", ARCHIVE_SERVER)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load archived switchboard server: {ARCHIVE_SERVER}")

_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_module)

# Restore stdout after the archived server suppresses it during import
sys.stdout = sys.__stdout__

# Point the archived server logic at the live repo-root room so every client
# joins the same bus and message store.
_module.BASE_DIR = str(ROOT_DIR)
_module.DB_FILE = str(ROOT_DIR / "war_room_messages.json")
_module.ARCHIVE_FILE = str(ROOT_DIR / "war_room_messages.archive.jsonl")
_module.LOCK_FILE = f"{_module.DB_FILE}.lock"
_module.MESSAGES_JSONL_FILE = str(ROOT_DIR / "war_room_messages.jsonl")
_module.MESSAGES_JSONL_LOCK = f"{_module.MESSAGES_JSONL_FILE}.lock"
_module.RECEIPTS_JSONL_FILE = str(ROOT_DIR / "war_room_receipts.jsonl")
_module.RECEIPTS_JSONL_LOCK = f"{_module.RECEIPTS_JSONL_FILE}.lock"
_module.AGENTS_JSON_FILE = str(ROOT_DIR / "war_room_agents.json")
_module.TASKS_FILE = str(ROOT_DIR / "war_room_tasks.json")
_module.TASKS_LOCK_FILE = f"{_module.TASKS_FILE}.lock"

mcp = _module.mcp
post_message = _module.post_message
state_lock = _module.state_lock
task_state_lock = _module.task_state_lock
load_state = _module.load_state
load_task_state = _module.load_task_state
write_state = _module.write_state
write_task_state = _module.write_task_state
task_store_status = _module.task_store_status
bootstrap_task_state_file = _module.bootstrap_task_state_file
create_message = _module.create_message
create_task_record = _module.create_task_record
create_task_event_record = _module.create_task_event_record
create_decision_record = _module.create_decision_record
list_tasks_snapshot = _module.list_tasks_snapshot
list_task_events_snapshot = _module.list_task_events_snapshot
list_decisions_snapshot = _module.list_decisions_snapshot
update_task_record = _module.update_task_record
update_decision_record = _module.update_decision_record


def _append_log(path: Path, message: str) -> None:
    with contextlib.suppress(Exception):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{_module.utc_now_iso()}] {message}\n")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"time": _module.utc_now_iso(), **payload}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _server_context_payload(event: str, **extra: Any) -> dict[str, Any]:
    parent_pid = os.getppid()
    return {
        "event": event,
        "instance_id": SERVER_INSTANCE_ID,
        "pid": os.getpid(),
        "ppid": parent_pid,
        "argv": sys.argv,
        "cwd": str(Path.cwd()),
        "python": sys.executable,
        "platform": platform.platform(),
        **extra,
    }


def _stream_snapshot(stream: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "closed": bool(getattr(stream, "closed", False)),
        "isatty": None,
        "type": type(stream).__name__,
    }
    with contextlib.suppress(Exception):
        payload["isatty"] = bool(stream.isatty())
    with contextlib.suppress(Exception):
        payload["name"] = str(getattr(stream, "name", "") or "")
    return payload


def _parent_snapshot(parent_pid: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parent_pid": parent_pid,
        "parent_alive": False,
        "parent_name": "",
    }
    try:
        import psutil

        payload["parent_alive"] = parent_pid > 0 and psutil.pid_exists(parent_pid)
        if payload["parent_alive"]:
            with contextlib.suppress(Exception):
                payload["parent_name"] = str(psutil.Process(parent_pid).name() or "")
    except Exception as exc:
        payload["parent_probe_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def _transport_state_payload(
    *,
    started_monotonic: float,
    exception: BaseException | None = None,
) -> dict[str, Any]:
    parent_pid = os.getppid()
    payload: dict[str, Any] = {
        "runtime_seconds": round(max(0.0, time.monotonic() - started_monotonic), 3),
        "stdin": _stream_snapshot(sys.stdin),
        "stdout": _stream_snapshot(sys.stdout),
        "stderr": _stream_snapshot(sys.stderr),
        "thread_count": threading.active_count(),
        "active_threads": [thread.name for thread in threading.enumerate()[:20]],
        **_parent_snapshot(parent_pid),
    }
    if exception is not None:
        payload["exception_type"] = type(exception).__name__
        payload["exception"] = str(exception)
        payload["exit_classification"] = "exception"
    elif not payload.get("parent_alive"):
        payload["exit_classification"] = "parent_exited"
    elif payload["stdin"].get("closed") or payload["stdout"].get("closed"):
        payload["exit_classification"] = "stdio_stream_closed"
    else:
        payload["exit_classification"] = "mcp_run_returned_without_exception"
    return payload


def _log_server_event(event: str, **extra: Any) -> None:
    _append_jsonl(LIFECYCLE_LOG, _server_context_payload(event, **extra))


def _run_startup_cleanup() -> dict[str, Any]:
    try:
        result = switchboard_server_cleanup.run_startup_cleanup(
            ROOT_DIR / "comms_server.py",
            current_pid=os.getpid(),
        )
    except Exception as exc:
        payload = {
            "event": "startup_cleanup_failed",
            "pid": os.getpid(),
            "error": f"{type(exc).__name__}: {exc}",
        }
        _append_jsonl(ERROR_LOG, payload)
        return {"enabled": False, "failed": True, "error": payload["error"], "exit_current": False}

    _log_server_event(
        "startup_cleanup",
        enabled=bool(result.get("enabled")),
        process_count=int(result.get("process_count") or 0),
        targets=list(result.get("targets") or []),
        exit_current=bool(result.get("exit_current")),
        actions=list(result.get("actions") or []),
        orphaned_pids=list(result.get("orphaned_pids") or []),
        same_parent_duplicate_pids=list(result.get("same_parent_duplicate_pids") or []),
        outdated_pids=list(result.get("outdated_pids") or []),
    )
    return result


def _run_server_heartbeat() -> None:
    """Background daemon that writes a heartbeat timestamp every 30 seconds."""
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            line = f"{now} pid={os.getpid()}\n"
            with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
        time.sleep(30)


if __name__ == "__main__":
    # The archived server expects its own __main__ block to restore the real
    # stdout before FastMCP takes ownership of the JSON-RPC stream. Do the same
    # here because this wrapper imports the archive module instead of executing
    # it directly.
    sys.stdout = sys.__stdout__
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("mcp.server").setLevel(logging.WARNING)
    atexit.register(lambda: _log_server_event("server_process_exit"))
    _log_server_event("server_process_start")
    cleanup_result = _run_startup_cleanup()
    if cleanup_result.get("exit_current"):
        _log_server_event(
            "server_exit_current_duplicate",
            same_parent_duplicate_pids=list(cleanup_result.get("same_parent_duplicate_pids") or []),
        )
        raise SystemExit(0)
    
    # Start server heartbeat daemon (Fix #5)
    heartbeat_thread = threading.Thread(target=_run_server_heartbeat, daemon=True, name="switchboard-heartbeat")
    heartbeat_thread.start()
    
    started_monotonic = time.monotonic()
    _log_server_event("mcp_run_start", transport="stdio")
    try:
        mcp.run(transport="stdio")
    except BaseException as exc:
        _append_jsonl(
            ERROR_LOG,
            _server_context_payload(
                "mcp_run_exception",
                **_transport_state_payload(
                    started_monotonic=started_monotonic,
                    exception=exc,
                ),
            ),
        )
        raise
    finally:
        _log_server_event(
            "mcp_run_returned",
            transport="stdio",
            **_transport_state_payload(started_monotonic=started_monotonic),
        )
