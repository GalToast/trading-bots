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

import build_kraken_live_fill_telemetry_board as board


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class KrakenLiveFillTelemetryBoardTests(unittest.TestCase):
    def test_rescue_exit_blocks_autonomous_promotion_even_when_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.jsonl"
            latest_path = root / "latest.json"
            write_jsonl(
                events_path,
                [
                    {
                        "action": "live_roundtrip_entry_submit_attempt",
                        "product_id": "TRAC-USD",
                    },
                    {
                        "action": "live_roundtrip_entry_submitted",
                        "product_id": "TRAC-USD",
                        "txid": "ENTRY",
                    },
                    {
                        "action": "live_roundtrip_exit_submitted",
                        "product_id": "TRAC-USD",
                        "txid": "TARGET",
                    },
                    {
                        "action": "live_roundtrip_manual_breakeven_exit_submitted",
                        "product_id": "TRAC-USD",
                        "txid": "RESCUE",
                    },
                ],
            )
            write_json(
                latest_path,
                {
                    "product_id": "TRAC-USD",
                    "entry_txid": "ENTRY",
                    "exit_txid": "TARGET",
                    "entry_status": {
                        "status": "closed",
                        "descr": {"type": "buy"},
                        "opentm": 100.0,
                        "closetm": 108.0,
                        "cost": "7.00",
                        "fee": "0.016",
                        "price": "0.3240",
                        "vol": "21.6",
                        "vol_exec": "21.6",
                    },
                    "exit_status": {
                        "status": "canceled",
                        "reason": "User requested",
                        "descr": {"type": "sell"},
                        "opentm": 110.0,
                        "closetm": 290.0,
                        "cost": "0.0",
                        "fee": "0.0",
                        "price": "0.0",
                        "vol": "21.6",
                        "vol_exec": "0.0",
                    },
                },
            )
            statuses = {
                "ENTRY": {
                    "status": "closed",
                    "descr": {"type": "buy"},
                    "opentm": 100.0,
                    "closetm": 108.0,
                    "cost": "7.00",
                    "fee": "0.016",
                    "price": "0.3240",
                    "vol": "21.6",
                    "vol_exec": "21.6",
                },
                "TARGET": {
                    "status": "canceled",
                    "reason": "User requested",
                    "descr": {"type": "sell"},
                    "opentm": 110.0,
                    "closetm": 290.0,
                    "cost": "0.0",
                    "fee": "0.0",
                    "price": "0.0",
                    "vol": "21.6",
                    "vol_exec": "0.0",
                },
                "RESCUE": {
                    "status": "closed",
                    "descr": {"type": "sell"},
                    "opentm": 300.0,
                    "closetm": 410.0,
                    "cost": "7.04",
                    "fee": "0.016",
                    "price": "0.3260",
                    "vol": "21.6",
                    "vol_exec": "21.6",
                },
            }
            original_query_orders = board.query_orders
            original_query_balances = board.query_balances
            try:
                board.query_orders = lambda _txids: statuses
                board.query_balances = lambda: {"ZUSD": "9.97"}
                payload = board.build_payload(
                    events_path=events_path,
                    latest_path=latest_path,
                    query_private=True,
                )
            finally:
                board.query_orders = original_query_orders
                board.query_balances = original_query_balances

            self.assertEqual(payload["summary"]["complete_live_roundtrips"], 1)
            self.assertEqual(payload["summary"]["green_after_fees"], 1)
            self.assertGreater(payload["summary"]["net_usd"], 0.0)
            self.assertEqual(payload["products"][0]["verdict"], "blocked_for_autonomous_live")
            self.assertIn("profit_target_exit_miss_observed", payload["summary"]["promotion_blockers"])
            self.assertIn("manual_or_rescue_exit_observed", payload["summary"]["promotion_blockers"])

    def test_book_snapshots_satisfy_microstructure_observability_fields(self) -> None:
        events = [
            {"action": "live_roundtrip_entry_submit_attempt", "product_id": "A-USD"},
            {"action": "live_roundtrip_entry_submitted", "product_id": "A-USD", "txid": "ENTRY"},
            {
                "action": "live_roundtrip_book_snapshot",
                "snapshot_label": "entry_order_submitted",
                "product_id": "A-USD",
                "txid": "ENTRY",
                "book_l10_imbalance_ratio": 1.7,
            },
            {"action": "live_roundtrip_exit_submitted", "product_id": "A-USD", "txid": "EXIT"},
            {
                "action": "live_roundtrip_book_snapshot",
                "snapshot_label": "exit_order_submitted",
                "product_id": "A-USD",
                "txid": "EXIT",
                "book_sell_vwap": 1.02,
            },
        ]
        statuses = {
            "ENTRY": {
                "status": "closed",
                "descr": {"type": "buy"},
                "opentm": 100.0,
                "closetm": 101.0,
                "cost": "10.00",
                "fee": "0.025",
                "price": "1.00",
                "vol": "10.0",
                "vol_exec": "10.0",
            },
            "EXIT": {
                "status": "closed",
                "descr": {"type": "sell"},
                "opentm": 102.0,
                "closetm": 103.0,
                "cost": "10.10",
                "fee": "0.025",
                "price": "1.01",
                "vol": "10.0",
                "vol_exec": "10.0",
            },
        }

        cycles = board.build_cycles(events, {"product_id": "A-USD"}, statuses)

        self.assertEqual(cycles[0]["observability_gaps"], [])
        self.assertEqual(cycles[0]["ghost_ratio_at_entry"], 1.7)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
