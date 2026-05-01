import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import adaptive_lattice_shadow_runner as runner


class AdaptiveLatticeShadowRunnerTests(unittest.TestCase):
    def write_registry(self, root: Path, *, lane_name: str, state_path: Path, event_path: Path) -> Path:
        registry_path = root / "registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "lanes": [
                        {
                            "name": lane_name,
                            "state_path": str(state_path),
                            "event_path": str(event_path),
                            "restart_args": [
                                "python",
                                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                                "--timeframe",
                                "M15",
                                "--step",
                                "0.0004",
                                "--max-open-per-side",
                                "2",
                            ],
                        }
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return registry_path

    def write_guarded_toxic_regime(self, root: Path) -> Path:
        regime_path = root / "regime_classification_live.json"
        regime_path.write_text(
            """
{
  "symbols": [
    {
      "symbol": "BTCUSD",
      "regime": "STRONG_TREND",
      "atr_percentile": 60.0,
      "directional_bias": 0.26,
      "current_atr": 463.3386,
      "avg_range": 467.7845,
      "range_atr_ratio": 1.0096,
      "range_atr_clamped_coeff": 0.99424,
      "range_atr_formula_step": 465.09136,
      "first_path_verdict": "never_green_toxic_continuation",
      "same_bar_open_burst_count": 12,
      "same_tick_open_burst_count": 12
    }
  ]
}
""".strip(),
            encoding="utf-8",
        )
        return regime_path

    def test_resolve_range_atr_formula_uses_live_range_metrics(self) -> None:
        step_plan = runner.resolve_steps(
            {
                "step_method": {
                    "kind": "range_atr_formula",
                    "formula": "step = avg_range * clamp(1.6 - 0.6 * range_atr_ratio, 0.5, 1.2)",
                }
            },
            15.0,
            {
                "avg_range": 300.0,
                "range_atr_ratio": 1.25,
                "range_atr_clamped_coeff": 0.85,
                "range_atr_formula_step": 255.0,
            },
        )

        self.assertEqual(step_plan["step"], 255.0)
        self.assertEqual(step_plan["step_buy"], 255.0)
        self.assertEqual(step_plan["step_sell"], 255.0)
        self.assertTrue(step_plan["formula_inputs_available"])
        self.assertEqual(step_plan["range_atr_clamped_coeff"], 0.85)
        self.assertIn("regime_classification_live", step_plan["step_source"])

    def test_resolve_range_atr_formula_falls_back_when_metrics_missing(self) -> None:
        step_plan = runner.resolve_steps(
            {"step_method": {"kind": "range_atr_formula"}},
            15.0,
            {"current_atr": 500.0, "step_coeff": 0.8},
        )

        self.assertEqual(step_plan["step"], 15.0)
        self.assertFalse(step_plan["formula_inputs_available"])
        self.assertEqual(sorted(step_plan["missing_inputs"]), ["avg_range", "range_atr_ratio"])
        self.assertTrue(any("range_atr_formula_inputs_missing" in item for item in step_plan["warnings"]))

    def test_btc_plan_uses_expected_shape_and_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = runner.build_plan(regime_path=self.write_guarded_toxic_regime(Path(tmp)))
        self.assertEqual(plan["baseline"]["lane_name"], "shadow_btcusd_m15_warp")
        self.assertEqual(plan["proposed_lane_name"], "shadow_btcusd_m15_adaptive_regime")
        self.assertEqual(
            plan["proposed_state_path"],
            "reports/penetration_lattice_shadow_btcusd_m15_adaptive_regime_state.json",
        )
        self.assertIn("step_review", plan)
        self.assertTrue(
            set(plan["runtime_overlay_contract"]["requested_overlays"]).issubset(
                set(plan["runtime_overlay_contract"]["supported_overlays"])
            )
        )
        self.assertEqual(
            plan["runtime_overlay_contract"]["supported_overlays"],
            ["guard_open_admission", "cluster_aware_escape", "suppress_additional_levels_after_burst"],
        )
        for overlay in plan["runtime_overlay_contract"]["executable_overlays"]:
            if overlay == "guard_open_admission":
                self.assertIn("--guard-open-admission", plan["proposed_command"])
            if overlay == "cluster_aware_escape":
                self.assertIn("--cluster-aware-escape", plan["proposed_command"])
            if overlay == "suppress_additional_levels_after_burst":
                self.assertIn("--suppress-additional-levels-after-burst", plan["proposed_command"])

    def test_btc_plan_uses_design_target_not_legacy_microstep_as_primary_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = runner.build_plan(regime_path=self.write_guarded_toxic_regime(Path(tmp)))
        # Status is manual_review_required because the real runtime audit has
        # 10 resets vs 1 close — survival constraint flags catastrophic reset rate.
        self.assertEqual(plan["status"], "manual_review_required")
        self.assertTrue(
            any("survival_constraint_blocked" in item for item in plan.get("warnings", [])),
        )
        self.assertFalse(any("adaptive_step_vs_baseline_ratio_high" in item for item in plan["warnings"]))
        self.assertFalse(any("runtime_overlay_not_yet_launchable" in item for item in plan["warnings"]))
        self.assertTrue(
            any(
                item.get("comparator_id") == "legacy_warp_baseline"
                and item.get("status") == "legacy_microstep_separation"
                for item in plan["step_review"]["comparators"]
            )
        )
        self.assertTrue(any("unified_design_target" == item.get("comparator_id") for item in plan["step_review"]["comparators"]))
        self.assertIn("python", plan["proposed_command"][0])
        self.assertIn("--raw-close-alpha", plan["proposed_command"])

    def test_plan_keeps_quiet_market_alive_when_costs_do_not_dominate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            regime_path = root / "regime_classification_live.json"
            registry_path = self.write_registry(
                root,
                lane_name="quiet_lane",
                state_path=root / "missing_state.json",
                event_path=root / "missing_events.jsonl",
            )
            runtime_audit_path = root / "missing_runtime_audit.json"
            regime_path.write_text(
                """
{
  "symbols": [
    {
      "symbol": "BTCUSD",
      "regime": "WEAK_TREND",
      "atr_percentile": 5.0,
      "directional_bias": 0.02,
      "current_atr": 100.0,
      "avg_range": 100.0,
      "range_atr_ratio": 1.0,
      "range_atr_clamped_coeff": 1.0,
      "range_atr_formula_step": 100.0
    }
  ]
}
""".strip(),
                encoding="utf-8",
            )
            plan = runner.build_plan(
                lane_name="quiet_lane",
                registry_path=registry_path,
                regime_path=regime_path,
                runtime_audit_path=runtime_audit_path,
            )

        self.assertEqual(plan["controller_recommendation"]["status"], "ok")
        self.assertEqual(plan["controller_recommendation"]["extractability_state"], "active_microstructure_candidate")
        self.assertTrue(plan["proposed_command"])
        self.assertEqual(
            plan["runtime_overlay_contract"]["supported_overlays"],
            ["guard_open_admission", "cluster_aware_escape", "suppress_additional_levels_after_burst"],
        )
        self.assertEqual(plan["runtime_overlay_contract"]["requested_overlays"], [])
        self.assertNotIn("--cluster-aware-escape", plan["proposed_command"])

    def test_plan_blocks_cost_dominated_quiet_market(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            regime_path = root / "regime_classification_live.json"
            registry_path = self.write_registry(
                root,
                lane_name="cost_lane",
                state_path=root / "missing_state.json",
                event_path=root / "missing_events.jsonl",
            )
            runtime_audit_path = root / "missing_runtime_audit.json"
            regime_path.write_text(
                """
{
  "symbols": [
    {
      "symbol": "BTCUSD",
      "regime": "WEAK_TREND",
      "atr_percentile": 5.0,
      "directional_bias": 0.02,
      "current_atr": 100.0,
      "avg_range": 100.0,
      "range_atr_ratio": 1.0,
      "range_atr_clamped_coeff": 1.0,
      "range_atr_formula_step": 100.0,
      "spread_to_range_ratio": 0.75
    }
  ]
}
""".strip(),
                encoding="utf-8",
            )
            plan = runner.build_plan(
                lane_name="cost_lane",
                registry_path=registry_path,
                regime_path=regime_path,
                runtime_audit_path=runtime_audit_path,
            )

        self.assertEqual(plan["status"], "unextractable_cost_dominated")
        self.assertEqual(plan["controller_recommendation"]["status"], "unextractable_cost_dominated")
        self.assertEqual(plan["proposed_command"], [])
        self.assertIn("cost domination", plan["warnings"][0])

    def test_non_btc_plan_derives_symbol_specific_shadow_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            regime_path = root / "regime_classification_live.json"
            state_path = root / "gbp_state.json"
            event_path = root / "gbp_events.jsonl"
            registry_path = self.write_registry(
                root,
                lane_name="live_gbpusd_adaptive_harness_941777",
                state_path=state_path,
                event_path=event_path,
            )
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["lanes"][0]["restart_args"] = [
                "scripts/live_penetration_lattice_tick_shadow.py",
                "--symbols",
                "GBPUSD",
                "--timeframe",
                "M1",
                "--step",
                "0.0002",
                "--max-open-per-side",
                "12",
                "--raw-close-alpha",
                "0.5",
                "--raw-rearm-variant",
                "rearm_lvl2_exc1",
                "--raw-sell-gap",
                "1",
                "--raw-buy-gap",
                "3",
            ]
            registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
            regime_path.write_text(
                """
{
  "symbols": [
    {
      "symbol": "GBPUSD",
      "regime": "STRONG_TREND",
      "atr_percentile": 55.0,
      "directional_bias": 0.30,
      "current_atr": 0.0011,
      "avg_range": 0.0015,
      "range_atr_ratio": 1.1,
      "same_bar_round_trip_rate": 0.05
    }
  ]
}
""".strip(),
                encoding="utf-8",
            )
            runtime_audit_path = root / "missing_runtime_audit.json"
            plan = runner.build_plan(
                lane_name="live_gbpusd_adaptive_harness_941777",
                symbol="GBPUSD",
                registry_path=registry_path,
                regime_path=regime_path,
                runtime_audit_path=runtime_audit_path,
            )

        self.assertEqual(plan["proposed_lane_name"], "shadow_gbpusd_m1_adaptive_regime")
        self.assertEqual(
            plan["proposed_state_path"],
            "reports/penetration_lattice_shadow_gbpusd_m1_adaptive_regime_state.json",
        )
        self.assertIn("--symbols", plan["proposed_command"])
        self.assertIn("GBPUSD", plan["proposed_command"])
        self.assertNotIn("--step", plan["proposed_command"])
        self.assertIn("--step-buy", plan["proposed_command"])
        self.assertIn("--step-sell", plan["proposed_command"])
        self.assertIn("--state-path", plan["proposed_command"])
        self.assertIn("reports/penetration_lattice_shadow_gbpusd_m1_adaptive_regime_state.json", plan["proposed_command"])
        self.assertNotIn("BTC", plan["step_review"]["review_read"])
        self.assertFalse(
            any(item.get("comparator_id") == "unified_design_target" for item in plan["step_review"]["comparators"])
        )

    def test_plan_without_runtime_audit_keeps_extension_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            regime_path = root / "regime_classification_live.json"
            registry_path = self.write_registry(
                root,
                lane_name="btc_lane",
                state_path=root / "missing_state.json",
                event_path=root / "missing_events.jsonl",
            )
            runtime_audit_path = root / "missing_runtime_audit.json"
            regime_path.write_text(
                """
{
  "symbols": [
    {
      "symbol": "BTCUSD",
      "regime": "STRONG_TREND",
      "atr_percentile": 55.0,
      "directional_bias": 0.35,
      "current_atr": 450.0,
      "avg_range": 460.0,
      "range_atr_ratio": 1.02,
      "range_atr_clamped_coeff": 0.99,
      "range_atr_formula_step": 455.4,
      "same_bar_round_trip_rate": 0.05
    }
  ]
}
""".strip(),
                encoding="utf-8",
            )
            plan = runner.build_plan(
                lane_name="btc_lane",
                registry_path=registry_path,
                regime_path=regime_path,
                runtime_audit_path=runtime_audit_path,
            )

        self.assertEqual(plan["controller_recommendation"]["recommended_shape_id"], "btcusd_regime_rangeatr_v1")
        self.assertFalse(plan["runtime_objective_context"]["audit_present"])
        self.assertFalse(plan["runtime_objective_context"]["close_conversion_pressure"])

    def test_runtime_overlay_contract_can_preserve_existing_cluster_tolerance(self) -> None:
        contract = runner.build_runtime_overlay_contract(
            {"--cluster-fill-tolerance": "0.25", "--burst-open-threshold": "4"},
            ["guard_open_admission", "cluster_aware_escape", "suppress_additional_levels_after_burst"],
        )

        self.assertEqual(
            contract["supported_overlays"],
            ["guard_open_admission", "cluster_aware_escape", "suppress_additional_levels_after_burst"],
        )
        self.assertEqual(
            contract["command_flags"],
            [
                "--guard-open-admission",
                "--cluster-aware-escape",
                "--cluster-fill-tolerance",
                "0.25",
                "--suppress-additional-levels-after-burst",
                "--burst-open-threshold",
                "4",
            ],
        )

    def test_runtime_objective_context_exposes_realized_performance_fields(self) -> None:
        objective = runner.runtime_objective_context(
            {
                "runtime_lane": {
                    "lane_name": "shadow_btcusd_m15_adaptive_regime",
                    "open_count": 2,
                    "runner_session_trade_closes": 0,
                    "runner_session_trade_realized_usd": 0.0,
                    "pre_start_state_carry_realized_usd": -17.77,
                    "realized_close_count": 8,
                    "realized_net_usd": 24.0,
                    "anchor_reset_count": 3,
                }
            }
        )

        self.assertTrue(objective["audit_present"])
        self.assertEqual(objective["realized_close_count"], 8)
        self.assertEqual(objective["realized_net_usd"], 24.0)
        self.assertEqual(objective["realized_avg_per_close"], 3.0)
        self.assertEqual(objective["anchor_reset_count"], 3)

    def test_build_plan_uses_lane_tape_read_to_drive_profit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "gbp_state.json"
            event_path = root / "gbp_events.jsonl"
            regime_path = root / "regime_classification_live.json"
            runtime_audit_path = root / "missing_runtime_audit.json"
            registry_path = self.write_registry(
                root,
                lane_name="gbp_tape_lane",
                state_path=state_path,
                event_path=event_path,
            )

            state_path.write_text(
                json.dumps(
                    {
                        "regime": "RANGE",
                        "spread_to_step_ratio": 0.15,
                        "spread_to_range_ratio": 0.05,
                        "current_atr": 0.0008,
                        "runner_session_trade_closes": 2,
                        "runner_session_trade_realized_usd": 4.0,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            event_path.write_text(
                "\n".join(
                    [
                        json.dumps({"event": "open_ticket", "ticket_id": "1", "timestamp": "2026-04-16T15:00:00", "side": "buy"}),
                        json.dumps({"event": "close_ticket", "ticket_id": "1", "timestamp": "2026-04-16T15:00:20", "realized_pnl": 2.0}),
                        json.dumps({"event": "open_ticket", "ticket_id": "2", "timestamp": "2026-04-16T15:01:00", "side": "sell"}),
                        json.dumps({"event": "close_ticket", "ticket_id": "2", "timestamp": "2026-04-16T15:01:25", "realized_pnl": 2.0}),
                        json.dumps({"event": "open_ticket", "ticket_id": "3", "timestamp": "2026-04-16T15:02:00", "side": "buy"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            regime_path.write_text(
                """
{
  "symbols": [
    {
      "symbol": "GBPUSD",
      "regime": "RANGE",
      "atr_percentile": 20.0,
      "directional_bias": 0.6
    }
  ]
}
""".strip(),
                encoding="utf-8",
            )

            plan = runner.build_plan(
                lane_name="gbp_tape_lane",
                symbol="GBPUSD",
                registry_path=registry_path,
                regime_path=regime_path,
                runtime_audit_path=runtime_audit_path,
            )

        self.assertTrue(plan["runtime_objective_context"]["tape_read_present"])
        self.assertEqual(plan["runtime_objective_context"]["tape_profit_mode"], "micro_harvest")
        self.assertEqual(plan["controller_recommendation"]["profit_mode"], "micro_harvest")
        self.assertAlmostEqual(plan["runtime_objective_context"]["runner_session_trade_realized_usd"], 4.0)


if __name__ == "__main__":
    unittest.main()
