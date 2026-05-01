#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_spot_cross_asset_leaderboard as leaderboard


class CoinbaseSpotCrossAssetLeaderboardTests(unittest.TestCase):
    def test_build_reports_combines_products_across_families_and_preserves_multi_product_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configs = root / "configs"
            reports = root / "reports"
            configs.mkdir(parents=True)
            reports.mkdir(parents=True)

            registry = [
                {
                    "name": "shadow_coinbase_arbusd_rsi7",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/coinbase_rsi_shadow_arbusd_state.json",
                    "restart_args": ["scripts/live_coinbase_rsi_shadow.py", "--product-id", "ARB-USD"],
                },
                {
                    "name": "shadow_coinbase_burst_balusd_live",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/shadow_coinbase_burst_balusd_live_state.json",
                    "restart_args": ["scripts/burst_fade_live_shadow.py", "--product-id", "BAL-USD"],
                },
                {
                    "name": "shadow_coinbase_burst_multicoin_rotation",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/shadow_coinbase_burst_multicoin_rotation_state.json",
                    "restart_args": ["scripts/burst_fade_multicoin_shadow.py"],
                },
                {
                    "name": "shadow_coinbase_experimental_rave_m15_ranging",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/rave_m15_ranging_state.json",
                    "restart_args": ["scripts/live_rave_m15_ranging.py"],
                },
            ]
            (configs / "penetration_lattice_runner_registry.json").write_text(json.dumps(registry), encoding="utf-8")

            (reports / "coinbase_rsi_shadow_arbusd_state.json").write_text(
                json.dumps(
                    {
                        "runner": {"pid": 1, "heartbeat_at": "2026-04-12T16:00:00+00:00"},
                        "state": {
                            "product_id": "ARB-USD",
                            "realized_net_usd": 0.2817,
                            "realized_closes": 14,
                            "cash_usd": 47.98,
                            "total_fees": 0.9125,
                            "signals_generated": 98,
                        },
                        "updated_at": "2026-04-12T16:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (reports / "shadow_coinbase_burst_balusd_live_state.json").write_text(
                json.dumps(
                    {
                        "runner": {"pid": 2, "heartbeat_at": "2026-04-12T16:00:00+00:00", "script": "burst_fade_live_shadow.py"},
                        "engine": {
                            "product_id": "BAL-USD",
                            "realized_net_usd": 39.1909,
                            "realized_closes": 147,
                            "wins": 133,
                            "losses": 14,
                            "win_rate": 90.48,
                            "cash": 87.1909,
                            "total_fees": 27.9554,
                        },
                        "updated_at": "2026-04-12T16:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (reports / "shadow_coinbase_burst_multicoin_rotation_state.json").write_text(
                json.dumps(
                    {
                        "runner": {"pid": 3, "heartbeat_at": "2026-04-12T16:00:00+00:00", "script": "burst_fade_multicoin_shadow.py"},
                        "engine": {
                            "products": ["BAL-USD", "PRL-USD"],
                            "realized_net_usd": 90.0416,
                            "closes": 496,
                            "wins": 422,
                            "losses": 74,
                            "win_rate": 85.08,
                            "cash": 138.0416,
                            "total_fees": 94.4939,
                            "open_positions": {},
                        },
                        "updated_at": "2026-04-12T16:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (reports / "rave_m15_ranging_state.json").write_text(
                json.dumps(
                    {
                        "runner": {"pid": 4, "heartbeat_at": "2026-04-12T16:00:00+00:00", "script": "live_rave_m15_ranging.py"},
                        "engine": {
                            "product_id": "RAVE-USD",
                            "realized_net_usd": 70.5809,
                            "closes": 48,
                            "wins": 30,
                            "losses": 18,
                            "win_rate": 62.5,
                            "cash": 118.5809,
                            "total_fees": 44.7261,
                        },
                        "updated_at": "2026-04-12T16:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            lane_rows, product_rows, family_rows = leaderboard.build_reports(
                registry_path=configs / "penetration_lattice_runner_registry.json",
                readiness_paths=[],
                now=datetime(2026, 4, 12, 16, 1, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(len(lane_rows), 4)
        self.assertEqual(product_rows[0]["product_id"], "RAVE-USD")
        self.assertEqual(product_rows[1]["product_id"], "BAL-USD")
        self.assertEqual(product_rows[2]["product_id"], "ARB-USD")
        self.assertEqual(product_rows[1]["best_lane_family"], "burst")
        self.assertEqual(product_rows[1]["positive_lanes"], 1)
        self.assertEqual([row["scope"] for row in lane_rows].count("multi_product"), 1)
        self.assertEqual(family_rows[0]["family"], "burst")
        self.assertEqual(family_rows[0]["multi_product_lanes"], 1)


if __name__ == "__main__":
    unittest.main()
