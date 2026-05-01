#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_eth_m5_control_proof_gate_board as board


class BuildEthM5ControlProofGateBoardTests(unittest.TestCase):
    def aligned_surface_inputs(self) -> tuple[dict[str, object], dict[str, object]]:
        control_config = {
            "name": "hungry_hippo_ethusd_m5_step14_control",
            "enabled": True,
            "state_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_state.json",
            "event_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_events.jsonl",
            "restart_args": ["--timeframe", "M5", "--step", "14"],
        }
        runner_registry = {
            "lanes": [
                {
                    "name": "hungry_hippo_ethusd_m5_step14_control",
                    "enabled": True,
                    "state_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_state.json",
                    "event_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_events.jsonl",
                }
            ]
        }
        return control_config, runner_registry

    def test_build_payload_blocks_on_control_normalization_drift(self) -> None:
        control_state = {
            "metadata": {
                "step": 14.0,
                "step_buy": 14.0,
                "step_sell": 14.0,
                "declared_step_price_units": 0.14,
                "declared_step_buy_price_units": 0.14,
                "declared_step_sell_price_units": 0.14,
                "raw_close_alpha": 1.0,
                "dynamic_geometry_enabled": False,
            },
            "runner": {"heartbeat_at": "3026-04-15T16:00:00+00:00", "pid": 123},
            "symbols": {
                "ETHUSD": {
                    "anchor": 2325.8,
                    "next_buy_level": 2320.8,
                    "next_sell_level": 2330.8,
                    "base_step_buy_px": 5.0,
                    "base_step_sell_px": 5.0,
                    "realized_closes": 11,
                    "realized_net_usd": 19.53,
                    "open_tickets": [],
                }
            },
        }
        reset_alerts = {
            "safe_lanes": [
                {
                    "lane": "hungry_hippo_ethusd_m5_step14_control",
                    "status": "SAFE",
                    "resets": 0,
                    "reset_rate_per_hour": 0.0,
                }
            ]
        }
        comparison_board = {
            "comparison_status": "blocked_until_control_normalized",
            "normalization_recommendation": {"recommended_control_step": 14.0, "recommended_control_reason": "freeze"},
            "comparison_protocol": {"blocked_by": ["mixed truths"]},
        }
        deployment_gate = {
            "rows": [
                {
                    "symbol": "ETHUSD",
                    "deployment_verdict": "hard_block",
                    "effective_spread_status": "CONTROL-UNDER-TEST",
                    "proof_closes": 17,
                    "guardrail_status": "blocked_by_guardrail",
                }
            ]
        }
        control_config, runner_registry = self.aligned_surface_inputs()

        payload = board.build_payload(control_state, reset_alerts, comparison_board, deployment_gate, control_config, runner_registry)
        self.assertEqual(payload["summary"]["verdict"], "blocked_by_control_normalization")
        self.assertIn("runtime_ladder_not_matching_declared_step", payload["blocking_reasons"])
        self.assertEqual(payload["proof_progress"]["closes_remaining"], 14)
        self.assertFalse(payload["control_runtime"]["runtime_stale"])
        self.assertIn("aligned to the registered step14 control surface", payload["leadership_read"][1])
        self.assertNotIn("canonical configured launch lane", " ".join(payload["advance_when"]))

    def test_build_payload_blocks_on_stale_runtime_first(self) -> None:
        control_state = {
            "metadata": {
                "step": 14.0,
                "step_buy": 14.0,
                "step_sell": 14.0,
                "declared_step_price_units": 0.14,
                "declared_step_buy_price_units": 0.14,
                "declared_step_sell_price_units": 0.14,
                "raw_close_alpha": 1.0,
            },
            "runner": {"heartbeat_at": "2026-04-15T15:00:00+00:00", "pid": 123},
            "symbols": {
                "ETHUSD": {
                    "anchor": 2325.8,
                    "next_buy_level": 2320.8,
                    "next_sell_level": 2330.8,
                    "base_step_buy_px": 0.14,
                    "base_step_sell_px": 0.14,
                    "realized_closes": 11,
                    "realized_net_usd": 19.53,
                    "open_tickets": [],
                }
            },
        }
        reset_alerts = {"safe_lanes": [{"lane": "hungry_hippo_ethusd_m5_step14_control", "status": "SAFE", "resets": 0, "reset_rate_per_hour": 0.0}]}
        comparison_board = {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}, "comparison_protocol": {"blocked_by": []}}
        deployment_gate = {"rows": [{"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST"}]}
        control_config, runner_registry = self.aligned_surface_inputs()

        payload = board.build_payload(control_state, reset_alerts, comparison_board, deployment_gate, control_config, runner_registry)
        self.assertEqual(payload["summary"]["verdict"], "blocked_by_stale_runtime")
        self.assertIn("runtime_heartbeat_stale", payload["blocking_reasons"])
        self.assertTrue(payload["control_runtime"]["runtime_stale"])

    def test_build_payload_blocks_on_surface_alignment_before_runtime_checks(self) -> None:
        control_state = {
            "metadata": {
                "step": 14.0,
                "step_buy": 14.0,
                "step_sell": 14.0,
                "declared_step_price_units": 0.14,
                "declared_step_buy_price_units": 0.14,
                "declared_step_sell_price_units": 0.14,
                "raw_close_alpha": 1.0,
            },
            "runner": {"heartbeat_at": "3026-04-15T16:00:00+00:00", "pid": 123},
            "symbols": {
                "ETHUSD": {
                    "anchor": 2325.8,
                    "next_buy_level": 2311.8,
                    "next_sell_level": 2339.8,
                    "realized_closes": 11,
                    "realized_net_usd": 19.53,
                    "open_tickets": [],
                }
            },
        }
        reset_alerts = {"safe_lanes": [{"lane": "hungry_hippo_ethusd_m5_step14_control", "status": "SAFE", "resets": 0, "reset_rate_per_hour": 0.0}]}
        comparison_board = {"comparison_status": "ready_for_clean_control_vs_variant", "normalization_recommendation": {"recommended_control_step": 14.0}, "comparison_protocol": {"blocked_by": []}}
        deployment_gate = {"rows": [{"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST"}]}
        control_config = {
            "name": "hungry_hippo_ethusd_m5_step14_control",
            "enabled": True,
            "state_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_state.json",
            "event_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_events.jsonl",
            "restart_args": ["--timeframe", "M5", "--step", "5"],
        }
        runner_registry = {
            "lanes": [
                {
                    "name": "hungry_hippo_ethusd_m5_step14_control",
                    "enabled": True,
                    "state_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_state.json",
                    "event_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_events.jsonl",
                }
            ]
        }

        payload = board.build_payload(control_state, reset_alerts, comparison_board, deployment_gate, control_config, runner_registry)
        self.assertEqual(payload["summary"]["verdict"], "blocked_by_surface_alignment")
        self.assertTrue(payload["summary"]["surface_alignment_blocked"])
        self.assertIn("control_board_and_launch_surface_declare_different_steps", payload["blocking_reasons"])
        self.assertNotIn("control_launch_surface_enabled_mismatch", payload["blocking_reasons"])
        self.assertNotIn("control_launch_surface_path_mismatch", payload["blocking_reasons"])
        self.assertNotIn("control_state_is_not_a_registered_launch_lane", payload["blocking_reasons"])
        self.assertTrue(payload["infra_alignment"]["control_state_registered_launch_lane"])
        self.assertFalse(payload["infra_alignment"]["control_state_orphaned_from_registry"])
        self.assertTrue(payload["infra_alignment"]["surface_alignment_blocked"])

    def test_build_payload_flags_operator_config_missing_from_registry(self) -> None:
        control_state = {
            "metadata": {
                "step": 14.0,
                "step_buy": 14.0,
                "step_sell": 14.0,
                "declared_step_price_units": 0.14,
                "declared_step_buy_price_units": 0.14,
                "declared_step_sell_price_units": 0.14,
                "raw_close_alpha": 1.0,
            },
            "runner": {"heartbeat_at": "3026-04-15T16:00:00+00:00", "pid": 123},
            "symbols": {
                "ETHUSD": {
                    "anchor": 2325.8,
                    "next_buy_level": 2320.8,
                    "next_sell_level": 2330.8,
                    "realized_closes": 11,
                    "realized_net_usd": 19.53,
                    "open_tickets": [],
                }
            },
        }
        reset_alerts = {"safe_lanes": [{"lane": "hungry_hippo_ethusd_m5_step14_control", "status": "SAFE", "resets": 0, "reset_rate_per_hour": 0.0}]}
        comparison_board = {"comparison_status": "blocked_until_control_normalized", "normalization_recommendation": {"recommended_control_step": 14.0}, "comparison_protocol": {"blocked_by": []}}
        deployment_gate = {"rows": [{"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST"}]}
        control_config = {
            "name": "hungry_hippo_ethusd_m5_step14_control",
            "enabled": True,
            "state_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_state.json",
            "event_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_events.jsonl",
            "restart_args": ["--timeframe", "M5", "--step", "14", "--step-buy", "14", "--step-sell", "14"],
        }
        runner_registry = {
            "lanes": [
                {
                    "name": "shadow_ethusd_m5_hungry_hippo_v1",
                    "enabled": False,
                    "state_path": "reports/penetration_lattice_shadow_ethusd_m5_hungry_hippo_v1_state.json",
                    "event_path": "reports/penetration_lattice_shadow_ethusd_m5_hungry_hippo_v1_events.jsonl",
                }
            ]
        }

        payload = board.build_payload(control_state, reset_alerts, comparison_board, deployment_gate, control_config, runner_registry)
        self.assertEqual(payload["summary"]["verdict"], "blocked_by_surface_alignment")
        self.assertFalse(payload["infra_alignment"]["registry_lane_found"])
        self.assertIn("control_launch_registry_lane_missing", payload["blocking_reasons"])
        self.assertIn("control_state_is_not_a_registered_launch_lane", payload["blocking_reasons"])

    def test_build_payload_accepts_price_unit_step_for_clean_runtime_geometry(self) -> None:
        control_state = {
            "metadata": {
                "step": 14.0,
                "step_buy": 14.0,
                "step_sell": 14.0,
                "declared_step_price_units": 0.14,
                "declared_step_buy_price_units": 0.14,
                "declared_step_sell_price_units": 0.14,
                "raw_close_alpha": 1.0,
                "dynamic_geometry_enabled": False,
            },
            "runner": {"heartbeat_at": "3026-04-15T16:00:00+00:00", "pid": 123},
            "symbols": {
                "ETHUSD": {
                    "anchor": 2325.8,
                    "next_buy_level": 2325.66,
                    "next_sell_level": 2325.94,
                    "base_step_buy_px": 0.14,
                    "base_step_sell_px": 0.14,
                    "realized_closes": 11,
                    "realized_net_usd": 19.53,
                    "open_tickets": [],
                }
            },
        }
        reset_alerts = {"safe_lanes": [{"lane": "hungry_hippo_ethusd_m5_step14_control", "status": "SAFE", "resets": 0, "reset_rate_per_hour": 0.0}]}
        comparison_board = {"comparison_status": "ready_for_clean_control_vs_variant", "normalization_recommendation": {"recommended_control_step": 14.0}, "comparison_protocol": {"blocked_by": []}}
        deployment_gate = {"rows": [{"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST"}]}
        control_config, runner_registry = self.aligned_surface_inputs()

        payload = board.build_payload(control_state, reset_alerts, comparison_board, deployment_gate, control_config, runner_registry)
        self.assertTrue(payload["control_runtime"]["geometry_normalized"])
        self.assertFalse(payload["control_runtime"]["dynamic_geometry_enabled"])
        self.assertEqual(payload["summary"]["verdict"], "continue_observation")

    def test_negative_expectancy_can_block_even_when_comparison_hygiene_is_ready(self) -> None:
        control_state = {
            "metadata": {
                "step": 14.0,
                "step_buy": 14.0,
                "step_sell": 14.0,
                "declared_step_price_units": 0.14,
                "declared_step_buy_price_units": 0.14,
                "declared_step_sell_price_units": 0.14,
                "raw_close_alpha": 1.0,
                "dynamic_geometry_enabled": False,
            },
            "runner": {"heartbeat_at": "3026-04-15T16:00:00+00:00", "pid": 123},
            "symbols": {
                "ETHUSD": {
                    "anchor": 2325.8,
                    "next_buy_level": 2325.66,
                    "next_sell_level": 2325.94,
                    "base_step_buy_px": 0.14,
                    "base_step_sell_px": 0.14,
                    "realized_closes": 12,
                    "realized_net_usd": -176.28,
                    "open_tickets": [],
                }
            },
        }
        reset_alerts = {"safe_lanes": [{"lane": "hungry_hippo_ethusd_m5_step14_control", "status": "SAFE", "resets": 0, "reset_rate_per_hour": 0.0}]}
        comparison_board = {"comparison_status": "ready_for_clean_control_vs_variant", "normalization_recommendation": {"recommended_control_step": 14.0}, "comparison_protocol": {"blocked_by": []}}
        deployment_gate = {"rows": [{"symbol": "ETHUSD", "deployment_verdict": "hard_block", "effective_spread_status": "CONTROL-UNDER-TEST"}]}
        control_config, runner_registry = self.aligned_surface_inputs()

        payload = board.build_payload(control_state, reset_alerts, comparison_board, deployment_gate, control_config, runner_registry)
        self.assertEqual(payload["summary"]["verdict"], "blocked_by_negative_expectancy")
        self.assertIn("realized_net_not_positive", payload["blocking_reasons"])
        self.assertNotIn("comparison_board_not_ready", payload["blocking_reasons"])

    def test_render_mentions_verdict_and_blocking_reasons(self) -> None:
        payload = {
            "generated_at": "2026-04-15T00:00:00+00:00",
            "leadership_read": ["one"],
            "summary": {"verdict": "continue_observation", "realized_closes": 20, "target_closes": 25, "closes_remaining": 5, "realized_net_usd": 10.0, "avg_per_close": 0.5, "reset_rate_per_hour": 0.0, "comparison_status": "ready"},
            "infra_alignment": {"surface_alignment_blocked": False},
            "control_runtime": {"declared_step_runner_units": 14.0},
            "proof_progress": {"realized_closes": 20},
            "reset_gate": {"status": "SAFE"},
            "comparison_gate": {"comparison_status": "ready", "blocked_by": []},
            "deployment_gate_context": {"deployment_verdict": "hard_block"},
            "blocking_reasons": ["proof_sample_below_target"],
            "advance_when": ["a"],
            "kill_when": ["k"],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("ETH M5 Control Proof Gate Board", markdown)
        self.assertIn("continue_observation", markdown)
        self.assertIn("Infra Alignment", markdown)
        self.assertIn("proof_sample_below_target", markdown)

    def test_render_clarifies_runner_vs_quote_price_units(self) -> None:
        payload = {
            "generated_at": "2026-04-15T00:00:00+00:00",
            "leadership_read": [
                "one",
                "two",
                "Control-runtime machine truth distinguishes runner step arguments (`14.0`) from converted quote-price geometry (`0.14`) so the room does not mistake unit conversion for a control mismatch.",
            ],
            "summary": {"verdict": "continue_observation", "realized_closes": 20, "target_closes": 25, "closes_remaining": 5, "realized_net_usd": 10.0, "avg_per_close": 0.5, "reset_rate_per_hour": 0.0, "comparison_status": "ready"},
            "infra_alignment": {"surface_alignment_blocked": False},
            "control_runtime": {
                "declared_step_runner_units": 14.0,
                "declared_step_quote_price_units": 0.14,
                "runtime_base_step_buy_px": 0.14,
            },
            "proof_progress": {"realized_closes": 20},
            "reset_gate": {"status": "SAFE"},
            "comparison_gate": {"comparison_status": "ready", "blocked_by": []},
            "deployment_gate_context": {"deployment_verdict": "hard_block"},
            "blocking_reasons": ["proof_sample_below_target"],
            "advance_when": ["a"],
            "kill_when": ["k"],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("declared_step_runner_units=14.0", markdown)
        self.assertIn("declared_step_quote_price_units=0.14", markdown)
        self.assertIn("unit conversion", markdown)


if __name__ == "__main__":
    unittest.main()
