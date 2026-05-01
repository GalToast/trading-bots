from __future__ import annotations

import unittest
from datetime import datetime, timezone

import scripts.build_fx_shadow_telemetry_contract_debt_board as board


class BuildFxShadowTelemetryContractDebtBoardTests(unittest.TestCase):
    def test_build_payload_surfaces_unlockable_first_wave_rows(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 4, 45, tzinfo=timezone.utc),
            queue_payload={
                "readiness": "shadow_recycle_queue_ready",
                "summary": {
                    "recycle_first_wave_count": 3,
                },
                "rows": [
                    {
                        "lane": "shadow_usdjpy_m15_warp",
                        "candidate_verdict": "blocked_fresh_start_contract",
                        "status": "pre_patch_runner_window",
                        "restart_posture": "shadow_restart_resets_path_state",
                        "activity_bucket": "hot",
                        "open_inventory_count": 3,
                        "trade_event_count": 18,
                        "hours_since_latest_trade": 0.99,
                        "state_path": "reports/penetration_lattice_shadow_usdjpy_m15_warp_state.json",
                        "event_path": "reports/penetration_lattice_shadow_usdjpy_m15_warp_events.jsonl",
                        "rationale": "blocked by fresh-start contract",
                    },
                    {
                        "lane": "shadow_xagusd_m15_warp",
                        "candidate_verdict": "recycle_first_wave",
                    },
                ],
            },
            registry_payload={
                "lanes": [
                    {
                        "name": "shadow_usdjpy_m15_warp",
                        "restart_args": [
                            "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                            "--symbol",
                            "USDJPY",
                            "--timeframe",
                            "M15",
                            "--step",
                            "0.08",
                            "--raw-close-alpha",
                            "1.0",
                            "--fresh-start",
                        ],
                    }
                ]
            },
        )

        self.assertEqual(payload["readiness"], "contract_debt_actionable")
        self.assertEqual(payload["summary"]["blocked_lane_count"], 1)
        self.assertEqual(payload["summary"]["unlockable_first_wave_count"], 1)
        self.assertEqual(payload["summary"]["current_safe_first_wave_count"], 3)
        self.assertEqual(payload["summary"]["projected_safe_first_wave_count"], 4)
        self.assertEqual(payload["summary"]["top_unlock_candidate"], "shadow_usdjpy_m15_warp")
        self.assertEqual(payload["rows"][0]["projected_verdict_without_fresh_start"], "recycle_first_wave")
        self.assertEqual(payload["rows"][0]["symbol"], "USDJPY")

    def test_build_payload_handles_clear_contract_debt(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 4, 45, tzinfo=timezone.utc),
            queue_payload={
                "readiness": "shadow_recycle_queue_ready",
                "summary": {"recycle_first_wave_count": 3},
                "rows": [
                    {
                        "lane": "shadow_xagusd_m15_warp",
                        "candidate_verdict": "recycle_first_wave",
                    }
                ],
            },
            registry_payload={"lanes": []},
        )

        self.assertEqual(payload["readiness"], "contract_debt_clear")
        self.assertEqual(payload["summary"]["blocked_lane_count"], 0)
        self.assertEqual(payload["rows"], [])

    def test_render_markdown_mentions_projected_queue_effect(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T04:45:00+00:00",
                "source_queue": "reports/fx_shadow_telemetry_recycle_board.json",
                "source_readiness": "shadow_recycle_queue_ready",
                "readiness": "contract_debt_actionable",
                "next_action": "fix contract debt",
                "summary": {
                    "blocked_lane_count": 2,
                    "unlockable_first_wave_count": 2,
                    "unlockable_second_wave_count": 0,
                    "current_safe_first_wave_count": 3,
                    "projected_safe_first_wave_count": 5,
                    "top_unlock_candidate": "shadow_usdjpy_m15_warp",
                },
                "rows": [
                    {
                        "lane": "shadow_usdjpy_m15_warp",
                        "symbol": "USDJPY",
                        "timeframe": "M15",
                        "step": "0.08",
                        "raw_close_alpha": "1.0",
                        "current_verdict": "blocked_fresh_start_contract",
                        "projected_verdict_without_fresh_start": "recycle_first_wave",
                        "open_inventory_count": 3,
                        "activity_bucket": "hot",
                        "hours_since_latest_trade": 0.99,
                        "trade_event_count": 18,
                        "state_path": "reports/penetration_lattice_shadow_usdjpy_m15_warp_state.json",
                        "event_path": "reports/penetration_lattice_shadow_usdjpy_m15_warp_events.jsonl",
                        "contract_change_required": "Remove `--fresh-start`.",
                        "current_block_reason": "blocked now",
                        "projected_rationale_without_fresh_start": "would become first-wave",
                    }
                ],
                "read_rules": ["rule one"],
            }
        )

        self.assertIn("FX Shadow Telemetry Contract Debt Board", markdown)
        self.assertIn("projected_safe_first_wave_count", markdown)
        self.assertIn("Contract Debt Detail", markdown)
        self.assertIn("recycle_first_wave", markdown)


if __name__ == "__main__":
    unittest.main()
