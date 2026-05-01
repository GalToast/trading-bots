#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_btcusd_h1_step_readiness_board as board


class BTCUSDH1StepReadinessBoardTests(unittest.TestCase):
    def test_lane_meta_uses_dynamic_live_step(self) -> None:
        meta = board.lane_meta(30.0)
        self.assertEqual(meta["live_btcusd_exc2_tight_941779"]["label"], "live_step30")
        self.assertEqual(meta["live_btcusd_exc2_tight_941779"]["step"], 30.0)

    def test_classify_first_trade_state_marks_flat_reanchoring(self) -> None:
        self.assertEqual(
            board.classify_first_trade_state(open_count=0, realized_closes=0, anchor_resets=4),
            "flat_reanchoring",
        )
        self.assertEqual(
            board.classify_first_trade_state(open_count=0, realized_closes=0, anchor_resets=0),
            "flat_waiting",
        )
        self.assertEqual(
            board.classify_first_trade_state(open_count=1, realized_closes=0, anchor_resets=4),
            "active_or_seeded",
        )

    def test_build_payload_prefers_top_replay_candidate_but_waits_for_forward_maturity(self) -> None:
        payload = board.build_payload(
            forward_map={
                "live_btcusd_exc2_tight_941779": {
                    "forward_status": "live_reference",
                    "new_closes": 0,
                    "realized_delta_usd": 0.0,
                    "realized_net_usd": 231.26,
                    "open_count": 15,
                    "floating_usd": -900.0,
                    "net_usd": -668.74,
                },
                "shadow_btcusd_h1_step30": {
                    "forward_status": "seeded_flat",
                    "new_closes": 2,
                    "realized_delta_usd": 1.25,
                    "realized_net_usd": 1.25,
                    "open_count": 1,
                    "floating_usd": -2.0,
                    "net_usd": -0.75,
                    "baseline_at": "2026-04-13T04:00:00+00:00",
                },
                "shadow_btcusd_h1_step50": {
                    "forward_status": "seeded_flat",
                    "new_closes": 1,
                    "realized_delta_usd": 0.5,
                    "realized_net_usd": 0.5,
                    "open_count": 0,
                    "floating_usd": 0.0,
                    "net_usd": 0.5,
                    "baseline_at": "2026-04-13T04:00:00+00:00",
                },
            },
            robustness_summary={
                30.0: {
                    "replay_rank": 1,
                    "window_wins": 3,
                    "avg_marked_net_usd": 793.39,
                    "avg_realized_net_usd": 1027.003,
                    "best_marked_net_usd": 1355.57,
                    "worst_marked_net_usd": 85.15,
                },
                45.0: {
                    "replay_rank": 3,
                    "window_wins": 0,
                    "avg_marked_net_usd": 207.517,
                    "avg_realized_net_usd": 787.087,
                    "best_marked_net_usd": 1125.89,
                    "worst_marked_net_usd": -1058.84,
                },
                50.0: {
                    "replay_rank": 2,
                    "window_wins": 0,
                    "avg_marked_net_usd": 286.43,
                    "avg_realized_net_usd": 814.323,
                    "best_marked_net_usd": 1133.38,
                    "worst_marked_net_usd": -1040.03,
                },
            },
            state_context={
                "live_btcusd_exc2_tight_941779": {
                    "runtime_age_hours": 4.0,
                    "anchor_resets": 0,
                    "state_step": 45.0,
                    "first_trade_state": "active_or_seeded",
                },
                "shadow_btcusd_h1_step30": {
                    "runtime_age_hours": 0.5,
                    "anchor_resets": 9,
                    "state_step": 30.0,
                    "first_trade_state": "flat_reanchoring",
                },
                "shadow_btcusd_h1_step50": {
                    "runtime_age_hours": 0.5,
                    "anchor_resets": 2,
                    "state_step": 50.0,
                    "first_trade_state": "flat_waiting",
                },
            },
            live_step=45.0,
        )
        self.assertEqual(payload["watch_lead"]["lane_name"], "shadow_btcusd_h1_step30")
        rows = {row["lane_name"]: row for row in payload["rows"]}
        self.assertEqual(rows["shadow_btcusd_h1_step30"]["readiness_state"], "top_replay_wait_forward")
        self.assertEqual(rows["shadow_btcusd_h1_step50"]["readiness_state"], "fallback_wait_forward")
        self.assertEqual(rows["live_btcusd_exc2_tight_941779"]["readiness_state"], "live_reference")
        self.assertEqual(rows["shadow_btcusd_h1_step30"]["first_trade_state"], "flat_reanchoring")
        self.assertEqual(rows["shadow_btcusd_h1_step30"]["anchor_resets"], 9)
        self.assertIn("still flat", rows["shadow_btcusd_h1_step30"]["first_trade_note"])
        self.assertIn("No BTC H1 step candidate clears", payload["leadership_read"][0])

    def test_build_payload_marks_positive_mature_run_for_review(self) -> None:
        payload = board.build_payload(
            forward_map={
                "live_btcusd_exc2_tight_941779": {
                    "forward_status": "live_reference",
                },
                "shadow_btcusd_h1_step30": {
                    "forward_status": "holding_up",
                    "new_closes": 22,
                    "realized_delta_usd": 18.0,
                    "realized_net_usd": 18.0,
                    "open_count": 2,
                    "floating_usd": -4.0,
                    "net_usd": 14.0,
                },
                "shadow_btcusd_h1_step50": {
                    "forward_status": "lagging",
                    "new_closes": 11,
                    "realized_delta_usd": -6.0,
                    "realized_net_usd": -6.0,
                    "open_count": 1,
                    "floating_usd": -3.0,
                    "net_usd": -9.0,
                },
            },
            robustness_summary={
                30.0: {"replay_rank": 1, "window_wins": 3},
                45.0: {"replay_rank": 3, "window_wins": 0},
                50.0: {"replay_rank": 2, "window_wins": 0},
            },
            state_context={
                "live_btcusd_exc2_tight_941779": {"first_trade_state": "active_or_seeded"},
                "shadow_btcusd_h1_step30": {"first_trade_state": "active_or_seeded"},
                "shadow_btcusd_h1_step50": {"first_trade_state": "active_or_seeded"},
            },
            live_step=45.0,
        )
        rows = {row["lane_name"]: row for row in payload["rows"]}
        self.assertEqual(rows["shadow_btcusd_h1_step30"]["readiness_state"], "promotion_ready_review")
        self.assertEqual(rows["shadow_btcusd_h1_step50"]["readiness_state"], "forward_negative_hold")
        self.assertIn("clears the early forward bar", payload["leadership_read"][0])


if __name__ == "__main__":
    unittest.main()
