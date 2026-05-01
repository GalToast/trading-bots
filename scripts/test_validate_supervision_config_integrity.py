#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate_supervision_config_integrity as validator


class ValidateSupervisionConfigIntegrityTests(unittest.TestCase):
    def test_validate_configs_accepts_strict_valid_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            registry_path = tmp / "registry.json"
            watchdog_path = tmp / "watchdog.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "lane_a",
                                "kind": "shadow_fx",
                                "state_path": "reports/lane_a_state.json",
                                "event_path": "reports/lane_a_events.jsonl",
                                "process_match_substrings": ["runner.py", "lane_a_state.json"],
                                "restart_args": [
                                    "runner.py",
                                    "--state-path",
                                    "reports/lane_a_state.json",
                                    "--event-path",
                                    "reports/lane_a_events.jsonl",
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            watchdog_path.write_text(
                json.dumps({"groups": {"shadow_watchdog": {"lanes": ["lane_a"]}}}),
                encoding="utf-8",
            )

            result = validator.validate_configs(
                repo_root=tmp,
                registry_path=registry_path,
                watchdog_groups_path=watchdog_path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["lane_count"], 1)

    def test_validate_configs_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            registry_path = tmp / "registry.json"
            watchdog_path = tmp / "watchdog.json"
            registry_path.write_text('{"lanes": []}trailing-junk', encoding="utf-8")
            watchdog_path.write_text(json.dumps({"groups": {}}), encoding="utf-8")

            result = validator.validate_configs(
                repo_root=tmp,
                registry_path=registry_path,
                watchdog_groups_path=watchdog_path,
            )

        self.assertFalse(result["ok"])
        self.assertTrue(any("invalid_json" in error for error in result["errors"]))

    def test_validate_configs_rejects_duplicate_and_unknown_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            registry_path = tmp / "registry.json"
            watchdog_path = tmp / "watchdog.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "lane_a",
                                "state_path": "reports/lane_a_state.json",
                                "process_match_substrings": ["runner.py"],
                                "restart_args": ["runner.py"],
                            },
                            {
                                "name": "lane_a",
                                "state_path": "reports/lane_b_state.json",
                                "process_match_substrings": ["runner.py"],
                                "restart_args": ["runner.py"],
                            },
                            {
                                "name": "lane_b",
                                "state_path": "reports/lane_b_state.json",
                                "process_match_substrings": [],
                                "restart_args": [],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            watchdog_path.write_text(
                json.dumps(
                    {
                        "groups": {
                            "shadow_watchdog": {
                                "lanes": ["lane_a", "lane_a", "lane_missing"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = validator.validate_configs(
                repo_root=tmp,
                registry_path=registry_path,
                watchdog_groups_path=watchdog_path,
            )

        self.assertFalse(result["ok"])
        self.assertIn("penetration_lattice_runner_registry.json: duplicate_lane_name=lane_a", result["errors"])
        self.assertIn("penetration_lattice_runner_registry.json: lanes[2]: missing_process_match_substrings", result["errors"])
        self.assertIn("penetration_lattice_runner_registry.json: lanes[2]: missing_restart_args", result["errors"])
        self.assertIn("watchdog_groups.json: groups.shadow_watchdog: duplicate_lane_name=lane_a", result["errors"])
        self.assertIn("watchdog_groups.json: groups.shadow_watchdog: unknown_lane=lane_missing", result["errors"])

    def test_validate_configs_rejects_cross_group_lane_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            registry_path = tmp / "registry.json"
            watchdog_path = tmp / "watchdog.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "lane_a",
                                "state_path": "reports/lane_a_state.json",
                                "event_path": "reports/lane_a_events.jsonl",
                                "process_match_substrings": ["runner.py", "lane_a_state.json"],
                                "restart_args": [
                                    "runner.py",
                                    "--state-path",
                                    "reports/lane_a_state.json",
                                    "--event-path",
                                    "reports/lane_a_events.jsonl",
                                ],
                            },
                            {
                                "name": "lane_b",
                                "state_path": "reports/lane_b_state.json",
                                "event_path": "reports/lane_b_events.jsonl",
                                "process_match_substrings": ["runner.py", "lane_b_state.json"],
                                "restart_args": [
                                    "runner.py",
                                    "--state-path",
                                    "reports/lane_b_state.json",
                                    "--event-path",
                                    "reports/lane_b_events.jsonl",
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            watchdog_path.write_text(
                json.dumps(
                    {
                        "groups": {
                            "fx_watchdog": {"lanes": ["lane_a"]},
                            "crypto_watchdog": {"lanes": ["lane_a", "lane_b"]},
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = validator.validate_configs(
                repo_root=tmp,
                registry_path=registry_path,
                watchdog_groups_path=watchdog_path,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "watchdog_groups.json: lane_in_multiple_groups=lane_a groups=crypto_watchdog,fx_watchdog",
            result["errors"],
        )

    def test_validate_configs_rejects_infrastructure_lane_in_watchdog_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            registry_path = tmp / "registry.json"
            watchdog_path = tmp / "watchdog.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "shared_price_feeder",
                                "kind": "infrastructure",
                                "state_path": "reports/shared_price_feeder_heartbeat.json",
                                "process_match_substrings": ["scripts/shared_price_feeder.py"],
                                "restart_args": ["scripts/shared_price_feeder.py"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            watchdog_path.write_text(
                json.dumps({"groups": {"crypto_watchdog": {"lanes": ["shared_price_feeder"]}}}),
                encoding="utf-8",
            )

            result = validator.validate_configs(
                repo_root=tmp,
                registry_path=registry_path,
                watchdog_groups_path=watchdog_path,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "watchdog_groups.json: groups.crypto_watchdog: infrastructure_lane_not_allowed=shared_price_feeder",
            result["errors"],
        )

    def test_validate_configs_rejects_restart_path_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            registry_path = tmp / "registry.json"
            watchdog_path = tmp / "watchdog.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "lane_a",
                                "state_path": "reports/lane_a_state.json",
                                "event_path": "reports/lane_a_events.jsonl",
                                "process_match_substrings": ["runner.py"],
                                "restart_args": [
                                    "runner.py",
                                    "--state-path",
                                    "reports/lane_a_state.json",
                                    "--event-path",
                                    "reports/lane_a_state.json",
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            watchdog_path.write_text(
                json.dumps({"groups": {"shadow_watchdog": {"lanes": ["lane_a"]}}}),
                encoding="utf-8",
            )

            result = validator.validate_configs(
                repo_root=tmp,
                registry_path=registry_path,
                watchdog_groups_path=watchdog_path,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "penetration_lattice_runner_registry.json: lanes[0]: restart_event_path_mismatch expected=reports/lane_a_events.jsonl actual=reports/lane_a_state.json",
            result["errors"],
        )

    def test_validate_configs_rejects_fx_shadow_launcher_with_m5_warp_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            registry_path = tmp / "registry.json"
            watchdog_path = tmp / "watchdog.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "lane_a",
                                "state_path": "reports/lane_a_state.json",
                                "event_path": "reports/lane_a_events.jsonl",
                                "process_match_substrings": ["scripts/live_penetration_lattice_tick_shadow.py"],
                                "restart_args": [
                                    "scripts/live_penetration_lattice_tick_shadow.py",
                                    "--symbol",
                                    "GBPUSD",
                                    "--timeframe",
                                    "M5",
                                    "--step",
                                    "0.00033",
                                    "--max-open-per-side",
                                    "12",
                                    "--state-path",
                                    "reports/lane_a_state.json",
                                    "--event-path",
                                    "reports/lane_a_events.jsonl",
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            watchdog_path.write_text(
                json.dumps({"groups": {"fx_watchdog": {"lanes": ["lane_a"]}}}),
                encoding="utf-8",
            )

            result = validator.validate_configs(
                repo_root=tmp,
                registry_path=registry_path,
                watchdog_groups_path=watchdog_path,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "penetration_lattice_runner_registry.json: lanes[0]: restart_script_incompatible script=scripts/live_penetration_lattice_tick_shadow.py flags=--max-open-per-side,--step,--symbol,--timeframe",
            result["errors"],
        )

    def test_validate_configs_rejects_registry_launcher_without_mt5_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            scripts_dir = tmp / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "raw_runner.py").write_text(
                "import MetaTrader5 as mt5\n"
                "def main():\n"
                "    return mt5.initialize()\n",
                encoding="utf-8",
            )
            registry_path = tmp / "registry.json"
            watchdog_path = tmp / "watchdog.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "lane_a",
                                "state_path": "reports/lane_a_state.json",
                                "event_path": "reports/lane_a_events.jsonl",
                                "process_match_substrings": ["scripts/raw_runner.py"],
                                "restart_args": [
                                    "scripts/raw_runner.py",
                                    "--state-path",
                                    "reports/lane_a_state.json",
                                    "--event-path",
                                    "reports/lane_a_events.jsonl",
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            watchdog_path.write_text(
                json.dumps({"groups": {"shadow_watchdog": {"lanes": ["lane_a"]}}}),
                encoding="utf-8",
            )

            result = validator.validate_configs(
                repo_root=tmp,
                registry_path=registry_path,
                watchdog_groups_path=watchdog_path,
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "penetration_lattice_runner_registry.json: lanes[0]: restart_script_missing_mt5_guard script=scripts/raw_runner.py",
            result["errors"],
        )

    def test_validate_configs_accepts_registry_launcher_with_mt5_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            scripts_dir = tmp / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "guarded_runner.py").write_text(
                "import MetaTrader5 as mt5\n"
                "import mt5_terminal_guard\n"
                "def main():\n"
                "    return mt5_terminal_guard.initialize_mt5(mt5_module=mt5)\n",
                encoding="utf-8",
            )
            registry_path = tmp / "registry.json"
            watchdog_path = tmp / "watchdog.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "lanes": [
                            {
                                "name": "lane_a",
                                "state_path": "reports/lane_a_state.json",
                                "event_path": "reports/lane_a_events.jsonl",
                                "process_match_substrings": ["scripts/guarded_runner.py"],
                                "restart_args": [
                                    "scripts/guarded_runner.py",
                                    "--state-path",
                                    "reports/lane_a_state.json",
                                    "--event-path",
                                    "reports/lane_a_events.jsonl",
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            watchdog_path.write_text(
                json.dumps({"groups": {"shadow_watchdog": {"lanes": ["lane_a"]}}}),
                encoding="utf-8",
            )

            result = validator.validate_configs(
                repo_root=tmp,
                registry_path=registry_path,
                watchdog_groups_path=watchdog_path,
            )

        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
