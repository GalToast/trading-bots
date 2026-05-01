from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import scripts.build_structure_shapeshifter_proof_board as board


class BuildStructureShapeshifterProofBoardTests(unittest.TestCase):
    def test_build_payload_reports_historical_box_only_without_current_runner_events(self) -> None:
        now = datetime(2026, 4, 16, 2, 40, tzinfo=timezone.utc)
        payload = board.build_payload(
            now=now,
            readiness={"verdict": "ready_for_shadow_review"},
            state={
                "metadata": {
                    "declared_step_price_units": 0.05,
                    "declared_step_buy_price_units": 0.05,
                    "declared_step_sell_price_units": 0.05,
                },
                "runner": {
                    "pid": 45108,
                    "started_at": (now - timedelta(minutes=1)).isoformat(),
                    "heartbeat_at": (now - timedelta(seconds=12)).isoformat(),
                    "tick_history_source_last": "shared_tick_cache",
                    "latest_tick_source_last": "shared_price_cache",
                },
                "symbols": {
                    "ETHUSD": {
                        "base_step_px": 5.0,
                        "base_step_buy_px": 15.278145,
                        "base_step_sell_px": 5.695403,
                        "realized_closes": 12,
                        "realized_net_usd": -158.28,
                        "anchor_resets": 1,
                        "open_tickets": [],
                    }
                },
            },
            events=[
                {
                    "action": "box_geometry_adjust",
                    "ts_utc": (now - timedelta(minutes=30)).isoformat(),
                    "reason": "resistance",
                }
            ],
        )

        self.assertEqual(payload["proof_status"], "historical_box_only")
        self.assertEqual(payload["events"]["structure_flip_count"], 0)
        self.assertEqual(payload["events"]["box_geometry_adjust_count"], 1)
        self.assertEqual(payload["events"]["box_geometry_adjust_count_since_runner_start"], 0)
        self.assertTrue(payload["geometry"]["runtime_mutation_detected"])
        self.assertTrue(payload["geometry"]["asymmetric_runtime"])
        self.assertTrue(payload["deployment_context"]["pre_enrichment_runtime_window"])

    def test_build_payload_reports_structure_flip_observed(self) -> None:
        now = datetime(2026, 4, 16, 2, 40, tzinfo=timezone.utc)
        payload = board.build_payload(
            now=now,
            readiness={"verdict": "ready_for_shadow_review"},
            state={
                "metadata": {
                    "declared_step_price_units": 0.05,
                    "declared_step_buy_price_units": 0.05,
                    "declared_step_sell_price_units": 0.05,
                },
                "runner": {
                    "pid": 45108,
                    "started_at": (now - timedelta(minutes=3)).isoformat(),
                    "heartbeat_at": (now - timedelta(seconds=5)).isoformat(),
                },
                "symbols": {
                    "ETHUSD": {
                        "base_step_px": 5.0,
                        "base_step_buy_px": 9.0,
                        "base_step_sell_px": 3.0,
                        "realized_closes": 3,
                        "realized_net_usd": 5.0,
                        "anchor_resets": 0,
                        "open_tickets": [{"ticket": 1}],
                    }
                },
            },
            events=[
                {
                    "action": "structure_flip",
                    "ts_utc": (now - timedelta(minutes=2)).isoformat(),
                    "reason": "structure_flip",
                }
            ],
        )

        self.assertEqual(payload["proof_status"], "structure_flip_observed")
        self.assertEqual(payload["events"]["structure_flip_count"], 1)
        self.assertEqual(payload["events"]["structure_flip_count_since_runner_start"], 1)
        self.assertEqual(payload["economics"]["open_ticket_count"], 1)

    def test_build_payload_reports_historical_box_only_when_current_runner_has_no_events(self) -> None:
        now = datetime(2026, 4, 16, 2, 40, tzinfo=timezone.utc)
        payload = board.build_payload(
            now=now,
            readiness={"verdict": "ready_for_shadow_review"},
            state={
                "metadata": {
                    "declared_step_price_units": 0.05,
                    "declared_step_buy_price_units": 0.05,
                    "declared_step_sell_price_units": 0.05,
                },
                "runner": {
                    "pid": 45108,
                    "started_at": now.isoformat(),
                    "heartbeat_at": (now - timedelta(seconds=10)).isoformat(),
                },
                "symbols": {
                    "ETHUSD": {
                        "base_step_px": 5.0,
                        "base_step_buy_px": 15.278145,
                        "base_step_sell_px": 5.695403,
                    }
                },
            },
            events=[
                {
                    "action": "box_geometry_adjust",
                    "ts_utc": (now - timedelta(minutes=30)).isoformat(),
                    "reason": "resistance",
                }
            ],
        )

        self.assertEqual(payload["proof_status"], "historical_box_only")
        self.assertEqual(payload["events"]["box_geometry_adjust_count"], 1)
        self.assertEqual(payload["events"]["box_geometry_adjust_count_since_runner_start"], 0)

    def test_build_payload_reports_stale_runtime(self) -> None:
        now = datetime(2026, 4, 16, 2, 40, tzinfo=timezone.utc)
        payload = board.build_payload(
            now=now,
            readiness={"verdict": "ready_for_shadow_review"},
            state={
                "metadata": {"declared_step_price_units": 0.05},
                "runner": {"heartbeat_at": (now - timedelta(minutes=5)).isoformat()},
                "symbols": {"ETHUSD": {}},
            },
            events=[],
        )

        self.assertEqual(payload["proof_status"], "stale_runtime")

    def test_render_markdown_mentions_structure_flip_gap(self) -> None:
        text = board.render_markdown(
            {
                "lane_name": "shadow_ethusd_m5_structure_shapeshifter",
                "symbol": "ETHUSD",
                "readiness_verdict": "ready_for_shadow_review",
                "proof_status": "historical_box_only",
                "runner": {
                    "fresh": True,
                    "pid": 45108,
                    "started_at": "2026-04-16T02:06:43+00:00",
                    "heartbeat_age_seconds": 10.0,
                    "tick_history_source_last": "shared_tick_cache",
                    "latest_tick_source_last": "shared_price_cache",
                },
                "deployment_context": {
                    "reference_code_path": "scripts/tick_penetration_lattice_core.py",
                    "reference_code_mtime": "2026-04-16T03:34:57+00:00",
                    "event_log_mtime": "2026-04-16T02:52:45+00:00",
                    "event_log_is_newer_than_reference_code": False,
                    "runner_started_after_reference_code": False,
                    "pre_enrichment_runtime_window": True,
                },
                "geometry": {
                    "declared_step_px": 0.05,
                    "declared_step_buy_px": 0.05,
                    "declared_step_sell_px": 0.05,
                    "base_step_px": 5.0,
                    "base_step_buy_px": 15.278145,
                    "base_step_sell_px": 5.695403,
                    "runtime_mutation_detected": True,
                    "asymmetric_runtime": True,
                },
                "events": {
                    "structure_flip_count": 0,
                    "structure_flip_count_since_runner_start": 0,
                    "box_geometry_adjust_count": 1,
                    "box_geometry_adjust_count_since_runner_start": 0,
                    "latest_structure_flip": {},
                    "latest_structure_flip_since_runner_start": {},
                    "latest_box_geometry_adjust": {"ts_utc": "2026-04-16T00:29:38+00:00", "reason": "resistance"},
                    "latest_box_geometry_adjust_since_runner_start": {},
                },
                "economics": {"realized_closes": 12, "realized_net_usd": -158.28, "anchor_resets": 1},
            }
        )

        self.assertIn("Structure Shapeshifter Proof Board", text)
        self.assertIn("historical_box_only", text)
        self.assertIn("structure_flip", text)
        self.assertIn("current runner", text)
        self.assertIn("pre_enrichment_runtime_window", text)


if __name__ == "__main__":
    unittest.main()
