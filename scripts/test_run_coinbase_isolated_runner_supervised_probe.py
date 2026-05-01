#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_coinbase_isolated_runner_supervised_probe as probe


class DummyCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class CoinbaseIsolatedRunnerSupervisedProbeTests(unittest.TestCase):
    def test_choose_target_row_picks_first_exact_run_now(self) -> None:
        row = probe.choose_target_row()
        self.assertEqual(row["coin"], "TRU-USD")
        self.assertEqual(row["queue_rank"], 1)

    def test_choose_target_row_accepts_specific_coin(self) -> None:
        row = probe.choose_target_row("SUP-USD")
        self.assertEqual(row["coin"], "SUP-USD")

    def test_build_payload_records_saved_probe_state(self) -> None:
        def fake_run(
            cmd: list[str],
            capture_output: bool,
            text: bool,
            timeout: int,
            cwd: str,
            creationflags: int,
        ) -> DummyCompletedProcess:
            state_path = Path(cmd[cmd.index("--state-path") + 1])
            state_path.write_text(
                json.dumps(
                    {
                        "total_equity": 48.0,
                        "total_pnl": 0.0,
                        "ledgers": {
                            "TRU-USD": {
                                "position": "active",
                                "signals": 1,
                                "closes": 0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            event_path = Path(cmd[cmd.index("--event-path") + 1])
            event_path.write_text("", encoding="utf-8")
            return DummyCompletedProcess(stdout="HB#1")

        with patch.object(probe.subprocess, "run", side_effect=fake_run):
            payload = probe.build_payload()

        self.assertEqual(payload["summary"]["target_coin"], "TRU-USD")
        self.assertEqual(payload["summary"]["status"], "probe_pass")
        self.assertEqual(payload["summary"]["position"], "active")
        self.assertEqual(payload["summary"]["signals"], 1)

    def test_supervised_probe_uses_no_window_creationflags(self) -> None:
        seen_flags: list[int] = []

        def fake_run(
            cmd: list[str],
            capture_output: bool,
            text: bool,
            timeout: int,
            cwd: str,
            creationflags: int,
        ) -> DummyCompletedProcess:
            seen_flags.append(creationflags)
            state_path = Path(cmd[cmd.index("--state-path") + 1])
            state_path.write_text(json.dumps({"ledgers": {"TRU-USD": {}}}), encoding="utf-8")
            event_path = Path(cmd[cmd.index("--event-path") + 1])
            event_path.write_text("", encoding="utf-8")
            return DummyCompletedProcess(stdout="HB#1")

        with patch.object(probe.subprocess, "run", side_effect=fake_run):
            payload = probe.build_payload()

        self.assertEqual(payload["summary"]["status"], "probe_pass")
        self.assertEqual(seen_flags, [probe.windows_no_window_creationflags()])

    def test_report_paths_keep_tru_default_and_scope_others(self) -> None:
        tru_json, tru_md = probe.report_paths("TRU-USD")
        sup_json, sup_md = probe.report_paths("SUP-USD")
        tru3_json, tru3_md = probe.report_paths("TRU-USD", max_cycles=3)

        self.assertEqual(tru_json.name, "coinbase_isolated_runner_supervised_probe.json")
        self.assertEqual(tru_md.name, "coinbase_isolated_runner_supervised_probe.md")
        self.assertEqual(tru3_json.name, "coinbase_isolated_runner_supervised_probe_truusd_3cycles.json")
        self.assertEqual(tru3_md.name, "coinbase_isolated_runner_supervised_probe_truusd_3cycles.md")
        self.assertEqual(sup_json.name, "coinbase_isolated_runner_supervised_probe_supusd_1cycles.json")
        self.assertEqual(sup_md.name, "coinbase_isolated_runner_supervised_probe_supusd_1cycles.md")


if __name__ == "__main__":
    unittest.main()
