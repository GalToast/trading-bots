#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_spot_rsi_scoreboard as scoreboard


class CoinbaseSpotRSIScoreboardTests(unittest.TestCase):
    def test_build_rows_uses_only_supervised_registry_lanes(self) -> None:
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
                    "name": "shadow_coinbase_mogusd_rsi4",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/coinbase_rsi_shadow_mogusd_state.json",
                    "restart_args": ["scripts/live_coinbase_rsi_bundle_shadow.py", "--config-path", "configs/coinbase_rsi_bundle_shadow.json"],
                },
                {
                    "name": "shadow_coinbase_xrpusd_piranha",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/coinbase_spot_shadow_xrpusd_piranha_state.json",
                    "restart_args": ["scripts/live_coinbase_spot_piranha_shadow.py", "--product-id", "XRP-USD"],
                },
            ]
            (configs / "penetration_lattice_runner_registry.json").write_text(json.dumps(registry), encoding="utf-8")

            state_payload = {
                "runner": {"pid": 72772, "heartbeat_at": "2026-04-11T18:09:50+00:00"},
                "state": {
                    "product_id": "ARB-USD",
                    "realized_net_usd": 0.4449,
                    "realized_closes": 2,
                    "in_position": False,
                    "cash_usd": 48.4,
                    "total_fees": 0.1301,
                    "signals_generated": 34,
                },
                "updated_at": "2026-04-11T18:09:50+00:00",
            }
            (reports / "coinbase_rsi_shadow_arbusd_state.json").write_text(json.dumps(state_payload), encoding="utf-8")
            (reports / "coinbase_rsi_shadow_mogusd_state.json").write_text(
                json.dumps(
                    {
                        "runner": {"pid": 79000, "heartbeat_at": "2026-04-11T18:09:55+00:00"},
                        "state": {
                            "product_id": "MOG-USD",
                            "realized_net_usd": 1.5,
                            "realized_closes": 1,
                            "in_position": True,
                            "cash_usd": 12.4,
                            "total_fees": 0.41,
                            "signals_generated": 12,
                        },
                        "updated_at": "2026-04-11T18:09:55+00:00",
                    }
                ),
                encoding="utf-8",
            )

            with (reports / "coinbase_spot_rsi_readiness_extended.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "product_id",
                        "candles_used",
                        "approx_hours",
                        "full_net_usd",
                        "full_trades",
                        "split_train_net_usd",
                        "split_train_trades",
                        "split_test_net_usd",
                        "split_test_trades",
                        "walkforward_positive_windows",
                        "walkforward_windows",
                        "verdict",
                        "note",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "product_id": "ARB-USD",
                        "candles_used": 822,
                        "approx_hours": 68.5,
                        "full_net_usd": 3.7457,
                        "full_trades": 15,
                        "split_train_net_usd": 2.2041,
                        "split_train_trades": 9,
                        "split_test_net_usd": 0.6274,
                        "split_test_trades": 5,
                        "walkforward_positive_windows": 2,
                        "walkforward_windows": 3,
                        "verdict": "probationary",
                        "note": "enough depth and repeated positive walk-forward windows",
                    }
                )

            rows = scoreboard.build_rows(
                registry_path=configs / "penetration_lattice_runner_registry.json",
                readiness_paths=[reports / "coinbase_spot_rsi_readiness_extended.csv"],
                now=datetime(2026, 4, 11, 18, 10, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["lane_name"], "shadow_coinbase_arbusd_rsi7")
        self.assertEqual(rows[0]["product_id"], "ARB-USD")
        self.assertEqual(rows[0]["readiness_verdict"], "probationary")
        self.assertEqual(rows[0]["walkforward"], "2/3")
        self.assertEqual(rows[1]["lane_name"], "shadow_coinbase_mogusd_rsi4")
        self.assertEqual(rows[1]["product_id"], "MOG-USD")
        self.assertEqual(rows[1]["readiness_verdict"], "unrated")
        self.assertEqual(rows[2]["lane_name"], "TOTAL")
        self.assertEqual(rows[2]["note"], "lanes=2")


if __name__ == "__main__":
    unittest.main()
