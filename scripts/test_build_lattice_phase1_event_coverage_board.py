from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

import scripts.build_lattice_phase1_event_coverage_board as board


class BuildLatticePhase1EventCoverageBoardTests(unittest.TestCase):
    def test_build_payload_marks_board_awaiting_patch_when_phase1_fields_are_absent(self) -> None:
        events = [
            {
                "action": "open_ticket",
                "symbol": "ETHUSD",
                "direction": "SELL",
                "time_msc": 111,
                "ts_utc": "2026-04-16T00:29:38+00:00",
            },
            {
                "action": "open_ticket",
                "symbol": "ETHUSD",
                "direction": "SELL",
                "time_msc": 111,
                "ts_utc": "2026-04-16T00:29:38+00:00",
            },
            {
                "action": "close_ticket",
                "symbol": "ETHUSD",
                "direction": "SELL",
                "realized_pnl": -5.0,
            },
        ]

        payload = board.build_payload(
            events=events,
            event_path=Path("reports/example.jsonl"),
            lane_label="example_lane",
            now=datetime(2026, 4, 16, 3, 20, tzinfo=timezone.utc),
            gap_payload={},
        )

        self.assertEqual(payload["readiness"], "awaiting_phase1_patch")
        self.assertEqual(payload["summary"]["covered_field_count"], 0)
        self.assertEqual(payload["same_tick_burst_summary"]["cluster_count_ge_2"], 1)
        self.assertEqual(payload["same_tick_burst_summary"]["max_open_count"], 2)
        self.assertEqual(payload["close_path_summary"]["phase1_close_metric_event_count"], 0)
        self.assertEqual(payload["market_state_hypothesis"]["verdict"], "insufficient_fresh_path_evidence")
        self.assertEqual(payload["rearm_timing_summary"]["rearm_open_count"], 0)

    def test_build_payload_marks_stale_log_when_gap_board_says_surface_is_present(self) -> None:
        events = [
            {
                "action": "open_ticket",
                "symbol": "ETHUSD",
                "direction": "SELL",
                "time_msc": 111,
                "ts_utc": "2026-04-16T00:29:38+00:00",
            },
            {
                "action": "close_ticket",
                "symbol": "ETHUSD",
                "direction": "SELL",
                "realized_pnl": -5.0,
                "ts_utc": "2026-04-16T00:31:00+00:00",
            },
        ]

        payload = board.build_payload(
            events=events,
            event_path=Path("reports/example.jsonl"),
            lane_label="example_lane",
            now=datetime(2026, 4, 16, 3, 20, tzinfo=timezone.utc),
            gap_payload={
                "readiness": "telemetry_surface_present",
                "summary": {
                    "required_present_count": 13,
                    "required_missing_count": 0,
                    "required_partial_count": 0,
                    "required_spec_gap_count": 0,
                },
            },
        )

        self.assertEqual(payload["readiness"], "stale_or_pre_enrichment_log")
        self.assertEqual(payload["summary"]["covered_field_count"], 0)
        self.assertIn("deployment_context", payload)

    def test_build_payload_marks_present_when_all_sections_have_coverage(self) -> None:
        events = [
            {
                "action": "open_ticket",
                "symbol": "ETHUSD",
                "direction": "SELL",
                "time_msc": 222,
                "spread_at_entry": 5.2,
                "entry_context": "burst_reclaim_probe",
                "session_bucket": "us",
                "base_step_px_at_open": 0.05,
                "same_tick_open_burst_count": 4,
                "same_bar_open_burst_count": 8,
                "anchor_distance_px_at_open": 1.5,
                "ts_utc": "2026-04-16T00:29:38+00:00",
            },
            {
                "action": "open_ticket",
                "symbol": "ETHUSD",
                "direction": "BUY",
                "time_msc": 333,
                "rearm_open": True,
                "token_age_at_fire_seconds": 14.0,
                "armed_duration_seconds": 18.0,
                "spread_at_entry": 4.9,
                "entry_context": "rearm|good_session|tight_spread",
                "session_bucket": "good_session",
                "base_step_px_at_open": 0.05,
                "same_tick_open_burst_count": 1,
                "same_bar_open_burst_count": 2,
                "anchor_distance_px_at_open": 0.8,
                "ts_utc": "2026-04-16T00:30:01+00:00",
            },
            {
                "action": "escape_tier2_surgical",
                "symbol": "ETHUSD",
                "direction": "SELL",
                "time_to_first_green_seconds": 5.0,
                "max_favorable_excursion_pnl": 1.2,
                "max_adverse_excursion_pnl": -4.1,
                "peak_pnl_before_exit": 1.8,
                "hold_seconds": 63.0,
                "first_green_before_fail": True,
                "reclaimed_trigger_level_seen": True,
                "retraced_0_25x_step_seen": True,
                "retraced_0_5x_step_seen": False,
                "realized_pnl": -5.0,
                "ts_utc": "2026-04-16T00:31:00+00:00",
            },
        ]

        payload = board.build_payload(
            events=events,
            event_path=Path("reports/example.jsonl"),
            lane_label="example_lane",
            now=datetime(2026, 4, 16, 3, 20, tzinfo=timezone.utc),
            gap_payload={
                "readiness": "telemetry_surface_present",
                "summary": {
                    "required_missing_count": 0,
                    "required_partial_count": 0,
                    "required_spec_gap_count": 0,
                    "required_present_count": 13,
                },
            },
        )

        self.assertEqual(payload["readiness"], "phase1_fields_present")
        self.assertEqual(payload["summary"]["covered_field_count"], payload["summary"]["field_count"])
        sections = {section["id"]: section for section in payload["sections"]}
        self.assertEqual(sections["open_context"]["covered_field_count"], 7)
        self.assertEqual(sections["close_path"]["covered_field_count"], 9)
        self.assertEqual(sections["rearm_timing"]["covered_field_count"], 2)
        self.assertEqual(payload["close_path_summary"]["phase1_close_metric_event_count"], 1)
        self.assertEqual(payload["close_path_summary"]["loss_with_first_green_count"], 1)
        self.assertEqual(payload["close_path_summary"]["avg_hold_seconds"], 63.0)
        self.assertEqual(payload["first_path_triage"]["verdict"], "went_green_failed_monetization")
        self.assertIn("went green", payload["first_path_triage"]["rationale"])
        self.assertEqual(payload["market_state_hypothesis"]["verdict"], "temporary_impact_but_poor_monetization")
        self.assertIn("close sequencing", payload["market_state_hypothesis"]["operator_question"])
        self.assertEqual(payload["rearm_timing_summary"]["avg_token_age_at_fire_seconds"], 14.0)

    def test_build_payload_triages_never_green_loss_when_first_close_fails_cold(self) -> None:
        events = [
            {
                "action": "open_ticket",
                "symbol": "ETHUSD",
                "direction": "SELL",
                "spread_at_entry": 4.8,
                "entry_context": "burst_reclaim_probe",
                "session_bucket": "us",
                "base_step_px_at_open": 0.05,
                "same_tick_open_burst_count": 1,
                "same_bar_open_burst_count": 1,
                "anchor_distance_px_at_open": 0.7,
                "ts_utc": "2026-04-16T00:29:38+00:00",
            },
            {
                "action": "escape_tier2_surgical",
                "symbol": "ETHUSD",
                "direction": "SELL",
                "max_favorable_excursion_pnl": 0.0,
                "max_adverse_excursion_pnl": -6.2,
                "peak_pnl_before_exit": 0.0,
                "hold_seconds": 28.0,
                "first_green_before_fail": False,
                "reclaimed_trigger_level_seen": False,
                "retraced_0_25x_step_seen": False,
                "retraced_0_5x_step_seen": False,
                "realized_pnl": -6.2,
                "ts_utc": "2026-04-16T00:30:01+00:00",
            },
        ]

        payload = board.build_payload(
            events=events,
            event_path=Path("reports/example.jsonl"),
            lane_label="example_lane",
            now=datetime(2026, 4, 16, 3, 20, tzinfo=timezone.utc),
            gap_payload={
                "readiness": "telemetry_surface_present",
                "summary": {
                    "required_missing_count": 0,
                    "required_partial_count": 0,
                    "required_spec_gap_count": 0,
                    "required_present_count": 13,
                },
            },
        )

        self.assertEqual(payload["first_path_triage"]["verdict"], "never_green_toxic_continuation")
        self.assertEqual(payload["first_path_triage"]["first_close_realized_pnl"], -6.2)
        self.assertFalse(payload["first_path_triage"]["first_close_retraced_0_5x_step_seen"])
        self.assertEqual(payload["market_state_hypothesis"]["verdict"], "repricing_or_toxic_flow_risk")
        self.assertEqual(payload["market_state_hypothesis"]["confidence"], "high")

    def test_render_markdown_mentions_burst_and_sections(self) -> None:
        payload = {
            "generated_at": "2026-04-16T03:20:00+00:00",
            "lane_label": "example_lane",
            "source_event_path": "reports/example.jsonl",
            "reference_code_path": "scripts/tick_penetration_lattice_core.py",
            "readiness": "awaiting_phase1_patch",
            "next_action": "Apply the patch.",
            "deployment_context": {
                "event_log_mtime": "2026-04-16T02:52:45+00:00",
                "reference_code_mtime": "2026-04-16T03:34:57+00:00",
                "event_log_is_newer_than_reference_code": False,
            },
            "summary": {
                "events_total": 3,
                "open_ticket_count": 2,
                "close_like_count": 1,
                "rearm_open_count": 0,
                "field_count": 18,
                "covered_field_count": 0,
                "zero_coverage_field_count": 18,
            },
            "same_tick_burst_summary": {
                "cluster_count_ge_2": 1,
                "max_open_count": 2,
                "largest_cluster": {
                    "symbol": "ETHUSD",
                    "direction": "SELL",
                    "ts_utc": "2026-04-16T00:29:38+00:00",
                },
            },
            "sections": [
                {
                    "label": "Open Ticket Context",
                    "event_count": 2,
                    "field_count": 7,
                    "covered_field_count": 0,
                    "fields": [
                        {
                            "name": "spread_at_entry",
                            "coverage_count": 0,
                            "event_count": 2,
                            "coverage_pct": 0.0,
                            "sample_value": None,
                        }
                    ],
                }
            ],
            "close_path_summary": {
                "phase1_close_metric_event_count": 0,
                "close_like_event_count": 1,
                "latest_close_like_ts_utc": "2026-04-16T00:29:38+00:00",
                "ttfg_present_count": 0,
                "ttfg_missing_count": 0,
                "loss_with_first_green_count": 0,
                "loss_without_first_green_count": 0,
                "avg_hold_seconds": None,
                "median_time_to_first_green_seconds": None,
                "avg_peak_pnl_before_exit": None,
                "avg_max_favorable_excursion_pnl": None,
                "avg_max_adverse_excursion_pnl": None,
                "reclaimed_trigger_level_count": 0,
                "retraced_half_step_count": 0,
            },
            "market_state_hypothesis": {
                "verdict": "insufficient_fresh_path_evidence",
                "confidence": "low",
                "rationale": "Need a fresh enriched close-like event first.",
                "operator_question": "Wait for the next event.",
            },
            "rearm_timing_summary": {
                "rearm_open_count": 0,
                "token_age_present_count": 0,
                "armed_duration_present_count": 0,
                "avg_token_age_at_fire_seconds": None,
                "avg_armed_duration_seconds": None,
            },
        }

        text = board.render_markdown(payload)

        self.assertIn("Lattice Phase 1 Event Coverage Board", text)
        self.assertIn("Deployment Context", text)
        self.assertIn("Same-Tick Burst Summary", text)
        self.assertIn("Close-Path Summary", text)
        self.assertIn("First-Path Triage", text)
        self.assertIn("Market-State Hypothesis", text)
        self.assertIn("Open Ticket Context", text)
        self.assertIn("spread_at_entry", text)


if __name__ == "__main__":
    unittest.main()
