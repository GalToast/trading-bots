#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_SERVER = ROOT / "archive" / "war-room" / "comms_server.py"

spec = importlib.util.spec_from_file_location("switchboard_archive_server_for_tests", ARCHIVE_SERVER)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load switchboard server for tests: {ARCHIVE_SERVER}")
server = importlib.util.module_from_spec(spec)
sys.modules.setdefault("switchboard_archive_server_for_tests", server)
spec.loader.exec_module(server)


class SwitchboardTaskboardTests(unittest.TestCase):
    def test_load_task_state_migrates_legacy_tasks_from_message_store_when_no_task_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            legacy_db = tmp_path / "war_room_messages.json"
            legacy_db.write_text(
                json.dumps(
                    {
                        "next_task_id": 3,
                        "next_task_event_id": 2,
                        "next_decision_id": 4,
                        "tasks": [{"id": 2, "title": "Legacy task", "status": "todo"}],
                        "task_events": [{"id": 1, "task_id": 2, "content": "legacy"}],
                        "decisions": [{"id": 3, "title": "Legacy decision", "status": "pending"}],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(server, "DB_FILE", str(legacy_db)), patch.object(
                server, "TASKS_FILE", str(tmp_path / "war_room_tasks.json")
            ):
                task_state = server.load_task_state()

        self.assertEqual(task_state["next_task_id"], 3)
        self.assertEqual(task_state["next_task_event_id"], 2)
        self.assertEqual(task_state["next_decision_id"], 4)
        self.assertEqual([task["id"] for task in task_state["tasks"]], [2])
        self.assertEqual([event["id"] for event in task_state["task_events"]], [1])
        self.assertEqual([decision["id"] for decision in task_state["decisions"]], [3])

    def test_coerce_state_backfills_task_ids(self) -> None:
        state = server.coerce_state(
            {
                "messages": [],
                "tasks": [{"title": "A"}, {"id": 5, "title": "B"}],
                "agents": {},
            }
        )
        self.assertEqual([task["id"] for task in state["tasks"]], [1, 5])
        self.assertEqual(state["next_task_id"], 6)

    def test_bounded_message_snapshot_defaults_to_recent_budget(self) -> None:
        messages = [{"id": idx, "content": f"m{idx}"} for idx in range(server.READ_MESSAGES_DEFAULT_LIMIT + 5)]

        bounded, truncated, omitted_count, effective_limit = server.bounded_message_snapshot(messages)

        self.assertTrue(truncated)
        self.assertEqual(omitted_count, 5)
        self.assertEqual(effective_limit, server.READ_MESSAGES_DEFAULT_LIMIT)
        self.assertEqual([message["id"] for message in bounded], list(range(5, server.READ_MESSAGES_DEFAULT_LIMIT + 5)))

    def test_bounded_message_snapshot_clamps_large_requested_limit(self) -> None:
        messages = [{"id": idx, "content": f"m{idx}"} for idx in range(server.READ_MESSAGES_MAX_LIMIT + 10)]

        bounded, truncated, omitted_count, effective_limit = server.bounded_message_snapshot(messages, limit=9999)

        self.assertTrue(truncated)
        self.assertEqual(omitted_count, 10)
        self.assertEqual(effective_limit, server.READ_MESSAGES_MAX_LIMIT)
        self.assertEqual(bounded[0]["id"], 10)

    def test_read_messages_since_returns_truncation_metadata(self) -> None:
        state = server.default_state()
        for idx in range(8):
            server.create_message(
                state,
                sender=f"sender-{idx}",
                to="ALL",
                content=f"message-{idx}",
                channel=server.DEFAULT_CHANNEL,
                thread_id="",
                message_type="message",
            )

        with patch.object(server, "READ_MESSAGES_DEFAULT_LIMIT", 3), patch.object(server, "READ_MESSAGES_MAX_LIMIT", 4), patch.object(
            server, "load_state", return_value=state
        ):
            result = server.read_messages_since(agent_name="qwen-test", after_id=0)

        self.assertTrue(result["truncated"])
        self.assertEqual(result["omitted_count"], 5)
        self.assertEqual(result["limit"], 3)
        self.assertEqual([message["content"] for message in result["messages"]], ["message-5", "message-6", "message-7"])

    def test_read_messages_since_compacts_large_message_content_by_default(self) -> None:
        state = server.default_state()
        server.create_message(
            state,
            sender="sender",
            to="ALL",
            content="x" * 50,
            channel=server.DEFAULT_CHANNEL,
            thread_id="",
            message_type="message",
        )

        with patch.object(server, "READ_MESSAGE_CONTENT_DEFAULT_CHARS", 10), patch.object(
            server, "READ_MESSAGE_CONTENT_MAX_CHARS", 20
        ), patch.object(server, "load_state", return_value=state):
            result = server.read_messages_since(agent_name="qwen-test", after_id=0, limit=1)

        self.assertTrue(result["content_truncated"])
        self.assertEqual(result["content_omitted_chars"], 40)
        self.assertTrue(result["messages"][0]["content"].startswith("x" * 10))
        self.assertTrue(result["messages"][0]["content_truncated"])
        self.assertEqual(result["messages"][0]["content_original_length"], 50)

    def test_read_messages_since_can_opt_into_full_content_narrowly(self) -> None:
        state = server.default_state()
        server.create_message(
            state,
            sender="sender",
            to="ALL",
            content="x" * 50,
            channel=server.DEFAULT_CHANNEL,
            thread_id="",
            message_type="message",
        )

        with patch.object(server, "READ_MESSAGE_CONTENT_DEFAULT_CHARS", 10), patch.object(
            server, "load_state", return_value=state
        ):
            result = server.read_messages_since(agent_name="qwen-test", after_id=0, limit=1, full_content=True)

        self.assertFalse(result["content_truncated"])
        self.assertEqual(result["content_omitted_chars"], 0)
        self.assertEqual(result["messages"][0]["content"], "x" * 50)

    def test_create_update_and_filter_tasks(self) -> None:
        state = server.default_state()
        first = server.create_task_record(
            state,
            title="Fix switchroom",
            creator="codex-cli",
            board="ops",
            owner="codex-cli",
            labels=["switchboard", "mcp"],
        )
        second = server.create_task_record(
            state,
            title="Archive stale task",
            creator="codex-cli",
            board="ops",
            status="done",
            labels=["cleanup"],
        )
        self.assertEqual(first["id"], 1)
        self.assertEqual(second["id"], 2)
        self.assertEqual(first["slice_description"], "Fix switchroom")
        self.assertEqual(first["stale_after_minutes"], 120)
        self.assertEqual(first["depends_on"], [])
        self.assertEqual(first["blocking_decision_id"], "")
        self.assertEqual(first["evidence"], {})

        updated = server.update_task_record(
            state,
            task_id=1,
            status="in_progress",
            priority="high",
            description="Make MCP coordination reliable",
            slice_description="Make MCP coordination reliable",
            depends_on=[1, 3],
            blocking_decision_id="dec-1",
            stale_after_minutes=45,
            evidence={"closes": 12},
        )
        self.assertEqual(updated["status"], "in_progress")
        self.assertEqual(updated["priority"], "high")
        self.assertEqual(updated["description"], "Make MCP coordination reliable")
        self.assertEqual(updated["slice_description"], "Make MCP coordination reliable")
        self.assertEqual(updated["depends_on"], [1, 3])
        self.assertEqual(updated["blocking_decision_id"], "dec-1")
        self.assertEqual(updated["stale_after_minutes"], 45)
        self.assertEqual(updated["evidence"], {"closes": 12})

        open_ops = server.list_tasks_snapshot(state, board="ops", include_done=False)
        self.assertEqual([task["id"] for task in open_ops], [1])
        open_alias = server.list_tasks_snapshot(state, status="open")
        self.assertEqual([task["id"] for task in open_alias], [1])
        done_alias = server.list_tasks_snapshot(state, status="done")
        self.assertEqual([task["id"] for task in done_alias], [2])

        labeled = server.list_tasks_snapshot(state, label="mcp")
        self.assertEqual([task["id"] for task in labeled], [1])

    def test_task_update_preserves_existing_evidence_when_omitted(self) -> None:
        state = server.default_state()
        task = server.create_task_record(
            state,
            title="Preserve evidence",
            creator="codex-cli",
            evidence={"closes": 12, "net": 44.5},
        )

        updated = server.update_task_record(
            state,
            task_id=task["id"],
            status="blocked",
        )

        self.assertEqual(updated["evidence"], {"closes": 12, "net": 44.5})

    def test_open_blocking_decision_requires_blocked_or_done_task_status(self) -> None:
        state = server.default_task_state()
        decision = server.create_decision_record(
            state,
            title="ETH operator call",
            creator="codex-cli",
            status="pending",
        )

        with self.assertRaisesRegex(ValueError, "status_must_be_blocked_or_done"):
            server.create_task_record(
                state,
                title="Should not bypass decision gate",
                creator="codex-cli",
                status="in_progress",
                blocking_decision_id=str(decision["id"]),
            )

        blocked = server.create_task_record(
            state,
            title="Blocked until operator decision",
            creator="codex-cli",
            status="blocked",
            blocking_decision_id=str(decision["id"]),
        )

        with self.assertRaisesRegex(ValueError, "status_must_be_blocked_or_done"):
            server.update_task_record(
                state,
                task_id=blocked["id"],
                status="in_progress",
            )

        with self.assertRaisesRegex(ValueError, "cannot_clear_or_change_until_resolved"):
            server.update_task_record(
                state,
                task_id=blocked["id"],
                blocking_decision_id="",
            )

    def test_list_tasks_snapshot_can_exclude_or_isolate_stale_tasks(self) -> None:
        state = server.default_state()
        stale_task = server.create_task_record(
            state,
            title="Stale slice",
            creator="codex-cli",
            owner="codex-cli",
            stale_after_minutes=1,
        )
        fresh_task = server.create_task_record(
            state,
            title="Fresh slice",
            creator="codex-cli",
            owner="codex-cli",
            stale_after_minutes=60,
        )
        stale_task["last_heartbeat"] = "2026-04-15T00:00:00+00:00"
        fresh_task["last_heartbeat"] = "2099-04-15T00:00:00+00:00"
        state["tasks"] = [stale_task, fresh_task]

        active_only = server.list_tasks_snapshot(state, include_stale=False)
        self.assertEqual([task["title"] for task in active_only], ["Fresh slice"])

        stale_only = server.list_tasks_snapshot(state, stale_only=True)
        self.assertEqual([task["title"] for task in stale_only], ["Stale slice"])

    def test_ownerless_tasks_are_not_flagged_stale(self) -> None:
        state = server.default_state()
        passive_task = server.create_task_record(
            state,
            title="Waiting market proof",
            creator="codex-cli",
            owner="",
            status="in_progress",
            stale_after_minutes=1,
        )
        passive_task["last_heartbeat"] = "2026-04-15T00:00:00+00:00"
        state["tasks"] = [passive_task]

        listed = server.list_tasks_snapshot(state, include_stale=True)

        self.assertEqual(len(listed), 1)
        self.assertFalse(listed[0]["stale"])

        stale_only = server.list_tasks_snapshot(state, stale_only=True)
        self.assertEqual(stale_only, [])

    def test_update_missing_task_raises(self) -> None:
        with self.assertRaises(KeyError):
            server.update_task_record(server.default_state(), task_id=99, status="done")

    def test_comment_task_records_event_and_refreshes_task_timestamp(self) -> None:
        state = server.default_state()
        task = server.create_task_record(
            state,
            title="Prove coordination health",
            creator="codex-cli",
            board="infra",
        )
        event = server.create_task_event_record(
            state,
            task_id=task["id"],
            author="codex-cli",
            content="Pinned next slice to doctor + task comments.",
        )
        self.assertEqual(event["id"], 1)
        self.assertEqual(event["task_id"], task["id"])
        self.assertEqual(event["type"], "comment")
        self.assertEqual(event["author"], "codex-cli")

        events = server.list_task_events_snapshot(state, task_id=task["id"], limit=10)
        self.assertEqual([item["id"] for item in events], [1])
        refreshed = server.list_tasks_snapshot(state, board="infra")[0]
        self.assertGreaterEqual(refreshed["updated_at"], task["updated_at"])

    def test_create_update_and_filter_decisions(self) -> None:
        state = server.default_task_state()
        first = server.create_decision_record(
            state,
            title="ETH retune direction",
            creator="codex-collab",
            summary="Choose control-vs-retune next step.",
            owner="@main",
            recommended_option="retune_step3",
            options=["kill", "retune_step3"],
            related_task_ids=[1],
            evidence={"closes": 39, "net": -312.46},
            labels=["eth", "decision"],
        )
        second = server.create_decision_record(
            state,
            title="USDJPY carry vs close",
            creator="codex-collab",
            status="resolved",
            labels=["fx"],
        )
        self.assertEqual(first["id"], 1)
        self.assertEqual(second["id"], 2)
        self.assertEqual(first["recommended_option"], "retune_step3")
        self.assertEqual(first["related_task_ids"], [1])

        updated = server.update_decision_record(
            state,
            decision_id=1,
            status="blocked",
            owner="@ops",
            recommended_option="retune_step5",
            related_task_ids=[1, 2],
        )
        self.assertEqual(updated["status"], "blocked")
        self.assertEqual(updated["owner"], "@ops")
        self.assertEqual(updated["recommended_option"], "retune_step5")
        self.assertEqual(updated["related_task_ids"], [1, 2])

        open_items = server.list_decisions_snapshot(state, include_done=False)
        self.assertEqual([decision["id"] for decision in open_items], [1])
        open_alias = server.list_decisions_snapshot(state, status="open")
        self.assertEqual([decision["id"] for decision in open_alias], [1])
        done_alias = server.list_decisions_snapshot(state, status="done")
        self.assertEqual([decision["id"] for decision in done_alias], [2])

        labeled = server.list_decisions_snapshot(state, label="decision")
        self.assertEqual([decision["id"] for decision in labeled], [1])

    def test_decision_update_preserves_existing_evidence_when_omitted(self) -> None:
        state = server.default_task_state()
        decision = server.create_decision_record(
            state,
            title="Preserve decision evidence",
            creator="codex-collab",
            evidence={"net": -312.46, "closes": 39},
        )

        updated = server.update_decision_record(
            state,
            decision_id=decision["id"],
            status="blocked",
        )

        self.assertEqual(updated["evidence"], {"net": -312.46, "closes": 39})

    def test_bootstrap_task_state_file_creates_dedicated_store_from_legacy_embedded_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            legacy_db = tmp_path / "war_room_messages.json"
            dedicated_tasks = tmp_path / "war_room_tasks.json"
            legacy_db.write_text(
                json.dumps(
                    {
                        "next_task_id": 3,
                        "next_task_event_id": 2,
                        "tasks": [{"id": 2, "title": "Legacy task", "status": "todo"}],
                        "task_events": [{"id": 1, "task_id": 2, "content": "legacy"}],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(server, "DB_FILE", str(legacy_db)), patch.object(
                server, "TASKS_FILE", str(dedicated_tasks)
            ), patch.object(server, "TASKS_LOCK_FILE", str(tmp_path / "war_room_tasks.json.lock")), patch.object(
                server, "BASE_DIR", str(tmp_path)
            ):
                before = server.task_store_status()
                self.assertEqual(before["source"], "legacy_embedded")
                self.assertFalse(dedicated_tasks.exists())

                bootstrapped = server.bootstrap_task_state_file()

                self.assertTrue(bootstrapped["bootstrapped"])
                self.assertEqual(bootstrapped["source"], "dedicated")
                self.assertTrue(dedicated_tasks.exists())
                migrated = server.load_task_state()

        self.assertEqual([task["id"] for task in migrated["tasks"]], [2])
        self.assertEqual([event["id"] for event in migrated["task_events"]], [1])


if __name__ == "__main__":
    unittest.main()
