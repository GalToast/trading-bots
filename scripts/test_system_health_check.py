#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import system_health_check as health

TEST_MT5_LOGIN = 100001


class FakeMt5:
    def __init__(self) -> None:
        self.shutdown_called = False

    def terminal_info(self):
        return SimpleNamespace(
            connected=True,
            trade_mode=2,
            name="MetaTrader 5",
            company="Hugosway",
        )

    def shutdown(self):
        self.shutdown_called = True


class SystemHealthCheckTests(unittest.TestCase):
    def test_summarize_python_processes_separates_expected_and_unexpected(self) -> None:
        rows = [
            {
                "ProcessId": 101,
                "ParentProcessId": 1,
                "CommandLine": 'python scripts/watch_penetration_lattice_runners.py --loop',
            },
            {
                "ProcessId": 102,
                "ParentProcessId": 2,
                "CommandLine": 'python "C:/Users/HP/Desktop/Temp while my comp is at the shop/trading-bots/comms_server.py"',
            },
            {
                "ProcessId": 103,
                "ParentProcessId": 2,
                "CommandLine": 'python "C:/Users/HP/Desktop/Temp while my comp is at the shop/trading-bots/comms_server.py"',
            },
            {
                "ProcessId": 104,
                "ParentProcessId": 9,
                "CommandLine": 'python scripts/ad_hoc_probe.py',
            },
            {
                "ProcessId": 105,
                "ParentProcessId": 9,
                "CommandLine": 'python scripts/another_probe.py',
            },
            {
                "ProcessId": 106,
                "ParentProcessId": 9,
                "CommandLine": 'python scripts/third_probe.py',
            },
        ]

        summary = health.summarize_python_processes(
            rows,
            expected_scripts={"scripts/watch_penetration_lattice_runners.py", "comms_server.py"},
        )

        self.assertEqual(summary["python_process_count"], 6)
        self.assertEqual(summary["expected_python_process_count"], 3)
        self.assertEqual(summary["unexpected_python_process_count"], 3)
        self.assertEqual(summary["comms_server_process_count"], 2)
        self.assertEqual(summary["comms_server_same_parent_duplicate_count"], 1)
        self.assertTrue(summary["zombie_risk"])
        self.assertIn("scripts/ad_hoc_probe.py#104", summary["unexpected_python_examples"])

    def test_get_watchdog_status_marks_ad_hoc_stale_artifacts_and_excludes_them(self) -> None:
        with TemporaryDirectory() as tmpdir:
            watchdog_dir = Path(tmpdir)
            (watchdog_dir / "fx_watchdog_loop_state.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "rows_total": 11,
                        "pid": 47428,
                        "updated_at": "2026-04-15T22:18:30+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (watchdog_dir / "fx_repro_loop_state.json").write_text(
                json.dumps(
                    {
                        "status": "starting",
                        "rows_total": 0,
                        "pid": 34740,
                        "updated_at": "2026-04-15T21:03:44+00:00",
                    }
                ),
                encoding="utf-8",
            )

            status = health.get_watchdog_status(
                watchdog_dir=str(watchdog_dir),
                configured_groups={"fx_watchdog"},
                now_dt=datetime(2026, 4, 15, 22, 30, tzinfo=timezone.utc),
                pid_running_fn=lambda pid: pid == 47428,
            )

        self.assertTrue(status["fx_watchdog"]["configured_group"])
        self.assertTrue(status["fx_watchdog"]["health_included"])
        self.assertTrue(status["fx_watchdog"]["pid_running"])
        self.assertFalse(status["fx_watchdog"]["stale_artifact"])
        self.assertFalse(status["fx_repro"]["configured_group"])
        self.assertFalse(status["fx_repro"]["health_included"])
        self.assertFalse(status["fx_repro"]["pid_running"])
        self.assertTrue(status["fx_repro"]["stale_artifact"])

    def test_get_mt5_status_uses_guard_contract_on_success(self) -> None:
        fake_mt5 = FakeMt5()
        payload = {
            "connected": True,
            "identity_ok": True,
            "login": TEST_MT5_LOGIN,
            "server": "Hugosway-Demo",
            "terminal_path": r"C:\Program Files\Hugosway\Hugosway PRO5 Terminal",
            "trade_allowed": True,
            "terminal_connected": True,
            "contract": {
                "binding_mode": "path_pinned",
            },
        }

        with mock.patch.dict(sys.modules, {"MetaTrader5": fake_mt5}):
            with mock.patch.object(health.mt5_terminal_guard, "initialize_mt5", return_value=(True, payload)) as init_mock:
                status = health.get_mt5_status()

        init_mock.assert_called_once_with(mt5_module=fake_mt5)
        self.assertTrue(status["connected"])
        self.assertTrue(status["identity_ok"])
        self.assertEqual(status["binding_mode"], "path_pinned")
        self.assertEqual(status["login"], TEST_MT5_LOGIN)
        self.assertEqual(status["server"], "Hugosway-Demo")
        self.assertEqual(status["name"], "MetaTrader 5")
        self.assertEqual(status["company"], "Hugosway")
        self.assertTrue(fake_mt5.shutdown_called)

    def test_get_mt5_status_returns_guard_failure_summary(self) -> None:
        fake_mt5 = FakeMt5()
        payload = {
            "reason": "identity_mismatch",
            "identity_mismatches": ["terminal_path_mismatch"],
            "login": 1000999,
            "server": "Wrong-Server",
            "terminal_path": r"C:\Wrong Terminal",
            "contract": {
                "binding_mode": "path_pinned",
            },
        }

        with mock.patch.dict(sys.modules, {"MetaTrader5": fake_mt5}):
            with mock.patch.object(health.mt5_terminal_guard, "initialize_mt5", return_value=(False, payload)) as init_mock:
                status = health.get_mt5_status()

        init_mock.assert_called_once_with(mt5_module=fake_mt5)
        self.assertFalse(status["connected"])
        self.assertFalse(status["identity_ok"])
        self.assertEqual(status["binding_mode"], "path_pinned")
        self.assertEqual(status["reason"], "identity_mismatch")
        self.assertIn("terminal_path_mismatch", status["error"])
        self.assertTrue(fake_mt5.shutdown_called)


if __name__ == "__main__":
    unittest.main()
