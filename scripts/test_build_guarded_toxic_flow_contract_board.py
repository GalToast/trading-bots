from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_guarded_toxic_flow_contract_board as board


class BuildGuardedToxicFlowContractBoardTests(unittest.TestCase):
    def test_build_payload_promotes_cluster_escape_and_demotes_spread_gate(self) -> None:
        payload = board.build_payload(
            incumbent_study={
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "adaptive_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                        "adaptive_lane": "shadow_btcusd_m15_warp_restore_v1",
                        "study_status": "study_ready",
                        "adaptive_profit_mode": "guarded_toxic_flow",
                        "adaptive_profit_mode_read": "guard flow",
                        "adaptive_objective_read": "objective read",
                    },
                    {
                        "symbol": "ETHUSD",
                        "adaptive_profit_mode": "trend_harvest",
                    },
                ]
            },
            burst_board={
                "summary": {
                    "prevent_with_2x_step": 0,
                    "prevent_with_3x_step": 0,
                    "prevent_with_5x_step": 0,
                },
                "lanes": [
                    {
                        "lane": "shadow_btcusd_m15_warp_restore_v1",
                        "burst_expansion_opens": 33,
                        "burst_expansion_escapes": 33,
                        "burst_expansion_pnl": -579.59,
                        "burst_escape_rate": 1.0,
                        "non_burst_escape_rate": 1.0,
                    }
                ],
            },
            spread_board={
                "symbols": [
                    {
                        "symbol": "BTCUSD",
                        "median_spread": 175.58,
                        "median_escape_spread": 175.58,
                        "escapes_above_2x": 0,
                    }
                ]
            },
            prevention_escape={
                "total_cluster_savable": -181.18,
                "total_prevention_savable": 0.0,
                "lanes": [
                    {
                        "lane": "penetration_lattice_shadow_ethusd_m5_structure_shapeshifter_events.jsonl",
                        "cluster_escape_pnl": -171.64,
                    }
                ],
            },
        )

        summary = payload["summary"]
        self.assertEqual(summary["guarded_symbols"], ["BTCUSD"])
        self.assertEqual(summary["spread_gate_verdict"], "demoted")
        self.assertEqual(summary["cluster_escape_verdict"], "promoted")
        self.assertEqual(summary["step_widening_verdict"], "unproven")

        row = payload["rows"][0]
        self.assertEqual(row["spread_evidence"]["verdict"], "demoted_as_primary_guard")
        self.assertEqual(row["escape_evidence"]["verdict"], "promote_cluster_escape")
        self.assertEqual(row["step_evidence"]["verdict"], "unproven_from_checked_in_board")
        self.assertEqual(row["runtime_evidence"]["verdict"], "guard_not_requested")
        self.assertEqual(row["contract"]["verdict"], "cluster_escape_primary_spread_demoted")
        self.assertEqual(row["contract"]["spread_gate_role"], "secondary_only")
        self.assertEqual(row["contract"]["escape_role"], "cluster_aware_escape_when_burst_clusters_form")

    def test_build_payload_surfaces_guarded_open_runtime_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir)
            state_path = reports_dir / "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json"
            event_path = reports_dir / "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_events.jsonl"
            state_path.write_text(
                '{"metadata":{"guard_open_admission":true},"symbols":{"BTCUSD":{"guard_open_admission":true}}}',
                encoding="utf-8",
            )
            event_path.write_text(
                "\n".join(
                    [
                        '{"ts_utc":"2026-04-16T10:00:00+00:00","action":"open_ticket"}',
                        '{"ts_utc":"2026-04-16T10:01:00+00:00","action":"open_guarded_admission","stage":"main_open","direction":"SELL"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(board, "REPORTS", reports_dir):
                payload = board.build_payload(
                    incumbent_study={
                        "rows": [
                            {
                                "symbol": "BTCUSD",
                                "adaptive_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                                "adaptive_lane": "shadow_btcusd_m15_warp_restore_v1",
                                "study_status": "study_ready",
                                "adaptive_profit_mode": "guarded_toxic_flow",
                                "adaptive_profit_mode_read": "guard flow",
                                "adaptive_objective_read": "objective read",
                                "adaptive_runtime_overlays": [
                                    "guard_open_admission",
                                    "cluster_aware_escape",
                                ],
                            }
                        ]
                    },
                    burst_board={"summary": {}, "lanes": []},
                    spread_board={"symbols": []},
                    prevention_escape={"total_cluster_savable": 0.0, "total_prevention_savable": 0.0, "lanes": []},
                )

        row = payload["rows"][0]
        self.assertEqual(row["runtime_evidence"]["verdict"], "guarded_open_observed")
        self.assertTrue(row["runtime_evidence"]["guard_open_admission_enabled"])
        self.assertEqual(row["runtime_evidence"]["guarded_admission_event_count"], 1)
        self.assertEqual(row["runtime_evidence"]["latest_guarded_stage"], "main_open")
        self.assertEqual(payload["summary"]["guard_runtime_observed_count"], 1)

    def test_render_markdown_mentions_contract_roles(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00Z",
                "summary": {
                    "guarded_symbols": ["BTCUSD"],
                    "spread_gate_verdict": "demoted",
                    "cluster_escape_verdict": "promoted",
                    "step_widening_verdict": "unproven",
                    "guard_runtime_observed_count": 1,
                    "guard_runtime_enabled_waiting_count": 0,
                    "guard_runtime_blind_count": 0,
                    "guard_runtime_explicitly_disabled_count": 0,
                    "contract_read": "contract summary",
                    "runtime_read": "runtime summary",
                },
                "leadership_read": ["one"],
                "rows": [
                    {
                        "symbol": "BTCUSD",
                        "adaptive_shape_id": "btcusd_rangeatr_cash_harvest_v1",
                        "adaptive_lane": "shadow_btcusd_m15_warp_restore_v1",
                        "study_status": "study_ready",
                        "adaptive_profit_mode_read": "guard flow",
                        "adaptive_objective_read": "objective read",
                        "spread_evidence": {"verdict": "demoted_as_primary_guard", "read": "spread read"},
                        "burst_evidence": {"verdict": "regime_guard_required", "read": "burst read"},
                        "escape_evidence": {"verdict": "promote_cluster_escape", "read": "escape read"},
                        "step_evidence": {"verdict": "unproven_from_checked_in_board", "read": "step read"},
                        "runtime_evidence": {
                            "verdict": "guarded_open_observed",
                            "read": "runtime read",
                            "state_path": "reports/penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json",
                            "event_path": "reports/penetration_lattice_shadow_btcusd_m15_warp_restore_v1_events.jsonl",
                        },
                        "contract": {
                            "verdict": "cluster_escape_primary_spread_demoted",
                            "primary_entry_guard": "same_bar_open_burst_count_at_open + regime_at_entry",
                            "spread_gate_role": "secondary_only",
                            "escape_role": "cluster_aware_escape_when_burst_clusters_form",
                            "step_widening_role": "secondary_hypothesis_until_checked_in_support",
                            "read": "contract read",
                        },
                    }
                ],
                "notes": ["note"],
            }
        )

        self.assertIn("Guarded Toxic Flow Contract Board", markdown)
        self.assertIn("cluster_escape_primary_spread_demoted", markdown)
        self.assertIn("secondary_only", markdown)
        self.assertIn("cluster_aware_escape_when_burst_clusters_form", markdown)
        self.assertIn("guarded_open_observed", markdown)
        self.assertIn("runtime summary", markdown)


if __name__ == "__main__":
    unittest.main()
