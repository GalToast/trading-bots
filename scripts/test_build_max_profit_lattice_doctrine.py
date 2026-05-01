from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_max_profit_lattice_doctrine as doctrine


class BuildMaxProfitLatticeDoctrineTests(unittest.TestCase):
    def test_build_payload_synthesizes_current_authority_surfaces(self) -> None:
        payload = doctrine.build_payload(
            perfection={
                "summary": {
                    "total_score": 9,
                    "max_score": 14,
                    "overall_verdict": "instrumented_but_not_yet_perfect",
                    "highest_priority_ready_task_id": "btc_restore_comparison_shadow",
                    "highest_priority_ready_title": "Launch the BTC restore comparison shadow",
                }
            },
            guarded_contract={
                "summary": {
                    "guarded_symbols": ["BTCUSD"],
                    "spread_gate_verdict": "demoted",
                    "cluster_escape_verdict": "promoted",
                    "step_widening_verdict": "unproven",
                    "contract_read": "Burst context first, spread second.",
                },
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "contract": {
                            "primary_entry_guard": "same_bar_open_burst_count_at_open + regime_at_entry",
                            "escape_role": "cluster_aware_escape_when_burst_clusters_form",
                        }
                    }
                ],
            },
            next_action_board={
                "summary": {
                    "launch_now_symbols": ["GBPUSD", "USDJPY"],
                    "preparatory_symbols": ["BTCUSD"],
                    "queue_contract_missing_symbols": ["USDCAD", "NZDUSD"],
                },
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "max_profit_posture": "preparatory_only",
                    },
                    {
                        "symbol": "GBPUSD",
                        "max_profit_posture": "launch_now",
                        "queue_task_id": "gbpusd_adaptive_comparison_packet",
                        "seat_actionability_status": "queue_ready_actionable",
                    },
                ],
            },
            contract_gap_board={
                "summary": {
                    "highest_contract_gap_symbol": "USDCAD",
                    "contract_gap_symbols": ["USDCAD", "NZDUSD"],
                },
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "proposed_queue_task_id": "usdcad_first_live_seat_contract",
                        "proposed_queue_lane": "shadow HH",
                    }
                ],
            },
            queue_packet_board={
                "summary": {
                    "proposal_symbols": ["USDCAD", "NZDUSD"],
                    "highest_ready_symbol": "USDCAD",
                },
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "proposal_status": "proposal_ready",
                        "task_id": "usdcad_first_live_seat_contract",
                    }
                ],
            },
            queue_adoption_board={
                "summary": {
                    "adopted_count": 0,
                    "missing_count": 2,
                    "highest_missing_symbol": "USDCAD",
                },
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "queue_adoption_status": "proposal_missing_from_queue",
                        "related_symbol_queue_task_ids": [],
                    }
                ],
            },
            queue_promotion_board={
                "summary": {
                    "highest_promotion_symbol": "USDCAD",
                    "promotion_symbols": ["USDCAD", "NZDUSD"],
                },
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "promotion_class": "promote_to_queue_now",
                        "task_id": "usdcad_first_live_seat_contract",
                    }
                ],
            },
            runner_plan={
                "runtime_overlay_contract": {
                    "supported_overlays": [
                        "guard_open_admission",
                        "cluster_aware_escape",
                        "suppress_additional_levels_after_burst",
                    ],
                    "requested_overlays": [],
                    "executable_overlays": [],
                    "unsupported_overlays": [],
                }
            },
            incumbent_study={
                "summary": {
                    "study_ready_symbols": ["BTCUSD"],
                    "blocked_symbols": ["GBPUSD"],
                    "research_only_symbols": ["EURUSD"],
                    "family_coverage": {
                        "crypto": "ready_candidate_present",
                        "fx": "blocked_candidate_present",
                        "commodity": "missing",
                    },
                    "btc_max_profit_contract": {
                        "verdict": "adaptive_candidate_defined_but_unproven",
                    },
                },
                "family_coverage": [
                    {"family": "fx", "verdict": "blocked_candidate_present", "read": "FX has a blocked candidate."},
                    {"family": "commodity", "verdict": "missing", "read": "Commodity family is missing."},
                ],
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "asset_class": "crypto",
                        "study_status": "study_ready",
                        "adaptive_stage": "shadow_ready",
                        "adaptive_profit_mode": "guarded_toxic_flow",
                        "btc_max_profit_comparison": {
                            "verdict": "adaptive_candidate_defined_but_unproven",
                        },
                    },
                    {
                        "symbol": "GBPUSD",
                        "asset_class": "fx",
                        "study_status": "blocked_runtime_or_launch_gap",
                        "adaptive_stage": "shadow_ready",
                        "adaptive_profit_mode": "trend_harvest",
                    },
                ],
            },
            seat_board={
                "summary": {
                    "highest_priority_seat_symbol": "BTCUSD",
                    "highest_actionable_seat_symbol": "GBPUSD",
                    "highest_actionable_queue_backed_symbol": "GBPUSD",
                    "actionable_unqueued_symbols": ["USDCAD", "NZDUSD"],
                    "queue_precedes_seat_symbols": ["BTCUSD"],
                    "overlay_launch_gap_symbols": ["BTCUSD"],
                },
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "seat_unblocker_action": "controlled_displacement_review",
                        "seat_actionability_status": "queue_ready_preparatory_only",
                        "seat_contract_gap_status": "queue_backed_preparatory_only",
                        "seat_overlay_launch_bridge_status": "overlay_launch_bridge_supported_but_unrequested",
                        "seat_unblocker_priority_rank": 1,
                    },
                    {
                        "symbol": "GBPUSD",
                        "seat_unblocker_action": "complete_challenger_comparison",
                        "seat_actionability_status": "queue_ready_actionable",
                        "seat_contract_gap_status": "queue_backed_actionable",
                        "seat_unblocker_priority_rank": 4,
                        "seat_unblocker_queue_task_id": "gbpusd_adaptive_comparison_packet",
                    },
                ],
            },
            telemetry_visibility={
                "summary": {
                    "total_event_files": 204,
                    "fully_enriched": 13,
                    "partially_enriched": 4,
                    "no_enrichment_with_closes": 134,
                }
            },
            telemetry_priority={
                "summary": {
                    "high_priority_count": 10,
                    "medium_priority_count": 11,
                },
                "lanes": [
                    {
                        "lane_name": "shadow_gbpusd_tick_forward",
                        "total_closes": 7315,
                        "watchdog_status": "ok",
                    }
                ],
            },
            inherited_active={
                "summary": {
                    "total_active_realized_usd": -692.65,
                    "total_inherited_realized_usd": -16725.05,
                    "total_active_closes": 928,
                    "total_inherited_closes": 19306,
                    "lanes_active_only": 8,
                }
            },
            escape_pattern={
                "summary": {
                    "total_natural_profits": 11173,
                    "total_natural_losses": 609,
                    "total_escape_losses": 1579,
                    "total_escape_profits": 83,
                }
            },
        )

        self.assertEqual(payload["summary"]["perfection_score"], "9/14")
        self.assertEqual(payload["summary"]["btc_max_profit_verdict"], "adaptive_candidate_defined_but_unproven")
        self.assertEqual(payload["guarded_doctrine"]["primary_entry_guard"], "same_bar_open_burst_count_at_open + regime_at_entry")
        self.assertEqual(payload["seat_truth"]["highest_actionable_queue_backed_symbol"], "GBPUSD")
        self.assertEqual(payload["execution_truth"]["highest_contract_gap_symbol"], "USDCAD")
        self.assertEqual(payload["queue_truth"]["highest_promotion_symbol"], "USDCAD")
        self.assertEqual(payload["queue_truth"]["missing_adoption_count"], 2)
        self.assertEqual(payload["overlay_launch_truth"]["overlay_launch_gap_symbols"], ["BTCUSD"])
        self.assertEqual(
            payload["overlay_launch_truth"]["supported_overlays"],
            ["guard_open_admission", "cluster_aware_escape", "suppress_additional_levels_after_burst"],
        )
        self.assertEqual(payload["symbol_focus"][0]["symbol"], "BTCUSD")
        self.assertEqual(payload["symbol_focus"][0]["max_profit_posture"], "preparatory_only")
        self.assertEqual(payload["symbol_focus"][0]["btc_max_profit_verdict"], "adaptive_candidate_defined_but_unproven")
        self.assertTrue(any(item["source"] == "max_profit_contract_gap_board" for item in payload["next_actions"]))
        self.assertTrue(any(item["source"] == "max_profit_queue_promotion_board" for item in payload["next_actions"]))

    def test_render_markdown_mentions_doctrine_sections(self) -> None:
        markdown = doctrine.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "perfection_score": "9/14",
                    "overall_verdict": "instrumented_but_not_yet_perfect",
                    "btc_max_profit_verdict": "adaptive_candidate_defined_but_unproven",
                    "study_ready_symbols": ["BTCUSD"],
                    "guarded_symbols": ["BTCUSD"],
                },
                "leadership_read": ["one"],
                "core_truth": {
                    "natural_profit_count": 11173,
                    "natural_loss_count": 609,
                    "escape_loss_count": 1579,
                    "active_realized_usd": -692.65,
                    "inherited_realized_usd": -16725.05,
                    "read": "core read",
                },
                "guarded_doctrine": {
                    "primary_entry_guard": "same_bar_open_burst_count_at_open + regime_at_entry",
                    "spread_gate_verdict": "demoted",
                    "cluster_escape_verdict": "promoted",
                    "step_widening_verdict": "unproven",
                    "read": "guard read",
                },
                "telemetry_truth": {
                    "total_event_files": 204,
                    "fully_enriched": 13,
                    "partially_enriched": 4,
                    "no_enrichment_with_closes": 134,
                    "high_priority_count": 10,
                    "medium_priority_count": 11,
                    "read": "telemetry read",
                },
                "adaptive_truth": {
                    "score": 9,
                    "max_score": 14,
                    "overall_verdict": "instrumented_but_not_yet_perfect",
                    "btc_max_profit_verdict": "adaptive_candidate_defined_but_unproven",
                    "family_coverage": {"fx": "blocked_candidate_present"},
                    "read": "adaptive read",
                },
                "execution_truth": {
                    "launch_now_symbols": ["GBPUSD"],
                    "preparatory_symbols": ["BTCUSD"],
                    "queue_contract_missing_symbols": ["USDCAD"],
                    "highest_contract_gap_symbol": "USDCAD",
                    "read": "execution read",
                },
                "queue_truth": {
                    "packet_symbols": ["USDCAD"],
                    "highest_ready_symbol": "USDCAD",
                    "adopted_count": 0,
                    "missing_adoption_count": 1,
                    "highest_missing_adoption_symbol": "USDCAD",
                    "highest_promotion_symbol": "USDCAD",
                    "promotion_symbols": ["USDCAD"],
                    "read": "queue read",
                },
                "overlay_launch_truth": {
                    "overlay_launch_gap_symbols": ["BTCUSD"],
                    "supported_overlays": ["guard_open_admission", "cluster_aware_escape"],
                    "requested_overlays": [],
                    "executable_overlays": [],
                    "unsupported_overlays": [],
                    "read": "overlay read",
                },
                "seat_truth": {
                    "highest_priority_seat_symbol": "BTCUSD",
                    "highest_actionable_seat_symbol": "GBPUSD",
                    "highest_actionable_queue_backed_symbol": "GBPUSD",
                    "actionable_unqueued_symbols": ["USDCAD"],
                    "queue_precedes_seat_symbols": ["BTCUSD"],
                    "read": "seat read",
                },
                "symbol_focus": [
                    {
                        "symbol": "BTCUSD",
                        "family": "crypto",
                        "study_status": "study_ready",
                        "profit_mode": "guarded_toxic_flow",
                        "max_profit_posture": "preparatory_only",
                        "seat_action": "controlled_displacement_review",
                        "seat_actionability_status": "queue_ready_preparatory_only",
                        "seat_contract_gap_status": "queue_backed_preparatory_only",
                        "seat_overlay_launch_bridge_status": "overlay_launch_bridge_supported_but_unrequested",
                    }
                ],
                "next_actions": [
                    {
                        "source": "adaptive_lattice_perfection_scorecard_board",
                        "title": "Launch the BTC restore comparison shadow",
                        "read": "perfection read",
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Max-Profit Adaptive Lattice Doctrine", markdown)
        self.assertIn("same_bar_open_burst_count_at_open + regime_at_entry", markdown)
        self.assertIn("adaptive_candidate_defined_but_unproven", markdown)
        self.assertIn("## Execution Truth", markdown)
        self.assertIn("## Queue Truth", markdown)
        self.assertIn("## Overlay Launch Truth", markdown)
        self.assertIn("| `BTCUSD` | `crypto` | `study_ready` | `guarded_toxic_flow` | `preparatory_only` |", markdown)


if __name__ == "__main__":
    unittest.main()
