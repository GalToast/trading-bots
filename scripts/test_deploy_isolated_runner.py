#!/usr/bin/env python3
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import deploy_isolated_runner as deploy


class DummyCompletedProcess:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


class DummyPopen:
    def __init__(self) -> None:
        self.pid = 12345

    def poll(self) -> None:
        return None


class DeployIsolatedRunnerTests(unittest.TestCase):
    def test_check_runner_alive_uses_no_window_creationflags(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> DummyCompletedProcess:
            calls.append({"cmd": cmd, **kwargs})
            return DummyCompletedProcess(stdout="")

        with patch.object(deploy.subprocess, "run", side_effect=fake_run):
            alive = deploy.check_runner_alive()

        self.assertFalse(alive)
        self.assertEqual(calls[0]["creationflags"], deploy.windows_no_window_creationflags())

    def test_background_main_uses_no_window_creationflags(self) -> None:
        popen_calls: list[dict[str, object]] = []

        def fake_popen(cmd: list[str], **kwargs: object) -> DummyPopen:
            popen_calls.append({"cmd": cmd, **kwargs})
            return DummyPopen()

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "deploy.log"
            argv = ["deploy_isolated_runner.py", "--coins", "TRU-USD", "--max-cycles", "1"]
            with patch.object(sys, "argv", argv):
                with patch.object(deploy, "LOG_PATH", log_path):
                    with patch.object(deploy, "validate_setup", return_value=[]):
                        with patch.object(deploy, "archive_state"):
                            with patch.object(deploy, "check_runner_alive", return_value=False):
                                with patch.object(deploy.time, "sleep"):
                                    with patch.object(deploy.subprocess, "Popen", side_effect=fake_popen):
                                        rc = deploy.main()

        self.assertEqual(rc, 0)
        self.assertEqual(popen_calls[0]["creationflags"], deploy.windows_no_window_creationflags())


if __name__ == "__main__":
    unittest.main()
