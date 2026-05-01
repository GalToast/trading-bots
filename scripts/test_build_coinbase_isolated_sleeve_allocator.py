#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_sleeve_allocator as allocator


class CoinbaseIsolatedSleeveAllocatorTests(unittest.TestCase):
    def test_primary_sleeve_catalog_has_expected_ordered_core(self) -> None:
        rows = allocator.build_primary_rows()

        self.assertEqual(len(rows), 7)
        self.assertEqual((rows[0]["coin"], rows[0]["strategy"]), ("RAVE-USD", "mom_10"))
        self.assertEqual((rows[1]["coin"], rows[1]["strategy"]), ("A8-USD", "mom_50"))
        self.assertEqual((rows[2]["coin"], rows[2]["strategy"]), ("CFG-USD", "mom_25"))
        self.assertEqual((rows[3]["coin"], rows[3]["strategy"]), ("NOM-USD", "range_breakout_shadow"))

    def test_conditional_reserve_queue_prioritizes_nom_then_rave(self) -> None:
        rows = allocator.build_conditional_rows()

        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual((rows[0]["coin"], rows[0]["strategy"]), ("NOM-USD", "momentum_registry_validation"))
        self.assertEqual((rows[1]["coin"], rows[1]["strategy"]), ("RAVE-USD", "rsi_mean_reversion_active"))

    def test_full_primary_stack_stops_at_336_and_keeps_reserve_at_384(self) -> None:
        payload = allocator.build_payload()
        tier_336 = next(row for row in payload["bankroll_tiers"] if row["bankroll_usd"] == 336)
        tier_384 = next(row for row in payload["bankroll_tiers"] if row["bankroll_usd"] == 384)

        self.assertEqual(payload["summary"]["unconditional_primary_capital_usd"], 336)
        self.assertEqual(tier_336["deployed_primary_sleeves"], 7)
        self.assertEqual(tier_336["cash_reserve_usd"], 0)
        self.assertEqual(tier_384["deployed_primary_sleeves"], 7)
        self.assertEqual(tier_384["cash_reserve_usd"], 48)
        self.assertIn("NOM-USD momentum_registry_validation", tier_384["reserve_action"])


if __name__ == "__main__":
    unittest.main()
