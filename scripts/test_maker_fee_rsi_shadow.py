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

import maker_fee_rsi_shadow as runner


class MakerFeeRsiShadowTests(unittest.TestCase):
    def test_loads_current_maker_reality_survivors_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "maker_reality.json"
            path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {"product_id": "SPX-USD", "current_verdict": "maker_taker_shadow_probe"},
                            {"product_id": "ZRX-USD", "current_verdict": "maker_maker_only_needs_exit_fill_proof"},
                            {"product_id": "TREE-USD", "current_verdict": "reject_post_only_fill_risk"},
                            {"product_id": "FLOCK-USD", "zero_maker_verdict": "maker_taker_shadow_probe"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            old_path = runner.MAKER_REALITY_BOARD
            runner.MAKER_REALITY_BOARD = path
            try:
                products = runner.load_maker_reality_products("current")
                product_ids = {row["product_id"] for row in products}
            finally:
                runner.MAKER_REALITY_BOARD = old_path

        self.assertEqual(product_ids, {"SPX-USD", "ZRX-USD"})

    def test_maker_taker_exit_charges_taker_fee(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            engine = runner.MakerFeeRsiEngine(
                starting_cash_usd=100.0,
                deploy_pct=0.8,
                maker_fee_bps=60.0,
                taker_fee_bps=120.0,
                exit_mode="maker_taker",
                profit_target_pct=1.0,
                stop_loss_pct=1.0,
                max_hold_bars=24,
            )
            for price in [1.0, 0.99, 0.98, 0.97, 0.96]:
                engine.update_price("SPX-USD", price)
            self.assertTrue(engine.open_position("SPX-USD", 1.0, event_path))
            engine.update_price("SPX-USD", 1.02)
            self.assertTrue(engine.check_exit("SPX-USD", 1.02, event_path))
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]

        close = events[-1]
        self.assertEqual(close["event"], "exit")
        self.assertEqual(close["exit_fee_bps"], 120.0)
        self.assertEqual(close["entry_fee_bps"], 60.0)
        self.assertEqual(close["exit_mode"], "maker_taker")


if __name__ == "__main__":
    unittest.main()
