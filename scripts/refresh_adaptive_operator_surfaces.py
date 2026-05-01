#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent


def refresh_commands() -> list[list[str]]:
    return [
        [sys.executable, "scripts/build_execution_monitor_report.py"],
        [sys.executable, "scripts/live_lane_dashboard.py"],
        [sys.executable, "scripts/build_organism_state_report.py"],
        [sys.executable, "scripts/build_live_btcusd_concentration_board.py"],
        [sys.executable, "scripts/build_booked_pnl_breakdown_board.py"],
        [sys.executable, "scripts/build_per_symbol_live_seat_board.py"],
        [sys.executable, "scripts/build_adaptive_lattice_proof_board.py"],
        [sys.executable, "scripts/build_adaptive_formula_input_coverage_board.py"],
        [sys.executable, "scripts/build_adaptive_transfer_board.py"],
        [sys.executable, "scripts/build_adaptive_optimizer_board.py"],
        [sys.executable, "scripts/build_adaptive_optimizer_reconciliation_board.py"],
        [sys.executable, "scripts/build_adaptive_optimizer_decision_board.py"],
        [sys.executable, "scripts/adaptive_lattice_shadow_runner.py"],
        [sys.executable, "scripts/build_btc_adaptive_runtime_audit.py"],
        [sys.executable, "scripts/build_btc_m15_warp_restore_board.py"],
        [sys.executable, "scripts/build_adaptive_controller_priors.py"],
        [sys.executable, "scripts/build_adaptive_btc_branch_decision_board.py"],
        [sys.executable, "scripts/build_gbpusd_adaptive_shadow_packet.py"],
        [sys.executable, "scripts/build_adaptive_lab_queue.py", "--skip-refresh-inputs"],
        [sys.executable, "scripts/build_adaptive_overnight_launch_packet_board.py"],
        [sys.executable, "scripts/build_adaptive_incumbent_study_board.py"],
        [sys.executable, "scripts/build_gbpusd_adaptive_first_path_board.py"],
        [sys.executable, "scripts/build_adaptive_lab_queue.py", "--skip-refresh-inputs"],
        [sys.executable, "scripts/build_adaptive_harness_acceptance_verdict_board.py"],
        [sys.executable, "scripts/build_adaptive_lattice_perfection_scorecard_board.py"],
        [sys.executable, "scripts/build_adaptive_shared_score_board.py"],
        [sys.executable, "scripts/build_adaptive_foundational_gap_status_board.py"],
        [sys.executable, "scripts/build_phase1_telemetry_visibility_board.py"],
        [sys.executable, "scripts/build_inherited_vs_active_pnl_board.py"],
        [sys.executable, "scripts/build_telemetry_enforcement_priority_board.py"],
        [sys.executable, "scripts/build_burst_expansion_prevention_board.py"],
        [sys.executable, "scripts/calibrate_spread_escape_thresholds.py"],
        [sys.executable, "scripts/build_prevention_escape_impact_board.py"],
        [sys.executable, "scripts/build_guarded_toxic_flow_contract_board.py"],
        [sys.executable, "scripts/build_per_symbol_live_seat_board.py"],
        [sys.executable, "scripts/build_max_profit_next_action_board.py"],
        [sys.executable, "scripts/build_execution_ready_seat_priority_board.py"],
        [sys.executable, "scripts/build_execution_ready_blind_spot_board.py"],
        [sys.executable, "scripts/build_btc_execution_ready_control_contract_board.py"],
        [sys.executable, "scripts/build_max_profit_contract_gap_board.py"],
        [sys.executable, "scripts/build_max_profit_queue_contract_packet.py"],
        [sys.executable, "scripts/build_max_profit_queue_adoption_board.py"],
        [sys.executable, "scripts/build_max_profit_queue_promotion_board.py"],
        [sys.executable, "scripts/build_max_profit_lattice_doctrine.py"],
        [sys.executable, "scripts/build_max_profit_taskboard_bridge.py"],
    ]


def run_commands(commands: list[list[str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for cmd in commands:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        record = {
            "command": cmd,
            "returncode": int(result.returncode),
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
        }
        results.append(record)
        if result.returncode != 0:
            raise RuntimeError(json.dumps(record, indent=2))
    return results


def main() -> int:
    commands = refresh_commands()
    results = run_commands(commands)
    print(
        json.dumps(
            {
                "commands_run": len(results),
                "commands": [{"command": row["command"], "returncode": row["returncode"]} for row in results],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
