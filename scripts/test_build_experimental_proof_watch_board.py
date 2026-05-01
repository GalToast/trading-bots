from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import scripts.build_experimental_proof_watch_board as board


class BuildExperimentalProofWatchBoardTests(unittest.TestCase):
    def test_build_payload_waiting_market_proof_when_eth_and_shapeshifter_are_healthy_but_idle(self) -> None:
        now = datetime(2026, 4, 16, 2, 45, tzinfo=timezone.utc)
        payload = board.build_payload(
            now=now,
            eth_payload={
                "generated_at": (now - timedelta(seconds=5)).isoformat(),
                "active_rows": [
                    {
                        "lane": "shadow_ethusd_m5_atr_optimized",
                        "timeframe": "M5",
                        "runner_pid": 4660,
                        "watchdog_status": "ok",
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "open_count": 0,
                        "anchor_resets": 2,
                        "runner_heartbeat_at": (now - timedelta(seconds=15)).isoformat(),
                    },
                    {
                        "lane": "shadow_ethusd_m15_atr_optimized",
                        "timeframe": "M15",
                        "runner_pid": 2296,
                        "watchdog_status": "ok",
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "open_count": 0,
                        "anchor_resets": 2,
                        "runner_heartbeat_at": (now - timedelta(seconds=12)).isoformat(),
                    },
                ],
            },
            shapeshifter_payload={
                "generated_at": (now - timedelta(seconds=3)).isoformat(),
                "proof_status": "historical_box_only",
                "readiness_verdict": "ready_for_shadow_review",
                "runner": {
                    "fresh": True,
                    "pid": 45108,
                    "heartbeat_at": (now - timedelta(seconds=10)).isoformat(),
                },
                "events": {
                    "structure_flip_count_since_runner_start": 0,
                    "box_geometry_adjust_count_since_runner_start": 0,
                },
                "economics": {
                    "realized_closes": 12,
                    "realized_net_usd": -158.28,
                    "anchor_resets": 2,
                },
            },
            coverage_payload={
                "generated_at": (now - timedelta(seconds=2)).isoformat(),
                "readiness": "awaiting_phase1_patch",
                "next_action": "Wait for the Phase 1 patch.",
                "summary": {
                    "field_count": 18,
                    "covered_field_count": 0,
                },
                "same_tick_burst_summary": {
                    "cluster_count_ge_2": 1,
                    "max_open_count": 12,
                    "largest_cluster": {"direction": "SELL"},
                },
                "close_path_summary": {
                    "phase1_close_metric_event_count": 0,
                    "loss_with_first_green_count": 0,
                    "loss_without_first_green_count": 0,
                    "avg_hold_seconds": None,
                    "median_time_to_first_green_seconds": None,
                    "avg_peak_pnl_before_exit": None,
                },
                "first_path_triage": {
                    "verdict": "awaiting_first_trade_path_event",
                    "rationale": "No fresh Phase 1 open_ticket or close-like event exists yet in the inspected log.",
                },
                "market_state_hypothesis": {
                    "verdict": "insufficient_fresh_path_evidence",
                    "confidence": "low",
                    "rationale": "No fresh close-path sample exists yet, so market-state classification would be guesswork.",
                },
                "rearm_timing_summary": {
                    "rearm_open_count": 0,
                    "avg_token_age_at_fire_seconds": None,
                },
            },
        )

        self.assertEqual(payload["overall_status"], "waiting_market_proof")
        self.assertEqual(payload["eth_atr"]["healthy_lane_count"], 2)
        self.assertEqual(payload["eth_atr"]["total_realized_closes"], 0)
        self.assertEqual(payload["shapeshifter"]["structure_flip_count_since_runner_start"], 0)
        self.assertEqual(payload["shapeshifter"]["phase1_event_covered_field_count"], 0)
        self.assertEqual(payload["shapeshifter"]["phase1_event_coverage_next_action"], "Wait for the Phase 1 patch.")
        self.assertEqual(payload["shapeshifter"]["phase1_close_metric_event_count"], 0)
        self.assertEqual(payload["shapeshifter"]["phase1_first_path_verdict"], "awaiting_first_trade_path_event")
        self.assertEqual(
            payload["shapeshifter"]["phase1_market_state_hypothesis_verdict"],
            "insufficient_fresh_path_evidence",
        )

    def test_build_payload_marks_needs_attention_when_event_log_predates_telemetry_code(self) -> None:
        now = datetime(2026, 4, 16, 2, 45, tzinfo=timezone.utc)
        payload = board.build_payload(
            now=now,
            eth_payload={"active_rows": []},
            shapeshifter_payload={
                "generated_at": (now - timedelta(seconds=3)).isoformat(),
                "proof_status": "historical_box_only",
                "readiness_verdict": "ready_for_shadow_review",
                "runner": {
                    "fresh": True,
                    "pid": 45108,
                    "heartbeat_at": (now - timedelta(seconds=10)).isoformat(),
                },
                "events": {
                    "structure_flip_count_since_runner_start": 0,
                    "box_geometry_adjust_count_since_runner_start": 0,
                },
                "economics": {},
            },
            coverage_payload={
                "generated_at": (now - timedelta(seconds=2)).isoformat(),
                "readiness": "stale_or_pre_enrichment_log",
                "next_action": "Rebuild against a fresh post-enrichment runtime log.",
                "summary": {
                    "field_count": 18,
                    "covered_field_count": 0,
                },
                "deployment_context": {
                    "event_log_is_newer_than_reference_code": False,
                },
                "same_tick_burst_summary": {},
                "close_path_summary": {
                    "phase1_close_metric_event_count": 0,
                    "loss_with_first_green_count": 0,
                    "loss_without_first_green_count": 0,
                    "avg_hold_seconds": None,
                    "median_time_to_first_green_seconds": None,
                    "avg_peak_pnl_before_exit": None,
                },
                "first_path_triage": {
                    "verdict": "awaiting_first_trade_path_event",
                    "rationale": "No fresh Phase 1 open_ticket or close-like event exists yet in the inspected log.",
                },
                "market_state_hypothesis": {
                    "verdict": "insufficient_fresh_path_evidence",
                    "confidence": "low",
                    "rationale": "No fresh close-path sample exists yet, so market-state classification would be guesswork.",
                },
                "rearm_timing_summary": {
                    "rearm_open_count": 0,
                    "avg_token_age_at_fire_seconds": None,
                },
            },
        )

        self.assertEqual(payload["overall_status"], "needs_attention")
        self.assertIn("fresh enriched event window", payload["next_action"])

    def test_build_payload_prioritizes_runtime_proof_when_structure_flip_arrives(self) -> None:
        now = datetime(2026, 4, 16, 2, 45, tzinfo=timezone.utc)
        payload = board.build_payload(
            now=now,
            eth_payload={"active_rows": []},
            shapeshifter_payload={
                "proof_status": "structure_flip_observed",
                "runner": {
                    "fresh": True,
                    "pid": 45108,
                    "heartbeat_at": (now - timedelta(seconds=5)).isoformat(),
                },
                "events": {
                    "structure_flip_count_since_runner_start": 1,
                    "box_geometry_adjust_count_since_runner_start": 1,
                },
                "economics": {},
            },
            coverage_payload={
                "generated_at": (now - timedelta(seconds=2)).isoformat(),
                "readiness": "awaiting_phase1_patch",
                "next_action": "Wait for the Phase 1 patch.",
                "summary": {
                    "field_count": 18,
                    "covered_field_count": 0,
                },
                "same_tick_burst_summary": {},
                "close_path_summary": {
                    "phase1_close_metric_event_count": 0,
                    "loss_with_first_green_count": 0,
                    "loss_without_first_green_count": 0,
                    "avg_hold_seconds": None,
                    "median_time_to_first_green_seconds": None,
                    "avg_peak_pnl_before_exit": None,
                },
                "first_path_triage": {
                    "verdict": "awaiting_first_trade_path_event",
                    "rationale": "No fresh Phase 1 open_ticket or close-like event exists yet in the inspected log.",
                },
                "market_state_hypothesis": {
                    "verdict": "insufficient_fresh_path_evidence",
                    "confidence": "low",
                    "rationale": "No fresh close-path sample exists yet, so market-state classification would be guesswork.",
                },
                "rearm_timing_summary": {
                    "rearm_open_count": 0,
                    "avg_token_age_at_fire_seconds": None,
                },
            },
        )

        self.assertEqual(payload["overall_status"], "new_runtime_proof_available")
        self.assertIn("structure-flip", payload["next_action"])
        self.assertEqual(payload["shapeshifter"]["phase1_event_field_count"], 18)

    def test_build_payload_waiting_post_restart_event_when_runners_are_already_post_patch(self) -> None:
        now = datetime(2026, 4, 16, 3, 48, tzinfo=timezone.utc)
        reference_code_mtime = (now - timedelta(minutes=10)).isoformat()
        payload = board.build_payload(
            now=now,
            eth_payload={
                "generated_at": (now - timedelta(seconds=5)).isoformat(),
                "active_rows": [
                    {
                        "lane": "shadow_ethusd_m5_atr_optimized",
                        "timeframe": "M5",
                        "runner_pid": 41748,
                        "watchdog_status": "ok",
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "open_count": 0,
                        "anchor_resets": 0,
                        "runner_heartbeat_at": (now - timedelta(seconds=12)).isoformat(),
                        "runner_started_at": (now - timedelta(seconds=20)).isoformat(),
                    },
                    {
                        "lane": "shadow_ethusd_m15_atr_optimized",
                        "timeframe": "M15",
                        "runner_pid": 30380,
                        "watchdog_status": "ok",
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "open_count": 0,
                        "anchor_resets": 0,
                        "runner_heartbeat_at": (now - timedelta(seconds=12)).isoformat(),
                        "runner_started_at": (now - timedelta(seconds=20)).isoformat(),
                    },
                ],
            },
            shapeshifter_payload={
                "generated_at": (now - timedelta(seconds=3)).isoformat(),
                "proof_status": "historical_box_only",
                "readiness_verdict": "ready_for_shadow_review",
                "runner": {
                    "fresh": True,
                    "pid": 24268,
                    "heartbeat_at": (now - timedelta(seconds=10)).isoformat(),
                },
                "deployment_context": {
                    "runner_started_after_reference_code": True,
                },
                "events": {
                    "structure_flip_count_since_runner_start": 0,
                    "box_geometry_adjust_count_since_runner_start": 0,
                },
                "economics": {
                    "realized_closes": 12,
                    "realized_net_usd": -158.28,
                    "anchor_resets": 2,
                },
            },
            coverage_payload={
                "generated_at": (now - timedelta(seconds=2)).isoformat(),
                "readiness": "stale_or_pre_enrichment_log",
                "next_action": "Rebuild against a fresh post-enrichment runtime log.",
                "deployment_context": {
                    "event_log_is_newer_than_reference_code": False,
                    "reference_code_mtime": reference_code_mtime,
                },
                "summary": {
                    "field_count": 18,
                    "covered_field_count": 0,
                },
                "same_tick_burst_summary": {
                    "cluster_count_ge_2": 1,
                    "max_open_count": 12,
                    "largest_cluster": {"direction": "SELL"},
                },
                "close_path_summary": {
                    "phase1_close_metric_event_count": 0,
                    "loss_with_first_green_count": 0,
                    "loss_without_first_green_count": 0,
                    "avg_hold_seconds": None,
                    "median_time_to_first_green_seconds": None,
                    "avg_peak_pnl_before_exit": None,
                },
                "first_path_triage": {
                    "verdict": "awaiting_first_trade_path_event",
                    "rationale": "No fresh Phase 1 open_ticket or close-like event exists yet in the inspected log.",
                },
                "market_state_hypothesis": {
                    "verdict": "insufficient_fresh_path_evidence",
                    "confidence": "low",
                    "rationale": "No fresh close-path sample exists yet, so market-state classification would be guesswork.",
                },
                "rearm_timing_summary": {
                    "rearm_open_count": 0,
                    "avg_token_age_at_fire_seconds": None,
                },
            },
        )

        self.assertEqual(payload["overall_status"], "waiting_post_restart_event")
        self.assertIn("already live", payload["next_action"])
        self.assertEqual(payload["eth_atr"]["post_patch_lane_count"], 2)
        self.assertTrue(payload["eth_atr"]["all_lanes_started_after_reference_code"])
        self.assertTrue(payload["shapeshifter"]["runner_started_after_reference_code"])

    def test_render_markdown_mentions_both_monitored_paths(self) -> None:
        text = board.render_markdown(
            {
                "generated_at": "2026-04-16T02:45:00+00:00",
                "overall_status": "waiting_post_restart_event",
                "next_action": "The telemetry-bearing runners are already live.",
                "eth_atr": {
                    "board_generated_at": "2026-04-16T02:44:00+00:00",
                    "healthy_lane_count": 3,
                    "lane_count": 3,
                    "total_realized_closes": 0,
                    "total_open_positions": 0,
                    "total_realized_net_usd": 0.0,
                    "latest_heartbeat_age_seconds": 12.0,
                    "post_patch_lane_count": 3,
                    "lanes": [
                        {
                            "lane": "shadow_ethusd_m5_atr_optimized",
                            "timeframe": "M5",
                            "runner_pid": 4660,
                            "watchdog_status": "ok",
                            "realized_closes": 0,
                            "realized_net_usd": 0.0,
                            "open_count": 0,
                            "anchor_resets": 2,
                            "runner_started_at": "2026-04-16T02:44:10+00:00",
                        }
                    ],
                },
                "shapeshifter": {
                    "board_generated_at": "2026-04-16T02:44:35+00:00",
                    "proof_status": "historical_box_only",
                    "readiness_verdict": "ready_for_shadow_review",
                    "runner_pid": 45108,
                    "runner_fresh": True,
                    "heartbeat_age_seconds": 20.0,
                    "structure_flip_count_since_runner_start": 0,
                    "box_geometry_adjust_count_since_runner_start": 0,
                    "realized_closes": 12,
                    "realized_net_usd": -158.28,
                    "anchor_resets": 2,
                    "phase1_event_coverage_readiness": "stale_or_pre_enrichment_log",
                    "phase1_event_covered_field_count": 0,
                    "phase1_event_field_count": 18,
                    "phase1_event_log_is_newer_than_reference_code": False,
                    "runner_started_after_reference_code": True,
                    "phase1_event_coverage_next_action": "Rebuild against a fresh post-enrichment runtime log.",
                    "phase1_same_tick_burst_cluster_count_ge_2": 1,
                    "phase1_same_tick_burst_max_open_count": 12,
                    "phase1_close_metric_event_count": 1,
                    "phase1_loss_with_first_green_count": 0,
                    "phase1_loss_without_first_green_count": 1,
                    "phase1_avg_hold_seconds": 63.0,
                    "phase1_median_time_to_first_green_seconds": None,
                    "phase1_avg_peak_pnl_before_exit": 1.8,
                    "phase1_first_path_verdict": "never_green_toxic_continuation",
                    "phase1_first_path_rationale": "The first close-like event realized a loss without ever recording first green.",
                    "phase1_first_path_close_ts_utc": "2026-04-16T02:44:40+00:00",
                    "phase1_first_path_close_realized_pnl": -5.0,
                    "phase1_first_path_close_ttfg_seconds": None,
                    "phase1_market_state_hypothesis_verdict": "repricing_or_toxic_flow_risk",
                    "phase1_market_state_hypothesis_confidence": "high",
                    "phase1_market_state_hypothesis_rationale": "The first post-fire path never went green and closed red, which fits toxic continuation more than temporary impact decay.",
                    "phase1_rearm_open_count": 0,
                    "phase1_avg_token_age_at_fire_seconds": None,
                },
            }
        )

        self.assertIn("Experimental Proof Watch Board", text)
        self.assertIn("ETH ATR Pack", text)
        self.assertIn("Structure Shapeshifter", text)
        self.assertIn("waiting_post_restart_event", text)
        self.assertIn("phase1_event_coverage_readiness", text)
        self.assertIn("phase1_event_coverage_next_action", text)
        self.assertIn("post_patch_lane_count", text)
        self.assertIn("runner_started_after_reference_code", text)
        self.assertIn("post-restart waiting window", text)
        self.assertIn("Coverage interpretation", text)
        self.assertIn("fresh post-enrichment runtime log", text)
        self.assertIn("Phase 1 path summary", text)
        self.assertIn("First-path triage", text)
        self.assertIn("never_green_toxic_continuation", text)
        self.assertIn("phase1_market_state_hypothesis_verdict", text)
        self.assertIn("repricing_or_toxic_flow_risk", text)
        self.assertIn("Market-state hypothesis", text)
        self.assertNotIn("before Task 29 lands", text)


if __name__ == "__main__":
    unittest.main()
