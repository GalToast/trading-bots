from __future__ import annotations

import unittest

import scripts.btc_stale_runtime_triage as triage


class BtcStaleRuntimeTriageTests(unittest.TestCase):
    def test_stale_with_live_broker_inventory_is_urgent(self) -> None:
        rank, action, severity, reason = triage.classify_action(
            verdict="stale_runtime",
            watchdog_status="",
            registry_enabled="disabled",
            watchdog_group="",
            open_count=6,
            broker_scoped_open_count=6,
            broker_total_open_count=6,
            scope_status="aligned",
            quarantine_reason="",
            reasons=[],
        )
        self.assertEqual(rank, 0)
        self.assertEqual(action, "inspect_live_carry_now")
        self.assertEqual(severity, "urgent")
        self.assertIn("open inventory", reason)

    def test_stale_state_only_mismatch_becomes_state_cleanup_not_broker_cleanup(self) -> None:
        rank, action, severity, reason = triage.classify_action(
            verdict="stale_runtime",
            watchdog_status="",
            registry_enabled="disabled",
            watchdog_group="",
            open_count=6,
            broker_scoped_open_count=0,
            broker_total_open_count=0,
            scope_status="scoped_mismatch",
            quarantine_reason="",
            reasons=[],
        )
        self.assertEqual(rank, 0)
        self.assertEqual(action, "clear_stale_state_or_document_parked")
        self.assertEqual(severity, "high")
        self.assertIn("broker-authoritative scope is already flat", reason)

    def test_enabled_stale_unsupervised_row_needs_watchdog_or_disable(self) -> None:
        rank, action, severity, reason = triage.classify_action(
            verdict="stale_runtime",
            watchdog_status="",
            registry_enabled="enabled",
            watchdog_group="",
            open_count=0,
            broker_scoped_open_count=0,
            broker_total_open_count=0,
            scope_status="",
            quarantine_reason="",
            reasons=[],
        )
        self.assertEqual(rank, 1)
        self.assertEqual(action, "wire_watchdog_or_disable")
        self.assertEqual(severity, "high")
        self.assertIn("outside any watchdog group", reason)

    def test_quarantined_restart_storm_is_not_feeder_fix_action(self) -> None:
        rank, action, severity, reason = triage.classify_action(
            verdict="shared_history_live_tick_backed",
            watchdog_status="quarantined",
            registry_enabled="unspecified",
            watchdog_group="feeder_crypto_canary",
            open_count=0,
            broker_scoped_open_count=0,
            broker_total_open_count=0,
            scope_status="",
            quarantine_reason="restart_storm=4/4 within 1800s",
            reasons=["forward=lagging realized=-9226.72 closes=88", "risk_resets=11>=5"],
        )
        self.assertEqual(rank, 4)
        self.assertEqual(action, "keep_quarantined_no_promotion")
        self.assertEqual(severity, "medium")
        self.assertIn("isolated", reason)

    def test_healthy_live_tick_row_is_watch_only(self) -> None:
        rank, action, severity, reason = triage.classify_action(
            verdict="direct_tick_live",
            watchdog_status="ok",
            registry_enabled="enabled",
            watchdog_group="crypto_watchdog",
            open_count=0,
            broker_scoped_open_count=0,
            broker_total_open_count=0,
            scope_status="aligned",
            quarantine_reason="",
            reasons=[],
        )
        self.assertEqual(rank, 5)
        self.assertEqual(action, "watch_only_honest_ticks")
        self.assertEqual(severity, "low")
        self.assertIn("live-tick behavior", reason)


if __name__ == "__main__":
    unittest.main()
