#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_coinbase_isolated_runner_exact_config_dry_probe as probe


class DummyCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class CoinbaseIsolatedRunnerExactConfigDryProbeTests(unittest.TestCase):
    def test_build_payload_only_runs_run_now_exact_rows(self) -> None:
        calls: list[list[str]] = []
        seen_flags: list[int] = []

        def fake_run(
            cmd: list[str],
            capture_output: bool,
            text: bool,
            timeout: int,
            cwd: str,
            creationflags: int,
        ) -> DummyCompletedProcess:
            calls.append(cmd)
            seen_flags.append(creationflags)
            return DummyCompletedProcess(stdout="DRY RUN complete. No live entries. 🎯")

        with patch.object(probe.subprocess, "run", side_effect=fake_run):
            payload = probe.build_payload()

        coins = [row["coin"] for row in payload["rows"]]
        self.assertEqual(coins, ["TRU-USD", "NOM-USD", "SUP-USD"])
        self.assertTrue(all("--dry-run" in cmd for cmd in calls))
        self.assertTrue(all(flag == probe.windows_no_window_creationflags() for flag in seen_flags))
        self.assertEqual(payload["summary"]["overall_status"], "all_pass")

    def test_failed_subprocess_marks_probe_fail(self) -> None:
        def fake_run(
            cmd: list[str],
            capture_output: bool,
            text: bool,
            timeout: int,
            cwd: str,
            creationflags: int,
        ) -> DummyCompletedProcess:
            if "TRU-USD" in cmd:
                return DummyCompletedProcess(returncode=1, stdout="boom")
            return DummyCompletedProcess(stdout="DRY RUN complete. No live entries. 🎯")

        with patch.object(probe.subprocess, "run", side_effect=fake_run):
            payload = probe.build_payload()

        rows = {row["coin"]: row for row in payload["rows"]}
        self.assertEqual(rows["TRU-USD"]["status"], "probe_fail")
        self.assertEqual(payload["summary"]["overall_status"], "has_failures")


if __name__ == "__main__":
    unittest.main()
