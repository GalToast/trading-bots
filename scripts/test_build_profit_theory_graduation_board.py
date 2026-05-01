#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_profit_theory_graduation_board as board


class BuildProfitTheoryGraduationBoardTests(unittest.TestCase):
    def test_build_payload_ranks_theories_by_current_stage(self) -> None:
        controller_priors = {
            "global_policy": {
                "graduation_funnel": {"theory_to_shadow": "requires proof"},
                "offensive_extreme_closure": {"status": "research_candidate", "read": "cheaply cut extremes"},
                "dual_lattice_hedge": {"status": "research_candidate", "read": "needs replay"},
            },
            "symbol_priors": {
                "GBPUSD": {"evidence": {"gbp_rearm_avg_per_close": 3.95}},
                "EURUSD": {"evidence": {"eur_rearm_avg_per_close": 3.17}, "guardrail_status": "blocked"},
                "BTCUSD": {"promotion_action": "hold_until_buy_realign"},
                "ETHUSD": {"failed_live_reference": {"avg_per_close": -9.21}},
                "US30": {"guardrail_status": "blocked"},
            },
        }
        salvage_board = {
            "lanes": [
                {"lane": "shadow_btcusd_m5_warp_step200", "avg_per_close": 69.98, "realized_closes": 2, "realized_net_usd": 139.96},
                {"lane": "live_btcusd_m15_warp", "avg_per_close": 4.5896},
            ]
        }
        promotion_queue = {
            "rows": [
                {"symbol": "NAS100", "next_action": "wait_for_session_window"},
                {"symbol": "US30", "next_action": "unblock_guardrails_first"},
            ]
        }
        btc_handoff = {
            "current_truth": {"regime_signal": {"action_bias": "SELL", "control_mode": "bounce_reversal"}},
            "proposed_downtrend_shape": {"computed_sell_step": 129.71464, "computed_buy_step": 389.14393, "alpha": 0.3},
        }
        btc_reconciliation = {
            "summary": {"status": "needs_reconcile", "config_name": "shadow_btcusd_m15_sell_tight_v1"}
        }
        eth_control_gate = {
            "summary": {
                "verdict": "blocked_by_negative_expectancy",
                "realized_closes": 12,
                "realized_net_usd": -176.28,
                "avg_per_close": -14.69,
                "comparison_status": "ready_for_clean_control_vs_variant",
            }
        }
        next_action_board = {
            "rows": [
                {"action": "treat_nas100_m15_breakout_buy_as_the_only_clean_research_only_shadow_candidate", "machine_truth": {"launch_verdict": "research_only", "guardrail_status": "promotable_now"}},
                {
                    "action": "continue_btc_m15_sell_tight_v2_forward_proof_and_watch_reset_behavior",
                    "machine_truth": {
                        "btc_launch_verdict": "research_only",
                        "btc_forward_proof_started": True,
                        "btc_realized_closes": 9,
                        "btc_realized_net_usd": -163.73,
                        "btc_anchor_resets": 14,
                        "btc_reset_rate_per_hour": 12.1711,
                        "btc_harvest_closes": 0,
                        "btc_escape_tier2_surgical_closes": 9,
                        "btc_close_mix_status": "zero_harvest_all_escape_so_far",
                    },
                },
            ]
        }
        bucket_split_summary = {
            "close_ticket": 153.71,
            "escape_tier0_offensive": -2074.07,
            "forced_unwind": -572.37,
        }
        btc_config = {
            "hungry_hippo_metadata": {"validation_status": "shadow_config_reconciled_2026_04_15"},
        }

        payload = board.build_payload(
            controller_priors,
            salvage_board,
            promotion_queue,
            btc_handoff,
            btc_reconciliation,
            eth_control_gate,
            next_action_board,
            bucket_split_summary,
            btc_config,
        )

        self.assertEqual(payload["rows"][0]["theory"], "fx_alpha_half_universal_prior")
        self.assertEqual(payload["rows"][1]["stage"], "tested_theory_waiting_for_positive_control_proof")
        self.assertEqual(payload["rows"][2]["stage"], "shadow_config_reconciled_waiting_forward_proof")
        self.assertIn("paired forward closes", payload["rows"][0]["next_move"])
        self.assertIn("single proof lane", payload["rows"][1]["next_move"])
        self.assertIn("aligned control lane", payload["rows"][1]["evidence"])
        self.assertEqual(payload["rows"][2]["machine_truth"]["btc_close_mix_status"], "zero_harvest_all_escape_so_far")
        self.assertEqual(payload["rows"][2]["machine_truth"]["btc_realized_closes"], 9)
        self.assertAlmostEqual(payload["rows"][2]["machine_truth"]["btc_realized_net_usd"], -163.73, places=2)
        self.assertIn("every realized close so far is still escape-only", payload["rows"][2]["evidence"])
        self.assertIn("blocked by negative control proof", payload["leadership_read"][1])
        self.assertEqual(payload["rows"][-1]["stage"], "simulation_required")

    def test_render_markdown_mentions_new_stage_names(self) -> None:
        payload = {
            "generated_at": "2026-04-15T01:00:00+00:00",
            "leadership_read": ["one"],
            "summary": {
                "theory_count": 1,
                "stage_counts": {"tested_theory_waiting_for_positive_control_proof": 1},
                "top_ready_rows": ["eth_m5_no_session_gate_harvest_rebuild"],
            },
            "rows": [
                {
                    "priority": 1,
                    "theory": "eth_m5_no_session_gate_harvest_rebuild",
                    "stage": "tested_theory_waiting_for_positive_control_proof",
                    "goal": "recover",
                    "evidence": "positive",
                    "machine_truth": {"control_avg_per_close": 1.77},
                    "next_move": "restore control",
                    "why_now": "best lane",
                }
            ],
        }

        markdown = board.render_markdown(payload)
        self.assertIn("Profit Theory Graduation Board", markdown)
        self.assertIn("tested_theory_waiting_for_positive_control_proof", markdown)
        self.assertIn("eth_m5_no_session_gate_harvest_rebuild", markdown)

    def test_parse_bucket_split_summary_reads_current_numbers(self) -> None:
        text = (
            "The GBPUSD HH bucket breakdown reveals that **core harvest (close_ticket) is profitable** "
            "(+$153.71) but **escape_tier0_offensive (-$2,074.07) and forced_unwind (-$572.37) destroy all profits and more**."
        )

        parsed = board.parse_bucket_split_summary(text)

        self.assertEqual(parsed["close_ticket"], 153.71)
        self.assertEqual(parsed["escape_tier0_offensive"], -2074.07)
        self.assertEqual(parsed["forced_unwind"], -572.37)


if __name__ == "__main__":
    unittest.main()
