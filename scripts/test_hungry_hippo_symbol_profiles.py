#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hungry_hippo_symbol_profiles as profiles


class HungryHippoSymbolProfilesTests(unittest.TestCase):
    def test_discover_symbols_collects_union_from_common_payload_shapes(self) -> None:
        symbols = profiles.discover_symbols(
            {"symbols": {"BTCUSD": {}, "ETHUSD": {}}},
            {"symbols": [{"symbol": "GBPUSD"}]},
            {"rows": [{"symbol": "nas100"}]},
            {"session_windows": {"xauusd": {"window": "06:00-10:00"}}},
        )
        self.assertEqual(symbols, ["BTCUSD", "ETHUSD", "GBPUSD", "NAS100", "XAUUSD"])

    def test_escape_defaults_scale_unknown_crypto_by_reference_size(self) -> None:
        btc_like = profiles.escape_defaults_for_symbol("AVAXUSD", atr_current=120.0, reference_step=140.0)
        self.assertEqual(btc_like["cut_count"], 2)
        self.assertEqual(btc_like["max_cut_loss"], 10.0)

        alt_like = profiles.escape_defaults_for_symbol("AVAXUSD", atr_current=0.25, reference_step=0.15)
        self.assertEqual(alt_like["max_escape_loss"], 2.0)

    def test_default_session_profile_is_family_scoped(self) -> None:
        self.assertEqual(profiles.default_session_profile_for_symbol("NAS100")["window"], "14:00-19:00")
        self.assertEqual(profiles.default_session_profile_for_symbol("USDCHF")["window"], "06:00-10:00+13:00-17:00")


if __name__ == "__main__":
    unittest.main()
