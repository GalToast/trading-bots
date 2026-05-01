import unittest
from unittest.mock import patch
from pathlib import Path
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import switchboard_server_cleanup as cleanup


class _FakeProc:
    def __init__(self, pid, ppid, cmdline, create_time, info=None):
        self.pid = pid
        self._ppid = ppid
        self._cmdline = cmdline
        self._create_time = create_time
        self.info = info or {}

    def ppid(self):
        return self._ppid

    def cmdline(self):
        return self._cmdline

    def create_time(self):
        return self._create_time


class SwitchboardServerCleanupTests(unittest.TestCase):
    def setUp(self):
        self.script_path = Path("comms_server.py").resolve()

    def test_find_duplicate_server_pids_matches_same_parent_only(self):
        processes = [
            _FakeProc(100, 10, ["python", str(self.script_path)], 1.0),
            _FakeProc(101, 10, ["python", str(self.script_path)], 2.0),
            _FakeProc(102, 11, ["python", str(self.script_path)], 3.0),
            _FakeProc(103, 10, ["python", "other.py"], 4.0),
        ]
        result = cleanup.find_duplicate_server_pids(
            processes,
            current_pid=101,
            parent_pid=10,
            script_path=self.script_path,
        )
        self.assertEqual(result, [100])

    def test_find_orphaned_server_pids_matches_missing_parent_only(self):
        processes = [
            _FakeProc(200, 99901, ["python", str(self.script_path)], 1.0),
            _FakeProc(201, 99902, ["python", str(self.script_path)], 2.0),
            _FakeProc(202, 99903, ["python", "other.py"], 3.0),
        ]
        original_pid_exists = cleanup.psutil.pid_exists
        try:
            cleanup.psutil.pid_exists = lambda pid: pid == 99902
            result = cleanup.find_orphaned_server_pids(
                processes,
                current_pid=201,
                script_path=self.script_path,
            )
        finally:
            cleanup.psutil.pid_exists = original_pid_exists
        self.assertEqual(result, [200])

    def test_cmdline_mentions_script_accepts_windows_style_suffix(self):
        cmdline = ["python", "C:\\temp\\nested\\comms_server.py"]
        self.assertTrue(cleanup._cmdline_mentions_script(cmdline, self.script_path))

    def test_safe_proc_attr_prefers_cached_info_snapshot(self):
        proc = _FakeProc(
            250,
            25,
            ["python", str(self.script_path)],
            1.0,
            info={"cmdline": ["python", "C:\\cached\\comms_server.py"]},
        )
        self.assertEqual(cleanup._safe_proc_attr(proc, "cmdline", []), ["python", "C:\\cached\\comms_server.py"])

    def test_server_code_mtime_uses_newest_switchboard_dependency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wrapper = root / "comms_server.py"
            archive = root / "archive" / "war-room" / "comms_server.py"
            helper = root / "switchboard_server_cleanup.py"
            archive.parent.mkdir(parents=True)
            for path in (wrapper, archive, helper):
                path.write_text("# test\n", encoding="utf-8")
            os.utime(wrapper, (10.0, 10.0))
            os.utime(archive, (25.0, 25.0))
            os.utime(helper, (15.0, 15.0))

            result = cleanup.server_code_mtime(wrapper)

        self.assertEqual(result, 25.0)

    def test_find_same_parent_duplicate_server_pids_keeps_newest_per_parent(self):
        processes = [
            _FakeProc(300, 41, ["python", str(self.script_path)], 1.0),
            _FakeProc(301, 41, ["python", str(self.script_path)], 2.0),
            _FakeProc(302, 41, ["python", str(self.script_path)], 3.0),
            _FakeProc(303, 42, ["python", str(self.script_path)], 4.0),
        ]
        result = cleanup.find_same_parent_duplicate_server_pids(processes, script_path=self.script_path)
        self.assertEqual(result, [300, 301])

    def test_build_startup_cleanup_plan_reports_older_siblings_and_targets_orphans_by_default(self):
        processes = [
            _FakeProc(400, 51, ["python", str(self.script_path)], 1.0),
            _FakeProc(401, 51, ["python", str(self.script_path)], 2.0),
            _FakeProc(402, 99901, ["python", str(self.script_path)], 0.5),
        ]
        original_pid_exists = cleanup.psutil.pid_exists
        try:
            cleanup.psutil.pid_exists = lambda pid: pid == 51
            plan = cleanup.build_startup_cleanup_plan(
                processes,
                current_pid=401,
                script_path=self.script_path,
            )
        finally:
            cleanup.psutil.pid_exists = original_pid_exists

        self.assertFalse(plan["exit_current"])
        self.assertEqual(plan["same_parent_duplicate_pids"], [400])
        self.assertEqual(plan["orphaned_pids"], [402])
        self.assertEqual(plan["targets"], [402])

    def test_build_startup_cleanup_plan_targets_older_siblings_when_enabled(self):
        processes = [
            _FakeProc(410, 51, ["python", str(self.script_path)], 1.0),
            _FakeProc(411, 51, ["python", str(self.script_path)], 2.0),
            _FakeProc(412, 99901, ["python", str(self.script_path)], 0.5),
        ]
        original_pid_exists = cleanup.psutil.pid_exists
        try:
            cleanup.psutil.pid_exists = lambda pid: pid == 51
            plan = cleanup.build_startup_cleanup_plan(
                processes,
                current_pid=411,
                script_path=self.script_path,
                terminate_duplicate_siblings=True,
            )
        finally:
            cleanup.psutil.pid_exists = original_pid_exists

        self.assertFalse(plan["exit_current"])
        self.assertEqual(plan["same_parent_duplicate_pids"], [410])
        self.assertEqual(plan["orphaned_pids"], [412])
        self.assertEqual(plan["targets"], [410, 412])
        self.assertEqual(plan["targets"], [410, 412])

    def test_find_outdated_server_pids_matches_processes_older_than_script(self):
        with patch.object(Path, "stat") as stat:
            stat.return_value.st_mtime = 10.0
            processes = [
                _FakeProc(430, 51, ["python", str(self.script_path)], 5.0),
                _FakeProc(431, 51, ["python", str(self.script_path)], 11.0),
                _FakeProc(432, 51, ["python", "other.py"], 1.0),
            ]
            result = cleanup.find_outdated_server_pids(
                processes,
                current_pid=431,
                script_path=self.script_path,
            )

        self.assertEqual(result, [430])

    def test_build_startup_cleanup_plan_reports_outdated_servers_but_does_not_target_by_default(self):
        with patch.object(Path, "stat") as stat:
            stat.return_value.st_mtime = 10.0
            processes = [
                _FakeProc(440, 51, ["python", str(self.script_path)], 5.0),
                _FakeProc(441, 52, ["python", str(self.script_path)], 11.0),
            ]
            original_pid_exists = cleanup.psutil.pid_exists
            try:
                cleanup.psutil.pid_exists = lambda pid: True
                plan = cleanup.build_startup_cleanup_plan(
                    processes,
                    current_pid=441,
                    script_path=self.script_path,
                )
            finally:
                cleanup.psutil.pid_exists = original_pid_exists

        self.assertEqual(plan["outdated_pids"], [440])
        self.assertEqual(plan["targets"], [])

    def test_build_startup_cleanup_plan_targets_outdated_servers_when_enabled(self):
        with patch.object(Path, "stat") as stat:
            stat.return_value.st_mtime = 10.0
            processes = [
                _FakeProc(445, 51, ["python", str(self.script_path)], 5.0),
                _FakeProc(446, 52, ["python", str(self.script_path)], 11.0),
            ]
            original_pid_exists = cleanup.psutil.pid_exists
            try:
                cleanup.psutil.pid_exists = lambda pid: True
                plan = cleanup.build_startup_cleanup_plan(
                    processes,
                    current_pid=446,
                    script_path=self.script_path,
                    terminate_outdated_servers=True,
                )
            finally:
                cleanup.psutil.pid_exists = original_pid_exists

        self.assertEqual(plan["outdated_pids"], [445])
        self.assertEqual(plan["targets"], [445])

    def test_build_startup_cleanup_plan_reports_current_older_duplicate_but_does_not_exit_by_default(self):
        processes = [
            _FakeProc(500, 61, ["python", str(self.script_path)], 1.0),
            _FakeProc(501, 61, ["python", str(self.script_path)], 2.0),
        ]
        original_pid_exists = cleanup.psutil.pid_exists
        try:
            cleanup.psutil.pid_exists = lambda pid: True
            plan = cleanup.build_startup_cleanup_plan(
                processes,
                current_pid=500,
                script_path=self.script_path,
            )
        finally:
            cleanup.psutil.pid_exists = original_pid_exists

        self.assertFalse(plan["exit_current"])
        self.assertEqual(plan["same_parent_duplicate_pids"], [500])
        self.assertEqual(plan["targets"], [])

    def test_build_startup_cleanup_plan_exits_when_current_process_is_older_duplicate_and_enabled(self):
        processes = [
            _FakeProc(510, 61, ["python", str(self.script_path)], 1.0),
            _FakeProc(511, 61, ["python", str(self.script_path)], 2.0),
        ]
        original_pid_exists = cleanup.psutil.pid_exists
        try:
            cleanup.psutil.pid_exists = lambda pid: True
            plan = cleanup.build_startup_cleanup_plan(
                processes,
                current_pid=510,
                script_path=self.script_path,
                terminate_duplicate_siblings=True,
            )
        finally:
            cleanup.psutil.pid_exists = original_pid_exists

        self.assertTrue(plan["exit_current"])
        self.assertEqual(plan["same_parent_duplicate_pids"], [510])
        self.assertEqual(plan["targets"], [])

    def test_run_startup_cleanup_can_be_disabled(self):
        with patch.dict(cleanup.os.environ, {"SWITCHBOARD_ENABLE_STARTUP_CLEANUP": "0"}, clear=True):
            result = cleanup.run_startup_cleanup(self.script_path, current_pid=777)

        self.assertFalse(result["enabled"])
        self.assertEqual(result["current_pid"], 777)
        self.assertEqual(result["targets"], [])
        self.assertEqual(result["actions"], [])

    def test_should_terminate_duplicate_siblings_defaults_off(self):
        with patch.dict(cleanup.os.environ, {}, clear=True):
            self.assertFalse(cleanup.should_terminate_duplicate_siblings())

    def test_should_terminate_duplicate_siblings_respects_explicit_enable(self):
        for value in ("1", "true", "yes", "on"):
            with self.subTest(value=value):
                with patch.dict(cleanup.os.environ, {"SWITCHBOARD_TERMINATE_DUPLICATE_SIBLINGS": value}, clear=True):
                    self.assertTrue(cleanup.should_terminate_duplicate_siblings())

    def test_should_terminate_duplicate_siblings_respects_explicit_disable(self):
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                with patch.dict(cleanup.os.environ, {"SWITCHBOARD_TERMINATE_DUPLICATE_SIBLINGS": value}, clear=True):
                    self.assertFalse(cleanup.should_terminate_duplicate_siblings())

    def test_should_terminate_outdated_servers_defaults_off(self):
        with patch.dict(cleanup.os.environ, {}, clear=True):
            self.assertFalse(cleanup.should_terminate_outdated_servers())

    def test_should_terminate_outdated_servers_respects_explicit_enable(self):
        for value in ("1", "true", "yes", "on"):
            with self.subTest(value=value):
                with patch.dict(cleanup.os.environ, {"SWITCHBOARD_TERMINATE_OUTDATED_SERVERS": value}, clear=True):
                    self.assertTrue(cleanup.should_terminate_outdated_servers())

    def test_should_terminate_outdated_servers_respects_explicit_disable(self):
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                with patch.dict(cleanup.os.environ, {"SWITCHBOARD_TERMINATE_OUTDATED_SERVERS": value}, clear=True):
                    self.assertFalse(cleanup.should_terminate_outdated_servers())

    def test_should_run_startup_cleanup_defaults_on(self):
        with patch.dict(cleanup.os.environ, {}, clear=True):
            self.assertTrue(cleanup.should_run_startup_cleanup())

    def test_should_run_startup_cleanup_respects_explicit_enable(self):
        for value in ("1", "true", "yes", "on"):
            with self.subTest(value=value):
                with patch.dict(cleanup.os.environ, {"SWITCHBOARD_ENABLE_STARTUP_CLEANUP": value}, clear=True):
                    self.assertTrue(cleanup.should_run_startup_cleanup())

    def test_should_run_startup_cleanup_respects_explicit_disable(self):
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                with patch.dict(cleanup.os.environ, {"SWITCHBOARD_ENABLE_STARTUP_CLEANUP": value}, clear=True):
                    self.assertFalse(cleanup.should_run_startup_cleanup())


if __name__ == "__main__":
    unittest.main()
