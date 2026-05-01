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

import build_coinbase_burst_shadow_scoreboard as scoreboard


class CoinbaseBurstShadowScoreboardTests(unittest.TestCase):
    def test_build_rows_uses_only_supervised_burst_registry_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configs = root / "configs"
            reports = root / "reports"
            configs.mkdir(parents=True)
            reports.mkdir(parents=True)

            registry = [
                {
                    "name": "shadow_coinbase_burst_multicoin_rotation",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/shadow_coinbase_burst_multicoin_rotation_state.json",
                    "restart_args": ["scripts/burst_fade_multicoin_shadow.py"],
                },
                {
                    "name": "shadow_coinbase_arbusd_rsi7",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/coinbase_rsi_shadow_arbusd_state.json",
                    "restart_args": ["scripts/live_coinbase_rsi_shadow.py", "--product-id", "ARB-USD"],
                },
            ]
            (configs / "penetration_lattice_runner_registry.json").write_text(
                json.dumps(registry), encoding="utf-8"
            )

            state_payload = {
                "runner": {"pid": 17916, "heartbeat_at": "2026-04-11T18:32:40+00:00", "script": "burst_fade_multicoin_shadow.py"},
                "engine": {
                    "products": ["BAL-USD", "CHECK-USD"],
                    "realized_net_usd": 89.5411,
                    "closes": 487,
                    "wins": 414,
                    "losses": 73,
                    "win_rate": 85.01,
                    "avg_pnl_per_close": 0.1839,
                    "cash": 113.5411,
                    "total_fees": 92.7747,
                    "open_positions": {"BAL-USD": {"entry": 0.1686}},
                },
                "updated_at": "2026-04-11T18:32:40+00:00",
            }
            (reports / "shadow_coinbase_burst_multicoin_rotation_state.json").write_text(
                json.dumps(state_payload), encoding="utf-8"
            )

            rows = scoreboard.build_rows(
                registry_path=configs / "penetration_lattice_runner_registry.json",
                now=datetime(2026, 4, 11, 18, 33, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["lane_name"], "shadow_coinbase_burst_multicoin_rotation")
        self.assertEqual(rows[0]["style"], "multicoin_rotation")
        self.assertEqual(rows[0]["open_count"], 1)
        self.assertEqual(rows[1]["lane_name"], "TOTAL")
        self.assertEqual(rows[1]["note"], "lanes=1")

    def test_build_rows_handles_single_symbol_burst_live_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configs = root / "configs"
            reports = root / "reports"
            configs.mkdir(parents=True)
            reports.mkdir(parents=True)

            registry = [
                {
                    "name": "shadow_coinbase_burst_balusd_live",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/shadow_coinbase_burst_balusd_live_state.json",
                    "restart_args": ["scripts/burst_fade_live_shadow.py", "--product-id", "BAL-USD"],
                }
            ]
            (configs / "penetration_lattice_runner_registry.json").write_text(
                json.dumps(registry), encoding="utf-8"
            )

            state_payload = {
                "runner": {"pid": 13048, "heartbeat_at": "2026-04-11T18:41:52+00:00", "script": "burst_fade_live_shadow.py"},
                "engine": {
                    "product_id": "BAL-USD",
                    "realized_net_usd": 39.1909,
                    "realized_closes": 147,
                    "wins": 133,
                    "losses": 14,
                    "win_rate": 90.48,
                    "avg_pnl_per_close": 0.2666,
                    "cash": 87.1909,
                    "total_fees": 27.9554,
                    "position": None,
                },
                "updated_at": "2026-04-11T18:41:52+00:00",
            }
            (reports / "shadow_coinbase_burst_balusd_live_state.json").write_text(
                json.dumps(state_payload), encoding="utf-8"
            )

            rows = scoreboard.build_rows(
                registry_path=configs / "penetration_lattice_runner_registry.json",
                now=datetime(2026, 4, 11, 18, 42, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(rows[0]["lane_name"], "shadow_coinbase_burst_balusd_live")
        self.assertEqual(rows[0]["closes"], 147)
        self.assertEqual(rows[0]["products_tracked"], 1)
        self.assertEqual(rows[1]["closes"], 147)


if __name__ == "__main__":
    unittest.main()
