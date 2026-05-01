from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import scripts.build_live_magic_scope_audit as audit


class BuildLiveMagicScopeAuditTests(unittest.TestCase):
    def test_collect_account_snapshot_adds_collection_timestamp(self) -> None:
        class DummyMT5:
            @staticmethod
            def account_info():
                return type(
                    "AccountInfo",
                    (),
                    {"balance": 100.0, "equity": 101.5, "profit": 1.5, "margin_level": 250.0},
                )()

        original_mt5 = audit.mt5
        audit.mt5 = DummyMT5()
        try:
            snapshot = audit.collect_account_snapshot()
        finally:
            audit.mt5 = original_mt5

        self.assertEqual(snapshot["balance_usd"], 100.0)
        self.assertEqual(snapshot["equity_usd"], 101.5)
        self.assertIn("collected_at", snapshot)

    def test_missing_enabled_defaults_to_true(self) -> None:
        self.assertTrue(audit.lane_enabled_value({"name": "live_rearm_941777"}))
        self.assertFalse(audit.lane_enabled_value({"name": "shadow_btcusd_m5", "enabled": False}))

    def test_disabled_lane_with_state_only_inventory_gets_specific_status(self) -> None:
        status, action = audit.classify_scope_status(
            live_magic=941780,
            lane_enabled=False,
            managed_open_count=6,
            broker_scoped_open_count=0,
            outside_scope_open_count=0,
        )
        self.assertEqual(status, "managed_state_only_flat_broker")
        self.assertEqual(action, "clear_stale_state_or_document_parked")

    def test_active_lane_with_flat_broker_stays_generic_scope_mismatch(self) -> None:
        status, action = audit.classify_scope_status(
            live_magic=941780,
            lane_enabled=True,
            managed_open_count=6,
            broker_scoped_open_count=0,
            outside_scope_open_count=0,
        )
        self.assertEqual(status, "scoped_mismatch")
        self.assertEqual(action, "inspect_rehydration_or_scope")

    def test_outside_scope_inventory_still_beats_generic_mismatch(self) -> None:
        status, action = audit.classify_scope_status(
            live_magic=941777,
            lane_enabled=True,
            managed_open_count=38,
            broker_scoped_open_count=38,
            outside_scope_open_count=39,
        )
        self.assertEqual(status, "outside_scope_legacy_inventory")
        self.assertEqual(action, "manual_review_do_not_autoclose")

    def test_collect_unassigned_live_symbol_positions_flags_live_symbol_with_unknown_magic(self) -> None:
        registry = [
            {"name": "live_btc_lane", "kind": "live_crypto", "enabled": True, "restart_args": ["--symbols", "BTCUSD", "--live-magic", "941781"]},
            {"name": "live_fx_lane", "kind": "live_fx", "enabled": True, "restart_args": ["--symbols", "EURUSD", "GBPUSD", "--live-magic", "941777"]},
        ]
        broker_positions_by_magic = {
            0: [
                {"ticket": 1, "symbol": "BTCUSD", "side": "BUY", "volume": 0.01, "price_open": 71585.66, "profit_usd": 290.73, "comment": "", "opened_at": "2026-04-12T04:51:38Z"},
                {"ticket": 2, "symbol": "XAUUSD", "side": "BUY", "volume": 0.01, "price_open": 3200.0, "profit_usd": 1.0, "comment": "", "opened_at": "2026-04-12T04:51:38Z"},
            ],
            941777: [
                {"ticket": 3, "symbol": "EURUSD", "side": "BUY", "volume": 0.01, "price_open": 1.1, "profit_usd": 1.0, "comment": "", "opened_at": "2026-04-12T04:51:38Z"},
            ],
        }

        positions = audit.collect_unassigned_live_symbol_positions(registry, broker_positions_by_magic)

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["symbol"], "BTCUSD")
        self.assertEqual(positions[0]["magic"], 0)

    def test_collect_unassigned_live_symbol_positions_ignores_attached_magic(self) -> None:
        registry = [
            {
                "name": "live_btc_lane",
                "kind": "live_crypto",
                "enabled": True,
                "restart_args": [
                    "--symbols",
                    "BTCUSD",
                    "--live-magic",
                    "941781",
                    "--attach-broker-magic",
                    "941785",
                ],
            }
        ]
        broker_positions_by_magic = {
            941785: [
                {"ticket": 1, "magic": 941785, "symbol": "BTCUSD"},
            ],
            941999: [
                {"ticket": 2, "magic": 941999, "symbol": "BTCUSD"},
            ],
        }

        positions = audit.collect_unassigned_live_symbol_positions(registry, broker_positions_by_magic)

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["magic"], 941999)


if __name__ == "__main__":
    unittest.main()
