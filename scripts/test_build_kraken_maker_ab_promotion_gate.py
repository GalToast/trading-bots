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

import build_kraken_maker_ab_promotion_gate as gate


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class KrakenMakerAbPromotionGateTests(unittest.TestCase):
    def test_lane_eligible_when_all_gates_pass(self) -> None:
        row = gate.gate_lane(
            {
                "lane": "parallel_cooldown",
                "realized_closes": 20,
                "wins": 20,
                "losses": 0,
                "realized_net_usd": 8.0,
                "cash_usd": 108.0,
                "avg_net_pct": 5.0,
                "max_concurrent_positions": 3,
                "open_positions": 0,
                "risk_flags": [],
                "realized_net_per_hour": 10.0,
            },
            {
                "ghost_marks": 20,
                "verdict": "banking_supported",
                "avg_delta_net": -0.1,
            },
            min_closes=20,
            max_losses=0,
            min_ghost_marks=20,
            require_parallel_exercised=True,
        )

        self.assertEqual(row["gate"], "eligible_for_next_shadow_stage")
        self.assertEqual(row["reasons"], [])

    def test_lane_collects_when_immature_but_green(self) -> None:
        row = gate.gate_lane(
            {
                "lane": "cooldown_only",
                "realized_closes": 10,
                "wins": 10,
                "losses": 0,
                "realized_net_usd": 4.0,
                "cash_usd": 104.0,
                "avg_net_pct": 4.0,
                "max_concurrent_positions": 1,
                "open_positions": 0,
                "risk_flags": [],
            },
            {
                "ghost_marks": 40,
                "verdict": "banking_supported",
                "avg_delta_net": -0.1,
            },
            min_closes=20,
            max_losses=0,
            min_ghost_marks=20,
            require_parallel_exercised=False,
        )

        self.assertEqual(row["gate"], "collect_more")
        self.assertIn("needs_20_closes", row["reasons"])

    def test_fresh_zero_close_lane_collects_without_negative_verdict(self) -> None:
        row = gate.gate_lane(
            {
                "lane": "new_guard",
                "realized_closes": 0,
                "wins": 0,
                "losses": 0,
                "realized_net_usd": 0.0,
                "cash_usd": 100.0,
                "avg_net_pct": 0.0,
                "max_concurrent_positions": 0,
                "open_positions": 0,
                "risk_flags": [],
            },
            {
                "ghost_marks": 0,
                "verdict": "collect_no_ghost_marks",
                "avg_delta_net": 0.0,
            },
            min_closes=20,
            max_losses=0,
            min_ghost_marks=20,
            require_parallel_exercised=False,
        )

        self.assertEqual(row["gate"], "collect_more")
        self.assertIn("needs_20_closes", row["reasons"])
        self.assertNotIn("not_net_positive", row["reasons"])

    def test_build_payload_joins_comparison_and_ghost_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            comparison_path = root / "comparison.json"
            ghost_path = root / "ghost.json"
            write_json(
                comparison_path,
                {
                    "generated_at": "2026-04-25T00:00:00+00:00",
                    "lanes": [
                        {
                            "lane": "cooldown_only",
                            "realized_closes": 20,
                            "wins": 20,
                            "losses": 0,
                            "realized_net_usd": 8.0,
                            "cash_usd": 108.0,
                            "avg_net_pct": 4.0,
                            "max_concurrent_positions": 1,
                            "open_positions": 0,
                            "risk_flags": [],
                        }
                    ],
                },
            )
            write_json(
                ghost_path,
                {
                    "lanes": [
                        {
                            "lane": "cooldown_only",
                            "ghost_marks": 20,
                            "verdict": "banking_supported",
                            "avg_delta_net": -0.1,
                        }
                    ]
                },
            )

            payload = gate.build_payload(
                comparison_path=comparison_path,
                ghost_path=ghost_path,
                min_closes=20,
                min_ghost_marks=20,
            )

            self.assertEqual(payload["summary"]["eligible_lanes"], ["cooldown_only"])

    def test_write_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = gate.build_payload(
                comparison_path=root / "missing-comparison.json",
                ghost_path=root / "missing-ghost.json",
            )
            json_path = root / "gate.json"
            md_path = root / "gate.md"

            gate.write_reports(payload, json_path=json_path, md_path=md_path)

            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("Kraken Maker A/B Promotion Gate", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
