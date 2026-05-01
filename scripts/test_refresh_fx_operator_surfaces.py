#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import refresh_fx_operator_surfaces as refresh


class RefreshFxOperatorSurfacesTests(unittest.TestCase):
    def test_watchdog_command_builds_group_paths(self) -> None:
        cmd = refresh.watchdog_command("shadow_watchdog", ["shadow_gbpusd_tick_forward", "shadow_eurusd_m15_fxmicro"])
        self.assertEqual(cmd[1], "scripts/watch_penetration_lattice_runners.py")
        self.assertIn("reports/watchdog/shadow_watchdog_report.json", cmd)
        self.assertEqual(cmd[-2:], ["shadow_gbpusd_tick_forward", "shadow_eurusd_m15_fxmicro"])

    def test_refresh_commands_include_builders_and_watchdogs(self) -> None:
        with patch.object(
            refresh,
            "group_lanes",
            side_effect=[["live_rearm_941777"], ["shadow_gbpusd_tick_forward"]],
        ):
            commands = refresh.refresh_commands()

        self.assertEqual(commands[0][1], "scripts/build_fx_live_alpha_recent_audit.py")
        self.assertEqual(commands[1][1], "scripts/build_fx_graduation_readiness.py")
        self.assertEqual(commands[2][1], "scripts/build_fx_proof_health_board.py")
        self.assertEqual(commands[3][1], "scripts/watch_penetration_lattice_runners.py")
        self.assertEqual(commands[4][1], "scripts/watch_penetration_lattice_runners.py")
        tail = [cmd[1] for cmd in commands[5:]]
        self.assertEqual(
            tail,
            [
                "scripts/index_event_logs.py",
                "scripts/build_execution_monitor_report.py",
                "scripts/live_lane_dashboard.py",
                "scripts/build_live_crypto_trigger_proximity_board.py",
                "scripts/build_live_crypto_step_atr_quality_board.py",
                "scripts/build_live_crypto_first_fill_pressure_board.py",
                "scripts/build_btc_m15_warp_restore_board.py",
                "scripts/build_eth_atr_runtime_status_board.py",
                "scripts/build_structure_shapeshifter_proof_board.py",
                "scripts/build_lattice_telemetry_gap_board.py",
                "scripts/build_lattice_phase1_event_coverage_board.py",
                "scripts/build_fx_phase1_telemetry_visibility_board.py",
                "scripts/build_fx_shadow_telemetry_recycle_board.py",
                "scripts/build_fx_shadow_telemetry_recycle_packet_board.py",
                "scripts/build_fx_shadow_telemetry_contract_debt_board.py",
                "scripts/build_phase1_telemetry_visibility_board.py",
                "scripts/build_inherited_vs_active_pnl_board.py",
                "scripts/build_experimental_proof_watch_board.py",
                "scripts/build_team_leverage_execution_docket.py",
                "scripts/build_blocker_leverage_board.py",
                "scripts/build_live_magic_scope_audit.py",
                "scripts/build_mt5_user_visibility_board.py",
                "scripts/track_m5_live_portfolio.py",
                "scripts/build_organism_state_report.py",
                "scripts/build_live_btcusd_concentration_board.py",
                "scripts/build_booked_pnl_breakdown_board.py",
                "scripts/build_memory_live_lanes.py",
                "scripts/build_per_symbol_live_seat_board.py",
            ],
        )
        self.assertEqual(commands[5][2:], ["--tail", "200"])
        team_leverage_cmd = next(cmd for cmd in commands if cmd[1] == "scripts/build_team_leverage_execution_docket.py")
        blocker_cmd = next(cmd for cmd in commands if cmd[1] == "scripts/build_blocker_leverage_board.py")
        self.assertEqual(team_leverage_cmd[2], "--skip-refresh-inputs")
        self.assertEqual(blocker_cmd[2], "--skip-refresh-inputs")

    def test_run_commands_raises_on_failure(self) -> None:
        class Result:
            def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        with patch.object(refresh.subprocess, "run", side_effect=[Result(0, "ok"), Result(1, "", "bad")]):
            with self.assertRaises(RuntimeError):
                refresh.run_commands([["python", "ok.py"], ["python", "bad.py"]])


if __name__ == "__main__":
    unittest.main()
