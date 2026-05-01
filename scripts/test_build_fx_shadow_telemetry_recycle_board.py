from __future__ import annotations

import unittest
from datetime import datetime, timezone

import scripts.build_fx_shadow_telemetry_recycle_board as board


class BuildFxShadowTelemetryRecycleBoardTests(unittest.TestCase):
    def test_build_payload_ranks_low_inventory_hot_shadow_first(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 4, 20, tzinfo=timezone.utc),
            fx_visibility_payload={
                "readiness": "fx_pre_patch_runner_windows",
                "rows": [
                    {
                        "lane": "shadow_xagusd_m15_warp",
                        "kind": "shadow_fx",
                        "status": "pre_patch_runner_window",
                        "restart_posture": "shadow_restart_resets_path_state",
                        "open_inventory_count": 1,
                        "trade_event_count": 45,
                        "latest_trade_event_ts_utc": "2026-04-16T03:32:09+00:00",
                    },
                    {
                        "lane": "shadow_gbpusd_m15_asym",
                        "kind": "shadow_fx",
                        "status": "pre_patch_runner_window",
                        "restart_posture": "shadow_restart_resets_path_state",
                        "open_inventory_count": 17,
                        "trade_event_count": 1189,
                        "latest_trade_event_ts_utc": "2026-04-16T04:02:42+00:00",
                    },
                    {
                        "lane": "live_rearm_941777",
                        "kind": "live_fx",
                        "status": "pre_patch_runner_window",
                        "restart_posture": "live_open_inventory_rehydratable",
                        "open_inventory_count": 4,
                        "trade_event_count": 97,
                        "latest_trade_event_ts_utc": "2026-04-16T03:54:42+00:00",
                    },
                ],
            },
            registry_payload={
                "lanes": [
                    {
                        "name": "shadow_xagusd_m15_warp",
                        "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py", "--symbol", "XAGUSD"],
                    },
                    {
                        "name": "shadow_gbpusd_m15_asym",
                        "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py", "--symbol", "GBPUSD"],
                    },
                ]
            },
            watchdog_payload={"groups": {"fx_watchdog": {"lanes": ["shadow_xagusd_m15_warp", "shadow_gbpusd_m15_asym"]}}},
        )

        self.assertEqual(payload["readiness"], "shadow_recycle_queue_ready")
        self.assertEqual(payload["summary"]["shadow_lane_count"], 2)
        self.assertEqual(payload["summary"]["top_recycle_candidate"], "shadow_xagusd_m15_warp")
        self.assertEqual(payload["rows"][0]["candidate_verdict"], "recycle_first_wave")
        self.assertEqual(payload["rows"][1]["candidate_verdict"], "preserve_continuity_first")
        self.assertFalse(payload["rows"][0]["has_fresh_start"])

    def test_build_payload_marks_post_patch_wait_as_non_candidate(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 4, 20, tzinfo=timezone.utc),
            fx_visibility_payload={
                "readiness": "fx_waiting_first_post_patch_trade_event",
                "rows": [
                    {
                        "lane": "shadow_usdjpy_m15_warp",
                        "kind": "shadow_fx",
                        "status": "awaiting_first_post_patch_trade_event",
                        "restart_posture": "shadow_restart_resets_path_state",
                        "open_inventory_count": 2,
                        "trade_event_count": 18,
                        "latest_trade_event_ts_utc": "2026-04-16T03:41:39+00:00",
                    }
                ],
            },
            registry_payload={
                "lanes": [
                    {
                        "name": "shadow_usdjpy_m15_warp",
                        "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py", "--symbol", "USDJPY"],
                    }
                ]
            },
        )

        self.assertEqual(payload["readiness"], "no_shadow_recycle_leverage")
        self.assertEqual(payload["rows"][0]["candidate_verdict"], "already_post_patch_wait")

    def test_build_payload_blocks_fresh_start_candidate(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 4, 20, tzinfo=timezone.utc),
            fx_visibility_payload={
                "readiness": "fx_pre_patch_runner_windows",
                "rows": [
                    {
                        "lane": "shadow_usdjpy_m15_warp",
                        "kind": "shadow_fx",
                        "status": "pre_patch_runner_window",
                        "restart_posture": "shadow_restart_resets_path_state",
                        "open_inventory_count": 3,
                        "trade_event_count": 18,
                        "latest_trade_event_ts_utc": "2026-04-16T03:41:39+00:00",
                    }
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
                            "--fresh-start",
                        ],
                    }
                ]
            },
            watchdog_payload={"groups": {"fx_watchdog": {"lanes": ["shadow_usdjpy_m15_warp"]}}},
        )

        self.assertEqual(payload["readiness"], "shadow_recycle_blocked_by_contract")
        self.assertEqual(payload["summary"]["blocked_fresh_start_contract_count"], 1)
        self.assertEqual(payload["summary"]["recycle_candidate_count"], 0)
        self.assertEqual(payload["rows"][0]["candidate_verdict"], "blocked_fresh_start_contract")
        self.assertTrue(payload["rows"][0]["has_fresh_start"])

    def test_render_markdown_mentions_recycle_verdicts(self) -> None:
        text = board.render_markdown(
            {
                "generated_at": "2026-04-16T04:20:00+00:00",
                "source_board": "reports/fx_phase1_telemetry_visibility_board.json",
                "source_readiness": "fx_pre_patch_runner_windows",
                "readiness": "shadow_recycle_queue_ready",
                "next_action": "Recycle one lane.",
                "summary": {
                    "shadow_lane_count": 1,
                    "recycle_candidate_count": 1,
                    "recycle_first_wave_count": 1,
                    "recycle_second_wave_count": 0,
                    "preserve_continuity_first_count": 0,
                    "blocked_fresh_start_contract_count": 0,
                    "already_post_patch_or_visible_count": 0,
                    "top_recycle_candidate": "shadow_xagusd_m15_warp",
                },
                "rows": [
                    {
                        "lane": "shadow_xagusd_m15_warp",
                        "candidate_verdict": "recycle_first_wave",
                        "has_fresh_start": False,
                        "open_inventory_count": 1,
                        "activity_bucket": "hot",
                        "hours_since_latest_trade": 0.8,
                        "trade_event_count": 45,
                        "status": "pre_patch_runner_window",
                        "rationale": "Low open inventory plus recent trade activity makes this the cheapest shadow continuity sacrifice for fresh telemetry evidence.",
                    }
                ],
            }
        )

        self.assertIn("FX Shadow Telemetry Recycle Board", text)
        self.assertIn("recycle_first_wave", text)
        self.assertIn("preserve_continuity_first", text)
        self.assertIn("blocked_fresh_start_contract", text)


if __name__ == "__main__":
    unittest.main()
