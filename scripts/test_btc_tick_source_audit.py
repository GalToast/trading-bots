from __future__ import annotations

import unittest

import scripts.btc_tick_source_audit as audit


class BtcTickSourceAuditTests(unittest.TestCase):
    def test_classify_tick_path_distinguishes_direct_vs_shared_vs_stale(self) -> None:
        verdict, reason = audit.classify_tick_path(
            shared_price_max_age_ms=0,
            latest_tick_source_last="symbol_info_tick",
            latest_tick_append_source_last="symbol_info_tick",
            heartbeat_age_minutes=1.0,
            stale_after_seconds=300,
        )
        self.assertEqual(verdict, "direct_tick_live")
        self.assertIn("without shared-price age gating", reason)

        verdict, _ = audit.classify_tick_path(
            shared_price_max_age_ms=1000,
            latest_tick_source_last="symbol_info_tick",
            latest_tick_append_source_last="symbol_info_tick",
            heartbeat_age_minutes=1.0,
            stale_after_seconds=300,
        )
        self.assertEqual(verdict, "shared_history_live_tick_backed")

        verdict, _ = audit.classify_tick_path(
            shared_price_max_age_ms=1000,
            latest_tick_source_last="copy_ticks_range",
            latest_tick_append_source_last="copy_ticks_range",
            heartbeat_age_minutes=10.0,
            stale_after_seconds=300,
        )
        self.assertEqual(verdict, "stale_runtime")


if __name__ == "__main__":
    unittest.main()
