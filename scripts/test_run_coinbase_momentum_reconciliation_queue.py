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

import run_coinbase_momentum_reconciliation_queue as runner


def make_cache_payload(product_id: str) -> dict:
    candles = []
    base = 1776003900
    for idx in range(12):
        open_px = 9.0 + idx * 0.2
        close_px = open_px + 0.1
        high_px = close_px + 0.2
        low_px = open_px - 0.1
        candles.append(
            {
                "time": base + idx * 300,
                "open": round(open_px, 4),
                "high": round(high_px, 4),
                "low": round(low_px, 4),
                "close": round(close_px, 4),
                "volume": 1.0,
            }
        )
    return {"product_id": product_id, "candles": candles}


class RunCoinbaseMomentumReconciliationQueueTests(unittest.TestCase):
    def test_build_results_uses_cache_and_classifies_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            cache_dir = reports / "candle_cache"
            reports.mkdir(parents=True)
            cache_dir.mkdir(parents=True)

            (reports / "coinbase_momentum_reconciliation_queue.json").write_text(
                json.dumps(
                    {
                        "queue": [
                            {
                                "coin": "ALEPH-USD",
                                "strategy": "mom_10",
                                "priority": "reconcile_next",
                                "library_sweep_partial_14d_net_usd": 6.7,
                                "reason": "positive library-backed momentum result without 30d reconciliation yet",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (cache_dir / "ALEPH_USD_FIVE_MINUTE_30d.json").write_text(
                json.dumps(make_cache_payload("ALEPH-USD")),
                encoding="utf-8",
            )

            old_reports = runner.REPORTS
            old_queue = runner.QUEUE_PATH
            old_snapshot = runner.SNAPSHOT_PATH
            old_cache = runner.CACHE_DIR
            old_json = runner.JSON_PATH
            old_md = runner.MD_PATH
            try:
                runner.REPORTS = reports
                runner.QUEUE_PATH = reports / "coinbase_momentum_reconciliation_queue.json"
                runner.SNAPSHOT_PATH = reports / "reconciliation_candles.json"
                runner.CACHE_DIR = cache_dir
                runner.JSON_PATH = reports / "out.json"
                runner.MD_PATH = reports / "out.md"
                payload = runner.build_results(
                    [
                        {
                            "coin": "ALEPH-USD",
                            "strategy": "mom_10",
                            "priority": "reconcile_next",
                            "library_sweep_partial_14d_net_usd": 6.7,
                            "reason": "positive library-backed momentum result without 30d reconciliation yet",
                        }
                    ]
                )
            finally:
                runner.REPORTS = old_reports
                runner.QUEUE_PATH = old_queue
                runner.SNAPSHOT_PATH = old_snapshot
                runner.CACHE_DIR = old_cache
                runner.JSON_PATH = old_json
                runner.MD_PATH = old_md

        result = payload["results"][0]
        self.assertEqual(result["source"], "cache")
        self.assertEqual(result["verdict"], "confirmed_positive")
        self.assertEqual(result["strategy"], "mom_10")


if __name__ == "__main__":
    unittest.main()
