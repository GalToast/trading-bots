#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_execution_monitor_report as monitor


class ExecutionMonitorTests(unittest.TestCase):
    def test_summarize_events_counts_broker_sync_inherited_closes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"ts_utc":"2026-04-14T17:04:35+00:00","action":"fresh_start_prime"}',
                        '{"ts_utc":"2026-04-14T17:04:36+00:00","action":"direct_live_broker_sync","old_realized_closes":0,"new_realized_closes":9,"old_realized_net_usd":0.0,"new_realized_net_usd":-58.25}',
                        '{"ts_utc":"2026-04-14T17:04:37+00:00","action":"tick_history_fallback"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = monitor.summarize_events(path)

        self.assertEqual(summary["trade_close_count"], 0)
        self.assertEqual(summary["broker_sync_inherited_closes"], 9)
        self.assertEqual(summary["broker_sync_inherited_realized_usd"], -58.25)

    def test_clamp_inherited_broker_sync_history_caps_to_state_ledger(self) -> None:
        closes, realized = monitor.clamp_inherited_broker_sync_history(
            inherited_closes=662,
            inherited_realized_usd=4158.32,
            state_snapshot={"closes": 331, "realized_net_usd": 2079.16},
        )

        self.assertEqual(closes, 331)
        self.assertEqual(realized, 2079.16)

    def test_watchdog_group_report_paths_reads_all_configured_groups(self) -> None:
        original_load_json = monitor.load_json
        try:
            monitor.load_json = lambda path: {
                "groups": {
                    "fx_watchdog": {},
                    "crypto_watchdog": {},
                    "feeder_crypto_canary": {},
                }
            } if path == monitor.WATCHDOG_GROUPS_CONFIG else {}
            paths = monitor.watchdog_group_report_paths()
        finally:
            monitor.load_json = original_load_json

        self.assertEqual(
            paths,
            [
                monitor.ROOT / "reports" / "watchdog" / "crypto_watchdog_report.json",
                monitor.ROOT / "reports" / "watchdog" / "feeder_crypto_canary_report.json",
                monitor.ROOT / "reports" / "watchdog" / "fx_watchdog_report.json",
            ],
        )

    def test_runner_status_note_reports_positive_only_hold_symbols_and_reason(self) -> None:
        note = monitor.runner_status_note(
            {
                "status": "positive_only_hold_active",
                "positive_only_hold_symbols": ["BTCUSD"],
                "positive_only_hold_reason": "forced_unwind_blocked_negative",
            }
        )
        self.assertEqual(
            note,
            "runner_status=positive_only_hold_active symbols=BTCUSD reason=forced_unwind_blocked_negative",
        )

    def test_live_quote_prefers_recent_tick_range_when_available(self) -> None:
        fixed_now = datetime(2026, 4, 12, 21, 0, tzinfo=timezone.utc)

        class DummyTick:
            bid = 999.0
            ask = 1000.0
            time_msc = 0

        class DummyMt5:
            COPY_TICKS_ALL = 7

            @staticmethod
            def symbol_select(symbol: str, visible: bool) -> bool:
                return symbol == "USDJPY" and visible

            @staticmethod
            def copy_ticks_range(symbol: str, start_dt: datetime, end_dt: datetime, flags: int):
                return [
                    {
                        "bid": 159.57,
                        "ask": 159.63,
                        "time_msc": int(fixed_now.timestamp() * 1000),
                    }
                ]

            @staticmethod
            def symbol_info_tick(symbol: str):
                return DummyTick()

        original_mt5 = monitor.mt5
        original_utc_now = monitor.utc_now
        try:
            monitor.mt5 = DummyMt5()
            monitor.utc_now = lambda: fixed_now
            quote = monitor.live_quote("USDJPY")
        finally:
            monitor.mt5 = original_mt5
            monitor.utc_now = original_utc_now

        self.assertEqual(quote["bid"], 159.57)
        self.assertEqual(quote["ask"], 159.63)
        self.assertEqual(quote["time_msc"], int(fixed_now.timestamp() * 1000))

    def test_live_quote_falls_back_to_symbol_info_tick_when_recent_range_missing(self) -> None:
        class DummyTick:
            bid = 159.83
            ask = 159.84
            time_msc = 1776052499752

        class DummyMt5:
            COPY_TICKS_ALL = 7

            @staticmethod
            def symbol_select(symbol: str, visible: bool) -> bool:
                return symbol == "USDJPY" and visible

            @staticmethod
            def copy_ticks_range(symbol: str, start_dt: datetime, end_dt: datetime, flags: int):
                return []

            @staticmethod
            def symbol_info_tick(symbol: str):
                return DummyTick()

        original_mt5 = monitor.mt5
        try:
            monitor.mt5 = DummyMt5()
            quote = monitor.live_quote("USDJPY")
        finally:
            monitor.mt5 = original_mt5

        self.assertEqual(quote["bid"], 159.83)
        self.assertEqual(quote["ask"], 159.84)
        self.assertEqual(quote["time_msc"], 1776052499752)

    def test_live_quote_prefers_fresher_symbol_info_tick_over_stale_tick_range(self) -> None:
        fixed_now = datetime(2026, 4, 14, 16, 40, tzinfo=timezone.utc)

        class DummyTick:
            bid = 2347.11
            ask = 2352.59
            time_msc = 1776195610572

        class DummyMt5:
            COPY_TICKS_ALL = 7

            @staticmethod
            def symbol_select(symbol: str, visible: bool) -> bool:
                return symbol == "ETHUSD" and visible

            @staticmethod
            def copy_ticks_range(symbol: str, start_dt: datetime, end_dt: datetime, flags: int):
                return [
                    {
                        "bid": 2382.47,
                        "ask": 2388.23,
                        "time_msc": 1776184815618,
                    }
                ]

            @staticmethod
            def symbol_info_tick(symbol: str):
                return DummyTick()

        original_mt5 = monitor.mt5
        original_utc_now = monitor.utc_now
        try:
            monitor.mt5 = DummyMt5()
            monitor.utc_now = lambda: fixed_now
            quote = monitor.live_quote("ETHUSD")
        finally:
            monitor.mt5 = original_mt5
            monitor.utc_now = original_utc_now

        self.assertEqual(quote["bid"], 2347.11)
        self.assertEqual(quote["ask"], 2352.59)
        self.assertEqual(quote["time_msc"], 1776195610572)

    def test_trigger_signature_requires_room_and_cross(self) -> None:
        metrics = {
            "open_count": 1,
            "max_open_total": 3,
            "next_buy_level": 100.0,
            "next_sell_level": 110.0,
        }
        self.assertEqual(monitor.trigger_signature(metrics, {"bid": 109.0, "ask": 99.0}), "BUY@100.00")
        self.assertEqual(monitor.trigger_signature(metrics, {"bid": 111.0, "ask": 112.0}), "SELL@110.00")
        self.assertEqual(monitor.trigger_signature({**metrics, "open_count": 3}, {"bid": 111.0, "ask": 112.0}), "")

    def test_trigger_signature_uses_symbol_precision_for_state_key(self) -> None:
        class DummyInfo:
            digits = 3

        class DummyMt5:
            @staticmethod
            def symbol_info(symbol: str):
                return DummyInfo() if symbol == "USDJPY" else None

        original_mt5 = monitor.mt5
        try:
            monitor.mt5 = DummyMt5()
            metrics_a = {
                "symbol": "USDJPY",
                "open_count": 1,
                "max_open_total": 20,
                "next_buy_level": 0.0,
                "next_sell_level": 159.118708,
            }
            metrics_b = {
                **metrics_a,
                "next_sell_level": 159.123708,
            }
            self.assertEqual(monitor.trigger_signature(metrics_a, {"bid": 159.2, "ask": 159.21}), "SELL@159.119")
            self.assertEqual(monitor.trigger_signature(metrics_b, {"bid": 159.2, "ask": 159.21}), "SELL@159.124")
        finally:
            monitor.mt5 = original_mt5

    def test_update_trigger_watch_marks_suspected_when_trade_event_does_not_arrive(self) -> None:
        now_dt = datetime(2026, 4, 12, 4, 0, tzinfo=timezone.utc)
        previous = {"signature": "SELL@110.00", "first_seen_at": "2026-04-12T03:57:00+00:00"}

        watch, alert = monitor.update_trigger_watch(
            previous,
            signature="SELL@110.00",
            now_dt=now_dt,
            last_trade_event_at="2026-04-12T03:56:30+00:00",
            threshold_seconds=120.0,
        )

        self.assertEqual(alert["execution_alert"], "suspected_missed_open")
        self.assertTrue(alert["suspected_missed_open"])
        self.assertFalse(alert["probable_missed_open"])
        self.assertEqual(alert["trigger_age_seconds"], 180.0)
        self.assertEqual(watch["signature"], "SELL@110.00")

    def test_update_trigger_watch_marks_probable_after_another_threshold_window(self) -> None:
        now_dt = datetime(2026, 4, 12, 4, 2, 30, tzinfo=timezone.utc)
        previous = {"signature": "BUY@100.00", "first_seen_at": "2026-04-12T03:57:00+00:00"}

        watch, alert = monitor.update_trigger_watch(
            previous,
            signature="BUY@100.00",
            now_dt=now_dt,
            last_trade_event_at="2026-04-12T03:56:30+00:00",
            threshold_seconds=120.0,
        )

        self.assertEqual(watch["signature"], "BUY@100.00")
        self.assertEqual(alert["execution_alert"], "probable_missed_open")
        self.assertTrue(alert["suspected_missed_open"])
        self.assertTrue(alert["probable_missed_open"])
        self.assertEqual(alert["trigger_age_seconds"], 330.0)

    def test_update_trigger_watch_resets_after_trade_event(self) -> None:
        now_dt = datetime(2026, 4, 12, 4, 0, tzinfo=timezone.utc)
        previous = {"signature": "SELL@110.00", "first_seen_at": "2026-04-12T03:57:00+00:00"}

        watch, alert = monitor.update_trigger_watch(
            previous,
            signature="SELL@110.00",
            now_dt=now_dt,
            last_trade_event_at="2026-04-12T03:59:45+00:00",
            threshold_seconds=30.0,
        )

        self.assertEqual(alert["execution_alert"], "")
        self.assertFalse(alert["suspected_missed_open"])
        self.assertEqual(watch["first_seen_at"], now_dt.isoformat())

    def test_update_trigger_watch_suppresses_alert_when_runner_intentionally_blocks_entries(self) -> None:
        now_dt = datetime(2026, 4, 12, 4, 2, 30, tzinfo=timezone.utc)
        previous = {"signature": "BUY@100.00", "first_seen_at": "2026-04-12T03:57:00+00:00"}

        watch, alert = monitor.update_trigger_watch(
            previous,
            signature="BUY@100.00",
            now_dt=now_dt,
            last_trade_event_at="2026-04-12T03:56:30+00:00",
            threshold_seconds=120.0,
            suppress_execution_alert=True,
        )

        self.assertIsNone(watch)
        self.assertEqual(alert["execution_alert"], "")
        self.assertFalse(alert["suspected_missed_open"])
        self.assertFalse(alert["probable_missed_open"])
        self.assertEqual(alert["trigger_age_seconds"], 0.0)

    def test_refine_execution_alert_downgrades_probable_without_lane_event_write(self) -> None:
        refined = monitor.refine_execution_alert(
            execution_alert="probable_missed_open",
            trigger_first_seen_at="2026-04-12T21:05:13+00:00",
            event_last_write_at="2026-04-12T20:07:52+00:00",
            state_last_write_at="2026-04-12T23:53:40+00:00",
            runner_heartbeat_at="2026-04-12T23:53:41+00:00",
            lane_quote_cross_after_trigger=False,
        )

        self.assertEqual(refined["execution_alert"], "")
        self.assertEqual(refined["raw_execution_alert"], "probable_missed_open")
        self.assertEqual(refined["execution_evidence_quality"], "state_heartbeat_without_event_write")
        self.assertFalse(refined["lane_event_write_after_trigger"])
        self.assertTrue(refined["state_write_after_trigger"])

    def test_refine_execution_alert_clears_without_lane_quote_proof(self) -> None:
        refined = monitor.refine_execution_alert(
            execution_alert="probable_missed_open",
            trigger_first_seen_at="2026-04-12T21:05:13+00:00",
            event_last_write_at="2026-04-12T21:05:20+00:00",
            state_last_write_at="2026-04-12T21:05:22+00:00",
            runner_heartbeat_at="2026-04-12T21:05:23+00:00",
            lane_quote_cross_after_trigger=False,
        )

        self.assertEqual(refined["execution_alert"], "")
        self.assertEqual(refined["execution_evidence_quality"], "lane_event_write_after_trigger_no_quote_proof")
        self.assertTrue(refined["lane_event_write_after_trigger"])

    def test_refine_execution_alert_keeps_probable_with_lane_quote_proof(self) -> None:
        refined = monitor.refine_execution_alert(
            execution_alert="probable_missed_open",
            trigger_first_seen_at="2026-04-12T21:05:13+00:00",
            event_last_write_at="2026-04-12T21:05:20+00:00",
            state_last_write_at="2026-04-12T21:05:22+00:00",
            runner_heartbeat_at="2026-04-12T21:05:23+00:00",
            lane_quote_cross_after_trigger=True,
        )

        self.assertEqual(refined["execution_alert"], "probable_missed_open")
        self.assertEqual(refined["execution_evidence_quality"], "lane_quote_cross_after_trigger")
        self.assertTrue(refined["lane_quote_cross_after_trigger"])

    def test_refine_execution_alert_clears_when_spread_block_after_trigger_exists(self) -> None:
        refined = monitor.refine_execution_alert(
            execution_alert="probable_missed_open",
            trigger_first_seen_at="2026-04-12T21:05:13+00:00",
            event_last_write_at="2026-04-12T21:05:20+00:00",
            state_last_write_at="2026-04-12T21:05:22+00:00",
            runner_heartbeat_at="2026-04-12T21:05:23+00:00",
            lane_quote_cross_after_trigger=True,
            last_spread_block_at="2026-04-12T21:05:21+00:00",
            spread_block_count=4,
        )

        self.assertEqual(refined["execution_alert"], "")
        self.assertEqual(refined["execution_evidence_quality"], "spread_block_after_trigger")
        self.assertTrue(refined["lane_quote_cross_after_trigger"])

    def test_refine_execution_alert_clears_when_guard_block_after_trigger_exists(self) -> None:
        refined = monitor.refine_execution_alert(
            execution_alert="probable_missed_open",
            trigger_first_seen_at="2026-04-12T21:05:13+00:00",
            event_last_write_at="2026-04-12T21:05:20+00:00",
            state_last_write_at="2026-04-12T21:05:22+00:00",
            runner_heartbeat_at="2026-04-12T21:05:23+00:00",
            lane_quote_cross_after_trigger=True,
            last_guard_block_at="2026-04-12T21:05:21+00:00",
            guard_block_count=2,
        )

        self.assertEqual(refined["execution_alert"], "")
        self.assertEqual(refined["execution_evidence_quality"], "guard_block_after_trigger")
        self.assertTrue(refined["lane_quote_cross_after_trigger"])

    def test_execution_alert_flags_follow_refined_alert(self) -> None:
        self.assertEqual(
            monitor.execution_alert_flags(""),
            {"suspected_missed_open": False, "probable_missed_open": False},
        )
        self.assertEqual(
            monitor.execution_alert_flags("suspected_missed_open"),
            {"suspected_missed_open": True, "probable_missed_open": False},
        )
        self.assertEqual(
            monitor.execution_alert_flags("probable_missed_open"),
            {"suspected_missed_open": True, "probable_missed_open": True},
        )

    def test_execution_alert_notes_drop_raw_note_when_alert_is_downgraded(self) -> None:
        notes = monitor.execution_alert_notes(
            raw_execution_alert="probable_missed_open",
            execution_alert="",
            signature="BUY@1.35208",
            trigger_age_seconds=320.8,
            execution_evidence_quality="state_heartbeat_without_event_write",
        )

        self.assertEqual(
            notes,
            ["execution_alert_downgraded=probable_missed_open->clear due_to=state_heartbeat_without_event_write"],
        )

    def test_execution_alert_notes_keep_raw_note_when_alert_survives(self) -> None:
        notes = monitor.execution_alert_notes(
            raw_execution_alert="suspected_missed_open",
            execution_alert="suspected_missed_open",
            signature="SELL@110.00",
            trigger_age_seconds=180.0,
            execution_evidence_quality="lane_quote_cross_after_trigger",
        )

        self.assertEqual(notes, ["suspected_missed_open=SELL@110.00 age=180.0s"])

    def test_runner_status_note_for_live_contract_friction_invalid(self) -> None:
        note = monitor.runner_status_note(
            {
                "status": "live_contract_friction_invalid",
                "live_admissibility_spread_to_step_ratio": 20.0,
                "live_admissibility_max_entry_spread_ratio": 0.3,
                "live_admissibility_block_count": 7,
            }
        )
        self.assertEqual(
            note,
            "runner_status=live_contract_friction_invalid spread_to_step=20.00 max_ratio=0.30 blocked=7",
        )

    def test_suppress_execution_alerts_for_runner_positive_only_hold(self) -> None:
        self.assertTrue(
            monitor.suppress_execution_alerts_for_runner(
                {"status": "positive_only_hold_active"}
            )
        )

    def test_suppress_execution_alerts_for_runner_live_contract_friction_invalid(self) -> None:
        self.assertTrue(
            monitor.suppress_execution_alerts_for_runner(
                {"live_admissibility_reason": "live_contract_friction_invalid"}
            )
        )

    def test_suppress_execution_alerts_for_runner_ok_does_not_suppress(self) -> None:
        self.assertFalse(monitor.suppress_execution_alerts_for_runner({"status": "ok"}))

    def test_runner_status_note_empty_for_ok(self) -> None:
        self.assertEqual(monitor.runner_status_note({"status": "ok"}), "")

    def test_runner_status_note_uses_live_admissibility_reason_when_status_ok(self) -> None:
        note = monitor.runner_status_note(
            {
                "status": "ok",
                "live_admissibility_reason": "live_contract_friction_invalid",
                "live_admissibility_spread_to_step_ratio": 11.0,
                "live_admissibility_max_entry_spread_ratio": 10.0,
            }
        )
        self.assertEqual(note, "runner_status=live_contract_friction_invalid spread_to_step=11.00 max_ratio=10.00")

    def test_summarize_spread_blocks_since_counts_recent_wide_spread_events(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"ts_utc":"2026-04-17T15:10:00+00:00","action":"open_blocked_wide_spread","spread_ratio":9.5,"max_entry_spread_ratio":0.3}',
                        '{"ts_utc":"2026-04-17T15:12:00+00:00","action":"open_blocked_wide_spread","entry_context":{"spread_ratio":12.88,"max_entry_spread_ratio":0.3}}',
                        '{"ts_utc":"2026-04-17T15:14:00+00:00","action":"open_blocked_wide_spread","spread_ratio":14.25,"max_entry_spread_ratio":0.3}',
                        '{"ts_utc":"2026-04-17T15:15:00+00:00","action":"tick_history_fallback"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = monitor.summarize_spread_blocks_since(
                path,
                datetime(2026, 4, 17, 15, 11, tzinfo=timezone.utc),
            )

        self.assertEqual(summary["blocked_count"], 2)
        self.assertEqual(summary["last_blocked_at"], "2026-04-17T15:14:00+00:00")
        self.assertEqual(summary["max_spread_ratio"], 14.25)
        self.assertEqual(summary["max_entry_spread_ratio"], 0.3)

    def test_summarize_guard_blocks_since_counts_recent_guard_events(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"ts_utc":"2026-04-17T15:09:00+00:00","action":"open_guarded_admission","recovery_signal_count":3}',
                        '{"ts_utc":"2026-04-17T15:10:00+00:00","action":"open_guarded_admission","recovery_signal_count":8}',
                        '{"ts_utc":"2026-04-17T15:14:00+00:00","action":"open_guarded_admission","recovery_signal_count":5}',
                        '{"ts_utc":"2026-04-17T15:15:00+00:00","action":"tick_history_fallback"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = monitor.summarize_guard_blocks_since(
                path,
                datetime(2026, 4, 17, 15, 10, tzinfo=timezone.utc),
            )

        self.assertEqual(summary["blocked_count"], 2)
        self.assertEqual(summary["last_blocked_at"], "2026-04-17T15:14:00+00:00")
        self.assertEqual(summary["max_recovery_signal_count"], 8)

    def test_synthetic_live_admissibility_runner_marks_direct_live_spread_gate_block(self) -> None:
        synthetic = monitor.synthetic_live_admissibility_runner(
            runner={},
            direct_live=True,
            runner_started_at=datetime(2026, 4, 17, 15, 11, tzinfo=timezone.utc),
            runner_trade_opens=0,
            runner_trade_closes=0,
            spread_blocks={
                "blocked_count": 5,
                "last_blocked_at": "2026-04-17T15:14:00+00:00",
                "max_spread_ratio": 14.25,
                "max_entry_spread_ratio": 0.3,
            },
        )

        self.assertEqual(synthetic["status"], "live_contract_friction_invalid")
        self.assertEqual(synthetic["live_admissibility_reason"], "live_contract_friction_invalid")
        self.assertEqual(synthetic["live_admissibility_block_count"], 5)
        self.assertEqual(synthetic["live_admissibility_spread_to_step_ratio"], 14.25)
        self.assertEqual(synthetic["live_admissibility_max_entry_spread_ratio"], 0.3)

    def test_synthetic_live_admissibility_runner_skips_when_runner_already_has_status(self) -> None:
        synthetic = monitor.synthetic_live_admissibility_runner(
            runner={"status": "positive_only_hold_active"},
            direct_live=True,
            runner_started_at=datetime(2026, 4, 17, 15, 11, tzinfo=timezone.utc),
            runner_trade_opens=0,
            runner_trade_closes=0,
            spread_blocks={
                "blocked_count": 5,
                "last_blocked_at": "2026-04-17T15:14:00+00:00",
                "max_spread_ratio": 14.25,
                "max_entry_spread_ratio": 0.3,
            },
        )

        self.assertEqual(synthetic, {})

    def test_direct_live_open_carry_metrics_counts_pre_start_tickets(self) -> None:
        runner_started_at = datetime(2026, 4, 17, 15, 11, 38, tzinfo=timezone.utc)
        payload = {
            "symbols": {
                "GBPUSD": {
                    "open_tickets": [
                        {"ticket_kind": "hedge", "opened_time": 1776419674},
                        {"ticket_kind": "core", "opened_msc": 1776000000000},
                        {"ticket_kind": "core", "opened_msc": 1776449904000},
                    ]
                }
            }
        }

        carry = monitor.direct_live_open_carry_metrics(payload, runner_started_at=runner_started_at)

        self.assertEqual(carry["carry_open_count"], 2)
        self.assertEqual(carry["carry_kind_counts"], {"core": 1, "hedge": 1})

    def test_direct_live_open_carry_metrics_returns_zero_without_runner_start(self) -> None:
        payload = {
            "symbols": {
                "GBPUSD": {
                    "open_tickets": [
                        {"ticket_kind": "hedge", "opened_time": 1776419674},
                    ]
                }
            }
        }

        carry = monitor.direct_live_open_carry_metrics(payload, runner_started_at=None)

        self.assertEqual(carry["carry_open_count"], 0)
        self.assertEqual(carry["carry_kind_counts"], {})

    def test_broker_scope_summary_separates_scoped_and_outside_inventory(self) -> None:
        summary = monitor.broker_scope_summary(
            {
                941777: [
                    {"ticket": 1, "magic": 941777, "symbol": "EURUSD"},
                    {"ticket": 2, "magic": 941777, "symbol": "GBPUSD"},
                    {"ticket": 3, "magic": 941777, "symbol": "USDJPY"},
                    {"ticket": 4, "magic": 941777, "symbol": "USDJPY"},
                ]
            },
            live_magic=941777,
            scoped_symbols={"EURUSD", "GBPUSD"},
        )

        self.assertEqual(summary["total_open_count"], 4)
        self.assertEqual(summary["scoped_open_count"], 2)
        self.assertEqual(summary["outside_open_count"], 2)
        self.assertEqual(summary["outside_counts"], {"USDJPY": 2})

    def test_lane_live_magics_includes_attached_broker_magics(self) -> None:
        lane = {
            "restart_args": [
                "--symbols",
                "BTCUSD",
                "--live-magic",
                "941781",
                "--attach-broker-magic",
                "941785",
                "--attach-broker-magic",
                "941786",
            ]
        }
        state_payload = {"metadata": {"attached_broker_magics": [941786, 941785, 941781]}}

        magics = monitor.lane_live_magics(lane, state_payload)

        self.assertEqual(magics, (941781, 941786, 941785))

    def test_broker_scope_summary_aggregates_attached_magics(self) -> None:
        summary = monitor.broker_scope_summary(
            {
                941781: [{"ticket": 1, "magic": 941781, "symbol": "BTCUSD"}],
                941785: [{"ticket": 2, "magic": 941785, "symbol": "BTCUSD"}],
                941786: [{"ticket": 3, "magic": 941786, "symbol": "ETHUSD"}],
            },
            live_magics=(941781, 941785, 941786),
            scoped_symbols={"BTCUSD"},
        )

        self.assertEqual(summary["live_magics"], [941781, 941785, 941786])
        self.assertEqual(summary["total_open_count"], 3)
        self.assertEqual(summary["scoped_open_count"], 2)
        self.assertEqual(summary["outside_open_count"], 1)
        self.assertEqual(summary["outside_counts"], {"ETHUSD": 1})
        self.assertEqual(summary["per_magic_counts"], {941781: 1, 941785: 1, 941786: 1})

    def test_summarize_trigger_quote_proof_since_detects_crossing_quote(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"ts_utc":"2026-04-12T21:05:10+00:00","action":"tick_history_fallback","bid":109.0,"ask":109.5}',
                        '{"ts_utc":"2026-04-12T21:05:20+00:00","action":"tick_history_fallback","bid":110.5,"ask":111.0}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = monitor.summarize_trigger_quote_proof_since(
                path,
                datetime(2026, 4, 12, 21, 5, 13, tzinfo=timezone.utc),
                next_buy_level=0.0,
                next_sell_level=110.0,
            )

        self.assertTrue(summary["event_after_trigger"])
        self.assertTrue(summary["quote_cross_after_trigger"])
        self.assertEqual(summary["quote_cross_event_at"], "2026-04-12T21:05:20+00:00")

    def test_resolve_event_path_infers_unified_symbol_event_file(self) -> None:
        lane = {"kind": "shadow_unified"}
        state_path = Path("reports/unified_shadow_btcusd_state.json")
        metrics = {"symbol": "BTCUSD"}

        event_path = monitor.resolve_event_path(lane, state_path, metrics)

        self.assertEqual(event_path, Path("reports/unified_shadow_btcusd_events.jsonl"))

    def test_extract_state_metrics_handles_ratio_sleeve_payload(self) -> None:
        payload = {
            "pair": "CFG/BTC",
            "mode": "synthetic_ratio_lattice_shadow",
            "positions": [{"level_idx": 4}, {"level_idx": 5}],
            "market": {"last_bar_time": 1776101700},
            "stats": {"total_closes": 3, "max_open_total": 2},
        }

        metrics = monitor.extract_state_metrics(payload)

        self.assertEqual(metrics["symbol"], "CFG/BTC")
        self.assertEqual(metrics["open_count"], 2)
        self.assertEqual(metrics["close_count"], 3)
        self.assertEqual(metrics["last_bar_time"], 1776101700)
        self.assertEqual(metrics["max_open_total"], 2)

    def test_extract_state_metrics_aggregates_multi_symbol_fx_payload(self) -> None:
        payload = {
            "symbols": {
                "EURUSD": {
                    "symbol": "EURUSD",
                    "open_tickets": [{"ticket": 1}, {"ticket": 2}],
                    "realized_closes": 51,
                    "last_bar_time": 1776122700,
                    "last_tick_msc": 1776122724369,
                    "max_open_total": 15,
                    "mode": "raw_stateful_rearm",
                    "next_buy_level": 1.16635,
                    "next_sell_level": 1.17685,
                },
                "GBPUSD": {
                    "symbol": "GBPUSD",
                    "open_tickets": [{"ticket": 3}, {"ticket": 4}, {"ticket": 5}],
                    "realized_closes": 99,
                    "last_bar_time": 1776122700,
                    "last_tick_msc": 1776122725718,
                    "max_open_total": 22,
                    "mode": "raw_stateful_rearm",
                    "next_buy_level": 1.33868,
                    "next_sell_level": 1.35108,
                },
            }
        }

        metrics = monitor.extract_state_metrics(payload)

        self.assertEqual(metrics["symbol"], "")
        self.assertEqual(metrics["open_count"], 5)
        self.assertEqual(metrics["close_count"], 150)
        self.assertEqual(metrics["last_bar_time"], 1776122700)
        self.assertEqual(metrics["last_tick_msc"], 1776122725718)
        self.assertEqual(metrics["max_open_total"], 37)
        self.assertEqual(metrics["next_buy_level"], 0.0)
        self.assertEqual(metrics["next_sell_level"], 0.0)
        self.assertEqual(metrics["mode"], "multi_symbol_aggregate")

    def test_extract_state_metrics_collects_tick_state_fields(self) -> None:
        payload = {
            "symbols": {
                "BTCUSD": {
                    "symbol": "BTCUSD",
                    "open_tickets": [
                        {"ticket": 11},
                        {"ticket": 12},
                    ],
                    "realized_closes": 27,
                    "last_bar_time": 1776122700,
                    "last_tick_msc": 1776122724369,
                    "max_open_total": 20,
                    "rearm_opens": 3,
                    "rearm_tokens": [{"direction": "BUY", "level": 50000}],
                    "anchor_resets": 2,
                    "anchor_resets_flat": 1,
                    "anchor_resets_risk": 1,
                    "lattice_started_time": 1776120000,
                    "max_floating_loss_usd": -15.5,
                    "offensive_positive_close_ticket_profit_usd": 31.2,
                    "offensive_spend_usd": 4.4,
                    "offensive_budget_share": 0.25,
                    "offensive_closure_enabled": True,
                    "offensive_safety_margin_usd": 2.0,
                    "offensive_safety_margin_pct": 0.2,
                    "offensive_cut_cooldown_bars": 5,
                    "offensive_breakeven_band_usd": 0.75,
                    "max_lattice_window_bars": 240,
                    "breakout_buffer_pips": 0.5,
                    "base_step_px": 7.5,
                    "base_step_sell_px": 8.0,
                    "base_step_buy_px": 7.0,
                    "reconcile_open_max_drift_px": 2.5,
                    "open_realism_mode": "tick_native",
                    "close_realism_mode": "tick_native",
                    "raw_close_alpha": 1.0,
                    "raw_close_style": "all_profitable",
                    "momentum_gate": True,
                    "mode": "tick_stateful_rearm",
                    "next_buy_level": 65000.0,
                    "next_sell_level": 65100.0,
                }
            }
        }

        metrics = monitor.extract_state_metrics(payload)

        self.assertEqual(metrics["open_count"], 2)
        self.assertEqual(metrics["close_count"], 27)
        self.assertEqual(metrics["rearm_opens"], 3)
        self.assertEqual(metrics["rearm_token_count"], 1)
        self.assertEqual(metrics["anchor_resets"], 2)
        self.assertEqual(metrics["lattice_started_time"], 1776120000)
        self.assertEqual(metrics["offensive_closure_enabled"], True)
        self.assertEqual(metrics["max_floating_loss_usd"], -15.5)
        self.assertEqual(metrics["open_realism_mode"], "tick_native")

    def test_summarize_events_counts_ratio_sleeve_actions(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ratio_events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"ts_utc":"2026-04-13T17:22:59+00:00","action":"runner_start"}',
                        '{"ts_utc":"2026-04-13T17:23:00+00:00","action":"open_sleeve"}',
                        '{"ts_utc":"2026-04-13T17:25:00+00:00","action":"close_sleeve"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            events = monitor.summarize_events(path)

        self.assertEqual(events["trade_open_count"], 1)
        self.assertEqual(events["trade_close_count"], 1)
        self.assertEqual(events["last_trade_action"], "close_sleeve")

    def test_summarize_trade_events_since_tracks_realized_close_pnl(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"ts_utc":"2026-04-14T19:54:24+00:00","action":"close_ticket","realized_pnl":-4.00}',
                        '{"ts_utc":"2026-04-14T19:54:26+00:00","action":"open_ticket"}',
                        '{"ts_utc":"2026-04-14T19:54:27+00:00","action":"close_ticket","realized_pnl":5.99}',
                        '{"ts_utc":"2026-04-14T19:54:28+00:00","action":"close_sleeve","net_usd":1.50}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = monitor.summarize_trade_events_since(
                path,
                datetime(2026, 4, 14, 19, 54, 25, tzinfo=timezone.utc),
            )

        self.assertEqual(summary["trade_open_count"], 1)
        self.assertEqual(summary["trade_close_count"], 2)
        self.assertEqual(summary["trade_close_realized_usd"], 7.49)

    def test_direct_live_state_carry_metrics_separates_inherited_and_runner_session(self) -> None:
        carry = monitor.direct_live_state_carry_metrics(
            {"closes": 16, "realized_net_usd": -124.40},
            inherited_closes=12,
            inherited_realized_usd=-110.54,
            runner_trade_closes=1,
            runner_trade_realized_usd=5.99,
        )

        self.assertEqual(carry["carry_closes"], 3)
        self.assertEqual(carry["carry_realized_usd"], -19.85)

    def test_clean_forward_metrics_uses_reset_snapshot(self) -> None:
        metrics = monitor.clean_forward_metrics(
            {"realized_net_usd": 19.25, "closes": 12},
            {"realized_net_usd": 11.0, "closes": 5, "reset_at": "2026-04-12T04:31:00+00:00", "reset_type": "stale_tick_repair"},
        )

        self.assertEqual(metrics["clean_forward_reset_at"], "2026-04-12T04:31:00+00:00")
        self.assertEqual(metrics["clean_forward_source"], "stale_tick_repair")
        self.assertEqual(metrics["clean_forward_realized_delta_usd"], 8.25)
        self.assertEqual(metrics["clean_forward_new_closes"], 7)
        self.assertFalse(metrics["clean_forward_counter_reset"])

    def test_clean_forward_metrics_clamps_after_runner_state_reset(self) -> None:
        metrics = monitor.clean_forward_metrics(
            {"realized_net_usd": 0.0, "closes": 0},
            {"realized_net_usd": 16.57, "closes": 1, "reset_at": "2026-04-12T04:57:20+00:00", "reset_type": "stale_tick_repair"},
        )

        self.assertEqual(metrics["clean_forward_realized_delta_usd"], 0.0)
        self.assertEqual(metrics["clean_forward_new_closes"], 0)
        self.assertTrue(metrics["clean_forward_counter_reset"])

    def test_single_position_session_parity_allows_backfill_carry_in(self) -> None:
        payload = {"engine": {"position": None, "open_count": 0}}
        runner = {"started_at": "2026-04-12T20:20:00+00:00"}
        original = monitor.summarize_trade_events_since
        try:
            monitor.summarize_trade_events_since = lambda path, since_dt: {
                "exists": True,
                "first_trade_event_at": "2026-04-12T20:23:00+00:00",
                "first_trade_action": "close",
                "last_trade_event_at": "2026-04-12T20:25:00+00:00",
                "last_trade_action": "close",
                "trade_open_count": 2,
                "trade_close_count": 3,
            }
            parity = monitor.single_position_session_parity(
                kind="shadow_coinbase_spot",
                state_payload=payload,
                metrics={"open_count": 0},
                runner=runner,
                event_path=Path("dummy.jsonl"),
            )
        finally:
            monitor.summarize_trade_events_since = original

        self.assertEqual(parity["session_carry_in"], 1)
        self.assertEqual(parity["parity_alert"], "")

    def test_single_position_session_parity_flags_mismatch(self) -> None:
        payload = {"engine": {"position": None, "open_count": 0}}
        runner = {"started_at": "2026-04-12T20:20:00+00:00"}
        original = monitor.summarize_trade_events_since
        try:
            monitor.summarize_trade_events_since = lambda path, since_dt: {
                "exists": True,
                "first_trade_event_at": "2026-04-12T20:21:00+00:00",
                "first_trade_action": "open",
                "last_trade_event_at": "2026-04-12T20:22:00+00:00",
                "last_trade_action": "open",
                "trade_open_count": 1,
                "trade_close_count": 0,
            }
            parity = monitor.single_position_session_parity(
                kind="shadow_coinbase_spot",
                state_payload=payload,
                metrics={"open_count": 0},
                runner=runner,
                event_path=Path("dummy.jsonl"),
            )
        finally:
            monitor.summarize_trade_events_since = original

        self.assertEqual(parity["parity_alert"], "single_position_session_parity_mismatch:expected_1_have_0")

    def test_forward_review_reason_surfaces_for_ratio_sleeve(self) -> None:
        reason = monitor.forward_review_reason(
            {"name": "shadow_coinbase_cfgbtc_ratio_sleeve", "kind": "shadow_coinbase_spot"},
            {"forward_status": "seeded_in_position", "realized_closes": "0"},
        )

        self.assertEqual(reason, "forward=seeded_in_position closes=0")

    def test_forward_review_reason_surfaces_realized_for_coinbase_spot_lane(self) -> None:
        reason = monitor.forward_review_reason(
            {"name": "shadow_coinbase_experimental_rotation_bb_rsi", "kind": "shadow_coinbase_spot"},
            {"forward_status": "holding_up", "realized_net_usd": "4.4380", "realized_closes": "56"},
        )

        self.assertEqual(reason, "forward=holding_up realized=+4.44 closes=56")

    def test_forward_review_reason_surfaces_realized_for_crypto_candidate_lane(self) -> None:
        reason = monitor.forward_review_reason(
            {"name": "shadow_btcusd_h1_step30", "kind": "shadow_crypto_candidate"},
            {"forward_status": "lagging", "realized_net_usd": "-12.345", "realized_closes": "18"},
        )

        self.assertEqual(reason, "forward=lagging realized=-12.35 closes=18")

    def test_load_combined_forward_review_rows_merges_multiple_csvs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path_a = Path(tmpdir) / "a.csv"
            path_b = Path(tmpdir) / "b.csv"
            path_a.write_text(
                "\n".join(
                    [
                        "lane_name,forward_status,realized_closes",
                        "shadow_coinbase_arbusd_rsi7,holding_up,37",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            path_b.write_text(
                "\n".join(
                    [
                        "lane_name,forward_status,realized_closes",
                        "shadow_btcusd_h1_step30,holding_up_in_position,18",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rows = monitor.load_combined_forward_review_rows([path_a, path_b])

        self.assertEqual(rows["shadow_coinbase_arbusd_rsi7"]["forward_status"], "holding_up")
        self.assertEqual(rows["shadow_btcusd_h1_step30"]["forward_status"], "holding_up_in_position")

    def test_proof_readiness_reason_surfaces_for_ratio_sleeve(self) -> None:
        reason = monitor.proof_readiness_reason(
            {"name": "shadow_coinbase_cfgbtc_ratio_sleeve"},
            {
                "role": "scale_up",
                "current_gate": "waiting_first_close",
                "deployment_posture": "shadow_only_scale_up",
            },
        )

        self.assertEqual(reason, "proof_role=scale_up gate=waiting_first_close posture=shadow_only_scale_up")

    def test_fx_graduation_reason_surfaces_progress_for_gbp_shadow_lane(self) -> None:
        reason = monitor.fx_graduation_reason(
            {"name": "shadow_gbpusd_tick_forward"},
            {
                "readiness": "shadow_proof_positive",
                "progress_label": "3/20 durable closes",
                "progress_pct": "15.0%",
                "next_gate": "accumulate_20_plus_clean_closes",
            },
        )

        self.assertEqual(
            reason,
            "fx_grad=shadow_proof_positive progress=3/20 durable closes(15.0%) next=accumulate_20_plus_clean_closes",
        )

    def test_should_monitor_trigger_suppresses_stale_fx_market(self) -> None:
        now_dt = datetime(2026, 4, 12, 21, 8, tzinfo=timezone.utc)
        self.assertFalse(
            monitor.should_monitor_trigger(
                "live_fx",
                {"last_bar_time": int(datetime(2026, 4, 10, 20, 54, tzinfo=timezone.utc).timestamp()), "next_buy_level": 1.17, "next_sell_level": 1.17},
                now_dt,
            )
        )

    def test_trade_event_gap_check_excludes_fx_lanes(self) -> None:
        self.assertFalse(monitor.trustworthy_trade_event_gap_check("live_fx"))
        self.assertFalse(monitor.trustworthy_trade_event_gap_check("shadow_fx"))
        self.assertTrue(monitor.trustworthy_trade_event_gap_check("live_crypto"))
        self.assertTrue(monitor.trustworthy_trade_event_gap_check("shadow_unified"))

    def test_trade_event_presence_check_keeps_fx_lanes(self) -> None:
        self.assertTrue(monitor.trustworthy_trade_event_presence_check("live_fx"))
        self.assertTrue(monitor.trustworthy_trade_event_presence_check("shadow_fx"))
        self.assertTrue(monitor.trustworthy_trade_event_presence_check("shadow_crypto"))
        self.assertFalse(monitor.trustworthy_trade_event_presence_check("shadow_coinbase_spot"))

    def test_missing_trade_events_note_suppressed_for_live_fx_with_fresh_runtime_state(self) -> None:
        note = monitor.missing_trade_events_note(
            kind="live_fx",
            open_count=4,
            last_trade_event_at=None,
            state_last_write_at="2026-04-17T06:06:39+00:00",
            runner_heartbeat_at="2026-04-17T06:06:40+00:00",
            runner_started_at="2026-04-17T00:50:00+00:00",
        )

        self.assertEqual(note, "")

    def test_missing_trade_events_note_kept_for_live_fx_without_fresh_runtime_state(self) -> None:
        note = monitor.missing_trade_events_note(
            kind="live_fx",
            open_count=3,
            last_trade_event_at=None,
            state_last_write_at="2026-04-16T16:48:41+00:00",
            runner_heartbeat_at="2026-04-16T16:48:41+00:00",
            runner_started_at="2026-04-17T00:50:00+00:00",
        )

        self.assertEqual(note, "missing_trade_events")


if __name__ == "__main__":
    unittest.main()
