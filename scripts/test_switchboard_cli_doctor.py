#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
CLI_PATH = ROOT / "scripts" / "switchboard_cli.py"

spec = importlib.util.spec_from_file_location("switchboard_cli_for_tests", CLI_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load switchboard CLI for tests: {CLI_PATH}")
cli = importlib.util.module_from_spec(spec)
sys.modules.setdefault("switchboard_cli_for_tests", cli)
spec.loader.exec_module(cli)


def clean_transport_payload() -> dict:
    return {
        "diagnosis": {
            "level": "ok",
            "reasons": ["transport_telemetry_clean"],
            "recommended_actions": [],
        },
        "heartbeat": {
            "exists": True,
            "updated_at": "2026-04-16T02:52:00+00:00",
            "age_seconds": 1.0,
            "stale": False,
        },
        "latest": {},
        "recent": {
            "mcp_run_returned_count_1h": 0,
            "mcp_run_exception_count_1h": 0,
            "tool_error_count_1h": 0,
        },
    }


class SwitchboardDoctorTests(unittest.TestCase):
    def test_build_doctor_payload_summarizes_store_tasks_and_server_health(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        state = {
            "messages": [
                {
                    "id": 7,
                    "time": now.isoformat(),
                    "from": "codex-cli",
                    "to": "ALL",
                    "channel": "general",
                    "content": "hello",
                }
            ],
            "next_message_id": 8,
            "agents": {
                "codex-cli": {"agent_id": "codex-cli", "last_seen": now.isoformat()},
                "stale-agent": {
                    "agent_id": "stale-agent",
                    "last_seen": (now - dt.timedelta(seconds=600)).isoformat(),
                },
            },
            "tasks": [
                {
                    "id": 1,
                    "title": "Open task",
                    "status": "in_progress",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
                {
                    "id": 2,
                    "title": "Done task",
                    "status": "done",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            ],
            "decisions": [
                {
                    "id": 1,
                    "title": "Open decision",
                    "status": "pending",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
                {
                    "id": 2,
                    "title": "Resolved decision",
                    "status": "resolved",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            ],
        }
        with patch.object(
            cli,
            "load_task_state",
            return_value={"tasks": state["tasks"], "task_events": [], "decisions": state["decisions"]},
        ), patch.object(
            cli,
            "task_store_status",
            return_value={
                "ok": True,
                "path": str(ROOT / "war_room_tasks.json"),
                "source": "legacy_embedded",
                "file_exists": False,
                "task_count": 2,
                "event_count": 0,
                "decision_count": 2,
            },
        ), patch.object(
            cli,
            "build_server_process_payload",
            return_value={
                "process_count": 2,
                "orphaned_pids": [],
                "same_parent_duplicate_pids": [301],
                "outdated_pids": [302],
                "reload_recommended": True,
                "script_mtime_iso": "2026-04-16T02:52:00+00:00",
                "risk_level": "warning",
                "risk_reasons": ["same_parent_duplicate_processes=301", "outdated_server_processes=302"],
                "recommended_actions": ["restart affected MCP clients"],
                "transport_telemetry": clean_transport_payload(),
            },
        ):
            payload = cli.build_doctor_payload(state, stale_after_seconds=300)

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["healthy"])
        self.assertEqual(payload["store"]["message_count"], 1)
        self.assertEqual(payload["store"]["last_id"], 7)
        self.assertEqual(payload["agents"], {"total": 2, "online": 1, "stale": 1})
        self.assertEqual(payload["tasks"]["total"], 2)
        self.assertEqual(payload["tasks"]["open"], 1)
        self.assertEqual(payload["tasks"]["done"], 1)
        self.assertEqual(payload["tasks"]["store"]["source"], "legacy_embedded")
        self.assertEqual(payload["decisions"], {"total": 2, "open": 1, "done": 1})
        self.assertEqual(
            payload["server"],
            {
                "process_count": 2,
                "orphaned_count": 0,
                "same_parent_duplicate_count": 1,
                "outdated_count": 1,
                "reload_recommended": True,
                "script_mtime_iso": "2026-04-16T02:52:00+00:00",
                "risk_level": "warning",
                "risk_reasons": ["same_parent_duplicate_processes=301", "outdated_server_processes=302"],
                "recommended_actions": ["restart affected MCP clients"],
                "transport": clean_transport_payload(),
            },
        )

    def test_build_doctor_payload_treats_outdated_server_as_unhealthy(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        state = {
            "messages": [],
            "next_message_id": 1,
            "agents": {},
        }
        with patch.object(
            cli,
            "load_task_state",
            return_value={"tasks": [], "task_events": [], "decisions": []},
        ), patch.object(
            cli,
            "task_store_status",
            return_value={
                "ok": True,
                "path": str(ROOT / "war_room_tasks.json"),
                "source": "dedicated",
                "file_exists": True,
                "task_count": 0,
                "event_count": 0,
                "decision_count": 0,
            },
        ), patch.object(
            cli,
            "build_server_process_payload",
            return_value={
                "process_count": 1,
                "orphaned_pids": [],
                "same_parent_duplicate_pids": [],
                "outdated_pids": [9812],
                "reload_recommended": True,
                "script_mtime_iso": now.isoformat(),
                "risk_level": "warning",
                "risk_reasons": ["outdated_server_processes=9812"],
                "recommended_actions": ["restart affected MCP clients"],
                "transport_telemetry": clean_transport_payload(),
            },
        ):
            payload = cli.build_doctor_payload(state, stale_after_seconds=300)

        self.assertFalse(payload["healthy"])
        self.assertEqual(payload["server"]["outdated_count"], 1)
        self.assertTrue(payload["server"]["reload_recommended"])
        self.assertEqual(payload["server"]["risk_level"], "warning")

    def test_build_server_process_payload_flags_processes_older_than_script_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            server_script = Path(tmpdir) / "comms_server.py"
            server_script.write_text("# test\n", encoding="utf-8")
            script_epoch = server_script.stat().st_mtime
            rows = [
                {"pid": 10, "ppid": 1, "parent_alive": True, "parent_name": "python", "created_at": script_epoch - 30.0},
                {"pid": 11, "ppid": 1, "parent_alive": True, "parent_name": "python", "created_at": script_epoch + 30.0},
            ]

            with (
                patch.object(cli, "SERVER_SCRIPT", server_script),
                patch.object(cli.server_cleanup, "list_server_processes", return_value=[]),
                patch.object(cli.server_cleanup, "snapshot_server_processes", return_value=rows),
                patch.object(cli.server_cleanup, "find_orphaned_server_pids", return_value=[]),
                patch.object(cli.server_cleanup, "find_same_parent_duplicate_server_pids", return_value=[]),
                patch.object(cli, "build_transport_telemetry_payload", return_value=clean_transport_payload()),
            ):
                payload = cli.build_server_process_payload()

        self.assertEqual(payload["outdated_pids"], [10])
        self.assertTrue(payload["reload_recommended"])
        self.assertTrue(payload["script_mtime_iso"])
        self.assertEqual(payload["risk_level"], "warning")
        self.assertIn("outdated_server_processes=10", payload["risk_reasons"])
        self.assertEqual(payload["transport_telemetry"]["diagnosis"]["level"], "ok")

    def test_build_doctor_payload_treats_transport_warning_as_unhealthy(self) -> None:
        warning_transport = clean_transport_payload()
        warning_transport["diagnosis"] = {
            "level": "warning",
            "reasons": ["recent_transport_returns=3"],
            "recommended_actions": ["inspect lifecycle rows"],
        }
        with patch.object(
            cli,
            "load_task_state",
            return_value={"tasks": [], "task_events": [], "decisions": []},
        ), patch.object(
            cli,
            "task_store_status",
            return_value={
                "ok": True,
                "path": str(ROOT / "war_room_tasks.json"),
                "source": "dedicated",
                "file_exists": True,
                "task_count": 0,
                "event_count": 0,
                "decision_count": 0,
            },
        ), patch.object(
            cli,
            "build_server_process_payload",
            return_value={
                "process_count": 1,
                "orphaned_pids": [],
                "same_parent_duplicate_pids": [],
                "outdated_pids": [],
                "reload_recommended": False,
                "script_mtime_iso": "2026-04-16T02:52:00+00:00",
                "risk_level": "ok",
                "risk_reasons": ["server_fleet_clean"],
                "recommended_actions": [],
                "transport_telemetry": warning_transport,
            },
        ):
            payload = cli.build_doctor_payload({"messages": [], "agents": {}}, stale_after_seconds=300)

        self.assertFalse(payload["healthy"])
        self.assertEqual(payload["server"]["transport"]["diagnosis"]["level"], "warning")
        self.assertIn("recent_transport_returns=3", payload["server"]["transport"]["diagnosis"]["reasons"])

    def test_build_transport_telemetry_payload_classifies_recent_churn(self) -> None:
        now = dt.datetime(2026, 4, 16, 2, 52, 0, tzinfo=dt.timezone.utc)
        lifecycle_rows = [
            {
                "time": (now - dt.timedelta(minutes=10)).isoformat(),
                "event": "mcp_run_returned",
                "instance_id": "a",
                "pid": 10,
                "exit_classification": "parent_exited",
            },
            {
                "time": (now - dt.timedelta(minutes=5)).isoformat(),
                "event": "mcp_run_returned",
                "instance_id": "b",
                "pid": 11,
                "exit_classification": "stdio_stream_closed",
            },
            {
                "time": (now - dt.timedelta(minutes=1)).isoformat(),
                "event": "mcp_run_returned",
                "instance_id": "c",
                "pid": 12,
                "exit_classification": "mcp_run_returned_without_exception",
            },
        ]
        error_rows = [
            {
                "time": (now - dt.timedelta(minutes=2)).isoformat(),
                "event": "mcp_run_exception",
                "instance_id": "d",
                "pid": 13,
                "exception_type": "BrokenPipeError",
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            heartbeat = Path(tmpdir) / "heartbeat.txt"
            heartbeat.write_text(f"{now.isoformat()} pid=12\n", encoding="utf-8")
            with (
                patch.object(cli, "SERVER_HEARTBEAT_FILE", heartbeat),
                patch.object(cli, "read_jsonl_tail", side_effect=[lifecycle_rows, error_rows, []]),
            ):
                payload = cli.build_transport_telemetry_payload(now=now)

        self.assertEqual(payload["diagnosis"]["level"], "warning")
        self.assertIn("recent_transport_returns=3", payload["diagnosis"]["reasons"])
        self.assertIn("recent_mcp_run_exceptions=1", payload["diagnosis"]["reasons"])
        self.assertEqual(payload["latest"]["mcp_run_returned"]["exit_classification"], "mcp_run_returned_without_exception")
        self.assertEqual(payload["heartbeat"]["age_seconds"], 0.0)

    def test_classify_server_fleet_distinguishes_clean_warning_and_critical(self) -> None:
        clean = cli.classify_server_fleet(
            {
                "process_count": 1,
                "orphaned_pids": [],
                "same_parent_duplicate_pids": [],
                "outdated_pids": [],
            }
        )
        self.assertEqual(clean["risk_level"], "ok")
        self.assertEqual(clean["risk_reasons"], ["server_fleet_clean"])

        warning = cli.classify_server_fleet(
            {
                "process_count": 3,
                "orphaned_pids": [],
                "same_parent_duplicate_pids": [12],
                "outdated_pids": [10],
            }
        )
        self.assertEqual(warning["risk_level"], "warning")
        self.assertIn("same_parent_duplicate_processes=12", warning["risk_reasons"])
        self.assertIn("outdated_server_processes=10", warning["risk_reasons"])

        critical = cli.classify_server_fleet(
            {
                "process_count": 1,
                "orphaned_pids": [99],
                "same_parent_duplicate_pids": [],
                "outdated_pids": [],
            }
        )
        self.assertEqual(critical["risk_level"], "critical")
        self.assertIn("orphaned_server_processes=99", critical["risk_reasons"])

    def test_command_server_cleanup_can_target_only_outdated_processes(self) -> None:
        args = argparse.Namespace(
            orphans_only=False,
            duplicates_only=False,
            outdated_only=True,
            apply=False,
            json=True,
        )
        with (
            patch.object(
                cli,
                "build_server_process_payload",
                return_value={
                    "script": str(ROOT / "comms_server.py"),
                    "process_count": 5,
                    "orphaned_pids": [11],
                    "same_parent_duplicate_pids": [12, 13],
                    "outdated_pids": [21, 22],
                },
            ),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            result = cli.command_server_cleanup(args)

        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["targets"], [21, 22])
        self.assertEqual(payload["outdated_pids"], [21, 22])
        self.assertTrue(payload["dry_run"])

    def test_command_server_cleanup_default_targets_stay_orphans_and_duplicates_only(self) -> None:
        args = argparse.Namespace(
            orphans_only=False,
            duplicates_only=False,
            outdated_only=False,
            apply=False,
            json=True,
        )
        with (
            patch.object(
                cli,
                "build_server_process_payload",
                return_value={
                    "script": str(ROOT / "comms_server.py"),
                    "process_count": 5,
                    "orphaned_pids": [11],
                    "same_parent_duplicate_pids": [12, 13],
                    "outdated_pids": [21, 22],
                },
            ),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            result = cli.command_server_cleanup(args)

        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["targets"], [11, 12, 13])
        self.assertEqual(payload["outdated_pids"], [21, 22])

    def test_build_coordination_sweep_payload_flags_unmatched_claim_messages(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        state = {
            "messages": [
                {
                    "id": 10,
                    "time": now.isoformat(),
                    "from": "codex-cli",
                    "from_agent_id": "codex-cli",
                    "to": "ALL",
                    "channel": "general",
                    "content": "Claiming stale-claim automation slice.",
                },
                {
                    "id": 11,
                    "time": now.isoformat(),
                    "from": "@mt5-main",
                    "from_agent_id": "codex-mt5-main",
                    "to": "ALL",
                    "channel": "general",
                    "content": "Claiming detached inventory action board slice.",
                },
                {
                    "id": 12,
                    "time": now.isoformat(),
                    "from": "qwen-collab",
                    "from_agent_id": "qwen-collab-20260415",
                    "to": "ALL",
                    "channel": "general",
                    "content": "Claiming task #7: ETH decommission packet slice.",
                },
                {
                    "id": 13,
                    "time": now.isoformat(),
                    "from": "codex-mini-ethm15-recovery",
                    "from_agent_id": "codex-mini-ethm15-recovery",
                    "to": "ALL",
                    "channel": "general",
                    "content": "Claiming task #8 (ETH M15 blocked follow-up).",
                },
            ],
            "agents": {},
        }
        task_state = {
            "tasks": [
                {
                    "id": 2,
                    "title": "Detached inventory board",
                    "status": "in_progress",
                    "owner": "codex-mt5-main",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_heartbeat": now.isoformat(),
                    "stale_after_minutes": 45,
                },
                {
                    "id": 7,
                    "title": "ETH decommission packet",
                    "status": "in_progress",
                    "owner": "qwen-collab",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_heartbeat": now.isoformat(),
                    "stale_after_minutes": 45,
                    "blocking_decision_id": "1",
                },
                {
                    "id": 8,
                    "title": "ETH M15 blocked follow-up",
                    "status": "blocked",
                    "owner": "codex-mini-ethm15-recovery",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_heartbeat": now.isoformat(),
                    "stale_after_minutes": 45,
                },
                {
                    "id": 9,
                    "title": "Already completed task",
                    "status": "completed",
                    "owner": "codex-mt5-hardening",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_heartbeat": now.isoformat(),
                    "stale_after_minutes": 45,
                }
            ],
            "decisions": [
                {
                    "id": 1,
                    "title": "Pending operator choice",
                    "status": "pending",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            ],
        }
        with (
            patch.object(cli, "load_task_state", return_value=task_state),
            patch.object(
                cli,
                "load_system_health_summary",
                return_value={
                    "available": True,
                    "health_status": "warning",
                    "python_process_count": 79,
                    "zombie_risk": True,
                    "non_ok_watchdog_count": 3,
                    "stale_watchdog_count": 2,
                    "non_ok_watchdog_groups": ["fx_repro", "test_single", "fx_watchdog_repro"],
                    "stale_watchdog_groups": ["fx_repro", "test_single"],
                    "mt5_connected": True,
                    "timestamp": now.isoformat(),
                },
            ),
        ):
            payload = cli.build_coordination_sweep_payload(
                state,
                channel="general",
                after_id=0,
                last=20,
            )

        self.assertEqual(payload["recent_message_count"], 4)
        self.assertEqual(payload["claim_message_count"], 4)
        self.assertEqual(payload["open_task_count"], 3)
        self.assertEqual(payload["active_task_count"], 2)
        self.assertEqual([task["id"] for task in payload["active_tasks"]], [2, 7])
        self.assertEqual(payload["queued_task_count"], 0)
        self.assertEqual(payload["queued_tasks"], [])
        self.assertEqual(payload["open_decision_count"], 1)
        self.assertEqual(payload["stale_task_count"], 0)
        self.assertEqual(payload["blocked_task_count"], 1)
        self.assertEqual(payload["decision_blocked_task_count"], 1)
        self.assertEqual([task["id"] for task in payload["decision_blocked_tasks"]], [7])
        self.assertEqual([task["id"] for task in payload["blocked_tasks"]], [8])
        self.assertEqual(payload["unmatched_claim_count"], 1)
        self.assertEqual([message["id"] for message in payload["unmatched_claim_messages"]], [10])
        self.assertEqual(payload["system_health"]["health_status"], "warning")
        self.assertEqual(payload["system_health"]["python_process_count"], 79)
        self.assertEqual(payload["system_health"]["stale_watchdog_groups"], ["fx_repro", "test_single"])

    def test_build_coordination_sweep_payload_keeps_pending_tasks_out_of_active_execution(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        task_state = {
            "tasks": [
                {
                    "id": 12,
                    "title": "Burst fade restart",
                    "status": "in_progress",
                    "owner": "codex-mt5-hardening",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_heartbeat": now.isoformat(),
                    "stale_after_minutes": 45,
                },
                {
                    "id": 16,
                    "title": "SOL retune candidate",
                    "status": "pending",
                    "owner": "",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_heartbeat": now.isoformat(),
                    "stale_after_minutes": 45,
                },
            ],
            "decisions": [],
        }

        with (
            patch.object(cli, "load_task_state", return_value=task_state),
            patch.object(cli, "load_system_health_summary", return_value={"available": False, "health_status": "missing"}),
        ):
            payload = cli.build_coordination_sweep_payload(
                {"messages": [], "agents": {}},
                channel="general",
                after_id=0,
                last=20,
            )

        self.assertEqual(payload["open_task_count"], 2)
        self.assertEqual(payload["active_task_count"], 1)
        self.assertEqual([task["id"] for task in payload["active_tasks"]], [12])
        self.assertEqual(payload["queued_task_count"], 1)
        self.assertEqual([task["id"] for task in payload["queued_tasks"]], [16])

    def test_build_coordination_sweep_payload_surfaces_closure_ready_and_owner_closeout_missing(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        task_state = {
            "tasks": [
                {
                    "id": 12,
                    "title": "Burst fade restart",
                    "status": "in_progress",
                    "owner": "codex-mt5-hardening",
                    "evidence": {},
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_heartbeat": now.isoformat(),
                    "stale_after_minutes": 45,
                },
                {
                    "id": 13,
                    "title": "ETH optimization",
                    "status": "in_progress",
                    "owner": "qwen-collab",
                    "evidence": {"lanes_launched": 3},
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_heartbeat": now.isoformat(),
                    "stale_after_minutes": 45,
                },
            ],
            "task_events": [
                {
                    "id": 1,
                    "task_id": 12,
                    "author": "codex-mini-ethm15-recovery",
                    "type": "comment",
                    "content": "Machine truth now supports closure. Remaining gap is owner-level success criteria / closeout language, not missing execution.",
                    "created_at": now.isoformat(),
                },
                {
                    "id": 2,
                    "task_id": 13,
                    "author": "codex-mini-ethm15-recovery",
                    "type": "comment",
                    "content": "Current machine truth check: task 13 is still pre-verdict and in passive sample-building mode.",
                    "created_at": now.isoformat(),
                },
            ],
            "decisions": [],
        }

        with (
            patch.object(cli, "load_task_state", return_value=task_state),
            patch.object(cli, "load_system_health_summary", return_value={"available": False, "health_status": "missing"}),
        ):
            payload = cli.build_coordination_sweep_payload(
                {"messages": [], "agents": {}},
                channel="general",
                after_id=0,
                last=20,
            )

        self.assertEqual(payload["closure_ready_task_count"], 1)
        self.assertEqual([task["id"] for task in payload["closure_ready_tasks"]], [12])
        self.assertEqual(payload["owner_closeout_missing_task_count"], 1)
        self.assertEqual([task["id"] for task in payload["owner_closeout_missing_tasks"]], [12])

    def test_parse_json_payload_preserves_none_and_supports_file_input(self) -> None:
        self.assertIsNone(cli.parse_json_payload(None))

        with tempfile.TemporaryDirectory() as tmpdir:
            payload_path = Path(tmpdir) / "payload.json"
            payload_path.write_text('{"closes": 12, "net": -44.5}\n', encoding="utf-8")

            payload = cli.parse_json_payload(None, str(payload_path))

        self.assertEqual(payload, {"closes": 12, "net": -44.5})

    def test_resolve_text_argument_reads_file_without_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content_path = Path(tmpdir) / "message.txt"
            content_path.write_text("line one\nline two\n", encoding="utf-8")

            resolved = cli.resolve_text_argument(None, str(content_path))

        self.assertEqual(resolved, "line one\nline two")

    def test_command_task_create_from_message_promotes_room_message_with_source_metadata(self) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        state = {
            "messages": [
                {
                    "id": 42,
                    "time": now,
                    "from": "qwen-collab",
                    "from_agent_id": "qwen-collab-20260415",
                    "to": "ALL",
                    "channel": "general",
                    "message_type": "message",
                    "content": "Claiming stale-operator-surface sweep: patching the highest-risk dashboard next.",
                }
            ]
        }
        task_state = {
            "next_task_id": 1,
            "next_task_event_id": 1,
            "next_decision_id": 1,
            "tasks": [],
            "task_events": [],
            "decisions": [],
        }
        args = argparse.Namespace(
            message_id=42,
            creator="codex-cli",
            title="",
            owner="",
            owner_from_message=True,
            board="experiments",
            status="pending",
            priority="high",
            slice_description="",
            slice_description_file=None,
            stale_after_minutes=120,
            evidence='{"severity":"high"}',
            evidence_file=None,
            labels=["ops", "dashboard"],
            json=True,
        )

        with (
            patch.object(cli, "load_state", return_value=state),
            patch.object(cli, "load_task_state", return_value=task_state),
            patch.object(cli, "write_task_state"),
            patch.object(cli, "task_state_lock", contextlib.nullcontext),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            result = cli.command_task_create_from_message(args)

        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        task = payload["task"]
        self.assertEqual(task["id"], 1)
        self.assertEqual(task["owner"], "qwen-collab-20260415")
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["status"], "pending")
        self.assertIn("Claiming stale-operator-surface sweep", task["title"])
        self.assertIn("Promoted from switchboard message 42", task["description"])
        self.assertIn("patching the highest-risk dashboard next", task["description"])
        self.assertEqual(task["evidence"]["source_message_id"], 42)
        self.assertEqual(task["evidence"]["source_sender"], "qwen-collab-20260415")
        self.assertEqual(task["evidence"]["severity"], "high")
        self.assertEqual(task["labels"], ["ops", "dashboard"])

    def test_command_task_comment_from_message_attaches_room_message_to_existing_task(self) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        state = {
            "messages": [
                {
                    "id": 77,
                    "time": now,
                    "from": "codex_tradingbots",
                    "from_agent_id": "codex_tradingbots",
                    "to": "ALL",
                    "channel": "general",
                    "message_type": "message",
                    "content": "Completed refresh-path hardening and validated commands_run=11.",
                }
            ]
        }
        task_state = {
            "next_task_id": 2,
            "next_task_event_id": 1,
            "next_decision_id": 1,
            "tasks": [{"id": 1, "title": "Burst-fade lane", "status": "in_progress"}],
            "task_events": [],
            "decisions": [],
        }
        args = argparse.Namespace(
            task_id=1,
            message_id=77,
            author="",
            author_from_message=True,
            json=True,
        )

        with (
            patch.object(cli, "load_state", return_value=state),
            patch.object(cli, "load_task_state", return_value=task_state),
            patch.object(cli, "write_task_state"),
            patch.object(cli, "task_state_lock", contextlib.nullcontext),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            result = cli.command_task_comment_from_message(args)

        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        event = payload["event"]
        self.assertEqual(event["task_id"], 1)
        self.assertEqual(event["author"], "codex_tradingbots")
        self.assertIn("Attached from switchboard message 77", event["content"])
        self.assertIn("Completed refresh-path hardening", event["content"])

    def test_is_claim_like_message_ignores_discussion_about_claims(self) -> None:
        self.assertFalse(
            cli.is_claim_like_message(
                {
                    "content": "@codex-cli — Auto-claim on first message would help, but I'm only reviewing the design here."
                }
            )
        )

    def test_load_system_health_summary_ignores_ad_hoc_watchdog_artifacts(self) -> None:
        now = dt.datetime(2026, 4, 15, 22, 30, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            report_file = Path(tmpdir) / "system_health_check.json"
            report_file.write_text(
                json.dumps(
                    {
                        "timestamp": now.isoformat(),
                        "python_process_count": 40,
                        "zombie_risk": False,
                        "mt5_status": {"connected": True, "terminal_connected": True},
                        "watchdog_status": {
                            "fx_watchdog": {
                                "status": "ok",
                                "age_seconds": 15,
                                "health_included": True,
                            },
                            "fx_repro": {
                                "status": "starting",
                                "age_seconds": 4000,
                                "health_included": False,
                                "stale_artifact": True,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = cli.load_system_health_summary(report_file=report_file)

        self.assertEqual(summary["health_status"], "ok")
        self.assertEqual(summary["non_ok_watchdog_count"], 0)
        self.assertEqual(summary["stale_watchdog_count"], 0)
        self.assertEqual(summary["non_ok_watchdog_groups"], [])
        self.assertEqual(summary["stale_watchdog_groups"], [])
        self.assertFalse(
            cli.is_claim_like_message(
                {
                    "content": "ETH retune is positive so far. Next: need to get loop mode running; investigating why it exits silently."
                }
            )
        )
        self.assertTrue(
            cli.is_claim_like_message(
                {
                    "content": "Claiming detached-inventory action board slice: building execution-grade commands."
                }
            )
        )

    def test_list_tasks_snapshot_splits_comma_packed_labels(self) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        task_state = {
            "tasks": [
                {
                    "id": 10,
                    "title": "Process health hardening",
                    "status": "in_progress",
                    "owner": "codex-mt5-hardening",
                    "labels": ["process-health,coordination,watchdog"],
                    "created_at": now,
                    "updated_at": now,
                    "last_heartbeat": now,
                    "stale_after_minutes": 45,
                }
            ]
        }

        tasks = cli.list_tasks_snapshot(task_state, label="coordination", include_done=False)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], 10)
        self.assertEqual(tasks[0]["labels"], ["process-health", "coordination", "watchdog"])


if __name__ == "__main__":
    unittest.main()
