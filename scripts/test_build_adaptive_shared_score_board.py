from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_adaptive_shared_score_board as board


class BuildAdaptiveSharedScoreBoardTests(unittest.TestCase):
    def test_build_payload_scores_incumbent_and_adaptive_rows_honestly(self) -> None:
        payload = board.build_payload(
            incumbent_study={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "asset_class": "crypto",
                        "study_status": "blocked_runtime_or_launch_gap",
                        "incumbent_present": True,
                        "incumbent_lane": "live_btc",
                        "incumbent_evidence_basis": "carry_weighted_live",
                        "incumbent_booked_usd": 1200.0,
                        "incumbent_close_count": 200,
                        "incumbent_seat_verdict": "contested_provisional_live_seat",
                        "adaptive_present": True,
                        "adaptive_lane": "shadow_btc",
                        "adaptive_candidate_verdict": "shadow_ready",
                        "adaptive_runtime_status": "hold_runtime_repair_candidate",
                        "why": "btc why",
                    },
                    {
                        "symbol": "GBPUSD",
                        "asset_class": "fx",
                        "study_status": "adaptive_shape_defined_packet_missing",
                        "incumbent_present": True,
                        "incumbent_lane": "live_gbp",
                        "incumbent_evidence_basis": "graduated_live_reference",
                        "incumbent_booked_usd": 100.0,
                        "incumbent_close_count": 10,
                        "incumbent_seat_verdict": "defended_but_contested_live_seat",
                        "adaptive_present": True,
                        "adaptive_lane": "shadow_gbp",
                        "adaptive_candidate_verdict": "shadow_ready",
                        "adaptive_runtime_status": "already_running_monitor_only",
                        "why": "gbp why",
                    },
                ]
            },
            overnight_packet={
                "rows": [
                    {
                        "lane_name": "shadow_btc",
                        "first_path_verdict": "never_green_toxic_continuation",
                        "first_path_close_realized_pnl": -17.56,
                        "artifact_trade_closes": 13,
                    },
                    {
                        "lane_name": "shadow_gbp",
                        "first_path_verdict": "",
                        "first_path_close_realized_pnl": None,
                        "artifact_trade_closes": 0,
                    },
                ]
            },
            booked_breakdown={
                "shadow_lattice": {
                    "rows": [
                        {
                            "lane": "shadow_btc",
                            "booked_usd": 25.0,
                            "runner_session_booked_usd": 0.0,
                            "clean_forward_delta_usd": 0.0,
                            "close_count": 13,
                            "notes": "-",
                        },
                        {
                            "lane": "shadow_gbp",
                            "booked_usd": 93.13,
                            "runner_session_booked_usd": 8.49,
                            "clean_forward_delta_usd": 0.0,
                            "close_count": 601,
                            "notes": "-",
                        },
                    ]
                }
            },
            organism_state={
                "live_lanes": [
                    {
                        "lane": "live_btc",
                        "realized_usd": "1248.65",
                        "closes": "277",
                        "floating_usd": "-33.34",
                        "notes": "pre_start_state_carry=277c/+1248.65",
                    },
                    {
                        "lane": "live_gbp",
                        "realized_usd": "724.72",
                        "closes": "326",
                        "floating_usd": "-0.15",
                        "notes": "runner_session_since_start=6c/+0.64 7o",
                    },
                ]
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["study_comparable_symbols"], ["BTCUSD", "GBPUSD"])
        self.assertEqual(summary["scored_symbols"], ["BTCUSD", "GBPUSD"])
        self.assertEqual(summary["shared_score_ready_symbols"], ["BTCUSD", "GBPUSD"])
        self.assertEqual(summary["adaptive_leading_symbols"], [])
        self.assertEqual(summary["incumbent_leading_symbols"], ["BTCUSD", "GBPUSD"])

        indexed = {row["symbol"]: row for row in payload["rows"]}
        self.assertEqual(indexed["BTCUSD"]["adaptive"]["basis"], "first_path_close_realized_pnl")
        self.assertEqual(indexed["BTCUSD"]["adaptive"]["components"]["toxicity"], -2)
        self.assertEqual(indexed["BTCUSD"]["comparison_verdict"], "incumbent_still_leading")
        self.assertEqual(indexed["GBPUSD"]["adaptive"]["basis"], "runner_session_booked_usd")
        self.assertEqual(indexed["GBPUSD"]["adaptive"]["components"]["readiness"], 3)
        self.assertEqual(indexed["GBPUSD"]["comparison_verdict"], "incumbent_still_leading")

    def test_render_markdown_mentions_score_contract_and_verdicts(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "study_comparable_symbols": ["BTCUSD"],
                    "scored_symbols": ["BTCUSD"],
                    "shared_score_ready_symbols": ["BTCUSD"],
                    "adaptive_leading_symbols": [],
                    "incumbent_leading_symbols": ["BTCUSD"],
                    "low_confidence_symbols": [],
                    "missing_adaptive_score_symbols": [],
                },
                "score_contract": {"profit_component": "p", "toxicity_penalty": "t"},
                "leadership_read": ["one"],
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "study_status": "blocked_runtime_or_launch_gap",
                        "comparison_verdict": "incumbent_still_leading",
                        "score_gap": -5,
                        "why": "because",
                        "incumbent": {
                            "lane": "live_btc",
                            "basis": "exact_live_realized",
                            "realized_usd": 100.0,
                            "usd_per_close": 2.0,
                            "score_total": 5,
                            "components": {"profit": 3},
                        },
                        "adaptive": {
                            "lane": "shadow_btc",
                            "basis": "first_path_close_realized_pnl",
                            "realized_usd": -10.0,
                            "usd_per_close": -10.0,
                            "first_path_verdict": "never_green_toxic_continuation",
                            "score_total": 0,
                            "components": {"toxicity": -2},
                        },
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Adaptive Shared Score Board", markdown)
        self.assertIn("Score Contract", markdown)
        self.assertIn("incumbent_still_leading", markdown)
        self.assertIn("toxicity_penalty", markdown)


if __name__ == "__main__":
    unittest.main()
