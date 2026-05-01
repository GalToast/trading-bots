import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_process_cleanup as cleanup


def _snap(
    pid: int,
    ppid: int,
    cmdline: list[str],
    *,
    now_ts: float = 1000.0,
    create_time: float = 900.0,
    name: str = "node.exe",
):
    return cleanup.snapshot_from_record(
        {
            "pid": pid,
            "ppid": ppid,
            "name": name,
            "cmdline": cmdline,
            "create_time": create_time,
        },
        now_ts=now_ts,
    )


class McpProcessCleanupTests(unittest.TestCase):
    def test_classify_family_detects_targeted_families(self):
        self.assertEqual(cleanup.classify_family("node chrome-devtools-mcp@latest"), "chrome_devtools")
        self.assertEqual(cleanup.classify_family("node @playwright/mcp cli.js"), "playwright")
        self.assertIsNone(cleanup.classify_family("node other.js"))

    def test_parse_explicit_parent_pid_reads_watchdog_flag(self):
        self.assertEqual(cleanup.parse_explicit_parent_pid("main.js --parent-pid=1234"), 1234)
        self.assertEqual(cleanup.parse_explicit_parent_pid("main.js --parent-pid 9876"), 9876)
        self.assertIsNone(cleanup.parse_explicit_parent_pid("main.js"))

    def test_build_cleanup_plan_marks_ownerless_instance(self):
        now_ts = 1000.0
        processes = [
            _snap(100, 5000, ["node", "npx-cli.js", "-y", "chrome-devtools-mcp@latest"], now_ts=now_ts),
            _snap(101, 100, ["node", "chrome-devtools-mcp.js"], now_ts=now_ts),
            _snap(102, 101, ["node", "main.js", "--parent-pid=101", "chrome-devtools-mcp"], now_ts=now_ts),
        ]
        plan = cleanup.build_cleanup_plan(
            processes,
            min_age_seconds=60.0,
            process_alive_fn=lambda pid: pid in {100, 101, 102},
        )
        self.assertEqual(plan["target_pids"], [100, 101, 102])
        self.assertEqual(plan["targets"][0]["reasons"], ["owner_missing"])

    def test_build_cleanup_plan_skips_young_ownerless_instance(self):
        now_ts = 1000.0
        processes = [
            _snap(
                200,
                5000,
                ["node", "npx-cli.js", "-y", "@playwright/mcp@latest"],
                now_ts=now_ts,
                create_time=970.0,
            ),
            _snap(201, 200, ["node", "@playwright/mcp", "cli.js"], now_ts=now_ts, create_time=970.0),
        ]
        plan = cleanup.build_cleanup_plan(
            processes,
            min_age_seconds=60.0,
            process_alive_fn=lambda pid: pid in {200, 201},
        )
        self.assertEqual(plan["target_pids"], [])

    def test_build_cleanup_plan_marks_duplicates_per_owner(self):
        now_ts = 1000.0
        processes = [
            _snap(300, 9000, ["node", "npx-cli.js", "-y", "chrome-devtools-mcp@latest"], now_ts=now_ts, create_time=850.0),
            _snap(301, 300, ["node", "chrome-devtools-mcp.js"], now_ts=now_ts, create_time=850.0),
            _snap(310, 9000, ["node", "npx-cli.js", "-y", "chrome-devtools-mcp@latest"], now_ts=now_ts, create_time=940.0),
            _snap(311, 310, ["node", "chrome-devtools-mcp.js"], now_ts=now_ts, create_time=940.0),
            _snap(9000, 1, ["codex.exe"], now_ts=now_ts, create_time=700.0, name="codex.exe"),
        ]
        plan = cleanup.build_cleanup_plan(
            processes,
            min_age_seconds=30.0,
            process_alive_fn=lambda pid: pid in {300, 301, 310, 311, 9000},
        )
        self.assertEqual(plan["target_pids"], [300, 301])
        self.assertEqual(plan["targets"][0]["reasons"], ["duplicate_instance_for_owner:310"])

    def test_build_cleanup_plan_marks_dead_explicit_parent(self):
        now_ts = 1000.0
        processes = [
            _snap(400, 8000, ["node", "main.js", "--parent-pid=7777", "chrome-devtools-mcp"], now_ts=now_ts, create_time=870.0),
            _snap(8000, 1, ["cmd.exe"], now_ts=now_ts, create_time=700.0, name="cmd.exe"),
        ]
        plan = cleanup.build_cleanup_plan(
            processes,
            min_age_seconds=60.0,
            process_alive_fn=lambda pid: pid in {400, 8000},
        )
        self.assertEqual(plan["target_pids"], [400])
        self.assertEqual(plan["targets"][0]["reasons"], ["explicit_parent_dead:7777"])


if __name__ == "__main__":
    unittest.main()
