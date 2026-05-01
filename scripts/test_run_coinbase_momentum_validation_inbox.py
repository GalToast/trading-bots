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

import run_coinbase_momentum_validation_inbox as runner


class RunCoinbaseMomentumValidationInboxTests(unittest.TestCase):
    def test_build_results_uses_registry_params_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)

            (reports / "coinbase_momentum_validation_inbox.json").write_text(
                json.dumps(
                    {
                        "validation_inbox": [
                            {
                                "coin": "TROLL-USD",
                                "strategy": "Momentum (lb=30, tp=15, sl=5)",
                                "tier": "B-TIER: 7d Positive, Not Fully Swept",
                                "registry_net_pnl": 39.53,
                                "param_hit_rate": None,
                                "action": "optimize_then_validate",
                                "reason": "positive 7d claim exists, but parameter sweep and 30d confirmation are still missing",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (reports / "master_deployment_registry.md").write_text(
                "\n".join(
                    [
                        "# Master Deployment Registry",
                        "",
                        "## B-TIER: 7d Positive, Not Fully Swept",
                        "",
                        "| Coin | Strategy | 7d PnL | WR | Trades | Notes |",
                        "|------|----------|--------|----|--------|-------|",
                        "| TROLL-USD | Momentum (lb=30, tp=15, sl=5) | +$39.53 | 80.0% | 10 | High WR, low trades |",
                    ]
                ),
                encoding="utf-8",
            )

            old_inbox = runner.INBOX_PATH
            old_registry = runner.REGISTRY_PATH
            old_json = runner.JSON_PATH
            old_md = runner.MD_PATH
            old_snapshot_path = runner.recon_runner.SNAPSHOT_PATH
            old_cache_dir = runner.recon_runner.CACHE_DIR
            try:
                runner.INBOX_PATH = reports / "coinbase_momentum_validation_inbox.json"
                runner.REGISTRY_PATH = reports / "master_deployment_registry.md"
                runner.JSON_PATH = reports / "out.json"
                runner.MD_PATH = reports / "out.md"
                runner.recon_runner.SNAPSHOT_PATH = reports / "reconciliation_candles.json"
                runner.recon_runner.CACHE_DIR = reports / "candle_cache"
                (runner.recon_runner.CACHE_DIR).mkdir(parents=True, exist_ok=True)
                (runner.recon_runner.CACHE_DIR / "TROLL_USD_FIVE_MINUTE_30d.json").write_text(
                    json.dumps(
                        {
                            "candles": [
                                {"time": i, "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i, "volume": 1}
                                for i in range(80)
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

                selected = runner.select_rows(
                    action="optimize_then_validate",
                    limit=1,
                    include_coins=[],
                    exclude_coins=[],
                )
                payload = runner.build_results(selected, fetch_missing=False)
            finally:
                runner.INBOX_PATH = old_inbox
                runner.REGISTRY_PATH = old_registry
                runner.JSON_PATH = old_json
                runner.MD_PATH = old_md
                runner.recon_runner.SNAPSHOT_PATH = old_snapshot_path
                runner.recon_runner.CACHE_DIR = old_cache_dir

        result = payload["results"][0]
        self.assertEqual(result["coin"], "TROLL-USD")
        self.assertEqual(result["lookback"], 30)
        self.assertEqual(result["tp_pct"], 15.0)
        self.assertEqual(result["sl_pct"], 5.0)
        self.assertEqual(result["max_hold"], 48)
        self.assertEqual(result["source"], "cache")
        self.assertIn(result["verdict"], {"confirmed_positive", "rejected", "flat"})

    def test_merge_results_keeps_prior_coins_and_updates_current_coin(self) -> None:
        existing = {
            "generated_at": "2026-04-12T17:00:00+00:00",
            "results": [
                {"coin": "SUP-USD", "reconciliation_30d_net_usd": 48.06, "action": "optimize_then_validate"},
                {"coin": "TROLL-USD", "reconciliation_30d_net_usd": -3.17, "action": "optimize_then_validate"},
            ],
        }
        payload = {
            "generated_at": "2026-04-12T18:00:00+00:00",
            "results": [
                {"coin": "TRU-USD", "reconciliation_30d_net_usd": 395.17, "action": "validate_30d_next"},
                {"coin": "SUP-USD", "reconciliation_30d_net_usd": 50.0, "action": "optimize_then_validate"},
            ],
        }

        merged = runner.merge_results(payload, existing_payload=existing)
        by_coin = {row["coin"]: row for row in merged["results"]}
        self.assertEqual(by_coin["TRU-USD"]["reconciliation_30d_net_usd"], 395.17)
        self.assertEqual(by_coin["SUP-USD"]["reconciliation_30d_net_usd"], 50.0)
        self.assertEqual(by_coin["TROLL-USD"]["reconciliation_30d_net_usd"], -3.17)

    def test_select_rows_can_fallback_to_registry_for_included_coin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports = root / "reports"
            reports.mkdir(parents=True)
            (reports / "coinbase_momentum_validation_inbox.json").write_text(
                json.dumps({"validation_inbox": []}),
                encoding="utf-8",
            )
            (reports / "master_deployment_registry.md").write_text(
                "\n".join(
                    [
                        "# Master Deployment Registry",
                        "",
                        "## B-TIER: 7d Positive, Not Fully Swept",
                        "",
                        "| Coin | Strategy | 7d PnL | WR | Trades | Notes |",
                        "|------|----------|--------|----|--------|-------|",
                        "| TROLL-USD | Momentum (lb=30, tp=15, sl=5) | +$39.53 | 80.0% | 10 | High WR, low trades |",
                    ]
                ),
                encoding="utf-8",
            )
            old_inbox = runner.INBOX_PATH
            old_registry = runner.REGISTRY_PATH
            try:
                runner.INBOX_PATH = reports / "coinbase_momentum_validation_inbox.json"
                runner.REGISTRY_PATH = reports / "master_deployment_registry.md"
                rows = runner.select_rows(
                    action="optimize_then_validate",
                    limit=3,
                    include_coins=["TROLL-USD"],
                    exclude_coins=[],
                )
            finally:
                runner.INBOX_PATH = old_inbox
                runner.REGISTRY_PATH = old_registry

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["coin"], "TROLL-USD")
        self.assertEqual(rows[0]["action"], "optimize_then_validate")


if __name__ == "__main__":
    unittest.main()
