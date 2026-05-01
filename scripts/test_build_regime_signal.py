#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_regime_signal as board


class RegimeSignalTests(unittest.TestCase):
    def test_merge_policy_seed_rows_adds_missing_symbols_without_overriding_existing_rows(self) -> None:
        merged = board.merge_policy_seed_rows(
            [
                {"symbol": "USDCAD", "control_mode": "trend_follow"},
                {"symbol": "GBPUSD", "control_mode": "trend_follow"},
            ],
            [
                {"symbol": "USDCAD", "control_mode": "bounce_reversal"},
                {"symbol": "USDCHF", "control_mode": "bounce_reversal"},
            ],
        )
        rows = {row["symbol"]: row for row in merged}

        self.assertEqual(rows["USDCAD"]["control_mode"], "trend_follow")
        self.assertEqual(rows["USDCHF"]["control_mode"], "bounce_reversal")
        self.assertEqual(sorted(rows), ["GBPUSD", "USDCAD", "USDCHF"])

    def test_btc_signal_captures_coarse_mtf_conflict(self) -> None:
        payload = board.build_payload()
        rows = {row["symbol"]: row for row in payload["rows"]}
        btc = rows["BTCUSD"]

        self.assertEqual(btc["symbol"], "BTCUSD")
        self.assertTrue(btc["coarse_regime"])
        self.assertTrue(btc["mtf_regime"])
        self.assertEqual(
            btc["control_mode"],
            board.derive_control_mode(
                reversal_signal=str(btc["reversal_signal"]),
                mtf_regime=str(btc["mtf_regime"]),
                mtf_bias=str(btc["mtf_bias"]),
                consensus=str(btc["consensus"]),
                normalized_regime=str(btc["normalized_regime"]),
            ),
        )
        self.assertIn(btc["action_bias"], {"BUY", "SELL", "NEUTRAL"})

    def test_breakout_control_mode_derivation_is_stable(self) -> None:
        control_mode = board.derive_control_mode(
            reversal_signal="BREAKOUT_UP",
            mtf_regime="AT_EXTREME_HIGH",
            mtf_bias="BUY",
            consensus="aligned",
            normalized_regime="trending",
        )
        action_bias = board.derive_action_bias(control_mode, "BUY")

        self.assertEqual(control_mode, "breakout_follow")
        self.assertEqual(action_bias, "BUY")

    def test_policy_seed_rows_make_usdchf_and_usdcad_available(self) -> None:
        payload = board.build_payload()
        rows = {row["symbol"]: row for row in payload["rows"]}

        self.assertIn("USDCHF", rows)
        self.assertIn("USDCAD", rows)
        self.assertIn("XRPUSD", rows)
        self.assertTrue(str(rows["USDCHF"]["control_mode"]))
        self.assertTrue(str(rows["USDCAD"]["control_mode"]))
        self.assertEqual(rows["XRPUSD"]["control_mode"], "breakout_follow")
        self.assertTrue(str(rows["USDCHF"]["why"]))
        self.assertTrue(str(rows["USDCAD"]["why"]))
        self.assertIn("bucket_split", str(rows["XRPUSD"]["why"]))


if __name__ == "__main__":
    unittest.main()
