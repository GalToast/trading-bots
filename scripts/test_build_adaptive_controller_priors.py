#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_controller_priors as priors


class BuildAdaptiveControllerPriorsTests(unittest.TestCase):
    def test_build_payload_carries_fx_and_m5_salvage_priors(self) -> None:
        config_to_perf = {
            "lanes": [
                {"lane": "live_btcusd_m15_warp", "symbol": "BTCUSD", "avg_per_close": 4.59, "realized_net_usd": 1266.74},
                {"lane": "fx_rearm_gbpusd", "symbol": "GBPUSD", "avg_per_close": 3.96, "realized_closes": 61},
                {"lane": "fx_rearm_eurusd", "symbol": "EURUSD", "avg_per_close": 3.17, "realized_closes": 58},
                {"lane": "shadow_ethusd_m5_warp_5", "symbol": "ETHUSD", "avg_per_close": 7.86, "realized_net_usd": 157.17, "realized_closes": 20},
                {"lane": "shadow_btcusd_m5_warp_step200", "symbol": "BTCUSD", "avg_per_close": 69.98, "realized_closes": 2},
                {"lane": "live_btcusd_m5_warp", "symbol": "BTCUSD", "avg_per_close": -36.91},
                {"lane": "live_ethusd_m5_warp", "symbol": "ETHUSD", "avg_per_close": -9.21, "realized_net_usd": -110.54, "realized_closes": 12},
            ]
        }
        real_world = {
            "winning_configs": [
                {"symbol": "GBPUSD", "per_close": 1.84},
                {"symbol": "EURUSD", "per_close": 2.2},
                {"symbol": "NAS100", "per_close": 76.6},
                {"symbol": "US30", "per_close": 27.2},
            ]
        }
        salvage = {"lanes": []}
        rearm = {"current_state_rearm_params": {"GBPUSD": {"canonical_guardrail_status": "aligned", "auto_rearm_allowed": True}, "EURUSD": {"canonical_guardrail_status": "blocked", "auto_rearm_allowed": False}, "BTCUSD": {"canonical_guardrail_status": "blocked"}, "ETHUSD": {"canonical_guardrail_status": "blocked"}, "NAS100": {"canonical_guardrail_status": "aligned"}, "US30": {"canonical_guardrail_status": "blocked"}}}
        promotion = {"rows": [{"symbol": "BTCUSD", "next_action": "hold_until_buy_realign"}, {"symbol": "ETHUSD", "next_action": "unblock_guardrails_first"}, {"symbol": "NAS100", "next_action": "wait_for_session_window"}]}
        regime = {"rows": [{"symbol": "BTCUSD", "action": "SELL"}]}

        payload = priors.build_payload(config_to_perf, real_world, salvage, rearm, promotion, regime)

        self.assertEqual(payload["global_policy"]["session_gate_policy"], "weighting_or_circuit_breaker_only")
        self.assertEqual(payload["symbol_priors"]["GBPUSD"]["close_alpha_prior"], 0.5)
        self.assertEqual(payload["symbol_priors"]["BTCUSD"]["promotion_action"], "hold_until_buy_realign")
        self.assertEqual(payload["symbol_priors"]["ETHUSD"]["m5_shadow_baseline"]["realized_closes"], 20)

    def test_render_markdown_mentions_global_policy(self) -> None:
        payload = {
            "generated_at": "2026-04-15T00:00:00+00:00",
            "leadership_read": ["one"],
            "global_policy": {
                "session_gate_policy": "weighting_or_circuit_breaker_only",
                "graduation_funnel": {"theory_to_shadow": "a", "shadow_to_live": "b", "live_to_scale": "c"},
                "offensive_extreme_closure": {"status": "research_candidate"},
                "dual_lattice_hedge": {"status": "research_candidate"},
            },
            "symbol_priors": {"GBPUSD": {"controller_role": "fx", "close_alpha_prior": 0.5, "guardrail_status": "aligned", "controller_read": "x"}},
            "ranked_hypotheses": [{"priority": 1, "hypothesis": "h", "status": "s", "why": "w"}],
        }

        markdown = priors.render_markdown(payload)
        self.assertIn("Adaptive Controller Priors", markdown)
        self.assertIn("Session gate policy", markdown)
        self.assertIn("GBPUSD", markdown)


if __name__ == "__main__":
    unittest.main()
