#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import process_lifecycle


class ProcessLifecycleTests(unittest.TestCase):
    def test_reconcile_on_startup_drops_stale_tracker_entries(self) -> None:
        tracker = {
            "lanes": {
                "alpha": {"pid": 111, "launched_at": "2026-04-15T23:00:00+00:00"},
                "beta": {"pid": 222, "launched_at": "2026-04-15T23:05:00+00:00"},
            }
        }

        with patch.object(
            process_lifecycle,
            "process_alive",
            side_effect=lambda pid: pid == 222,
        ):
            reconciliation = process_lifecycle.reconcile_on_startup(tracker)

        self.assertEqual(
            reconciliation["stale_entries"],
            [
                {
                    "lane": "alpha",
                    "stale_pid": 111,
                    "launched_at": "2026-04-15T23:00:00+00:00",
                }
            ],
        )
        self.assertEqual(reconciliation["orphaned_lanes"], [])
        self.assertNotIn("alpha", reconciliation["reconciled_tracker"]["lanes"])
        self.assertIn("beta", reconciliation["reconciled_tracker"]["lanes"])

    def test_reconcile_on_startup_never_targets_peer_watchdog_loops(self) -> None:
        tracker = {"lanes": {}}
        peer_watchdog = (
            "python.exe scripts/watch_penetration_lattice_runners.py "
            "--repair --loop --loop-name crypto_watchdog --lanes shadow_ethusd_m5_atr_optimized"
        )

        with patch.object(process_lifecycle, "process_alive", return_value=True), patch.object(
            process_lifecycle.subprocess,
            "run",
            side_effect=AssertionError(
                f"startup reconciliation should not enumerate peer watchdog loops: {peer_watchdog}"
            ),
        ):
            reconciliation = process_lifecycle.reconcile_on_startup(tracker)

        self.assertEqual(reconciliation["stale_entries"], [])
        self.assertEqual(reconciliation["orphaned_lanes"], [])

    def test_sweep_lane_processes_ignores_watchdog_supervisor_false_match(self) -> None:
        process_rows = [
            {
                "ProcessId": 27728,
                "CommandLine": (
                    "python.exe scripts/watch_penetration_lattice_runners.py "
                    "--repair --loop --loop-name fx_watchdog "
                    "--lanes live_rearm_941777 live_momentum_alpha50_941778"
                ),
            },
            {
                "ProcessId": 32060,
                "CommandLine": (
                    "python.exe scripts/live_penetration_lattice_tick_shadow.py "
                    "--direct-live --symbols EURUSD GBPUSD "
                    "--live-magic 941777 "
                    "--state-path reports/penetration_lattice_live_source_state.json"
                ),
            },
        ]

        with patch.object(
            process_lifecycle.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout=process_lifecycle.json.dumps(process_rows)),
        ):
            matches = process_lifecycle.sweep_lane_processes(
                lane_name="live_rearm_941777",
                magic=941777,
                state_path="reports/penetration_lattice_live_source_state.json",
            )

        self.assertEqual([row["pid"] for row in matches], [32060])


if __name__ == "__main__":
    unittest.main()
