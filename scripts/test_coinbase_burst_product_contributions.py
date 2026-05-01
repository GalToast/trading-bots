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

import build_coinbase_burst_product_contributions as contributions


class CoinbaseBurstProductContributionsTests(unittest.TestCase):
    def test_aggregate_rows_rolls_up_lane_and_product_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configs = root / "configs"
            reports = root / "reports"
            configs.mkdir(parents=True)
            reports.mkdir(parents=True)

            registry = [
                {
                    "name": "shadow_coinbase_burst_roundrobin_best",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/shadow_coinbase_burst_roundrobin_best_state.json",
                    "event_path": "reports/shadow_coinbase_burst_roundrobin_best_events.jsonl",
                    "restart_args": ["scripts/burst_fade_roundrobin_shadow.py"],
                }
            ]
            (configs / "penetration_lattice_runner_registry.json").write_text(json.dumps(registry), encoding="utf-8")
            (reports / "shadow_coinbase_burst_roundrobin_best_events.jsonl").write_text(
                "\n".join(
                    [
                        "# header",
                        json.dumps({"action": "close_target", "product": "ALEPH-USD", "net": 12.0, "fees": 1.0, "burst_range": 2.5}),
                        json.dumps({"action": "close_stop", "product": "ALEPH-USD", "net": -5.0, "fees": 1.1, "burst_range": 1.5}),
                        json.dumps({"action": "close_target", "product": "BAL-USD", "net": 3.0, "fees": 0.8, "burst_range": 3.0}),
                    ]
                ),
                encoding="utf-8",
            )

            rows = contributions.aggregate_rows(registry_path=configs / "penetration_lattice_runner_registry.json")

        self.assertEqual(rows[0]["lane_name"], "ALL_BURST_LANES")
        self.assertEqual(rows[0]["product_id"], "ALEPH-USD")
        self.assertEqual(rows[0]["realized_net_usd"], 7.0)
        self.assertEqual(rows[0]["close_events"], 2)
        self.assertEqual(rows[2]["lane_name"], "shadow_coinbase_burst_roundrobin_best")
        self.assertEqual(rows[2]["product_id"], "ALEPH-USD")
        self.assertEqual(rows[2]["win_rate"], 50.0)


if __name__ == "__main__":
    unittest.main()
