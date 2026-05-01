#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_ratio_forward_review as review


class CoinbaseRatioForwardReviewTests(unittest.TestCase):
    def test_classify_seeded_flat_without_closes(self) -> None:
        row = {
            "realized_closes": 0,
            "realized_net_usd": 0.0,
            "open_count": 0,
        }
        status, note = review.classify_forward_row(row)
        self.assertEqual(status, "seeded_flat")
        self.assertIn("too few closes", note)

    def test_build_row_marks_bootstrap_positive(self) -> None:
        row = review.build_row(
            {
                "pair": "CFG/ETH",
                "stats": {
                    "realized_pnl_den": 0.00052,
                    "realized_pnl_usd_mark": 1.1612,
                    "total_closes": 4,
                    "wins": 4,
                    "losses": 0,
                },
                "positions": [{"level_idx": 0}],
                "account": {
                    "parked_den_units": 0.009,
                    "total_equity_usd_mark": 26.12,
                },
                "market": {
                    "last_ratio": 0.0000821,
                },
                "runner": {
                    "heartbeat_at": "2026-04-13T17:00:00+00:00",
                },
            },
            lane_name="shadow_coinbase_cfgeth_ratio_sleeve",
        )
        self.assertEqual(row["lane_name"], "shadow_coinbase_cfgeth_ratio_sleeve")
        self.assertEqual(row["forward_status"], "bootstrap_positive")
        self.assertEqual(row["open_count"], 1)

    def test_iter_ratio_lanes_filters_registry_to_ratio_sleeves(self) -> None:
        rows = review.iter_ratio_lanes(
            {
                "lanes": [
                    {"name": "shadow_coinbase_cfgeth_ratio_sleeve", "kind": "shadow_coinbase_spot", "state_path": "reports/a.json"},
                    {"name": "shadow_coinbase_cfgbtc_ratio_sleeve", "kind": "shadow_coinbase_spot", "state_path": "reports/b.json"},
                    {"name": "shadow_coinbase_experimental_misc", "kind": "shadow_coinbase_spot", "state_path": "reports/c.json"},
                    {"name": "shadow_btcusd_h1_step30", "kind": "shadow_crypto_candidate", "state_path": "reports/d.json"},
                ]
            }
        )
        self.assertEqual(
            rows,
            [
                {"lane_name": "shadow_coinbase_cfgbtc_ratio_sleeve", "state_path": "reports/b.json"},
                {"lane_name": "shadow_coinbase_cfgeth_ratio_sleeve", "state_path": "reports/a.json"},
            ],
        )

    def test_build_rows_loads_all_ratio_lane_payloads(self) -> None:
        registry_rows = [
            {"lane_name": "shadow_coinbase_cfgbtc_ratio_sleeve", "state_path": "reports/cfg_btc.json"},
            {"lane_name": "shadow_coinbase_cfgeth_ratio_sleeve", "state_path": "reports/cfg_eth.json"},
        ]
        payloads = {
            str(review.ROOT / "reports/cfg_btc.json"): {
                "pair": "CFG/BTC",
                "stats": {"realized_pnl_den": 0.001, "realized_pnl_usd_mark": 0.5, "total_closes": 1},
                "positions": [],
                "account": {"parked_den_units": 0.0002, "total_equity_usd_mark": 25.1},
                "market": {"last_ratio": 0.0000034},
            },
            str(review.ROOT / "reports/cfg_eth.json"): {
                "pair": "CFG/ETH",
                "stats": {"realized_pnl_den": 0.0, "realized_pnl_usd_mark": 0.0, "total_closes": 0},
                "positions": [],
                "account": {"parked_den_units": 0.01, "total_equity_usd_mark": 24.8},
                "market": {"last_ratio": 0.00009},
            },
        }

        def fake_load_json(path: Path) -> dict[str, object]:
            return payloads.get(str(path), {})

        with patch.object(review, "load_json", side_effect=fake_load_json):
            rows = review.build_rows(registry_rows)

        self.assertEqual([row["pair"] for row in rows], ["CFG/BTC", "CFG/ETH"])
        self.assertEqual(rows[0]["lane_name"], "shadow_coinbase_cfgbtc_ratio_sleeve")
        self.assertEqual(rows[1]["forward_status"], "seeded_flat")


if __name__ == "__main__":
    unittest.main()
