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

import build_kraken_maker_next_proof_board as board


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class KrakenMakerNextProofBoardTests(unittest.TestCase):
    def test_collect_more_until_ratio50_matures(self) -> None:
        ratio = board.ratio50_readiness(
            {
                "realized_closes": 7,
                "losses": 0,
                "ghost_marks": 12,
                "open_positions": 0,
                "realized_net_usd": 2.7,
                "reasons": ["needs_20_closes", "needs_20_ghost_marks"],
            },
            {},
            min_closes=20,
            min_ghost_marks=20,
        )

        self.assertEqual(ratio["status"], "collect_more")
        self.assertEqual(ratio["next_action"], "keep_ratio50_running_until_20_clean_closes_and_20_ghost_marks")
        self.assertEqual(ratio["closes_remaining"], 13)
        self.assertEqual(ratio["ghost_marks_remaining"], 8)

    def test_ready_state_points_to_parallel_ratio50_shadow(self) -> None:
        ratio = board.ratio50_readiness(
            {
                "realized_closes": 20,
                "losses": 0,
                "ghost_marks": 25,
                "open_positions": 0,
                "realized_net_usd": 8.0,
                "reasons": [],
            },
            {},
            min_closes=20,
            min_ghost_marks=20,
        )

        self.assertEqual(ratio["status"], "ready_for_parallel_ratio50_shadow")
        self.assertEqual(ratio["next_action"], "launch_parallel_ratio50_shadow_only")

    def test_loss_blocks_next_launch(self) -> None:
        ratio = board.ratio50_readiness(
            {
                "realized_closes": 8,
                "losses": 1,
                "ghost_marks": 20,
                "open_positions": 0,
                "realized_net_usd": 2.0,
                "reasons": ["loss_limit_exceeded"],
            },
            {},
            min_closes=20,
            min_ghost_marks=20,
        )

        self.assertEqual(ratio["status"], "failed_red_packet")
        self.assertEqual(ratio["next_action"], "autopsy_ratio50_loss_before_parallel_or_sizing")

    def test_build_payload_joins_gate_comparison_and_hot_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate_path = root / "gate.json"
            comparison_path = root / "comparison.json"
            hot_path = root / "hot.json"
            write_json(
                gate_path,
                {
                    "generated_at": "2026-04-25T00:00:00+00:00",
                    "lanes": [
                        {
                            "lane": "cooldown_ratio50",
                            "gate": "collect_more",
                            "realized_closes": 7,
                            "losses": 0,
                            "ghost_marks": 12,
                            "open_positions": 0,
                            "realized_net_usd": 2.7,
                            "reasons": ["needs_20_closes"],
                        },
                        {"lane": "cooldown_only", "gate": "do_not_promote"},
                    ],
                },
            )
            write_json(comparison_path, {"lanes": []})
            write_json(
                hot_path,
                {
                    "rows": [
                        {"product_id": "FOLKS-USD", "classification": "admitted_now"},
                        {"product_id": "HOUSE-USD", "classification": "reentry_blocked"},
                    ]
                },
            )

            payload = board.build_payload(gate_path=gate_path, comparison_path=comparison_path, hot_scan_path=hot_path)

            self.assertEqual(payload["summary"]["primary_status"], "collect_more")
            self.assertEqual(payload["summary"]["blocked_lanes"], ["cooldown_only"])
            self.assertEqual(payload["summary"]["admitted_now"], ["FOLKS-USD"])
            self.assertEqual(payload["summary"]["reentry_blocked"], ["HOUSE-USD"])
            self.assertEqual(payload["summary"]["next_shadow_command"], "")
            self.assertIn("--systemic-selection-limit 3", payload["summary"]["pending_next_shadow_command_after_maturity"])
            self.assertIn("ratio50_size12", payload["summary"]["alternate_next_shadow_command_after_maturity"])
            self.assertIn("--max-quote-usd 12.0", payload["summary"]["alternate_next_shadow_command_after_maturity"])
            self.assertIn("live_exec_dds25", payload["summary"]["isolated_dds25_shadow_command_after_fast_cooldown_maturity"])
            self.assertIn("--enable-dds", payload["summary"]["isolated_dds25_shadow_command_after_fast_cooldown_maturity"])
            self.assertIn("--max-quote-usd 25.0", payload["summary"]["isolated_dds25_shadow_command_after_fast_cooldown_maturity"])

    def test_ready_payload_exposes_parallel_ratio50_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate_path = root / "gate.json"
            comparison_path = root / "comparison.json"
            hot_path = root / "hot.json"
            write_json(
                gate_path,
                {
                    "lanes": [
                        {
                            "lane": "cooldown_ratio50",
                            "gate": "eligible_for_next_shadow_stage",
                            "realized_closes": 20,
                            "losses": 0,
                            "ghost_marks": 25,
                            "open_positions": 0,
                            "realized_net_usd": 8.0,
                            "reasons": [],
                        }
                    ],
                },
            )
            write_json(comparison_path, {"lanes": []})
            write_json(hot_path, {"rows": []})

            payload = board.build_payload(gate_path=gate_path, comparison_path=comparison_path, hot_scan_path=hot_path)

            command = payload["summary"]["next_shadow_command"]
            self.assertEqual(payload["summary"]["primary_status"], "ready_for_parallel_ratio50_shadow")
            self.assertIn("--systemic-max-positions 3", command)
            self.assertIn("parallel_ratio50", command)
            self.assertIn("--max-quote-usd 8.0", command)
            self.assertIn("--enforce-min-notional", command)
            self.assertIn("--systemic-min-live-to-board-spread-ratio 0.50", command)
            self.assertIn("cooldown_ratio50_size12", payload["summary"]["alternate_next_shadow_command_after_maturity"])

    def test_launched_parallel_ratio50_becomes_primary_monitor_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate_path = root / "gate.json"
            comparison_path = root / "comparison.json"
            hot_path = root / "hot.json"
            parallel_events = root / "parallel-events.jsonl"
            parallel_events.write_text("", encoding="utf-8")
            write_json(
                gate_path,
                {
                    "lanes": [
                        {
                            "lane": "cooldown_ratio50",
                            "gate": "eligible_for_next_shadow_stage",
                            "realized_closes": 20,
                            "losses": 0,
                            "ghost_marks": 25,
                            "open_positions": 0,
                            "realized_net_usd": 8.0,
                            "reasons": [],
                        },
                        {
                            "lane": "parallel_ratio50",
                            "gate": "collect_more",
                            "realized_closes": 0,
                            "losses": 0,
                            "ghost_marks": 0,
                            "open_positions": 0,
                            "max_concurrent_positions": 0,
                            "realized_net_usd": 0.0,
                            "reasons": ["needs_20_closes", "needs_20_ghost_marks", "parallel_not_exercised"],
                        },
                    ],
                },
            )
            write_json(
                comparison_path,
                {
                    "lanes": [
                        {
                            "lane": "parallel_ratio50",
                            "events_path": str(parallel_events),
                            "state_path": str(root / "parallel-state.json"),
                            "realized_closes": 0,
                            "losses": 0,
                            "open_positions": 0,
                            "max_concurrent_positions": 0,
                            "realized_net_usd": 0.0,
                        }
                    ]
                },
            )
            write_json(hot_path, {"rows": []})

            payload = board.build_payload(gate_path=gate_path, comparison_path=comparison_path, hot_scan_path=hot_path)

            self.assertEqual(payload["summary"]["primary_lane"], "parallel_ratio50")
            self.assertEqual(payload["summary"]["primary_status"], "collect_more")
            self.assertEqual(payload["summary"]["next_shadow_command"], "")
            self.assertTrue(payload["parallel_ratio50"]["started"])

    def test_failed_parallel_ratio50_exposes_taker_guard_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate_path = root / "gate.json"
            comparison_path = root / "comparison.json"
            hot_path = root / "hot.json"
            parallel_events = root / "parallel-events.jsonl"
            parallel_events.write_text("", encoding="utf-8")
            write_json(
                gate_path,
                {
                    "lanes": [
                        {
                            "lane": "cooldown_ratio50",
                            "gate": "eligible_for_next_shadow_stage",
                            "realized_closes": 20,
                            "losses": 0,
                            "ghost_marks": 25,
                            "open_positions": 0,
                            "realized_net_usd": 8.0,
                            "reasons": [],
                        },
                        {
                            "lane": "parallel_ratio50",
                            "gate": "do_not_promote",
                            "realized_closes": 21,
                            "losses": 1,
                            "ghost_marks": 40,
                            "open_positions": 0,
                            "max_concurrent_positions": 3,
                            "realized_net_usd": 8.7,
                            "reasons": ["loss_limit_exceeded"],
                        },
                    ],
                },
            )
            write_json(
                comparison_path,
                {
                    "lanes": [
                        {
                            "lane": "parallel_ratio50",
                            "events_path": str(parallel_events),
                            "state_path": str(root / "parallel-state.json"),
                            "realized_closes": 21,
                            "losses": 1,
                            "open_positions": 0,
                            "max_concurrent_positions": 3,
                            "realized_net_usd": 8.7,
                        }
                    ]
                },
            )
            write_json(hot_path, {"rows": []})

            payload = board.build_payload(gate_path=gate_path, comparison_path=comparison_path, hot_scan_path=hot_path)

            command = payload["summary"]["next_shadow_command"]
            self.assertEqual(payload["summary"]["primary_lane"], "parallel_ratio50")
            self.assertEqual(payload["summary"]["primary_status"], "failed_red_packet")
            self.assertIn("parallel_ratio50_taker_guard", command)
            self.assertIn("--systemic-max-positions 3", command)
            self.assertIn("corrected taker-insurance rerun", payload["summary"]["read"])

    def test_dds25_started_does_not_steal_primary_before_fast_cooldown_matures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate_path = root / "gate.json"
            comparison_path = root / "comparison.json"
            hot_path = root / "hot.json"
            fast_events = root / "fast-events.jsonl"
            dds_events = root / "dds-events.jsonl"
            fast_events.write_text("", encoding="utf-8")
            dds_events.write_text("", encoding="utf-8")
            write_json(
                gate_path,
                {
                    "lanes": [
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
                            "gate": "collect_more",
                            "realized_closes": 10,
                            "losses": 0,
                            "ghost_marks": 35,
                            "open_positions": 0,
                            "max_concurrent_positions": 3,
                            "realized_net_usd": 3.8,
                            "reasons": ["needs_20_closes"],
                        },
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_dds25",
                            "gate": "collect_more",
                            "realized_closes": 0,
                            "losses": 0,
                            "ghost_marks": 0,
                            "open_positions": 0,
                            "max_concurrent_positions": 0,
                            "realized_net_usd": 0.0,
                            "reasons": ["needs_20_closes", "needs_20_ghost_marks"],
                        },
                    ],
                },
            )
            write_json(
                comparison_path,
                {
                    "lanes": [
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
                            "events_path": str(fast_events),
                            "state_path": str(root / "fast-state.json"),
                            "realized_closes": 10,
                            "losses": 0,
                            "open_positions": 0,
                            "max_concurrent_positions": 3,
                            "realized_net_usd": 3.8,
                        },
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_dds25",
                            "events_path": str(dds_events),
                            "state_path": str(root / "dds-state.json"),
                            "realized_closes": 0,
                            "losses": 0,
                            "open_positions": 0,
                            "max_concurrent_positions": 0,
                            "realized_net_usd": 0.0,
                        },
                    ]
                },
            )
            write_json(hot_path, {"rows": []})

            payload = board.build_payload(gate_path=gate_path, comparison_path=comparison_path, hot_scan_path=hot_path)

            self.assertEqual(payload["summary"]["primary_lane"], "parallel_ratio50_taker_guard_live_exec_fast_cooldown")
            self.assertTrue(payload["parallel_ratio50_taker_guard_live_exec_dds25"]["started"])

    def test_failed_dds25_does_not_steal_primary_after_fast_cooldown_matures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate_path = root / "gate.json"
            comparison_path = root / "comparison.json"
            hot_path = root / "hot.json"
            fast_events = root / "fast-events.jsonl"
            dds_events = root / "dds-events.jsonl"
            fast_events.write_text("", encoding="utf-8")
            dds_events.write_text("", encoding="utf-8")
            write_json(
                gate_path,
                {
                    "lanes": [
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
                            "gate": "eligible_for_next_shadow_stage",
                            "realized_closes": 38,
                            "losses": 0,
                            "ghost_marks": 140,
                            "open_positions": 0,
                            "max_concurrent_positions": 3,
                            "realized_net_usd": 13.8,
                            "reasons": [],
                        },
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_dds25",
                            "gate": "do_not_promote",
                            "realized_closes": 28,
                            "losses": 2,
                            "ghost_marks": 102,
                            "open_positions": 0,
                            "max_concurrent_positions": 3,
                            "realized_net_usd": 12.5,
                            "reasons": ["loss_limit_exceeded"],
                        },
                    ],
                },
            )
            write_json(
                comparison_path,
                {
                    "lanes": [
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
                            "events_path": str(fast_events),
                            "state_path": str(root / "fast-state.json"),
                            "realized_closes": 38,
                            "losses": 0,
                            "open_positions": 0,
                            "max_concurrent_positions": 3,
                            "realized_net_usd": 13.8,
                        },
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_dds25",
                            "events_path": str(dds_events),
                            "state_path": str(root / "dds-state.json"),
                            "realized_closes": 28,
                            "losses": 2,
                            "open_positions": 0,
                            "max_concurrent_positions": 3,
                            "realized_net_usd": 12.5,
                        },
                    ]
                },
            )
            write_json(hot_path, {"rows": []})

            payload = board.build_payload(gate_path=gate_path, comparison_path=comparison_path, hot_scan_path=hot_path)

            self.assertEqual(payload["summary"]["primary_lane"], "parallel_ratio50_taker_guard_live_exec_fast_cooldown")
            self.assertEqual(payload["summary"]["primary_status"], "ready_for_next_shadow_stage")
            self.assertIn("dds25_fixed", payload["summary"]["next_shadow_command"])
            self.assertIn("dds25_fixed_texas_safe_epoch1", payload["summary"]["next_shadow_command"])
            self.assertIn("--systemic-exclude-products FOLKS-USD", payload["summary"]["next_shadow_command"])
            self.assertNotIn(
                "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_ab_state.json",
                payload["summary"]["next_shadow_command"],
            )
            self.assertIn("--max-quote-usd 25.0", payload["summary"]["next_shadow_command"])
            self.assertEqual(payload["parallel_ratio50_taker_guard_live_exec_dds25"]["status"], "failed_red_packet")

    def test_started_dds25_fixed_stays_primary_while_predecessor_keeps_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate_path = root / "gate.json"
            comparison_path = root / "comparison.json"
            hot_path = root / "hot.json"
            fast_events = root / "fast-events.jsonl"
            fixed_events = root / "fixed-events.jsonl"
            fast_events.write_text("", encoding="utf-8")
            fixed_events.write_text("", encoding="utf-8")
            write_json(
                gate_path,
                {
                    "lanes": [
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
                            "gate": "collect_more",
                            "realized_closes": 45,
                            "losses": 0,
                            "ghost_marks": 175,
                            "open_positions": 1,
                            "max_concurrent_positions": 3,
                            "realized_net_usd": 16.3,
                            "reasons": ["open_residue"],
                        },
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_dds25_fixed",
                            "gate": "collect_more",
                            "realized_closes": 3,
                            "losses": 0,
                            "ghost_marks": 10,
                            "open_positions": 0,
                            "max_concurrent_positions": 1,
                            "realized_net_usd": 1.99,
                            "reasons": ["needs_20_closes", "needs_20_ghost_marks", "parallel_not_exercised"],
                        },
                    ],
                },
            )
            write_json(
                comparison_path,
                {
                    "lanes": [
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
                            "events_path": str(fast_events),
                            "state_path": str(root / "fast-state.json"),
                            "realized_closes": 45,
                            "losses": 0,
                            "open_positions": 1,
                            "max_concurrent_positions": 3,
                            "realized_net_usd": 16.3,
                        },
                        {
                            "lane": "parallel_ratio50_taker_guard_live_exec_dds25_fixed",
                            "events_path": str(fixed_events),
                            "state_path": str(root / "fixed-state.json"),
                            "realized_closes": 3,
                            "losses": 0,
                            "open_positions": 0,
                            "max_concurrent_positions": 1,
                            "realized_net_usd": 1.99,
                        },
                    ]
                },
            )
            write_json(hot_path, {"rows": []})

            payload = board.build_payload(gate_path=gate_path, comparison_path=comparison_path, hot_scan_path=hot_path)

            self.assertEqual(payload["summary"]["primary_lane"], "parallel_ratio50_taker_guard_live_exec_dds25_fixed")
            self.assertEqual(payload["summary"]["primary_status"], "collect_more")
            self.assertEqual(payload["summary"]["next_shadow_command"], "")

    def test_write_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = board.build_payload(
                gate_path=root / "missing-gate.json",
                comparison_path=root / "missing-comparison.json",
                hot_scan_path=root / "missing-hot.json",
            )
            json_path = root / "next.json"
            md_path = root / "next.md"

            board.write_reports(payload, json_path=json_path, md_path=md_path)

            self.assertTrue(json_path.exists())
            self.assertIn("Kraken Maker Next Proof Board", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
