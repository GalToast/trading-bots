from __future__ import annotations

import unittest
from datetime import datetime, timezone

import scripts.build_fx_shadow_telemetry_recycle_packet_board as board


class BuildFxShadowTelemetryRecyclePacketBoardTests(unittest.TestCase):
    def test_build_payload_groups_safe_and_blocked_candidates(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 4, 40, tzinfo=timezone.utc),
            queue_payload={
                "readiness": "shadow_recycle_queue_ready",
                "rows": [
                    {
                        "lane": "shadow_xagusd_m15_warp",
                        "candidate_verdict": "recycle_first_wave",
                        "watchdog_groups": ["fx_watchdog"],
                        "state_path": "reports/penetration_lattice_shadow_xagusd_m15_warp_state.json",
                        "event_path": "reports/penetration_lattice_shadow_xagusd_m15_warp_events.jsonl",
                        "has_fresh_start": False,
                        "open_inventory_count": 1,
                    },
                    {
                        "lane": "shadow_usdjpy_m15_warp",
                        "candidate_verdict": "blocked_fresh_start_contract",
                        "watchdog_groups": ["fx_watchdog"],
                        "state_path": "reports/penetration_lattice_shadow_usdjpy_m15_warp_state.json",
                        "event_path": "reports/penetration_lattice_shadow_usdjpy_m15_warp_events.jsonl",
                        "has_fresh_start": True,
                        "open_inventory_count": 3,
                    },
                ],
            },
            registry_payload={
                "lanes": [
                    {
                        "name": "shadow_xagusd_m15_warp",
                        "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py", "--symbol", "XAGUSD", "--timeframe", "M15", "--step", "0.151", "--raw-close-alpha", "0.5"],
                    },
                    {
                        "name": "shadow_usdjpy_m15_warp",
                        "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py", "--symbol", "USDJPY", "--timeframe", "M15", "--step", "0.08", "--raw-close-alpha", "1.0", "--fresh-start"],
                    },
                ]
            },
        )

        self.assertEqual(payload["readiness"], "packet_ready_first_wave")
        self.assertEqual(payload["summary"]["safe_first_wave_count"], 1)
        self.assertEqual(payload["summary"]["blocked_fresh_start_contract_count"], 1)
        self.assertEqual(payload["summary"]["top_safe_candidate"], "shadow_xagusd_m15_warp")
        self.assertEqual(payload["safe_first_wave"][0]["symbol"], "XAGUSD")
        self.assertEqual(payload["blocked_fresh_start_contract"][0]["symbol"], "USDJPY")

    def test_render_markdown_mentions_no_go_rules(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T04:40:00+00:00",
                "source_queue": "reports/fx_shadow_telemetry_recycle_board.json",
                "source_readiness": "shadow_recycle_queue_ready",
                "readiness": "packet_ready_first_wave",
                "next_action": "Use the first packet.",
                "summary": {
                    "safe_first_wave_count": 1,
                    "safe_second_wave_count": 0,
                    "blocked_fresh_start_contract_count": 1,
                    "preserve_continuity_first_count": 0,
                    "top_safe_candidate": "shadow_xagusd_m15_warp",
                },
                "safe_first_wave": [
                    {
                        "lane": "shadow_xagusd_m15_warp",
                        "symbol": "XAGUSD",
                        "timeframe": "M15",
                        "step": "0.151",
                        "raw_close_alpha": "0.5",
                        "has_fresh_start": False,
                        "open_inventory_count": 1,
                        "state_path": "reports/penetration_lattice_shadow_xagusd_m15_warp_state.json",
                        "event_path": "reports/penetration_lattice_shadow_xagusd_m15_warp_events.jsonl",
                    }
                ],
                "safe_second_wave": [],
                "blocked_fresh_start_contract": [],
                "preserve_continuity_first": [],
                "watch_steps": ["step one"],
                "no_go_rules": ["Do not restart live FX lanes from this packet."],
            }
        )

        self.assertIn("FX Shadow Telemetry Recycle Packet Board", markdown)
        self.assertIn("Safe First Wave", markdown)
        self.assertIn("No-Go Rules", markdown)
        self.assertIn("Do not restart live FX lanes", markdown)


if __name__ == "__main__":
    unittest.main()
