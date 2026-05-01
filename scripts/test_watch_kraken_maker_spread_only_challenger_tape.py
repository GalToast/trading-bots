#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_maker_spread_only_challenger_review as review
import watch_kraken_maker_spread_only_challenger_tape as watcher


class KrakenMakerSpreadOnlyChallengerTapeTests(unittest.TestCase):
    def test_candidate_filter_keeps_spread_only_low_mer(self) -> None:
        rows = [
            {"product_id": "BMB-USD", "playbook": "maker_harvest", "spread_bps": 520.0, "mer": 0.5, "vol_24h_usd": 1000.0},
            {"product_id": "HOUSE-USD", "playbook": "maker_harvest", "spread_bps": 350.0, "mer": 4.0, "vol_24h_usd": 1000.0},
            {"product_id": "DOG-USD", "playbook": "maker_harvest", "spread_bps": 80.0, "mer": 0.4, "vol_24h_usd": 1000.0},
        ]

        filtered = watcher.candidate_rows(rows, min_board_spread_bps=300.0, max_board_mer=2.0, min_vol_24h_usd=0.0)

        self.assertEqual([row["product_id"] for row in filtered], ["BMB-USD"])

    def test_exit_metrics_separate_harvest_from_liquidation(self) -> None:
        entry = watcher.make_entry(
            row={"product_id": "BMB-USD", "spread_bps": 500.0, "mer": 0.5},
            tick={"bid": 1.0, "ask": 1.05, "spread_bps": 487.8},
            now_iso="2026-04-25T00:00:00+00:00",
            now_epoch=100.0,
            quote_usd=4.0,
            maker_fee_bps=25.0,
            horizons=[30],
        )

        metrics = watcher.calc_exit_metrics(
            entry,
            {"bid": 1.0, "ask": 1.05, "spread_bps": 487.8},
            maker_fee_bps=25.0,
            taker_fee_bps=40.0,
        )

        self.assertLess(metrics["bid_taker_net_pct_on_cost"], 0.0)
        self.assertGreater(metrics["ask_maker_net_pct_on_cost"], 0.0)

    def test_fill_evidence_requires_public_touch_or_cross(self) -> None:
        entry = watcher.make_entry(
            row={"product_id": "BMB-USD", "spread_bps": 500.0, "mer": 0.5},
            tick={"bid": 1.0, "ask": 1.05, "last": 1.05, "spread_bps": 487.8},
            now_iso="2026-04-25T00:00:00+00:00",
            now_epoch=100.0,
            quote_usd=4.0,
            maker_fee_bps=25.0,
            horizons=[30],
        )

        no_fill = watcher.fill_evidence(entry, {"bid": 1.0, "ask": 1.04, "last": 1.03}, now_iso="x", now_epoch=110.0)
        last_fill = watcher.fill_evidence(entry, {"bid": 1.0, "ask": 1.04, "last": 1.0}, now_iso="x", now_epoch=110.0)
        bid_cross = watcher.fill_evidence(entry, {"bid": 0.99, "ask": 1.04, "last": 1.02}, now_iso="x", now_epoch=110.0)

        self.assertFalse(no_fill["fill_supported"])
        self.assertTrue(last_fill["fill_supported"])
        self.assertEqual(last_fill["fill_evidence_method"], "last_trade_at_or_below_entry_bid")
        self.assertTrue(bid_cross["fill_supported"])
        self.assertEqual(bid_cross["fill_evidence_method"], "best_bid_moved_through_entry_bid")

    def test_mark_pending_emits_stop_before_horizon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            entry = watcher.make_entry(
                row={"product_id": "BMB-USD", "spread_bps": 500.0, "mer": 0.5},
                tick={"bid": 1.0, "ask": 1.05, "spread_bps": 487.8},
                now_iso="2026-04-25T00:00:00+00:00",
                now_epoch=100.0,
                quote_usd=4.0,
                maker_fee_bps=25.0,
                horizons=[30],
            )
            state = {"pending": {entry["entry_id"]: entry}, "cooldowns": {}}

            emitted = watcher.mark_pending(
                state=state,
                ticks={"BMB-USD": {"bid": 0.99, "ask": 1.04, "spread_bps": 492.6}},
                now_iso="2026-04-25T00:00:10+00:00",
                now_epoch=110.0,
                event_path=event_path,
                maker_fee_bps=25.0,
                taker_fee_bps=40.0,
                stop_bid_taker_net_pct=-0.5,
                min_harvest_net_pct=0.10,
            )

            self.assertEqual(emitted, 2)
            self.assertEqual(state["pending"], {})
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["action"], "spread_only_challenger_stop")
            self.assertTrue(events[-1]["fill_supported"])

    def test_review_summarizes_horizon_marks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            rows = [
                {"action": "spread_only_challenger_open", "product_id": "BMB-USD", "entry_id": "BMB-USD:1"},
                {
                    "action": "spread_only_challenger_mark",
                    "product_id": "BMB-USD",
                    "entry_id": "BMB-USD:1",
                    "horizon_seconds": 30,
                    "fill_supported": True,
                    "ask_maker_net_pct_on_cost": 1.0,
                    "bid_taker_net_pct_on_cost": -0.4,
                    "ask_maker_net_usd": 0.04,
                    "bid_taker_net_usd": -0.016,
                    "spread_harvest_clears": True,
                },
            ]
            event_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            payload = review.build_payload(event_path)

            self.assertEqual(payload["summary"]["opens"], 1)
            self.assertEqual(payload["summary"]["horizons"]["30"]["marks"], 1)
            self.assertEqual(payload["summary"]["horizons"]["30"]["fill_supported_marks"], 1)
            self.assertEqual(payload["summary"]["horizons"]["30"]["harvest_clear_rate"], 1.0)

    def test_review_flags_no_fill_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            rows = [
                {"action": "spread_only_challenger_open", "product_id": "BMB-USD", "entry_id": "BMB-USD:1"},
                {"action": "spread_only_challenger_complete", "product_id": "BMB-USD", "entry_id": "BMB-USD:1", "fill_supported": False},
            ]
            event_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            payload = review.build_payload(event_path)

            self.assertEqual(payload["summary"]["missed_or_unproven_completes"], 1)
            self.assertEqual(payload["summary"]["verdict"], ["proof_has_no_public_fill_support_yet"])


if __name__ == "__main__":
    unittest.main()
