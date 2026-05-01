#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "configs" / "watchdog_groups.json"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def group_lanes(group_name: str) -> list[str]:
    payload = load_json(CONFIG_PATH)
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    group = groups.get(group_name) if isinstance(groups.get(group_name), dict) else {}
    lanes = group.get("lanes") if isinstance(group.get("lanes"), list) else []
    return [str(lane) for lane in lanes if str(lane).strip()]


def watchdog_command(group_name: str, lanes: list[str]) -> list[str]:
    return [
        sys.executable,
        "scripts/watch_penetration_lattice_runners.py",
        "--report-json",
        f"reports/watchdog/{group_name}_report.json",
        "--report-md",
        f"reports/watchdog/{group_name}_report.md",
        "--events-jsonl",
        f"reports/watchdog/{group_name}_events.jsonl",
        "--loop-state-json",
        f"reports/watchdog/{group_name}_loop_state.json",
        "--loop-name",
        group_name,
        "--lanes",
        *lanes,
    ]


def refresh_commands() -> list[list[str]]:
    fx_lanes = group_lanes("fx_watchdog")
    shadow_lanes = group_lanes("shadow_watchdog")
    commands: list[list[str]] = [
        [sys.executable, "scripts/build_fx_live_alpha_recent_audit.py"],
        [sys.executable, "scripts/build_fx_graduation_readiness.py"],
        [sys.executable, "scripts/build_fx_proof_health_board.py"],
    ]
    if fx_lanes:
        commands.append(watchdog_command("fx_watchdog", fx_lanes))
    if shadow_lanes:
        commands.append(watchdog_command("shadow_watchdog", shadow_lanes))
    commands.extend(
        [
            [sys.executable, "scripts/index_event_logs.py", "--tail", "200"],
            [sys.executable, "scripts/build_execution_monitor_report.py"],
            [sys.executable, "scripts/live_lane_dashboard.py"],
            [sys.executable, "scripts/build_live_crypto_trigger_proximity_board.py"],
            [sys.executable, "scripts/build_live_crypto_step_atr_quality_board.py"],
            [sys.executable, "scripts/build_live_crypto_first_fill_pressure_board.py"],
            [sys.executable, "scripts/build_btc_m15_warp_restore_board.py"],
            [sys.executable, "scripts/build_eth_atr_runtime_status_board.py"],
            [sys.executable, "scripts/build_structure_shapeshifter_proof_board.py"],
            [sys.executable, "scripts/build_lattice_telemetry_gap_board.py"],
            [sys.executable, "scripts/build_lattice_phase1_event_coverage_board.py"],
            [sys.executable, "scripts/build_fx_phase1_telemetry_visibility_board.py"],
            [sys.executable, "scripts/build_fx_shadow_telemetry_recycle_board.py"],
            [sys.executable, "scripts/build_fx_shadow_telemetry_recycle_packet_board.py"],
            [sys.executable, "scripts/build_fx_shadow_telemetry_contract_debt_board.py"],
            [sys.executable, "scripts/build_phase1_telemetry_visibility_board.py"],
            [sys.executable, "scripts/build_inherited_vs_active_pnl_board.py"],
            [sys.executable, "scripts/build_experimental_proof_watch_board.py"],
            [sys.executable, "scripts/build_team_leverage_execution_docket.py", "--skip-refresh-inputs"],
            [sys.executable, "scripts/build_blocker_leverage_board.py", "--skip-refresh-inputs"],
            [sys.executable, "scripts/build_live_magic_scope_audit.py"],
            [sys.executable, "scripts/build_mt5_user_visibility_board.py"],
            [sys.executable, "scripts/track_m5_live_portfolio.py"],
            [sys.executable, "scripts/build_organism_state_report.py"],
            [sys.executable, "scripts/build_live_btcusd_concentration_board.py"],
            [sys.executable, "scripts/build_booked_pnl_breakdown_board.py"],
            [sys.executable, "scripts/build_memory_live_lanes.py"],
            [sys.executable, "scripts/build_per_symbol_live_seat_board.py"],
        ]
    )
    return commands


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
