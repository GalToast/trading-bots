#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import refresh_adaptive_operator_surfaces as refresh


class RefreshAdaptiveOperatorSurfacesTests(unittest.TestCase):
    def test_refresh_commands_include_current_adaptive_authority_stack(self) -> None:
        commands = refresh.refresh_commands()
        script_names = [cmd[1] for cmd in commands]
        self.assertEqual(
            script_names[:27],
            [
                "scripts/build_execution_monitor_report.py",
                "scripts/live_lane_dashboard.py",
                "scripts/build_organism_state_report.py",
                "scripts/build_live_btcusd_concentration_board.py",
                "scripts/build_booked_pnl_breakdown_board.py",
                "scripts/build_per_symbol_live_seat_board.py",
                "scripts/build_adaptive_lattice_proof_board.py",
                "scripts/build_adaptive_formula_input_coverage_board.py",
                "scripts/build_adaptive_transfer_board.py",
                "scripts/build_adaptive_optimizer_board.py",
                "scripts/build_adaptive_optimizer_reconciliation_board.py",
                "scripts/build_adaptive_optimizer_decision_board.py",
                "scripts/adaptive_lattice_shadow_runner.py",
                "scripts/build_btc_adaptive_runtime_audit.py",
                "scripts/build_btc_m15_warp_restore_board.py",
                "scripts/build_adaptive_controller_priors.py",
                "scripts/build_adaptive_btc_branch_decision_board.py",
                "scripts/build_gbpusd_adaptive_shadow_packet.py",
                "scripts/build_adaptive_lab_queue.py",
                "scripts/build_adaptive_overnight_launch_packet_board.py",
                "scripts/build_adaptive_incumbent_study_board.py",
                "scripts/build_gbpusd_adaptive_first_path_board.py",
                "scripts/build_adaptive_lab_queue.py",
                "scripts/build_adaptive_harness_acceptance_verdict_board.py",
                "scripts/build_adaptive_lattice_perfection_scorecard_board.py",
                "scripts/build_adaptive_shared_score_board.py",
                "scripts/build_adaptive_foundational_gap_status_board.py",
            ],
        )
        self.assertEqual(
            script_names[27:],
            [
                "scripts/build_phase1_telemetry_visibility_board.py",
                "scripts/build_inherited_vs_active_pnl_board.py",
                "scripts/build_telemetry_enforcement_priority_board.py",
                "scripts/build_burst_expansion_prevention_board.py",
                "scripts/calibrate_spread_escape_thresholds.py",
                "scripts/build_prevention_escape_impact_board.py",
                "scripts/build_guarded_toxic_flow_contract_board.py",
                "scripts/build_per_symbol_live_seat_board.py",
                "scripts/build_max_profit_next_action_board.py",
                "scripts/build_execution_ready_seat_priority_board.py",
                "scripts/build_execution_ready_blind_spot_board.py",
                "scripts/build_btc_execution_ready_control_contract_board.py",
                "scripts/build_max_profit_contract_gap_board.py",
                "scripts/build_max_profit_queue_contract_packet.py",
                "scripts/build_max_profit_queue_adoption_board.py",
                "scripts/build_max_profit_queue_promotion_board.py",
                "scripts/build_max_profit_lattice_doctrine.py",
                "scripts/build_max_profit_taskboard_bridge.py",
            ],
        )
        self.assertEqual(len(commands), 45)
        self.assertEqual(commands[18][2], "--skip-refresh-inputs")
        self.assertEqual(commands[22][2], "--skip-refresh-inputs")

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
