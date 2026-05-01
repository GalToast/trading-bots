#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_ratio_proof_readiness as readiness


class CoinbaseRatioProofReadinessTests(unittest.TestCase):
    def test_classify_gate_waiting_first_entry(self) -> None:
        gate = readiness.classify_gate(forward_status="seeded_flat", open_count=0, closes=0)
        self.assertEqual(gate, "waiting_first_entry")

    def test_classify_gate_waiting_first_close(self) -> None:
        gate = readiness.classify_gate(forward_status="seeded_in_position", open_count=2, closes=0)
        self.assertEqual(gate, "waiting_first_close")

    def test_classify_posture_respects_pair_role(self) -> None:
        self.assertEqual(
            readiness.classify_posture(pair="CFG/ETH", current_gate="waiting_first_entry"),
            "keep_shadowing_first_proof",
        )
        self.assertEqual(
            readiness.classify_posture(pair="CFG/BTC", current_gate="waiting_first_close"),
            "shadow_only_scale_up",
        )

    def test_build_rows_uses_forward_review_and_registry(self) -> None:
        registry_payload = {
            "lanes": [
                {
                    "name": "shadow_coinbase_cfgeth_ratio_sleeve",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/cfg_eth_synthetic_sleeve_shadow_state.json",
                    "stale_after_seconds": 180,
                },
                {
                    "name": "shadow_coinbase_cfgbtc_ratio_sleeve",
                    "kind": "shadow_coinbase_spot",
                    "state_path": "reports/cfg_btc_synthetic_sleeve_shadow_state.json",
                    "stale_after_seconds": 180,
                },
            ]
        }
        state_payloads = {
            str(readiness.ROOT / "reports/cfg_eth_synthetic_sleeve_shadow_state.json"): {
                "pair": "CFG/ETH",
                "metadata": {"numerator_product": "CFG-USD", "denominator_product": "ETH-USD"},
                "runner": {"pid": 111, "heartbeat_at": "2026-04-13T17:20:00+00:00"},
            },
            str(readiness.ROOT / "reports/cfg_btc_synthetic_sleeve_shadow_state.json"): {
                "pair": "CFG/BTC",
                "metadata": {"numerator_product": "CFG-USD", "denominator_product": "BTC-USD"},
                "runner": {"pid": 222, "heartbeat_at": "2026-04-13T17:20:10+00:00"},
            },
        }
        forward_rows = {
            "shadow_coinbase_cfgeth_ratio_sleeve": {
                "lane_name": "shadow_coinbase_cfgeth_ratio_sleeve",
                "forward_status": "seeded_flat",
                "realized_closes": "0",
                "open_count": "0",
                "realized_net_usd": "0.0",
                "equity_usd_mark": "24.9",
                "forward_note": "too few closes for a forward verdict",
            },
            "shadow_coinbase_cfgbtc_ratio_sleeve": {
                "lane_name": "shadow_coinbase_cfgbtc_ratio_sleeve",
                "forward_status": "seeded_in_position",
                "realized_closes": "0",
                "open_count": "2",
                "realized_net_usd": "0.0",
                "equity_usd_mark": "24.8",
                "forward_note": "too few closes for a forward verdict",
            },
        }

        def fake_load_json(path: Path):
            if str(path) == str(readiness.REGISTRY_PATH):
                return registry_payload
            return state_payloads[str(path)]

        with (
            patch.object(readiness, "load_json", side_effect=fake_load_json),
            patch.object(readiness, "load_forward_rows", return_value=forward_rows),
        ):
            rows = readiness.build_rows(now=datetime(2026, 4, 13, 17, 21, tzinfo=timezone.utc))

        self.assertEqual([row["pair"] for row in rows], ["CFG/ETH", "CFG/BTC"])
        self.assertEqual(rows[0]["current_gate"], "waiting_first_entry")
        self.assertEqual(rows[1]["deployment_posture"], "shadow_only_scale_up")


if __name__ == "__main__":
    unittest.main()
