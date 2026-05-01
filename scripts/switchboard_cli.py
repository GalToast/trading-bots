#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

from comms_server import (
    bootstrap_task_state_file,
    create_decision_record,
    create_message,
    create_task_event_record,
    create_task_record,
    list_decisions_snapshot,
    list_task_events_snapshot,
    list_tasks_snapshot,
    load_state,
    load_task_state,
    state_lock,
    task_state_lock,
    task_store_status,
    update_decision_record,
    update_task_record,
    write_state,
    write_task_state,
)
import switchboard_server_cleanup as server_cleanup


DEFAULT_CHANNEL = "general"
DEFAULT_STALE_AFTER_SECONDS = 300
DB_FILE = ROOT / "war_room_messages.json"
ARCHIVE_FILE = ROOT / "war_room_messages.archive.jsonl"
TASKS_FILE = ROOT / "war_room_tasks.json"
SYSTEM_HEALTH_REPORT_FILE = ROOT / "reports" / "system_health_check.json"
SERVER_SCRIPT = ROOT / "comms_server.py"
SWITCHBOARD_REPORT_DIR = ROOT / "reports" / "switchboard"
SERVER_LIFECYCLE_LOG = SWITCHBOARD_REPORT_DIR / "switchboard_server_lifecycle.log"
SERVER_ERROR_LOG = SWITCHBOARD_REPORT_DIR / "switchboard_server_errors.log"
TOOL_ERROR_LOG = SWITCHBOARD_REPORT_DIR / "switchboard_tool_errors.log"
SERVER_HEARTBEAT_FILE = SWITCHBOARD_REPORT_DIR / "switchboard_heartbeat.txt"
CLAIM_MESSAGE_PATTERN = re.compile(r"^\s*(claiming|investigating|taking)\b", re.IGNORECASE)
TASK_ID_PATTERN = re.compile(r"\btask\s*#\s*(\d+)\b", re.IGNORECASE)
QUEUED_TASK_STATUSES = {"todo", "pending", "queued", "backlog", "candidate", "ready"}
CLOSEOUT_READY_PATTERNS = (
    "supports closure",
    "closure-grade",
    "machine truth now supports closure",
    "remaining gap is owner-level success criteria / closeout language",
    "remaining gap is owner-level success criteria",
    "remaining gap is owner closeout language",
    "remaining gap is just owner closeout language",
    "remaining gap is owner-level closeout language",
    "remaining task-12 gap is owner-level success criteria / closeout language",
    "remaining task-12 gap is owner-level success criteria",
    "remaining task-12 gap is owner closeout language",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Direct-file fallback CLI for the local switchboard chat store."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show switchboard store status.")
    status_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Show a combined switchboard/taskboard/server health snapshot.",
    )
    doctor_parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=DEFAULT_STALE_AFTER_SECONDS,
        help="Mark agents stale if heartbeat age exceeds this threshold.",
    )
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    agents_parser = subparsers.add_parser("agents", help="List known agents.")
    agents_parser.add_argument("--include-stale", action="store_true", help="Include stale agents.")
    agents_parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=DEFAULT_STALE_AFTER_SECONDS,
        help="Mark agents stale if heartbeat age exceeds this threshold.",
    )
    agents_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    read_parser = subparsers.add_parser("read", help="Read broadcast channel traffic.")
    read_parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    read_parser.add_argument("--after-id", type=int, default=0)
    read_parser.add_argument("--last", type=int, default=20)
    read_parser.add_argument("--sender", default="", help="Filter by sender token.")
    read_parser.add_argument("--contains", default="", help="Filter by substring.")
    read_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    inbox_parser = subparsers.add_parser("inbox", help="Read messages relevant to one agent.")
    inbox_parser.add_argument("--agent", required=True, help="Agent id, nickname, display name, or tag.")
    inbox_parser.add_argument("--after-id", type=int, default=0)
    inbox_parser.add_argument("--last", type=int, default=20)
    inbox_parser.add_argument("--channel", default="")
    inbox_parser.add_argument(
        "--include-broadcast",
        action="store_true",
        help="Include channel-wide broadcast messages.",
    )
    inbox_parser.add_argument("--sender", default="", help="Filter by sender token.")
    inbox_parser.add_argument("--contains", default="", help="Filter by substring.")
    inbox_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    tail_parser = subparsers.add_parser("tail", help="Poll the broadcast channel until interrupted.")
    tail_parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    tail_parser.add_argument("--after-id", type=int, default=0)
    tail_parser.add_argument("--poll-seconds", type=float, default=2.0)
    tail_parser.add_argument("--sender", default="", help="Filter by sender token.")
    tail_parser.add_argument("--contains", default="", help="Filter by substring.")

    post_parser = subparsers.add_parser("post", help="Post a message directly to the switchboard store.")
    post_parser.add_argument("--sender", required=True)
    post_content_group = post_parser.add_mutually_exclusive_group(required=True)
    post_content_group.add_argument("--content")
    post_content_group.add_argument("--content-file")
    post_parser.add_argument("--to", default="ALL")
    post_parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    post_parser.add_argument("--thread-id", default="")
    post_parser.add_argument("--type", default="message", dest="message_type")
    post_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_create_parser = subparsers.add_parser("task-create", help="Create a structured switchboard task.")
    task_create_parser.add_argument("--title", required=True)
    task_create_parser.add_argument("--creator", required=True)
    task_create_parser.add_argument("--description", default="")
    task_create_parser.add_argument("--description-file")
    task_create_parser.add_argument("--slice-description", default="")
    task_create_parser.add_argument("--slice-description-file")
    task_create_parser.add_argument("--board", default="main")
    task_create_parser.add_argument("--status", default="todo")
    task_create_parser.add_argument("--priority", default="normal")
    task_create_parser.add_argument("--owner", default="")
    task_create_parser.add_argument("--depends-on", action="append", default=[], type=int)
    task_create_parser.add_argument("--blocking-decision-id", default="")
    task_create_parser.add_argument("--stale-after-minutes", type=int, default=120)
    task_create_parser.add_argument("--evidence")
    task_create_parser.add_argument("--evidence-file")
    task_create_parser.add_argument("--label", action="append", dest="labels", default=[])
    task_create_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_create_from_message_parser = subparsers.add_parser(
        "task-create-from-message",
        help="Promote a switchboard room message into a structured task with source metadata attached.",
    )
    task_create_from_message_parser.add_argument("--message-id", required=True, type=int)
    task_create_from_message_parser.add_argument("--creator", required=True)
    task_create_from_message_parser.add_argument("--title", default="")
    task_create_from_message_parser.add_argument("--owner", default="")
    task_create_from_message_parser.add_argument(
        "--owner-from-message",
        action="store_true",
        help="Default the task owner to the message sender when --owner is omitted.",
    )
    task_create_from_message_parser.add_argument("--board", default="main")
    task_create_from_message_parser.add_argument("--status", default="todo")
    task_create_from_message_parser.add_argument("--priority", default="normal")
    task_create_from_message_parser.add_argument("--slice-description", default="")
    task_create_from_message_parser.add_argument("--slice-description-file")
    task_create_from_message_parser.add_argument("--stale-after-minutes", type=int, default=120)
    task_create_from_message_parser.add_argument("--evidence")
    task_create_from_message_parser.add_argument("--evidence-file")
    task_create_from_message_parser.add_argument("--label", action="append", dest="labels", default=[])
    task_create_from_message_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_list_parser = subparsers.add_parser("task-list", help="List structured switchboard tasks.")
    task_list_parser.add_argument("--board", default="")
    task_list_parser.add_argument("--status", default="")
    task_list_parser.add_argument("--owner", default="")
    task_list_parser.add_argument("--contains", default="")
    task_list_parser.add_argument("--label", default="")
    task_list_parser.add_argument(
        "--include-stale",
        action="store_true",
        help="Include stale in-progress tasks in results.",
    )
    task_list_parser.add_argument(
        "--stale-only",
        action="store_true",
        help="Only show stale tasks.",
    )
    task_list_parser.add_argument("--include-done", action="store_true", help="Include done/closed tasks.")
    task_list_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_update_parser = subparsers.add_parser("task-update", help="Update a structured switchboard task.")
    task_update_parser.add_argument("--task-id", required=True, type=int)
    task_update_parser.add_argument("--title")
    task_update_parser.add_argument("--description")
    task_update_parser.add_argument("--description-file")
    task_update_parser.add_argument("--board")
    task_update_parser.add_argument("--status")
    task_update_parser.add_argument("--priority")
    task_update_parser.add_argument("--owner")
    task_update_parser.add_argument("--slice-description")
    task_update_parser.add_argument("--slice-description-file")
    task_update_parser.add_argument("--depends-on", action="append", type=int, dest="depends_on")
    task_update_parser.add_argument("--blocking-decision-id")
    task_update_parser.add_argument("--stale-after-minutes", type=int, dest="stale_after_minutes")
    task_update_parser.add_argument("--evidence")
    task_update_parser.add_argument("--evidence-file")
    task_update_parser.add_argument("--label", action="append", dest="labels")
    task_update_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_claim_parser = subparsers.add_parser(
        "task-claim",
        help="Claim a task and refresh its heartbeat in one step.",
    )
    task_claim_parser.add_argument("--task-id", required=True, type=int)
    task_claim_parser.add_argument("--owner", required=True)
    task_claim_parser.add_argument("--status", default="in_progress")
    task_claim_parser.add_argument("--stale-after-minutes", type=int, dest="stale_after_minutes")
    task_claim_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_release_parser = subparsers.add_parser(
        "task-release",
        help="Release a task by clearing its owner and optionally changing status.",
    )
    task_release_parser.add_argument("--task-id", required=True, type=int)
    task_release_parser.add_argument("--status", default="todo")
    task_release_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_heartbeat_parser = subparsers.add_parser("task-heartbeat", help="Refresh a task heartbeat.")
    task_heartbeat_parser.add_argument("--task-id", required=True, type=int)
    task_heartbeat_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_comment_parser = subparsers.add_parser(
        "task-comment",
        help="Add a structured comment or handoff note to a taskboard item.",
    )
    task_comment_parser.add_argument("--task-id", required=True, type=int)
    task_comment_parser.add_argument("--author", required=True)
    task_comment_group = task_comment_parser.add_mutually_exclusive_group(required=True)
    task_comment_group.add_argument("--content")
    task_comment_group.add_argument("--content-file")
    task_comment_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_comment_from_message_parser = subparsers.add_parser(
        "task-comment-from-message",
        help="Attach a switchboard room message onto an existing task as a structured comment.",
    )
    task_comment_from_message_parser.add_argument("--task-id", required=True, type=int)
    task_comment_from_message_parser.add_argument("--message-id", required=True, type=int)
    task_comment_from_message_parser.add_argument("--author", default="")
    task_comment_from_message_parser.add_argument(
        "--author-from-message",
        action="store_true",
        help="Default the task comment author to the message sender when --author is omitted.",
    )
    task_comment_from_message_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_events_parser = subparsers.add_parser(
        "task-events",
        help="List structured comments and activity for one taskboard item.",
    )
    task_events_parser.add_argument("--task-id", required=True, type=int)
    task_events_parser.add_argument("--limit", type=int, default=20)
    task_events_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_store_status_parser = subparsers.add_parser(
        "task-store-status",
        help="Show whether the taskboard is using the dedicated task store or legacy embedded fallback.",
    )
    task_store_status_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    task_bootstrap_parser = subparsers.add_parser(
        "task-bootstrap",
        help="Create the dedicated task store from legacy embedded task data when needed.",
    )
    task_bootstrap_parser.add_argument("--force", action="store_true", help="Rewrite the dedicated task store even if it already exists.")
    task_bootstrap_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    decision_create_parser = subparsers.add_parser("decision-create", help="Create a structured decision queue item.")
    decision_create_parser.add_argument("--title", required=True)
    decision_create_parser.add_argument("--creator", required=True)
    decision_create_parser.add_argument("--summary", default="")
    decision_create_parser.add_argument("--summary-file")
    decision_create_parser.add_argument("--status", default="pending")
    decision_create_parser.add_argument("--owner", default="")
    decision_create_parser.add_argument("--recommended-option", default="")
    decision_create_parser.add_argument("--option", action="append", dest="options", default=[])
    decision_create_parser.add_argument("--related-task-id", action="append", dest="related_task_ids", default=[], type=int)
    decision_create_parser.add_argument("--evidence")
    decision_create_parser.add_argument("--evidence-file")
    decision_create_parser.add_argument("--label", action="append", dest="labels", default=[])
    decision_create_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    decision_list_parser = subparsers.add_parser("decision-list", help="List structured decision queue items.")
    decision_list_parser.add_argument("--status", default="")
    decision_list_parser.add_argument("--owner", default="")
    decision_list_parser.add_argument("--contains", default="")
    decision_list_parser.add_argument("--label", default="")
    decision_list_parser.add_argument("--include-done", action="store_true", help="Include resolved/closed decisions.")
    decision_list_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    decision_update_parser = subparsers.add_parser("decision-update", help="Update a structured decision queue item.")
    decision_update_parser.add_argument("--decision-id", required=True, type=int)
    decision_update_parser.add_argument("--title")
    decision_update_parser.add_argument("--summary")
    decision_update_parser.add_argument("--summary-file")
    decision_update_parser.add_argument("--status")
    decision_update_parser.add_argument("--owner")
    decision_update_parser.add_argument("--recommended-option")
    decision_update_parser.add_argument("--option", action="append", dest="options")
    decision_update_parser.add_argument("--related-task-id", action="append", dest="related_task_ids", type=int)
    decision_update_parser.add_argument("--evidence")
    decision_update_parser.add_argument("--evidence-file")
    decision_update_parser.add_argument("--label", action="append", dest="labels")
    decision_update_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    coordination_sweep_parser = subparsers.add_parser(
        "coordination-sweep",
        help="Summarize stale tasks, open decisions, and recent unmatched claim-like room messages.",
    )
    coordination_sweep_parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    coordination_sweep_parser.add_argument("--after-id", type=int, default=0)
    coordination_sweep_parser.add_argument("--last", type=int, default=50)
    coordination_sweep_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    server_status_parser = subparsers.add_parser(
        "server-status",
        help="Inspect comms_server.py MCP server processes.",
    )
    server_status_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    server_cleanup_parser = subparsers.add_parser(
        "server-cleanup",
        help="Terminate orphaned or same-parent duplicate comms_server.py processes.",
    )
    server_cleanup_parser.add_argument(
        "--orphans-only",
        action="store_true",
        help="Only target orphaned comms_server.py processes.",
    )
    server_cleanup_parser.add_argument(
        "--duplicates-only",
        action="store_true",
        help="Only target same-parent duplicate comms_server.py processes.",
    )
    server_cleanup_parser.add_argument(
        "--outdated-only",
        action="store_true",
        help="Target live comms_server.py processes that predate the checked-in server file.",
    )
    server_cleanup_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually terminate the targeted processes. Without this flag, emit a dry-run plan only.",
    )
    server_cleanup_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    return parser.parse_args()


def parse_timestamp(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value))
    except Exception:
        return None


def parse_log_timestamp(row: dict[str, Any]) -> dt.datetime | None:
    timestamp = parse_timestamp(str(row.get("time") or ""))
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    return timestamp


def read_jsonl_tail(path: Path, max_rows: int = 200) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max_rows:]:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def compact_event(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "time",
        "event",
        "instance_id",
        "pid",
        "ppid",
        "parent_pid",
        "parent_alive",
        "parent_name",
        "runtime_seconds",
        "transport",
        "exit_classification",
        "exception_type",
        "error_type",
        "error",
    )
    return {key: row.get(key) for key in keys if key in row}


def build_transport_telemetry_payload(now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    lifecycle_rows = read_jsonl_tail(SERVER_LIFECYCLE_LOG, max_rows=300)
    error_rows = read_jsonl_tail(SERVER_ERROR_LOG, max_rows=100)
    tool_error_rows = read_jsonl_tail(TOOL_ERROR_LOG, max_rows=100)

    def latest_event(event_name: str, rows: list[dict[str, Any]] = lifecycle_rows) -> dict[str, Any] | None:
        for row in reversed(rows):
            if str(row.get("event") or "") == event_name:
                return row
        return None

    recent_cutoff = now - dt.timedelta(hours=1)
    recent_returns = [
        row
        for row in lifecycle_rows
        if str(row.get("event") or "") == "mcp_run_returned"
        and (parse_log_timestamp(row) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) >= recent_cutoff
    ]
    recent_exceptions = [
        row
        for row in error_rows
        if str(row.get("event") or "") == "mcp_run_exception"
        and (parse_log_timestamp(row) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) >= recent_cutoff
    ]
    recent_tool_errors = [
        row
        for row in tool_error_rows
        if (parse_log_timestamp(row) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) >= recent_cutoff
    ]

    heartbeat_exists = SERVER_HEARTBEAT_FILE.exists()
    heartbeat_at = ""
    heartbeat_age_seconds: float | None = None
    if heartbeat_exists:
        with contextlib.suppress(Exception):
            line = SERVER_HEARTBEAT_FILE.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0]
            heartbeat_at = line.split(" ", 1)[0].strip()
            parsed = parse_timestamp(heartbeat_at)
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                heartbeat_age_seconds = round(max(0.0, (now - parsed).total_seconds()), 1)

    level = "ok"
    reasons: list[str] = []
    actions: list[str] = []
    if not lifecycle_rows:
        level = "warning"
        reasons.append("no_lifecycle_log_rows")
        actions.append("run python scripts/probe_switchboard_mcp.py --timeout-seconds 8 --unbuffered")
    if not heartbeat_exists:
        level = "warning"
        reasons.append("missing_server_heartbeat")
        actions.append("start or probe an MCP client to refresh switchboard heartbeat")
    elif heartbeat_age_seconds is None:
        level = "warning"
        reasons.append("unparseable_server_heartbeat")
    elif heartbeat_age_seconds > 120:
        level = "warning"
        reasons.append(f"stale_server_heartbeat_seconds={heartbeat_age_seconds}")
        actions.append("run python scripts/probe_switchboard_mcp.py --timeout-seconds 8 --unbuffered")
    if recent_exceptions:
        level = "warning"
        reasons.append(f"recent_mcp_run_exceptions={len(recent_exceptions)}")
        actions.append("inspect reports/switchboard/switchboard_server_errors.log")
    if len(recent_returns) >= 3:
        level = "warning"
        reasons.append(f"recent_transport_returns={len(recent_returns)}")
        actions.append("inspect lifecycle rows for exit_classification and parent process churn")
    if recent_tool_errors:
        level = "warning"
        reasons.append(f"recent_tool_errors={len(recent_tool_errors)}")
        actions.append("inspect reports/switchboard/switchboard_tool_errors.log")
    if not reasons:
        reasons.append("transport_telemetry_clean")

    return {
        "ok": True,
        "lifecycle_log": str(SERVER_LIFECYCLE_LOG),
        "error_log": str(SERVER_ERROR_LOG),
        "tool_error_log": str(TOOL_ERROR_LOG),
        "heartbeat_file": str(SERVER_HEARTBEAT_FILE),
        "heartbeat": {
            "exists": heartbeat_exists,
            "updated_at": heartbeat_at,
            "age_seconds": heartbeat_age_seconds,
            "stale": heartbeat_age_seconds is None or heartbeat_age_seconds > 120,
        },
        "latest": {
            "server_process_start": compact_event(latest_event("server_process_start") or {}),
            "mcp_run_start": compact_event(latest_event("mcp_run_start") or {}),
            "mcp_run_returned": compact_event(latest_event("mcp_run_returned") or {}),
            "mcp_run_exception": compact_event(latest_event("mcp_run_exception", error_rows) or {}),
            "tool_error": compact_event(tool_error_rows[-1] if tool_error_rows else {}),
        },
        "recent": {
            "mcp_run_returned_count_1h": len(recent_returns),
            "mcp_run_exception_count_1h": len(recent_exceptions),
            "tool_error_count_1h": len(recent_tool_errors),
            "mcp_run_returned": [compact_event(row) for row in recent_returns[-10:]],
            "mcp_run_exceptions": [compact_event(row) for row in recent_exceptions[-10:]],
            "tool_errors": [compact_event(row) for row in recent_tool_errors[-10:]],
        },
        "diagnosis": {
            "level": level,
            "reasons": reasons,
            "recommended_actions": sorted(set(actions)),
        },
    }


def read_text_argument_file(path_text: str) -> str:
    text = Path(path_text).read_text(encoding="utf-8")
    return text.rstrip("\r\n")


def resolve_text_argument(value: str | None, file_path: str | None) -> str | None:
    if file_path:
        return read_text_argument_file(file_path)
    return value


def message_by_id(state: dict[str, Any], message_id: int) -> dict[str, Any]:
    for message in state.get("messages") or []:
        if int(message.get("id") or 0) == int(message_id):
            return message
    raise ValueError(f"message_not_found:{message_id}")


def derive_task_title_from_message(message: dict[str, Any]) -> str:
    content = str(message.get("content") or "")
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[#>*`\-\u2705\u26a0\ufe0f\ud83d\udea8\ud83d\udcca\s]+", "", line)
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"\s+", " ", line).strip(" -:")
        if line:
            return line[:120]
    sender = str(message.get("from_agent_id") or message.get("from") or "switchboard")
    return f"Promoted room message from {sender}"[:120]


def message_to_task_description(message: dict[str, Any]) -> str:
    source = str(message.get("from_agent_id") or message.get("from") or "")
    timestamp = str(message.get("time") or "")
    channel = str(message.get("channel") or DEFAULT_CHANNEL)
    message_type = str(message.get("message_type") or "message")
    content = str(message.get("content") or "").rstrip()
    header = (
        f"Promoted from switchboard message {int(message.get('id') or 0)} "
        f"by {source or 'unknown'} at {timestamp or 'unknown time'} "
        f"({channel}/{message_type})."
    )
    if not content:
        return header
    return f"{header}\n\n{content}"


def message_to_task_comment(message: dict[str, Any]) -> str:
    source = str(message.get("from_agent_id") or message.get("from") or "unknown")
    timestamp = str(message.get("time") or "unknown time")
    channel = str(message.get("channel") or DEFAULT_CHANNEL)
    message_type = str(message.get("message_type") or "message")
    content = str(message.get("content") or "").rstrip()
    header = (
        f"Attached from switchboard message {int(message.get('id') or 0)} "
        f"by {source} at {timestamp} ({channel}/{message_type})."
    )
    if not content:
        return header
    return f"{header}\n\n{content}"


def parse_json_payload(value: str | None, file_path: str | None = None) -> dict[str, Any] | None:
    if file_path:
        text = read_text_argument_file(file_path).strip()
    elif value is None:
        return None
    else:
        text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_agent_matches(state: dict[str, Any], token: str) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    aliases: set[str] = set()
    target = str(token or "").strip().lower()
    if not target:
        return ids, aliases

    agents = state.get("agents") or {}
    for agent_id, payload in agents.items():
        agent = payload if isinstance(payload, dict) else {}
        values = {
            str(agent_id).strip(),
            str(agent.get("nickname") or "").strip(),
            str(agent.get("display_name") or "").strip(),
            str(agent.get("tag") or "").strip(),
        }
        tag = str(agent.get("tag") or "").strip()
        if tag:
            values.add(f"@{tag}")
        lowered = {value.lower() for value in values if value}
        if target in lowered:
            ids.add(str(agent_id))
            aliases.update(lowered)

    if not ids:
        ids.add(str(token).strip())
        aliases.add(target)
    return ids, aliases


def sender_matches(state: dict[str, Any], message: dict[str, Any], sender_token: str) -> bool:
    sender_token = str(sender_token or "").strip()
    if not sender_token:
        return True
    ids, aliases = resolve_agent_matches(state, sender_token)
    from_agent_id = str(message.get("from_agent_id") or "")
    if from_agent_id and from_agent_id in ids:
        return True
    from_label = str(message.get("from") or "").strip().lower()
    return from_label in aliases


def contains_matches(message: dict[str, Any], needle: str) -> bool:
    needle = str(needle or "").strip().lower()
    if not needle:
        return True
    content = str(message.get("content") or "").lower()
    return needle in content


def broadcast_messages(
    state: dict[str, Any],
    *,
    channel: str,
    after_id: int,
    sender: str,
    contains: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for message in state.get("messages") or []:
        if int(message.get("id") or 0) <= after_id:
            continue
        if str(message.get("channel") or DEFAULT_CHANNEL) != channel:
            continue
        if str(message.get("to") or "").upper() != "ALL":
            continue
        if not sender_matches(state, message, sender):
            continue
        if not contains_matches(message, contains):
            continue
        results.append(message)
    return results


def inbox_messages(
    state: dict[str, Any],
    *,
    agent_token: str,
    after_id: int,
    channel: str,
    include_broadcast: bool,
    sender: str,
    contains: str,
) -> list[dict[str, Any]]:
    ids, aliases = resolve_agent_matches(state, agent_token)
    results: list[dict[str, Any]] = []
    for message in state.get("messages") or []:
        if int(message.get("id") or 0) <= after_id:
            continue
        message_channel = str(message.get("channel") or DEFAULT_CHANNEL)
        if channel and message_channel != channel:
            continue

        to_agent_id = str(message.get("to_agent_id") or "")
        to_label = str(message.get("to") or "").strip().lower()
        is_broadcast = str(message.get("to") or "").upper() == "ALL"
        if not (
            (include_broadcast and is_broadcast)
            or (to_agent_id and to_agent_id in ids)
            or (to_label and to_label in aliases)
        ):
            continue
        if not sender_matches(state, message, sender):
            continue
        if not contains_matches(message, contains):
            continue
        results.append(message)
    return results


def build_status(state: dict[str, Any]) -> dict[str, Any]:
    messages = state.get("messages") or []
    agents = state.get("agents") or {}
    latest = messages[-1] if messages else {}
    return {
        "ok": True,
        "db_file": str(DB_FILE),
        "archive_file": str(ARCHIVE_FILE),
        "message_count": len(messages),
        "agent_count": len(agents),
        "next_message_id": int(state.get("next_message_id") or 1),
        "last_id": int(latest.get("id") or 0),
        "last_time": str(latest.get("time") or ""),
    }


def build_doctor_payload(state: dict[str, Any], stale_after_seconds: int) -> dict[str, Any]:
    status = build_status(state)
    agents = list_agents_payload(state, stale_after_seconds=stale_after_seconds, include_stale=True)
    task_state = load_task_state()
    tasks = list_tasks_snapshot(task_state, include_done=True)
    decisions = list_decisions_snapshot(task_state, include_done=True)
    open_statuses = {"done", "closed", "complete", "completed", "cancelled", "canceled"}
    open_decision_statuses = {"done", "closed", "resolved", "cancelled", "archived"}
    open_tasks = [task for task in tasks if str(task.get("status") or "").strip().lower() not in open_statuses]
    open_decisions = [
        decision
        for decision in decisions
        if str(decision.get("status") or "").strip().lower() not in open_decision_statuses
    ]
    server = build_server_process_payload()
    transport_telemetry = server.get("transport_telemetry") or {}
    transport_diagnosis = transport_telemetry.get("diagnosis") or {}
    return {
        "ok": True,
        "store": status,
        "agents": {
            "total": len(agents),
            "online": sum(1 for agent in agents if not agent.get("stale", True)),
            "stale": sum(1 for agent in agents if agent.get("stale", True)),
        },
        "tasks": {
            "store": task_store_status(),
            "total": len(tasks),
            "open": len(open_tasks),
            "done": len(tasks) - len(open_tasks),
        },
        "decisions": {
            "total": len(decisions),
            "open": len(open_decisions),
            "done": len(decisions) - len(open_decisions),
        },
        "server": {
            "process_count": int(server.get("process_count") or 0),
            "orphaned_count": len(list(server.get("orphaned_pids") or [])),
            "same_parent_duplicate_count": len(list(server.get("same_parent_duplicate_pids") or [])),
            "outdated_count": len(list(server.get("outdated_pids") or [])),
            "reload_recommended": bool(server.get("reload_recommended")),
            "script_mtime_iso": str(server.get("script_mtime_iso") or ""),
            "risk_level": str(server.get("risk_level") or "unknown"),
            "risk_reasons": list(server.get("risk_reasons") or []),
            "recommended_actions": list(server.get("recommended_actions") or []),
            "transport": transport_telemetry,
        },
        "healthy": (
            str(server.get("risk_level") or "") == "ok"
            and str(transport_diagnosis.get("level") or "ok") == "ok"
        ),
    }


def stale_flag(agent: dict[str, Any], stale_after_seconds: int) -> bool:
    last_seen = parse_timestamp(str(agent.get("last_seen") or ""))
    if last_seen is None:
        return True
    now = dt.datetime.now(dt.timezone.utc)
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=dt.timezone.utc)
    return (now - last_seen).total_seconds() > stale_after_seconds


def list_agents_payload(state: dict[str, Any], stale_after_seconds: int, include_stale: bool) -> list[dict[str, Any]]:
    agents = []
    for agent_id in sorted((state.get("agents") or {}).keys()):
        raw = state["agents"][agent_id]
        agent = dict(raw if isinstance(raw, dict) else {})
        agent["stale"] = stale_flag(agent, stale_after_seconds)
        if include_stale or not agent["stale"]:
            agents.append(agent)
    return agents


def render_message(message: dict[str, Any]) -> str:
    msg_id = int(message.get("id") or 0)
    timestamp = str(message.get("time") or "")
    sender = str(message.get("from") or message.get("from_agent_id") or "?")
    recipient = str(message.get("to") or "ALL")
    channel = str(message.get("channel") or DEFAULT_CHANNEL)
    message_type = str(message.get("message_type") or "message")
    thread_id = str(message.get("thread_id") or "")
    content = str(message.get("content") or "")
    header = f"[{msg_id}] {timestamp} {sender} -> {recipient} ({channel}/{message_type})"
    if thread_id:
        header = f"{header} thread={thread_id}"
    return f"{header}\n  {content}"


def emit_messages(messages: list[dict[str, Any]]) -> None:
    if not messages:
        print("No messages.")
        return
    for message in messages:
        print(render_message(message))


def command_status(args: argparse.Namespace) -> int:
    payload = build_status(load_state())
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"db_file={payload['db_file']}")
        print(f"archive_file={payload['archive_file']}")
        print(f"message_count={payload['message_count']} agent_count={payload['agent_count']}")
        print(f"last_id={payload['last_id']} next_message_id={payload['next_message_id']}")
        print(f"last_time={payload['last_time'] or 'n/a'}")
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    payload = build_doctor_payload(load_state(), stale_after_seconds=args.stale_after_seconds)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"healthy={payload['healthy']}")
    store = payload["store"]
    print(
        f"messages={store['message_count']} agents={payload['agents']['total']} "
        f"tasks_open={payload['tasks']['open']} tasks_done={payload['tasks']['done']}"
    )
    print(
        f"decisions_open={payload['decisions']['open']} "
        f"decisions_done={payload['decisions']['done']}"
    )
    print(
        f"task_store={payload['tasks']['store']['source']} "
        f"path={payload['tasks']['store']['path']}"
    )
    print(
        f"server_processes={payload['server']['process_count']} "
        f"orphaned={payload['server']['orphaned_count']} "
        f"same_parent_duplicates={payload['server']['same_parent_duplicate_count']} "
        f"outdated={payload['server']['outdated_count']} "
        f"reload_recommended={payload['server']['reload_recommended']} "
        f"risk={payload['server']['risk_level']}"
    )
    if payload["server"]["risk_reasons"]:
        print("risk_reasons=" + "; ".join(payload["server"]["risk_reasons"]))
    if payload["server"]["recommended_actions"]:
        print("recommended_actions=" + "; ".join(payload["server"]["recommended_actions"]))
    if payload["server"]["script_mtime_iso"]:
        print(f"server_script_mtime={payload['server']['script_mtime_iso']}")
    transport = payload["server"].get("transport") or {}
    diagnosis = transport.get("diagnosis") or {}
    if diagnosis:
        print(f"transport={diagnosis.get('level', 'unknown')}")
        if diagnosis.get("reasons"):
            print("transport_reasons=" + "; ".join(str(reason) for reason in diagnosis["reasons"]))
        if diagnosis.get("recommended_actions"):
            print("transport_actions=" + "; ".join(str(action) for action in diagnosis["recommended_actions"]))
    heartbeat = transport.get("heartbeat") or {}
    if heartbeat.get("exists"):
        print(
            f"transport_heartbeat={heartbeat.get('updated_at') or 'n/a'} "
            f"age_seconds={heartbeat.get('age_seconds')}"
        )
    print(f"last_id={store['last_id']} last_time={store['last_time'] or 'n/a'}")
    return 0


def command_agents(args: argparse.Namespace) -> int:
    state = load_state()
    agents = list_agents_payload(state, args.stale_after_seconds, args.include_stale)
    payload = {"ok": True, "count": len(agents), "agents": agents}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if not agents:
        print("No agents.")
        return 0
    for agent in agents:
        print(
            f"{agent.get('agent_id', '')} "
            f"display={agent.get('display_name', '')} "
            f"status={agent.get('status', '')} "
            f"stale={agent.get('stale', True)} "
            f"last_seen={agent.get('last_seen', '')}"
        )
    return 0


def command_read(args: argparse.Namespace) -> int:
    state = load_state()
    messages = broadcast_messages(
        state,
        channel=args.channel,
        after_id=args.after_id,
        sender=args.sender,
        contains=args.contains,
    )
    if args.last > 0:
        messages = messages[-args.last :]
    payload = {
        "ok": True,
        "channel": args.channel,
        "count": len(messages),
        "last_id": int(messages[-1]["id"]) if messages else args.after_id,
        "messages": messages,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        emit_messages(messages)
    return 0


def command_inbox(args: argparse.Namespace) -> int:
    state = load_state()
    messages = inbox_messages(
        state,
        agent_token=args.agent,
        after_id=args.after_id,
        channel=args.channel,
        include_broadcast=args.include_broadcast,
        sender=args.sender,
        contains=args.contains,
    )
    if args.last > 0:
        messages = messages[-args.last :]
    payload = {
        "ok": True,
        "agent": args.agent,
        "count": len(messages),
        "last_id": int(messages[-1]["id"]) if messages else args.after_id,
        "messages": messages,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        emit_messages(messages)
    return 0


def command_tail(args: argparse.Namespace) -> int:
    after_id = int(args.after_id)
    try:
        while True:
            state = load_state()
            messages = broadcast_messages(
                state,
                channel=args.channel,
                after_id=after_id,
                sender=args.sender,
                contains=args.contains,
            )
            if messages:
                emit_messages(messages)
                after_id = int(messages[-1]["id"])
            time.sleep(max(float(args.poll_seconds), 0.2))
    except KeyboardInterrupt:
        return 0


def command_post(args: argparse.Namespace) -> int:
    content = resolve_text_argument(args.content, getattr(args, "content_file", None)) or ""
    with state_lock():
        state = load_state()
        message = create_message(
            state,
            sender=args.sender,
            to=args.to,
            content=content,
            channel=args.channel,
            thread_id=args.thread_id,
            message_type=args.message_type,
        )
        write_state(state)
    payload = {"ok": True, "message": message}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_message(message))
    return 0


def render_task(task: dict[str, Any]) -> str:
    labels = ",".join(str(item) for item in list(task.get("labels") or [])) or "-"
    stale = task.get("stale", False)
    stale_after_minutes = int(task.get("stale_after_minutes") or 120)
    last_heartbeat = str(task.get("last_heartbeat") or task.get("updated_at") or "")
    return (
        f"[task:{task.get('id')}] "
        f"{task.get('board', 'main')}/{task.get('status', 'todo')} "
        f"priority={task.get('priority', 'normal')} "
        f"owner={task.get('owner', '-') or '-'} "
        f"stale={str(stale).lower()} stale_after_minutes={stale_after_minutes} "
        f"heartbeat={last_heartbeat} labels={labels}\n"
        f"  {task.get('title', '')}\n"
        f"  {task.get('description', '')}"
    )


def render_task_event(event: dict[str, Any]) -> str:
    return (
        f"[task-event:{event.get('id')}] "
        f"task={event.get('task_id')} "
        f"type={event.get('type', 'comment')} "
        f"author={event.get('author', '-')}\n"
        f"  {event.get('content', '')}"
    )


def render_decision(decision: dict[str, Any]) -> str:
    labels = ",".join(str(item) for item in list(decision.get("labels") or [])) or "-"
    options = ",".join(str(item) for item in list(decision.get("options") or [])) or "-"
    related = ",".join(str(item) for item in list(decision.get("related_task_ids") or [])) or "-"
    return (
        f"[decision:{decision.get('id')}] "
        f"status={decision.get('status', 'pending')} "
        f"owner={decision.get('owner', '-') or '-'} "
        f"recommended={decision.get('recommended_option', '-') or '-'} "
        f"related_tasks={related} labels={labels}\n"
        f"  {decision.get('title', '')}\n"
        f"  {decision.get('summary', '')}\n"
        f"  options={options}"
    )


def emit_tasks(tasks: list[dict[str, Any]]) -> None:
    if not tasks:
        print("No tasks.")
        return
    for task in tasks:
        print(render_task(task))


def emit_task_events(events: list[dict[str, Any]]) -> None:
    if not events:
        print("No task events.")
        return
    for event in events:
        print(render_task_event(event))


def emit_decisions(decisions: list[dict[str, Any]]) -> None:
    if not decisions:
        print("No decisions.")
        return
    for decision in decisions:
        print(render_decision(decision))


def is_claim_like_message(message: dict[str, Any]) -> bool:
    content = str(message.get("content") or "")
    for line in content.splitlines():
        if not line.strip():
            continue
        return bool(CLAIM_MESSAGE_PATTERN.search(line))
    return False


def owner_has_open_task(task_state: dict[str, Any], owner_token: str) -> bool:
    open_tasks = list_tasks_snapshot(
        task_state,
        owner=owner_token,
        include_stale=True,
        include_done=False,
    )
    return bool(open_tasks)


def claim_message_owner_tokens(message: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for raw in (message.get("from_agent_id"), message.get("from")):
        token = str(raw or "").strip()
        if not token:
            continue
        if token not in candidates:
            candidates.append(token)
        unprefixed = token.lstrip("@").strip()
        if unprefixed and unprefixed not in candidates:
            candidates.append(unprefixed)
    return candidates


def claim_message_task_id(message: dict[str, Any]) -> int | None:
    content = str(message.get("content") or "")
    match = TASK_ID_PATTERN.search(content)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def task_owner_matches_tokens(task: dict[str, Any], owner_tokens: list[str]) -> bool:
    task_owner = str(task.get("owner") or "").strip()
    if not task_owner:
        return False
    candidates = [task_owner, task_owner.lstrip("@").strip()]
    return any(candidate and candidate in owner_tokens for candidate in candidates)


def task_events_by_task(task_state: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for event in task_state.get("task_events") or []:
        task_id = int(event.get("task_id") or 0)
        if task_id <= 0:
            continue
        grouped.setdefault(task_id, []).append(event if isinstance(event, dict) else {})
    return grouped


def task_has_closeout_ready_signal(task: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    for event in reversed(events[-5:]):
        content = str(event.get("content") or "").lower()
        if not content:
            continue
        if any(pattern in content for pattern in CLOSEOUT_READY_PATTERNS):
            return True
    return False


def load_system_health_summary(report_file: Path = SYSTEM_HEALTH_REPORT_FILE) -> dict[str, Any]:
    summary = {
        "available": False,
        "health_status": "missing",
        "timestamp": "",
        "python_process_count": 0,
        "zombie_risk": False,
        "mt5_connected": None,
        "non_ok_watchdog_count": 0,
        "stale_watchdog_count": 0,
        "non_ok_watchdog_groups": [],
        "stale_watchdog_groups": [],
    }
    try:
        raw = json.loads(report_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return summary

    watchdog_status = raw.get("watchdog_status") or {}
    non_ok_watchdogs: list[str] = []
    stale_watchdogs: list[str] = []
    if isinstance(watchdog_status, dict):
        for group_name, group_status in watchdog_status.items():
            info = group_status if isinstance(group_status, dict) else {}
            if not bool(info.get("health_included", True)):
                continue
            status = str(info.get("status") or "").strip().lower()
            age_seconds = float(info.get("age_seconds") or 0)
            if status != "ok":
                non_ok_watchdogs.append(str(group_name))
            if status != "ok" and age_seconds >= 300:
                stale_watchdogs.append(str(group_name))

    mt5_status = raw.get("mt5_status") or {}
    mt5_connected = None
    if isinstance(mt5_status, dict):
        mt5_connected = bool(mt5_status.get("connected")) and bool(mt5_status.get("terminal_connected", True))

    zombie_risk = bool(raw.get("zombie_risk"))
    health_status = "ok"
    if zombie_risk or non_ok_watchdogs or mt5_connected is False:
        health_status = "warning"

    summary.update(
        {
            "available": True,
            "health_status": health_status,
            "timestamp": str(raw.get("timestamp") or ""),
            "python_process_count": int(raw.get("python_process_count") or 0),
            "zombie_risk": zombie_risk,
            "mt5_connected": mt5_connected,
            "non_ok_watchdog_count": len(non_ok_watchdogs),
            "stale_watchdog_count": len(stale_watchdogs),
            "non_ok_watchdog_groups": non_ok_watchdogs,
            "stale_watchdog_groups": stale_watchdogs,
        }
    )
    return summary


def build_coordination_sweep_payload(state: dict[str, Any], *, channel: str, after_id: int, last: int) -> dict[str, Any]:
    task_state = load_task_state()
    events_by_task = task_events_by_task(task_state)
    messages = broadcast_messages(
        state,
        channel=channel,
        after_id=after_id,
        sender="",
        contains="",
    )
    if last > 0:
        messages = messages[-last:]
    claim_messages = [message for message in messages if is_claim_like_message(message)]
    unmatched_claims: list[dict[str, Any]] = []
    for message in claim_messages:
        owner_tokens = claim_message_owner_tokens(message)
        claimed_task_id = claim_message_task_id(message)
        if claimed_task_id is not None:
            matching_task = next(
                (task for task in (task_state.get("tasks") or []) if int(task.get("id") or 0) == claimed_task_id),
                None,
            )
            if matching_task is not None and task_owner_matches_tokens(matching_task, owner_tokens):
                continue
        if owner_tokens and any(owner_has_open_task(task_state, owner_token) for owner_token in owner_tokens):
            continue
        unmatched_claims.append(message)

    stale_tasks = list_tasks_snapshot(
        task_state,
        include_stale=True,
        stale_only=True,
        include_done=False,
    )
    blocked_statuses = {"blocked", "waiting", "paused", "on_hold"}
    open_tasks = list_tasks_snapshot(
        task_state,
        include_stale=True,
        include_done=False,
    )
    blocked_tasks = [
        task
        for task in open_tasks
        if str(task.get("status") or "").strip().lower() in blocked_statuses
    ]
    queued_tasks = [
        task
        for task in open_tasks
        if str(task.get("status") or "").strip().lower() in QUEUED_TASK_STATUSES
    ]
    active_tasks = [
        task
        for task in open_tasks
        if str(task.get("status") or "").strip().lower() not in blocked_statuses
        and str(task.get("status") or "").strip().lower() not in QUEUED_TASK_STATUSES
    ]
    open_decisions = list_decisions_snapshot(task_state, include_done=False)
    open_decision_ids = {
        int(decision.get("id") or 0)
        for decision in open_decisions
        if int(decision.get("id") or 0) > 0
    }
    decision_blocked_tasks = [
        task
        for task in active_tasks
        if str(task.get("blocking_decision_id") or "").strip().isdigit()
        and int(str(task.get("blocking_decision_id") or "0")) in open_decision_ids
    ]
    closure_ready_tasks = [
        task
        for task in active_tasks
        if task_has_closeout_ready_signal(task, events_by_task.get(int(task.get("id") or 0), []))
    ]
    owner_closeout_missing_tasks = [
        task
        for task in closure_ready_tasks
        if str(task.get("owner") or "").strip()
        and not bool(task.get("evidence") or {})
    ]
    system_health = load_system_health_summary()
    return {
        "ok": True,
        "channel": channel,
        "after_id": after_id,
        "recent_message_count": len(messages),
        "stale_task_count": len(stale_tasks),
        "open_task_count": len(open_tasks),
        "active_task_count": len(active_tasks),
        "blocked_task_count": len(blocked_tasks),
        "queued_task_count": len(queued_tasks),
        "decision_blocked_task_count": len(decision_blocked_tasks),
        "closure_ready_task_count": len(closure_ready_tasks),
        "owner_closeout_missing_task_count": len(owner_closeout_missing_tasks),
        "open_decision_count": len(open_decisions),
        "claim_message_count": len(claim_messages),
        "unmatched_claim_count": len(unmatched_claims),
        "stale_tasks": stale_tasks,
        "active_tasks": active_tasks,
        "blocked_tasks": blocked_tasks,
        "queued_tasks": queued_tasks,
        "decision_blocked_tasks": decision_blocked_tasks,
        "closure_ready_tasks": closure_ready_tasks,
        "owner_closeout_missing_tasks": owner_closeout_missing_tasks,
        "open_decisions": open_decisions,
        "unmatched_claim_messages": unmatched_claims,
        "system_health": system_health,
    }


def command_task_create(args: argparse.Namespace) -> int:
    description = resolve_text_argument(args.description, getattr(args, "description_file", None)) or ""
    slice_description = resolve_text_argument(
        args.slice_description,
        getattr(args, "slice_description_file", None),
    ) or ""
    with task_state_lock():
        state = load_task_state()
        task = create_task_record(
            state,
            title=args.title,
            creator=args.creator,
            description=description,
            slice_description=slice_description,
            board=args.board,
            status=args.status,
            priority=args.priority,
            owner=args.owner,
            depends_on=list(args.depends_on or []),
            blocking_decision_id=args.blocking_decision_id,
            stale_after_minutes=int(args.stale_after_minutes or 120),
            evidence=parse_json_payload(args.evidence, getattr(args, "evidence_file", None)),
            labels=list(args.labels or []),
        )
        write_task_state(state)
    payload = {"ok": True, "task": task}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_task(task))
    return 0


def command_task_create_from_message(args: argparse.Namespace) -> int:
    state = load_state()
    message = message_by_id(state, args.message_id)
    title = str(args.title or "").strip() or derive_task_title_from_message(message)
    description = message_to_task_description(message)
    slice_description = resolve_text_argument(
        args.slice_description,
        getattr(args, "slice_description_file", None),
    ) or title
    owner = str(args.owner or "").strip()
    if not owner and bool(args.owner_from_message):
        owner = str(message.get("from_agent_id") or message.get("from") or "").strip()
    evidence = parse_json_payload(args.evidence, getattr(args, "evidence_file", None)) or {}
    evidence = {
        "source_message_id": int(message.get("id") or 0),
        "source_channel": str(message.get("channel") or DEFAULT_CHANNEL),
        "source_sender": str(message.get("from_agent_id") or message.get("from") or ""),
        "source_time": str(message.get("time") or ""),
        "source_message_type": str(message.get("message_type") or "message"),
        **evidence,
    }
    with task_state_lock():
        task_state = load_task_state()
        task = create_task_record(
            task_state,
            title=title,
            creator=args.creator,
            description=description,
            slice_description=slice_description,
            board=args.board,
            status=args.status,
            priority=args.priority,
            owner=owner,
            stale_after_minutes=int(args.stale_after_minutes or 120),
            evidence=evidence,
            labels=list(args.labels or []),
        )
        write_task_state(task_state)
    payload = {"ok": True, "task": task, "source_message": message}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_task(task))
    return 0


def command_task_list(args: argparse.Namespace) -> int:
    state = load_task_state()
    tasks = list_tasks_snapshot(
        state,
        board=args.board,
        status=args.status,
        owner=args.owner,
        contains=args.contains,
        label=args.label,
        include_stale=bool(args.include_stale),
        stale_only=bool(args.stale_only),
        include_done=bool(args.include_done),
    )
    payload = {"ok": True, "count": len(tasks), "tasks": tasks}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        emit_tasks(tasks)
    return 0


def command_task_update(args: argparse.Namespace) -> int:
    description = resolve_text_argument(args.description, getattr(args, "description_file", None))
    slice_description = resolve_text_argument(
        args.slice_description,
        getattr(args, "slice_description_file", None),
    )
    with task_state_lock():
        state = load_task_state()
        task = update_task_record(
            state,
            task_id=args.task_id,
            title=args.title,
            description=description,
            board=args.board,
            status=args.status,
            priority=args.priority,
            owner=args.owner,
            slice_description=slice_description,
            depends_on=list(args.depends_on or []),
            blocking_decision_id=args.blocking_decision_id,
            stale_after_minutes=args.stale_after_minutes,
            evidence=parse_json_payload(args.evidence, getattr(args, "evidence_file", None)),
            labels=args.labels,
        )
        write_task_state(state)
    payload = {"ok": True, "task": task}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_task(task))
    return 0


def command_task_claim(args: argparse.Namespace) -> int:
    with task_state_lock():
        state = load_task_state()
        task = update_task_record(
            state,
            task_id=args.task_id,
            owner=args.owner,
            status=args.status,
            stale_after_minutes=args.stale_after_minutes,
            heartbeat=True,
        )
        write_task_state(state)
    payload = {"ok": True, "task": task}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_task(task))
    return 0


def command_task_release(args: argparse.Namespace) -> int:
    with task_state_lock():
        state = load_task_state()
        task = update_task_record(
            state,
            task_id=args.task_id,
            owner="",
            status=args.status,
        )
        write_task_state(state)
    payload = {"ok": True, "task": task}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_task(task))
    return 0


def command_task_comment(args: argparse.Namespace) -> int:
    content = resolve_text_argument(args.content, getattr(args, "content_file", None)) or ""
    with task_state_lock():
        state = load_task_state()
        event = create_task_event_record(
            state,
            task_id=args.task_id,
            author=args.author,
            content=content,
            event_type="comment",
        )
        write_task_state(state)
    payload = {"ok": True, "event": event}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_task_event(event))
    return 0


def command_task_comment_from_message(args: argparse.Namespace) -> int:
    state = load_state()
    message = message_by_id(state, args.message_id)
    author = str(args.author or "").strip()
    if not author and bool(args.author_from_message):
        author = str(message.get("from_agent_id") or message.get("from") or "").strip()
    if not author:
        raise ValueError("author_required_or_use_author_from_message")
    content = message_to_task_comment(message)
    with task_state_lock():
        task_state = load_task_state()
        event = create_task_event_record(
            task_state,
            task_id=args.task_id,
            author=author,
            content=content,
            event_type="comment",
        )
        write_task_state(task_state)
    payload = {"ok": True, "event": event, "source_message": message}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_task_event(event))
    return 0


def command_task_events(args: argparse.Namespace) -> int:
    state = load_task_state()
    events = list_task_events_snapshot(state, task_id=args.task_id, limit=args.limit)
    payload = {"ok": True, "count": len(events), "events": events}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        emit_task_events(events)
    return 0


def command_task_store_status(args: argparse.Namespace) -> int:
    payload = task_store_status()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"path={payload['path']}")
    print(f"source={payload['source']} file_exists={payload['file_exists']}")
    print(
        f"task_count={payload['task_count']} "
        f"event_count={payload['event_count']} "
        f"decision_count={payload['decision_count']}"
    )
    return 0


def command_task_bootstrap(args: argparse.Namespace) -> int:
    payload = bootstrap_task_state_file(force=bool(args.force))
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"path={payload['path']}")
    print(f"source={payload['source']} file_exists={payload['file_exists']}")
    print(
        f"task_count={payload['task_count']} "
        f"event_count={payload['event_count']} "
        f"decision_count={payload['decision_count']}"
    )
    print(f"bootstrapped={payload['bootstrapped']}")
    return 0


def command_decision_create(args: argparse.Namespace) -> int:
    summary = resolve_text_argument(args.summary, getattr(args, "summary_file", None)) or ""
    with task_state_lock():
        state = load_task_state()
        decision = create_decision_record(
            state,
            title=args.title,
            creator=args.creator,
            summary=summary,
            status=args.status,
            owner=args.owner,
            recommended_option=args.recommended_option,
            options=list(args.options or []),
            related_task_ids=list(args.related_task_ids or []),
            evidence=parse_json_payload(args.evidence, getattr(args, "evidence_file", None)),
            labels=list(args.labels or []),
        )
        write_task_state(state)
    payload = {"ok": True, "decision": decision}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_decision(decision))
    return 0


def command_decision_list(args: argparse.Namespace) -> int:
    state = load_task_state()
    decisions = list_decisions_snapshot(
        state,
        status=args.status,
        owner=args.owner,
        contains=args.contains,
        label=args.label,
        include_done=bool(args.include_done),
    )
    payload = {"ok": True, "count": len(decisions), "decisions": decisions}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        emit_decisions(decisions)
    return 0


def command_decision_update(args: argparse.Namespace) -> int:
    summary = resolve_text_argument(args.summary, getattr(args, "summary_file", None))
    with task_state_lock():
        state = load_task_state()
        decision = update_decision_record(
            state,
            decision_id=args.decision_id,
            title=args.title,
            summary=summary,
            status=args.status,
            owner=args.owner,
            recommended_option=args.recommended_option,
            options=args.options,
            related_task_ids=args.related_task_ids,
            evidence=parse_json_payload(args.evidence, getattr(args, "evidence_file", None)),
            labels=args.labels,
        )
        write_task_state(state)
    payload = {"ok": True, "decision": decision}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_decision(decision))
    return 0


def command_coordination_sweep(args: argparse.Namespace) -> int:
    payload = build_coordination_sweep_payload(
        load_state(),
        channel=args.channel,
        after_id=args.after_id,
        last=args.last,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(
        f"recent_messages={payload['recent_message_count']} "
        f"claim_messages={payload['claim_message_count']} "
        f"unmatched_claims={payload['unmatched_claim_count']}"
    )
    print(
        f"open_tasks={payload['open_task_count']} "
        f"queued_tasks={payload['queued_task_count']} "
        f"stale_tasks={payload['stale_task_count']} "
        f"blocked_tasks={payload['blocked_task_count']} "
        f"decision_blocked_tasks={payload['decision_blocked_task_count']} "
        f"closure_ready_tasks={payload['closure_ready_task_count']} "
        f"owner_closeout_missing_tasks={payload['owner_closeout_missing_task_count']} "
        f"open_decisions={payload['open_decision_count']}"
    )
    system_health = payload["system_health"]
    if system_health.get("available"):
        print(
            f"system_health={system_health['health_status']} "
            f"python_processes={system_health['python_process_count']} "
            f"zombie_risk={system_health['zombie_risk']} "
            f"non_ok_watchdogs={system_health['non_ok_watchdog_count']}"
        )
        if system_health["stale_watchdog_groups"]:
            print("stale_watchdog_groups=" + ",".join(system_health["stale_watchdog_groups"]))
    else:
        print("system_health=missing")
    if payload["active_tasks"]:
        print("active_task_titles=" + ",".join(str(task.get("title") or "") for task in payload["active_tasks"]))
    if payload["queued_tasks"]:
        print("queued_task_titles=" + ",".join(str(task.get("title") or "") for task in payload["queued_tasks"]))
    if payload["stale_tasks"]:
        print("stale_task_titles=" + ",".join(str(task.get("title") or "") for task in payload["stale_tasks"]))
    if payload["blocked_tasks"]:
        print("blocked_task_titles=" + ",".join(str(task.get("title") or "") for task in payload["blocked_tasks"]))
    if payload["decision_blocked_tasks"]:
        print(
            "decision_blocked_task_titles="
            + ",".join(str(task.get("title") or "") for task in payload["decision_blocked_tasks"])
        )
    if payload["closure_ready_tasks"]:
        print(
            "closure_ready_task_titles="
            + ",".join(str(task.get("title") or "") for task in payload["closure_ready_tasks"])
        )
    if payload["owner_closeout_missing_tasks"]:
        print(
            "owner_closeout_missing_task_titles="
            + ",".join(str(task.get("title") or "") for task in payload["owner_closeout_missing_tasks"])
        )
    if payload["open_decisions"]:
        print("open_decision_titles=" + ",".join(str(decision.get("title") or "") for decision in payload["open_decisions"]))
    if payload["unmatched_claim_messages"]:
        print(
            "unmatched_claim_ids="
            + ",".join(str(message.get("id") or "") for message in payload["unmatched_claim_messages"])
        )
    return 0


def command_task_heartbeat(args: argparse.Namespace) -> int:
    with task_state_lock():
        state = load_task_state()
        task = update_task_record(state, task_id=args.task_id, heartbeat=True)
        write_task_state(state)
    payload = {"ok": True, "task": task}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_task(task))
    return 0


def classify_server_fleet(payload: dict[str, Any]) -> dict[str, Any]:
    process_count = int(payload.get("process_count") or 0)
    orphaned = list(payload.get("orphaned_pids") or [])
    duplicates = list(payload.get("same_parent_duplicate_pids") or [])
    outdated = list(payload.get("outdated_pids") or [])
    reasons: list[str] = []
    actions: list[str] = []
    risk_level = "ok"

    if process_count <= 0:
        risk_level = "critical"
        reasons.append("no_comms_server_processes")
        actions.append("restart the MCP client or run a fresh probe")
    if orphaned:
        risk_level = "critical"
        reasons.append(f"orphaned_server_processes={','.join(str(pid) for pid in orphaned)}")
        actions.append("run server-cleanup --orphans-only --apply")
    if outdated:
        if risk_level == "ok":
            risk_level = "warning"
        reasons.append(f"outdated_server_processes={','.join(str(pid) for pid in outdated)}")
        actions.append("restart affected MCP clients or run server-cleanup --outdated-only --apply when peers are safe to recycle")
    if duplicates:
        if risk_level == "ok":
            risk_level = "warning"
        reasons.append(f"same_parent_duplicate_processes={','.join(str(pid) for pid in duplicates)}")
        actions.append("leave attached peers alone unless transport is dead; use server-cleanup --duplicates-only --apply only for safe stale clients")

    if not reasons:
        reasons.append("server_fleet_clean")
    return {
        "risk_level": risk_level,
        "risk_reasons": reasons,
        "recommended_actions": actions,
    }


def build_server_process_payload() -> dict[str, Any]:
    processes = server_cleanup.list_server_processes(SERVER_SCRIPT)
    rows = server_cleanup.snapshot_server_processes(processes)
    orphaned = server_cleanup.find_orphaned_server_pids(processes, current_pid=0, script_path=SERVER_SCRIPT)
    duplicates = server_cleanup.find_same_parent_duplicate_server_pids(processes, script_path=SERVER_SCRIPT)
    script_mtime_epoch = 0.0
    script_mtime_iso = ""
    script_mtime_epoch = server_cleanup.server_code_mtime(SERVER_SCRIPT)
    if script_mtime_epoch > 0:
        script_mtime_iso = dt.datetime.fromtimestamp(script_mtime_epoch, tz=dt.timezone.utc).isoformat()
    outdated_pids = sorted(
        int(row.get("pid") or 0)
        for row in rows
        if script_mtime_epoch > 0 and float(row.get("created_at") or 0.0) < script_mtime_epoch
    )
    payload = {
        "ok": True,
        "script": str(SERVER_SCRIPT),
        "script_mtime_epoch": script_mtime_epoch,
        "script_mtime_iso": script_mtime_iso,
        "process_count": len(rows),
        "orphaned_pids": orphaned,
        "same_parent_duplicate_pids": duplicates,
        "outdated_pids": outdated_pids,
        "reload_recommended": bool(outdated_pids),
        "processes": rows,
    }
    payload.update(classify_server_fleet(payload))
    payload["transport_telemetry"] = build_transport_telemetry_payload()
    return payload


def command_server_status(args: argparse.Namespace) -> int:
    payload = build_server_process_payload()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"script={payload['script']}")
    print(
        f"process_count={payload['process_count']} "
        f"orphaned={len(payload['orphaned_pids'])} "
        f"same_parent_duplicates={len(payload['same_parent_duplicate_pids'])} "
        f"outdated={len(payload['outdated_pids'])} "
        f"reload_recommended={payload['reload_recommended']} "
        f"risk={payload['risk_level']}"
    )
    if payload["risk_reasons"]:
        print("risk_reasons=" + "; ".join(payload["risk_reasons"]))
    if payload["recommended_actions"]:
        print("recommended_actions=" + "; ".join(payload["recommended_actions"]))
    if payload["script_mtime_iso"]:
        print(f"script_mtime={payload['script_mtime_iso']}")
    transport = payload.get("transport_telemetry") or {}
    diagnosis = transport.get("diagnosis") or {}
    if diagnosis:
        print(f"transport={diagnosis.get('level', 'unknown')}")
        if diagnosis.get("reasons"):
            print("transport_reasons=" + "; ".join(str(reason) for reason in diagnosis["reasons"]))
        if diagnosis.get("recommended_actions"):
            print("transport_actions=" + "; ".join(str(action) for action in diagnosis["recommended_actions"]))
    latest_return = ((transport.get("latest") or {}).get("mcp_run_returned") or {})
    if latest_return:
        print(
            "last_transport_return="
            f"{latest_return.get('time', '')} "
            f"classification={latest_return.get('exit_classification', '')} "
            f"runtime_seconds={latest_return.get('runtime_seconds', '')}"
        )
    heartbeat = transport.get("heartbeat") or {}
    if heartbeat.get("exists"):
        print(
            f"transport_heartbeat={heartbeat.get('updated_at') or 'n/a'} "
            f"age_seconds={heartbeat.get('age_seconds')}"
        )
    if not payload["processes"]:
        print("No comms_server.py processes.")
        return 0
    outdated = set(int(pid) for pid in payload["outdated_pids"])
    for row in payload["processes"]:
        print(
            f"pid={row['pid']} ppid={row['ppid']} "
            f"parent_alive={row['parent_alive']} parent_name={row['parent_name'] or '-'} "
            f"outdated={int(row['pid']) in outdated}"
        )
    return 0


def command_server_cleanup(args: argparse.Namespace) -> int:
    payload = build_server_process_payload()
    orphaned = list(payload["orphaned_pids"])
    duplicates = list(payload["same_parent_duplicate_pids"])
    outdated = list(payload["outdated_pids"])
    selected_groups = []
    if args.orphans_only:
        selected_groups.append(orphaned)
    if args.duplicates_only:
        selected_groups.append(duplicates)
    if args.outdated_only:
        selected_groups.append(outdated)
    if selected_groups:
        targets = sorted({pid for group in selected_groups for pid in group})
    else:
        targets = sorted(set(orphaned) | set(duplicates))

    result: dict[str, Any] = {
        "ok": True,
        "script": payload["script"],
        "process_count": payload["process_count"],
        "outdated_pids": outdated,
        "targets": targets,
        "dry_run": not args.apply,
    }
    if args.apply:
        result["actions"] = server_cleanup.terminate_processes(targets)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print(f"script={result['script']}")
    print(f"process_count={result['process_count']}")
    print(f"targets={','.join(str(pid) for pid in targets) if targets else 'none'}")
    if not args.apply:
        print("dry_run=true")
        return 0
    for action in result.get("actions") or []:
        line = f"pid={action['pid']} status={action['status']}"
        if action.get("error"):
            line = f"{line} error={action['error']}"
        print(line)
    return 0


def main() -> int:
    try:
        args = parse_args()
        if args.command == "status":
            return command_status(args)
        if args.command == "doctor":
            return command_doctor(args)
        if args.command == "agents":
            return command_agents(args)
        if args.command == "read":
            return command_read(args)
        if args.command == "inbox":
            return command_inbox(args)
        if args.command == "tail":
            return command_tail(args)
        if args.command == "post":
            return command_post(args)
        if args.command == "task-create":
            return command_task_create(args)
        if args.command == "task-create-from-message":
            return command_task_create_from_message(args)
        if args.command == "task-list":
            return command_task_list(args)
        if args.command == "task-update":
            return command_task_update(args)
        if args.command == "task-claim":
            return command_task_claim(args)
        if args.command == "task-release":
            return command_task_release(args)
        if args.command == "task-heartbeat":
            return command_task_heartbeat(args)
        if args.command == "task-comment":
            return command_task_comment(args)
        if args.command == "task-comment-from-message":
            return command_task_comment_from_message(args)
        if args.command == "task-events":
            return command_task_events(args)
        if args.command == "task-store-status":
            return command_task_store_status(args)
        if args.command == "task-bootstrap":
            return command_task_bootstrap(args)
        if args.command == "decision-create":
            return command_decision_create(args)
        if args.command == "decision-list":
            return command_decision_list(args)
        if args.command == "decision-update":
            return command_decision_update(args)
        if args.command == "coordination-sweep":
            return command_coordination_sweep(args)
        if args.command == "server-status":
            return command_server_status(args)
        if args.command == "server-cleanup":
            return command_server_cleanup(args)
        raise ValueError(f"Unknown command: {args.command}")
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
