from __future__ import annotations

import unittest
from datetime import datetime, timezone

import scripts.build_fx_phase1_telemetry_visibility_board as board


class BuildFxPhase1TelemetryVisibilityBoardTests(unittest.TestCase):
    def test_build_payload_marks_pre_patch_runner_window(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 4, 0, tzinfo=timezone.utc),
            reference_code_mtime="2026-04-16T03:34:57+00:00",
            watchdog_payload={"groups": {"fx_watchdog": {"lanes": ["shadow_gbpusd_m15_warp"]}}},
            registry_payload={
                "lanes": [
                    {
                        "name": "shadow_gbpusd_m15_warp",
                        "kind": "shadow_fx",
                        "state_path": "reports/gbp_state.json",
                        "event_path": "reports/gbp_events.jsonl",
                    }
                ]
            },
            state_payloads={
                "shadow_gbpusd_m15_warp": {
                    "runner": {
                        "started_at": "2026-04-15T21:40:52+00:00",
                        "heartbeat_at": "2026-04-16T03:57:40+00:00",
                    }
                }
            },
            event_payloads={
                "shadow_gbpusd_m15_warp": [
                    {"action": "open_ticket", "ts_utc": "2026-04-16T03:05:39+00:00"},
                    {"action": "close_ticket", "ts_utc": "2026-04-16T03:10:00+00:00"},
                ]
            },
        )

        self.assertEqual(payload["readiness"], "fx_pre_patch_runner_windows")
        self.assertEqual(payload["rows"][0]["status"], "pre_patch_runner_window")

    def test_build_payload_marks_waiting_first_post_patch_trade_event(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 4, 0, tzinfo=timezone.utc),
            reference_code_mtime="2026-04-16T03:34:57+00:00",
            watchdog_payload={"groups": {"fx_watchdog": {"lanes": ["shadow_usdjpy_m15_warp"]}}},
            registry_payload={
                "lanes": [
                    {
                        "name": "shadow_usdjpy_m15_warp",
                        "kind": "shadow_fx",
                        "state_path": "reports/jpy_state.json",
                        "event_path": "reports/jpy_events.jsonl",
                    }
                ]
            },
            state_payloads={
                "shadow_usdjpy_m15_warp": {
                    "runner": {
                        "started_at": "2026-04-16T03:50:00+00:00",
                        "heartbeat_at": "2026-04-16T03:57:39+00:00",
                    }
                }
            },
            event_payloads={"shadow_usdjpy_m15_warp": [{"action": "tick_history_fallback", "ts_utc": "2026-04-16T03:56:09+00:00"}]},
        )

        self.assertEqual(payload["readiness"], "fx_waiting_first_post_patch_trade_event")
        self.assertEqual(payload["rows"][0]["status"], "awaiting_first_post_patch_trade_event")

    def test_build_payload_marks_phase1_visible(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 4, 0, tzinfo=timezone.utc),
            reference_code_mtime="2026-04-16T03:34:57+00:00",
            watchdog_payload={"groups": {"fx_watchdog": {"lanes": ["shadow_audusd_m15_warp"]}}},
            registry_payload={
                "lanes": [
                    {
                        "name": "shadow_audusd_m15_warp",
                        "kind": "shadow_fx",
                        "state_path": "reports/aud_state.json",
                        "event_path": "reports/aud_events.jsonl",
                    }
                ]
            },
            state_payloads={
                "shadow_audusd_m15_warp": {
                    "runner": {
                        "started_at": "2026-04-16T03:50:00+00:00",
                        "heartbeat_at": "2026-04-16T03:57:40+00:00",
                    }
                }
            },
            event_payloads={
                "shadow_audusd_m15_warp": [
                    {
                        "action": "open_ticket",
                        "ts_utc": "2026-04-16T03:58:00+00:00",
                        "spread_at_entry": 0.8,
                        "entry_context": "fresh|good_session|tight_spread",
                    }
                ]
            },
        )

        self.assertEqual(payload["readiness"], "fx_phase1_visible")
        self.assertEqual(payload["rows"][0]["status"], "phase1_visible")
        self.assertEqual(payload["rows"][0]["covered_field_count"], 2)

    def test_render_markdown_mentions_statuses(self) -> None:
        text = board.render_markdown(
            {
                "generated_at": "2026-04-16T04:00:00+00:00",
                "reference_code_path": "scripts/tick_penetration_lattice_core.py",
                "reference_code_mtime": "2026-04-16T03:34:57+00:00",
                "readiness": "fx_pre_patch_runner_windows",
                "next_action": "Wait.",
                "summary": {
                    "lane_count": 1,
                    "phase1_visible_count": 0,
                    "awaiting_first_post_patch_trade_event_count": 0,
                    "pre_patch_runner_window_count": 1,
                    "post_patch_runner_without_phase1_fields_count": 0,
                    "no_trade_events_seen_count": 0,
                    "flat_restart_candidate_count": 0,
                    "open_inventory_lane_count": 1,
                    "live_rehydratable_restart_count": 1,
                    "shadow_path_reset_restart_count": 0,
                },
                "rows": [
                    {
                        "lane": "shadow_gbpusd_m15_warp",
                        "status": "pre_patch_runner_window",
                        "open_inventory_count": 3,
                        "restart_posture": "shadow_restart_resets_path_state",
                        "trade_event_count": 2,
                        "covered_field_count": 0,
                        "field_count": 16,
                        "runner_started_at": "2026-04-15T21:40:52+00:00",
                        "latest_trade_event_ts_utc": "2026-04-16T03:10:00+00:00",
                        "rationale": "The latest reviewed FX trade events come from a runner window that started before the telemetry-bearing code.",
                    }
                ],
            }
        )

        self.assertIn("FX Phase 1 Telemetry Visibility Board", text)
        self.assertIn("pre_patch_runner_window", text)
        self.assertIn("awaiting_first_post_patch_trade_event", text)
        self.assertIn("live_open_inventory_rehydratable", board.render_markdown(
            {
                "generated_at": "2026-04-16T04:00:00+00:00",
                "reference_code_path": "scripts/tick_penetration_lattice_core.py",
                "reference_code_mtime": "2026-04-16T03:34:57+00:00",
                "readiness": "fx_pre_patch_runner_windows",
                "next_action": "Wait.",
                "summary": {
                    "lane_count": 1,
                    "phase1_visible_count": 0,
                    "awaiting_first_post_patch_trade_event_count": 0,
                    "pre_patch_runner_window_count": 1,
                    "post_patch_runner_without_phase1_fields_count": 0,
                    "no_trade_events_seen_count": 0,
                    "flat_restart_candidate_count": 0,
                    "open_inventory_lane_count": 1,
                    "live_rehydratable_restart_count": 1,
                    "shadow_path_reset_restart_count": 0,
                },
                "rows": [
                    {
                        "lane": "live_rearm_941777",
                        "status": "pre_patch_runner_window",
                        "open_inventory_count": 4,
                        "restart_posture": "live_open_inventory_rehydratable",
                        "trade_event_count": 2,
                        "covered_field_count": 0,
                        "field_count": 16,
                        "runner_started_at": "2026-04-15T21:40:52+00:00",
                        "latest_trade_event_ts_utc": "2026-04-16T03:10:00+00:00",
                        "rationale": "The latest reviewed FX trade events come from a runner window that started before the telemetry-bearing code.",
                    }
                ],
            }
        ))


if __name__ == "__main__":
    unittest.main()
