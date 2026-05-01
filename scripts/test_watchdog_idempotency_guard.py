"""Tests for the watchdog idempotency guard (_find_running_lane_pid)."""

import json
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil


class FindRunningLanePidTest(unittest.TestCase):
    """Tests for _find_running_lane_pid idempotency guard."""

    def _import_func(self):
        from watch_penetration_lattice_runners import _find_running_lane_pid
        return _find_running_lane_pid

    def test_returns_none_when_psutil_missing(self):
        """Should return None when psutil is not available."""
        fn = self._import_func()
        with patch("watch_penetration_lattice_runners.psutil", None):
            result = fn("test_lane", "/nonexistent/path")
        self.assertIsNone(result)

    def test_returns_none_when_state_file_missing(self):
        """Should return None when state file doesn't exist."""
        fn = self._import_func()
        mock_psutil = MagicMock()
        mock_psutil.process_iter.return_value = []
        with patch("watch_penetration_lattice_runners.psutil", mock_psutil):
            result = fn("test_lane", "/nonexistent/state.json")
        self.assertIsNone(result)

    def test_returns_pid_when_state_file_fresh_and_process_running(self):
        """Should return PID when state file is fresh and process is running."""
        fn = self._import_func()
        state = {"runner": {"pid": 12345}}
        state_path = Path("test_state_temp.json")
        state_path.write_text(json.dumps(state))
        try:
            mock_proc = MagicMock()
            mock_proc.is_running.return_value = True
            mock_proc.status.return_value = "running"
            mock_proc.cmdline.return_value = ["python", "script.py", "--state-path", str(state_path)]

            mock_psutil = MagicMock()
            mock_psutil.Process.return_value = mock_proc
            mock_psutil.process_iter.return_value = []
            mock_psutil.NoSuchProcess = psutil.NoSuchProcess
            mock_psutil.AccessDenied = psutil.AccessDenied
            mock_psutil.ZombieProcess = psutil.ZombieProcess

            with patch("watch_penetration_lattice_runners.psutil", mock_psutil):
                with patch("time.time", return_value=state_path.stat().st_mtime + 10):
                    result = fn("test_lane", str(state_path))
            self.assertEqual(result, 12345)
        finally:
            state_path.unlink(missing_ok=True)

    def test_returns_none_when_state_file_stale(self):
        """Should return None when state file is too old."""
        fn = self._import_func()
        state = {"runner": {"pid": 12345}}
        state_path = Path("test_state_stale.json")
        old_time = time.time() - 300
        state_path.write_text(json.dumps(state))
        try:
            mock_psutil = MagicMock()
            mock_psutil.process_iter.return_value = []
            mock_psutil.NoSuchProcess = psutil.NoSuchProcess
            mock_psutil.AccessDenied = psutil.AccessDenied
            mock_psutil.ZombieProcess = psutil.ZombieProcess

            with patch("watch_penetration_lattice_runners.psutil", mock_psutil):
                with patch.object(Path, "stat", return_value=MagicMock(st_mtime=old_time)):
                    result = fn("test_lane", str(state_path))
            self.assertIsNone(result)
        finally:
            state_path.unlink(missing_ok=True)

    def test_returns_none_when_process_not_running(self):
        """Should return None when process is not running."""
        fn = self._import_func()
        state = {"runner": {"pid": 12345}}
        state_path = Path("test_state_noproc.json")
        state_path.write_text(json.dumps(state))
        try:
            mock_psutil = MagicMock()
            mock_psutil.NoSuchProcess = psutil.NoSuchProcess
            mock_psutil.AccessDenied = psutil.AccessDenied
            mock_psutil.ZombieProcess = psutil.ZombieProcess
            mock_psutil.Process.side_effect = psutil.NoSuchProcess(12345)
            mock_psutil.process_iter.return_value = []

            with patch("watch_penetration_lattice_runners.psutil", mock_psutil):
                with patch("time.time", return_value=state_path.stat().st_mtime + 10):
                    result = fn("test_lane", str(state_path))
            self.assertIsNone(result)
        finally:
            state_path.unlink(missing_ok=True)

    def test_finds_process_by_state_path_scan(self):
        """Should find process by scanning exact state-path flags when state file check fails."""
        fn = self._import_func()
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 54321,
            "name": "python.exe",
            "cmdline": ["python", "script.py", "--state-path", "reports/state.json"],
        }

        mock_psutil = MagicMock()
        mock_psutil.NoSuchProcess = psutil.NoSuchProcess
        mock_psutil.AccessDenied = psutil.AccessDenied
        mock_psutil.ZombieProcess = psutil.ZombieProcess
        mock_psutil.Process.side_effect = psutil.NoSuchProcess(54321)
        mock_psutil.process_iter.return_value = [mock_proc]

        with patch("watch_penetration_lattice_runners.psutil", mock_psutil):
            result = fn("test_lane", "reports/state.json")
        self.assertEqual(result, 54321)

    def test_ignores_fresh_start_processes(self):
        """Should not match processes with --fresh-start flag."""
        fn = self._import_func()
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 99999,
            "name": "python.exe",
            "cmdline": ["python", "script.py", "--state-path", "reports/state.json", "--fresh-start"],
        }

        mock_psutil = MagicMock()
        mock_psutil.NoSuchProcess = psutil.NoSuchProcess
        mock_psutil.AccessDenied = psutil.AccessDenied
        mock_psutil.ZombieProcess = psutil.ZombieProcess
        mock_psutil.Process.side_effect = psutil.NoSuchProcess(99999)
        mock_psutil.process_iter.return_value = [mock_proc]

        with patch("watch_penetration_lattice_runners.psutil", mock_psutil):
            result = fn("test_lane", "reports/state.json")
        self.assertIsNone(result)

    def test_ignores_watchdog_loop_when_lane_name_matches_but_state_path_does_not(self):
        """Should not confuse the watchdog supervisor for a lane child."""
        fn = self._import_func()
        watchdog_proc = MagicMock()
        watchdog_proc.info = {
            "pid": 14888,
            "name": "python.exe",
            "cmdline": [
                "python",
                "scripts/watch_penetration_lattice_runners.py",
                "--loop",
                "--loop-name",
                "fx_watchdog",
                "--lanes",
                "live_rearm_941777",
            ],
        }

        mock_psutil = MagicMock()
        mock_psutil.NoSuchProcess = psutil.NoSuchProcess
        mock_psutil.AccessDenied = psutil.AccessDenied
        mock_psutil.ZombieProcess = psutil.ZombieProcess
        mock_psutil.process_iter.return_value = [watchdog_proc]

        with patch("watch_penetration_lattice_runners.psutil", mock_psutil):
            result = fn("live_rearm_941777", "reports/penetration_lattice_live_source_state.json")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
