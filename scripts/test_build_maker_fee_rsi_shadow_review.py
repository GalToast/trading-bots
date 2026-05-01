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

import build_maker_fee_rsi_shadow_review as review


class MakerFeeRsiShadowReviewTests(unittest.TestCase):
    def test_marks_open_position_after_taker_exit_fee(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state = root / "state.json"
            events = root / "events.jsonl"
            state.write_text(
                json.dumps(
                    {
                        "cash_usd": 20,
                        "realized_net_usd": 0,
                        "realized_closes": 0,
                        "maker_fee_bps": 60,
                        "taker_fee_bps": 120,
                        "exit_mode": "maker_taker",
                        "open_positions": {
                            "SPX-USD": {
                                "product_id": "SPX-USD",
                                "entry_price": 1.0,
                                "quantity": 79.52,
                                "cost_usd": 80.0,
                                "entry_fee": 0.48,
                                "highest_price": 1.03,
                                "target_pct": 5,
                                "stop_pct": 1,
                                "entry_rsi": 20,
                                "max_hold_bars": 24,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            events.write_text(json.dumps({"event": "entry", "product_id": "SPX-USD"}) + "\n", encoding="utf-8")
            payload = review.build_payload(
                state_path=state,
                events_path=events,
                ticks={"SPX-USD": {"bid": 1.02, "ask": 1.021, "mid": 1.0205}},
            )

        self.assertEqual(payload["summary"]["proof_verdict"], "open_proof_collecting")
        self.assertEqual(payload["summary"]["open_positions"], 1)
        self.assertGreater(payload["positions"][0]["gross_mfe_pct"], payload["positions"][0]["gross_move_pct"])
        self.assertLess(payload["positions"][0]["net_pct_now"], payload["positions"][0]["gross_move_pct"])


if __name__ == "__main__":
    unittest.main()
