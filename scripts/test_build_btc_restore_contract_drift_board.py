from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_btc_restore_contract_drift_board as board


class BuildBtcRestoreContractDriftBoardTests(unittest.TestCase):
    def test_compare_contracts_detects_no_packet_registry_drift(self) -> None:
        comparison = board.compare_contracts(
            [
                "python",
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "--symbol",
                "BTCUSD",
                "--fresh-start",
                "--poll-seconds",
                "30",
            ],
            [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "--symbol",
                "BTCUSD",
                "--fresh-start",
                "--poll-seconds",
                "30",
            ],
        )
        self.assertEqual(comparison["verdict"], "aligned")
        self.assertEqual(comparison["mismatches"], [])

    def test_build_payload_separates_contract_alignment_from_artifact_residue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            configs = root / "configs"
            reports.mkdir(parents=True, exist_ok=True)
            (reports / "watchdog").mkdir(parents=True, exist_ok=True)
            configs.mkdir(parents=True, exist_ok=True)
            state_path = reports / "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "shared_price_max_age_ms": 1000,
                            "direct_live": False,
                        },
                        "runner": {
                            "started_at": "2026-04-16T05:50:25+00:00",
                            "heartbeat_at": "2026-04-16T05:50:56+00:00",
                            "latest_tick_source_last": "shared_price_cache",
                            "latest_tick_append_source_last": "shared_price_cache",
                            "tick_history_source_last": "copy_ticks_range",
                        },
                    }
                ),
                encoding="utf-8",
            )
            overnight = {
                "rows": [
                    {
                        "packet_id": "btc_restore_comparison_shadow",
                        "lane_name": board.LANE,
                        "action_status": "hold_runtime_repair_candidate",
                        "command": [
                            "python",
                            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                            "--symbol",
                            "BTCUSD",
                            "--fresh-start",
                            "--poll-seconds",
                            "30",
                            "--state-path",
                            "reports/penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json",
                        ],
                    }
                ]
            }
            incident = {
                "summary": {
                    "quarantine_reason": "restart_storm=4/4 within 1800s",
                    "quarantined_until": "2099-04-16T06:20:55+00:00",
                }
            }
            registry = {
                "lanes": [
                    {
                        "name": board.LANE,
                        "restart_args": [
                            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                            "--symbol",
                            "BTCUSD",
                            "--fresh-start",
                            "--poll-seconds",
                            "30",
                            "--state-path",
                            "reports/penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json",
                        ],
                    }
                ]
            }

            original_root = board.ROOT
            original_reports = board.REPORTS
            original_watchdog = board.WATCHDOG
            original_configs = board.CONFIGS
            original_overnight = board.OVERNIGHT_PACKET_PATH
            original_incident = board.INCIDENT_PATH
            original_registry = board.REGISTRY_PATH
            try:
                board.ROOT = root
                board.REPORTS = reports
                board.WATCHDOG = reports / "watchdog"
                board.CONFIGS = configs
                board.OVERNIGHT_PACKET_PATH = reports / "adaptive_overnight_launch_packet_board.json"
                board.INCIDENT_PATH = reports / "btc_restore_supervision_incident_board.json"
                board.REGISTRY_PATH = configs / "penetration_lattice_runner_registry.json"
                board.OVERNIGHT_PACKET_PATH.write_text(json.dumps(overnight), encoding="utf-8")
                board.INCIDENT_PATH.write_text(json.dumps(incident), encoding="utf-8")
                board.REGISTRY_PATH.write_text(json.dumps(registry), encoding="utf-8")

                payload = board.build_payload()
            finally:
                board.ROOT = original_root
                board.REPORTS = original_reports
                board.WATCHDOG = original_watchdog
                board.CONFIGS = original_configs
                board.OVERNIGHT_PACKET_PATH = original_overnight
                board.INCIDENT_PATH = original_incident
                board.REGISTRY_PATH = original_registry

        summary = payload["summary"]
        self.assertEqual(summary["packet_registry_contract_verdict"], "aligned")
        self.assertEqual(summary["artifact_contract_verdict"], "artifact_residue_mismatch")
        self.assertEqual(summary["artifact_shared_price_max_age_ms"], 1000)
        self.assertEqual(summary["artifact_latest_tick_source_last"], "shared_price_cache")
        self.assertEqual(summary["relaunch_gate"], "wait_for_quarantine_then_clean_relaunch_on_current_contract")
        self.assertTrue(any("checked-in restore contract is not the main drift surface" in line for line in payload["leadership_read"]))

    def test_build_payload_recognizes_current_contract_already_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            configs = root / "configs"
            reports.mkdir(parents=True, exist_ok=True)
            (reports / "watchdog").mkdir(parents=True, exist_ok=True)
            configs.mkdir(parents=True, exist_ok=True)
            state_path = reports / "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "shared_price_max_age_ms": 0,
                            "direct_live": False,
                        },
                        "runner": {
                            "started_at": "2026-04-16T06:26:53+00:00",
                            "heartbeat_at": "2026-04-16T06:28:53+00:00",
                            "latest_tick_source_last": "symbol_info_tick",
                            "latest_tick_append_source_last": "symbol_info_tick",
                            "tick_history_source_last": "copy_ticks_range",
                        },
                    }
                ),
                encoding="utf-8",
            )
            overnight = {
                "rows": [
                    {
                        "packet_id": "btc_restore_comparison_shadow",
                        "lane_name": board.LANE,
                        "action_status": "already_running_monitor_only",
                        "command": [
                            "python",
                            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                            "--symbol",
                            "BTCUSD",
                            "--fresh-start",
                            "--shared-price-max-age-ms",
                            "0",
                            "--poll-seconds",
                            "30",
                            "--state-path",
                            "reports/penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json",
                        ],
                    }
                ]
            }
            incident = {"summary": {}}
            registry = {
                "lanes": [
                    {
                        "name": board.LANE,
                        "restart_args": [
                            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                            "--symbol",
                            "BTCUSD",
                            "--fresh-start",
                            "--shared-price-max-age-ms",
                            "0",
                            "--poll-seconds",
                            "30",
                            "--state-path",
                            "reports/penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json",
                        ],
                    }
                ]
            }

            original_root = board.ROOT
            original_reports = board.REPORTS
            original_watchdog = board.WATCHDOG
            original_configs = board.CONFIGS
            original_overnight = board.OVERNIGHT_PACKET_PATH
            original_incident = board.INCIDENT_PATH
            original_registry = board.REGISTRY_PATH
            try:
                board.ROOT = root
                board.REPORTS = reports
                board.WATCHDOG = reports / "watchdog"
                board.CONFIGS = configs
                board.OVERNIGHT_PACKET_PATH = reports / "adaptive_overnight_launch_packet_board.json"
                board.INCIDENT_PATH = reports / "btc_restore_supervision_incident_board.json"
                board.REGISTRY_PATH = configs / "penetration_lattice_runner_registry.json"
                board.OVERNIGHT_PACKET_PATH.write_text(json.dumps(overnight), encoding="utf-8")
                board.INCIDENT_PATH.write_text(json.dumps(incident), encoding="utf-8")
                board.REGISTRY_PATH.write_text(json.dumps(registry), encoding="utf-8")

                payload = board.build_payload()
            finally:
                board.ROOT = original_root
                board.REPORTS = original_reports
                board.WATCHDOG = original_watchdog
                board.CONFIGS = original_configs
                board.OVERNIGHT_PACKET_PATH = original_overnight
                board.INCIDENT_PATH = original_incident
                board.REGISTRY_PATH = original_registry

        summary = payload["summary"]
        self.assertEqual(summary["packet_registry_contract_verdict"], "aligned")
        self.assertEqual(summary["artifact_contract_verdict"], "artifact_matches_checked_in_contract")
        self.assertEqual(summary["artifact_shared_price_max_age_ms"], 0)
        self.assertEqual(summary["artifact_latest_tick_source_last"], "symbol_info_tick")
        self.assertEqual(summary["relaunch_gate"], "current_contract_running_monitor_only")
        self.assertTrue(any("matches the checked-in contract" in line for line in payload["leadership_read"]))

    def test_render_markdown_mentions_relaunch_gate(self) -> None:
        text = board.render_markdown(
            {
                "generated_at": "2026-04-16T06:20:00+00:00",
                "summary": {
                    "lane": board.LANE,
                    "overnight_action_status": "hold_runtime_repair_candidate",
                    "incident_quarantine_reason": "restart_storm=4/4 within 1800s",
                    "incident_quarantined_until": "2099-04-16T06:20:55+00:00",
                    "packet_registry_contract_verdict": "aligned",
                    "artifact_contract_verdict": "artifact_residue_mismatch",
                    "artifact_shared_price_max_age_ms": 1000,
                    "artifact_latest_tick_source_last": "shared_price_cache",
                    "relaunch_gate": "wait_for_quarantine_then_clean_relaunch_on_current_contract",
                },
                "leadership_read": ["line1"],
                "contract_comparison": {
                    "packet_bools": ["--fresh-start"],
                    "registry_bools": ["--fresh-start"],
                    "packet_values": {"--symbol": "btcusd"},
                    "registry_values": {"--symbol": "btcusd"},
                    "mismatches": [],
                },
                "artifact_state": {
                    "state_path": "reports/penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json",
                    "exists": True,
                    "metadata_shared_price_max_age_ms": 1000,
                    "metadata_direct_live": False,
                    "runner_started_at": "2026-04-16T05:50:25+00:00",
                    "runner_heartbeat_at": "2026-04-16T05:50:56+00:00",
                    "latest_tick_source_last": "shared_price_cache",
                    "latest_tick_append_source_last": "shared_price_cache",
                    "tick_history_source_last": "copy_ticks_range",
                    "drift_issues": ["metadata_shared_price_max_age_ms expected=0 observed=1000"],
                },
                "notes": ["note"],
            }
        )
        self.assertIn("BTC Restore Contract Drift Board", text)
        self.assertIn("wait_for_quarantine_then_clean_relaunch_on_current_contract", text)
        self.assertIn("artifact_residue_mismatch", text)
