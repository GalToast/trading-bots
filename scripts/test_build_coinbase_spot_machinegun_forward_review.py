#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_coinbase_spot_machinegun_forward_review import build_payload, write_reports


class MachinegunForwardReviewTests(unittest.TestCase):
    def test_build_payload_marks_position_and_rotation_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_path = root / "state.json"
            strategy_path = root / "strategy.json"
            tape_path = root / "tape.jsonl"
            state_path.write_text(
                json.dumps(
                    {
                        "state": {
                            "cash_usd": 10.0,
                            "starting_cash_usd": 50.0,
                            "realized_net_usd": 0.0,
                            "realized_closes": 0,
                            "total_fees": 0.48,
                            "taker_fee_bps": 120.0,
                            "rotation_buffer_pct": 0.5,
                            "min_profit_to_trail_usd": 0.01,
                            "profit_lock_retention_pct": 35.0,
                            "target_net_pct_per_hour": 5.0,
                            "target_started_at": "2026-04-23T00:00:00+00:00",
                            "position": {
                                "product_id": "HOLD-USD",
                                "playbook": "fee_hurdle_breakout_trailer",
                                "entry_price": 1.0,
                                "quantity": 39.52,
                                "cost_usd": 40.0,
                                "entry_fee": 0.48,
                                "opened_at": "2026-04-23T00:00:00+00:00",
                                "highest_bid": 1.04,
                                "trail_giveback_pct": 1.0,
                                "entry_edge_over_hurdle_pct": 1.0,
                                "max_net_pnl": 0.5,
                                "max_net_pct_on_cost": 1.25,
                            },
                        },
                        "runner": {"pid": 123, "shadow_only": True},
                    }
                ),
                encoding="utf-8",
            )
            strategy_path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "rank": 1,
                                "product_id": "NEXT-USD",
                                "playbook": "hot_potato_hour_rotation",
                                "hurdle_state": "clears_hour_hurdle",
                                "machinegun_score": 10.0,
                                "edge_over_hurdle_pct": 4.0,
                                "ret_15m_pct": 2.0,
                                "ret_60m_pct": 5.0,
                                "spread_bps": 5.0,
                                "trail_giveback_pct": 0.5,
                            },
                            {
                                "rank": 2,
                                "product_id": "HOLD-USD",
                                "playbook": "fee_hurdle_breakout_trailer",
                                "hurdle_state": "clears_fast_hurdle",
                                "machinegun_score": 5.0,
                                "edge_over_hurdle_pct": 1.0,
                                "ret_15m_pct": 1.0,
                                "ret_60m_pct": 1.0,
                                "spread_bps": 4.0,
                                "trail_giveback_pct": 1.0,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            tape_path.write_text(
                json.dumps({"ts_utc": "2026-04-23T00:00:00+00:00", "decision": {"decision": "hold"}, "top_candidates": [{"product_id": "NEXT-USD"}]})
                + "\n",
                encoding="utf-8",
            )
            payload = build_payload(
                state_path=state_path,
                strategy_path=strategy_path,
                opportunity_tape_path=tape_path,
                no_live_tick=True,
                tape_limit=10,
            )
            self.assertEqual(payload["rotation_review"]["decision"], "rotate_to_challenger")
            self.assertGreater(payload["current_position"]["net_if_closed"], 0.0)
            self.assertTrue(payload["current_position"]["profit_lock_armed"])
            self.assertGreater(payload["current_position"]["profit_lock_floor_usd"], 0.0)
            self.assertEqual(payload["banking_target"]["target_net_pct_per_hour"], 5.0)
            self.assertIn("status", payload["banking_target"])
            self.assertEqual(payload["opportunity_tape_summary"]["top_product_counts"]["NEXT-USD"], 1)
            write_reports(payload, json_path=root / "out.json", csv_path=root / "out.csv", md_path=root / "out.md")
            report = (root / "out.md").read_text(encoding="utf-8")
            self.assertIn("rotate_to_challenger", report)
            self.assertIn("5%/Hour Banking Target", report)

    def test_build_payload_skips_timing_cooloff_top_challenger(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_path = root / "state.json"
            strategy_path = root / "strategy.json"
            tape_path = root / "tape.jsonl"
            state_path.write_text(
                json.dumps(
                    {
                        "state": {
                            "cash_usd": 10.0,
                            "starting_cash_usd": 50.0,
                            "realized_net_usd": -5.0,
                            "taker_fee_bps": 120.0,
                            "rotation_buffer_pct": 0.5,
                            "entry_confirmation_polls": 2,
                            "target_pressure_min_entry_edge_pct": 3.0,
                            "target_pressure_min_live_move_bps": 5.0,
                            "target_pressure_live_override_bps": 12.0,
                            "target_pressure_live_override_min_edge_pct": 1.25,
                            "candidate_streaks": {"BLOCKED-USD": 4, "NEXT-USD": 4, "HOLD-USD": 4},
                            "live_momentum": {
                                "BLOCKED-USD": {"samples": 2, "move_bps": 10.0, "live_move_streak": 2},
                                "NEXT-USD": {"samples": 2, "move_bps": 10.0, "live_move_streak": 2},
                                "HOLD-USD": {"samples": 2, "move_bps": 10.0, "live_move_streak": 2},
                            },
                            "ghost_timing_cooloff_min_closes": 3,
                            "ghost_timing_cooloff_max_avg_loss_pct": 3.0,
                            "ghost_stats": {
                                "BLOCKED-USD": {"closes": 3, "wins": 0, "losses": 3, "net_pct": -12.0},
                            },
                            "ghost_positions": {
                                "BLOCKED-USD": {"highest_bid": 1.05},
                            },
                            "position": {
                                "product_id": "HOLD-USD",
                                "playbook": "hot_potato_hour_rotation",
                                "entry_price": 1.0,
                                "quantity": 39.52,
                                "cost_usd": 40.0,
                                "entry_fee": 0.48,
                                "opened_at": "2026-04-23T00:00:00+00:00",
                                "highest_bid": 1.01,
                                "trail_giveback_pct": 1.0,
                                "entry_edge_over_hurdle_pct": 1.0,
                                "max_net_pnl": -0.48,
                                "max_net_pct_on_cost": -1.2,
                            },
                        },
                        "runner": {"pid": 123, "shadow_only": True},
                    }
                ),
                encoding="utf-8",
            )
            strategy_path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "rank": 1,
                                "product_id": "BLOCKED-USD",
                                "playbook": "fee_hurdle_breakout_trailer",
                                "hurdle_state": "clears_fast_hurdle",
                                "machinegun_score": 30.0,
                                "edge_over_hurdle_pct": 10.0,
                                "ret_15m_pct": 4.0,
                                "ret_60m_pct": 6.0,
                                "spread_bps": 5.0,
                                "trail_giveback_pct": 0.5,
                            },
                            {
                                "rank": 2,
                                "product_id": "NEXT-USD",
                                "playbook": "hot_potato_hour_rotation",
                                "hurdle_state": "clears_hour_hurdle",
                                "machinegun_score": 20.0,
                                "edge_over_hurdle_pct": 5.0,
                                "ret_15m_pct": 2.0,
                                "ret_60m_pct": 5.0,
                                "spread_bps": 5.0,
                                "trail_giveback_pct": 0.5,
                            },
                            {
                                "rank": 3,
                                "product_id": "HOLD-USD",
                                "playbook": "hot_potato_hour_rotation",
                                "hurdle_state": "clears_hour_hurdle",
                                "machinegun_score": 5.0,
                                "edge_over_hurdle_pct": 1.0,
                                "ret_15m_pct": 1.0,
                                "ret_60m_pct": 1.0,
                                "spread_bps": 4.0,
                                "trail_giveback_pct": 1.0,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            tape_path.write_text("", encoding="utf-8")
            payload = build_payload(
                state_path=state_path,
                strategy_path=strategy_path,
                opportunity_tape_path=tape_path,
                no_live_tick=True,
                tape_limit=10,
            )
            self.assertEqual(payload["rotation_review"]["decision"], "rotate_to_challenger")
            self.assertEqual(payload["rotation_review"]["best_challenger_product_id"], "NEXT-USD")
            blocked = next(row for row in payload["strategy_top"] if row["product_id"] == "BLOCKED-USD")
            self.assertEqual(blocked["admission_state"], "blocked")
            self.assertIn("ghost_timing_cooloff", blocked["admission_reason"])
            write_reports(payload, json_path=root / "out.json", csv_path=root / "out.csv", md_path=root / "out.md")
            report = (root / "out.md").read_text(encoding="utf-8")
            self.assertIn("ghost_timing_cooloff", report)


if __name__ == "__main__":
    unittest.main()
