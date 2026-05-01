from __future__ import annotations

import unittest
from datetime import datetime, timezone

import scripts.build_lattice_telemetry_gap_board as board


class BuildLatticeTelemetryGapBoardTests(unittest.TestCase):
    def test_build_payload_marks_behavior_metrics_missing_when_lattice_has_no_fields(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 3, 15, tzinfo=timezone.utc),
            behavior_text="""
time_to_first_green_seconds
max_favorable_excursion_pnl
spread_at_entry
entry_context
""",
            lattice_core_text="open_ticket close_ticket realized_net_usd",
            lattice_runtime_text="latest_tick_source_last shared_price_cache",
        )

        metrics = {item["id"]: item for item in payload["metrics"]}

        self.assertEqual(metrics["time_to_first_green"]["status"], "missing")
        self.assertEqual(metrics["mfe"]["status"], "missing")
        self.assertEqual(metrics["spread_at_entry"]["status"], "missing")
        self.assertEqual(metrics["tick_source_context"]["status"], "partial")
        self.assertEqual(payload["readiness"], "telemetry_port_needed")

    def test_build_payload_marks_partial_and_present_rows(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 3, 15, tzinfo=timezone.utc),
            behavior_text="spread_at_entry regime_at_entry",
            lattice_core_text="""
open_tickets realized_net_usd realized_closes reclaimed_trigger_level_seen retraced_0_25x_step_seen retraced_0_5x_step_seen
""",
            lattice_runtime_text="""
latest_tick_source_last tick_history_source_last shared_price_cache session_bucket
""",
        )

        metrics = {item["id"]: item for item in payload["metrics"]}

        self.assertEqual(metrics["tick_source_context"]["status"], "present")
        self.assertEqual(metrics["inventory_pressure_summary"]["status"], "present")
        self.assertEqual(metrics["penetration_quality_summary"]["status"], "present")

    def test_build_payload_marks_new_phase1_rows_present_or_partial(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 3, 15, tzinfo=timezone.utc),
            behavior_text="""
time_to_first_green_seconds
max_favorable_excursion_pnl
max_adverse_excursion_pnl
peak_pnl_before_exit
hold_seconds
first_green_before_fail
spread_at_entry
entry_context
regime_at_entry
""",
            lattice_core_text="""
time_to_first_green_seconds max_favorable_excursion_pnl max_adverse_excursion_pnl peak_pnl_before_exit hold_seconds first_green_before_fail spread_at_entry entry_context regime_at_entry token_age_at_fire_seconds armed_duration_seconds created_time armed_at_time rearm_to_first_green_seconds rearm_to_fail_seconds open_tickets realized_net_usd realized_closes reclaimed_trigger_level_seen retraced_0_25x_step_seen retraced_0_5x_step_seen
""",
            lattice_runtime_text="latest_tick_source_last tick_history_source_last shared_price_cache session_bucket",
        )

        metrics = {item["id"]: item for item in payload["metrics"]}

        self.assertEqual(metrics["time_to_first_green"]["status"], "present")
        self.assertEqual(metrics["mfe"]["status"], "present")
        self.assertEqual(metrics["mae"]["status"], "present")
        self.assertEqual(metrics["spread_at_entry"]["status"], "present")
        self.assertEqual(metrics["entry_context"]["status"], "present")
        self.assertEqual(metrics["rearm_token_age"]["status"], "present")
        self.assertEqual(metrics["regime_at_entry"]["status"], "present")
        self.assertEqual(metrics["rearm_outcome_metrics"]["status"], "present")
        self.assertEqual(metrics["tick_source_context"]["status"], "present")
        self.assertEqual(payload["readiness"], "telemetry_surface_present")

    def test_build_payload_treats_phase2_metrics_as_deferred_not_blocking(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 3, 15, tzinfo=timezone.utc),
            behavior_text="""
time_to_first_green_seconds
max_favorable_excursion_pnl
max_adverse_excursion_pnl
peak_pnl_before_exit
hold_seconds
first_green_before_fail
spread_at_entry
entry_context
regime_at_entry
""",
            lattice_core_text="""
time_to_first_green_seconds max_favorable_excursion_pnl max_adverse_excursion_pnl peak_pnl_before_exit hold_seconds first_green_before_fail spread_at_entry entry_context token_age_at_fire_seconds armed_duration_seconds created_time armed_at_time rearm_to_first_green_seconds rearm_to_fail_seconds open_tickets realized_net_usd realized_closes reclaimed_trigger_level_seen retraced_0_25x_step_seen retraced_0_5x_step_seen session_bucket
""",
            lattice_runtime_text="latest_tick_source_last tick_history_source_last shared_price_cache",
        )

        metrics = {item["id"]: item for item in payload["metrics"]}

        self.assertEqual(metrics["regime_at_entry"]["status"], "deferred")
        self.assertEqual(payload["summary"]["deferred_count"], 1)
        self.assertEqual(payload["readiness"], "telemetry_surface_present")

    def test_render_markdown_mentions_readiness_and_metric_table(self) -> None:
        text = board.render_markdown(
            {
                "generated_at": "2026-04-16T03:15:00+00:00",
                "readiness": "telemetry_port_needed",
                "next_action": "Port missing metrics.",
                "summary": {
                    "total_metrics": 2,
                    "present_count": 0,
                    "partial_count": 1,
                    "missing_count": 1,
                    "spec_gap_count": 0,
                    "deferred_count": 0,
                    "required_metric_count": 2,
                    "required_present_count": 0,
                    "required_partial_count": 1,
                    "required_missing_count": 1,
                    "required_spec_gap_count": 0,
                },
                "metrics": [
                    {
                        "label": "Time To First Green",
                        "category": "per_ticket_lifecycle",
                        "phase": "phase1",
                        "status": "missing",
                        "target_fields": ["time_to_first_green_seconds"],
                        "deferred_fields": [],
                        "behavior_match_count": 1,
                        "behavior_expected_count": 1,
                        "lattice_match_count": 0,
                        "lattice_expected_count": 0,
                    }
                ],
            }
        )

        self.assertIn("Lattice Telemetry Gap Board", text)
        self.assertIn("telemetry_port_needed", text)
        self.assertIn("Metric Matrix", text)
        self.assertIn("Time To First Green", text)


if __name__ == "__main__":
    unittest.main()
