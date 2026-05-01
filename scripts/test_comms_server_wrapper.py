import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import comms_server


class CommsServerWrapperTests(unittest.TestCase):
    def test_startup_cleanup_success_is_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle_log = Path(tmp) / "lifecycle.jsonl"
            cleanup_result = {
                "enabled": True,
                "process_count": 2,
                "targets": [111],
                "exit_current": False,
                "actions": [{"pid": 111, "status": "terminated"}],
            }
            with patch.object(comms_server, "LIFECYCLE_LOG", lifecycle_log), patch.object(
                comms_server.switchboard_server_cleanup,
                "run_startup_cleanup",
                return_value=cleanup_result,
            ):
                result = comms_server._run_startup_cleanup()

            self.assertEqual(result, cleanup_result)
            row = json.loads(lifecycle_log.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["event"], "startup_cleanup")
            self.assertEqual(row["targets"], [111])
            self.assertFalse(row["exit_current"])

    def test_startup_cleanup_failure_is_nonfatal_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            error_log = Path(tmp) / "errors.jsonl"
            with patch.object(comms_server, "ERROR_LOG", error_log), patch.object(
                comms_server.switchboard_server_cleanup,
                "run_startup_cleanup",
                side_effect=RuntimeError("boom"),
            ):
                result = comms_server._run_startup_cleanup()

            self.assertFalse(result["exit_current"])
            self.assertTrue(result["failed"])
            self.assertIn("RuntimeError: boom", result["error"])
            self.assertIn("startup_cleanup_failed", error_log.read_text(encoding="utf-8"))

    def test_write_state_skips_snapshot_compaction_without_message_overflow(self):
        module = comms_server._module
        state = {"agents": {}, "messages": [{"id": 1}, {"id": 2}]}

        with patch.object(module, "MAX_ACTIVE_MESSAGES", 10), patch.object(
            module, "_write_agents_json"
        ) as write_agents, patch.object(module, "_compact_jsonl_to_snapshot") as compact:
            module.write_state(state)

        write_agents.assert_called_once_with({})
        compact.assert_not_called()

    def test_write_state_compacts_snapshot_when_message_window_overflows(self):
        module = comms_server._module
        state = {"agents": {}, "messages": [{"id": 1}, {"id": 2}, {"id": 3}]}

        with patch.object(module, "MAX_ACTIVE_MESSAGES", 2), patch.object(
            module, "_write_agents_json"
        ), patch.object(module, "prune_state_for_write", return_value=state) as prune, patch.object(
            module, "_compact_jsonl_to_snapshot"
        ) as compact:
            module.write_state(state)

        prune.assert_called_once_with(state)
        compact.assert_called_once()

    def test_heartbeat_agent_uses_narrow_agents_store_and_short_timeout(self):
        module = comms_server._module
        seen = {}

        @contextlib.contextmanager
        def fake_state_lock(timeout_seconds=module.LOCK_TIMEOUT_SECONDS):
            seen["timeout_seconds"] = timeout_seconds
            yield

        with patch.object(module, "HEARTBEAT_LOCK_TIMEOUT_SECONDS", 2.5), patch.object(
            module, "state_lock", fake_state_lock
        ), patch.object(module, "_load_agents_json", return_value={"agent-1": {"agent_id": "agent-1"}}), patch.object(
            module, "ensure_agent_record", return_value={"agent_id": "agent-1", "status": "online"}
        ) as ensure_agent, patch.object(module, "_write_agents_json") as write_agents, patch.object(
            module, "load_state"
        ) as load_state, patch.object(module, "write_state") as write_state:
            result = module.heartbeat_agent("agent-1")

        self.assertTrue(result["ok"])
        self.assertEqual(seen["timeout_seconds"], 2.5)
        ensure_agent.assert_called_once()
        write_agents.assert_called_once_with({"agent-1": {"agent_id": "agent-1"}})
        load_state.assert_not_called()
        write_state.assert_not_called()

    def test_heartbeat_agent_returns_retryable_error_on_lock_failure(self):
        module = comms_server._module

        with patch.object(module, "state_lock", side_effect=TimeoutError("busy")), patch.object(
            module, "log_tool_error"
        ) as log_tool_error:
            result = module.heartbeat_agent("agent-1")

        self.assertFalse(result["ok"])
        self.assertTrue(result["retryable"])
        self.assertEqual(result["error_type"], "TimeoutError")
        log_tool_error.assert_called_once()

    def test_transport_state_payload_classifies_parent_exit(self):
        with patch.object(comms_server.os, "getppid", return_value=999999), patch.object(
            comms_server,
            "_parent_snapshot",
            return_value={"parent_pid": 999999, "parent_alive": False, "parent_name": ""},
        ):
            payload = comms_server._transport_state_payload(started_monotonic=0.0)

        self.assertEqual(payload["exit_classification"], "parent_exited")
        self.assertFalse(payload["parent_alive"])
        self.assertIn("stdin", payload)
        self.assertIn("runtime_seconds", payload)

    def test_transport_state_payload_classifies_exception(self):
        exc = BrokenPipeError("closed")
        with patch.object(
            comms_server,
            "_parent_snapshot",
            return_value={"parent_pid": 123, "parent_alive": True, "parent_name": "codex.exe"},
        ):
            payload = comms_server._transport_state_payload(started_monotonic=0.0, exception=exc)

        self.assertEqual(payload["exit_classification"], "exception")
        self.assertEqual(payload["exception_type"], "BrokenPipeError")
        self.assertEqual(payload["parent_name"], "codex.exe")


if __name__ == "__main__":
    unittest.main()
