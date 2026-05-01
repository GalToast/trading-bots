#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
import os
from pathlib import Path
from time import time
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import watch_penetration_lattice_runners as watchdog


class WatchdogLiveAlertTests(unittest.TestCase):
    def test_acquire_loop_lock_claims_empty_lock_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "watchdog.lock"
            acquired, payload = watchdog.acquire_loop_lock(
                lock_path,
                loop_name="shadow_watchdog",
                stale_after_seconds=180.0,
            )

        self.assertTrue(acquired)
        self.assertEqual(int(payload["pid"]), os.getpid())
        self.assertEqual(payload["loop_name"], "shadow_watchdog")

    def test_acquire_loop_lock_rejects_live_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "watchdog.lock"
            lock_path.write_text(
                '{"pid": 4321, "loop_name": "crypto_watchdog", "create_time": 10.0}',
                encoding="utf-8",
            )

            class FakeProcess:
                def __init__(self, pid: int):
                    self.pid = pid

                def create_time(self) -> float:
                    return 10.0

                def is_running(self) -> bool:
                    return True

            fake_psutil = type(
                "FakePsutil",
                (),
                {
                    "Error": RuntimeError,
                    "Process": staticmethod(lambda pid: FakeProcess(pid)),
                },
            )

            with patch.object(watchdog, "psutil", fake_psutil):
                acquired, payload = watchdog.acquire_loop_lock(
                    lock_path,
                    loop_name="crypto_watchdog",
                    stale_after_seconds=180.0,
                )

        self.assertFalse(acquired)
        self.assertEqual(int(payload["pid"]), 4321)

    def test_acquire_loop_lock_reclaims_dead_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "watchdog.lock"
            lock_path.write_text(
                '{"pid": 987654, "loop_name": "shadow_watchdog", "create_time": 20.0}',
                encoding="utf-8",
            )
            now = time()
            os.utime(lock_path, (now - 600.0, now - 600.0))

            class FakePsutil:
                Error = RuntimeError

                @staticmethod
                def Process(pid: int):
                    raise RuntimeError("missing")

            with patch.object(watchdog, "psutil", FakePsutil):
                acquired, payload = watchdog.acquire_loop_lock(
                    lock_path,
                    loop_name="shadow_watchdog",
                    stale_after_seconds=180.0,
                )

        self.assertTrue(acquired)
        self.assertEqual(int(payload["pid"]), os.getpid())

    def test_list_python_processes_uses_psutil_without_shelling_out(self) -> None:
        class FakeProc:
            def __init__(self, info):
                self.info = info

        fake_psutil = type(
            "FakePsutil",
            (),
            {
                "Error": RuntimeError,
                "process_iter": staticmethod(
                    lambda attrs: iter(
                        [
                            FakeProc(
                                {
                                    "pid": 1234,
                                    "name": "python.exe",
                                    "cmdline": ["python.exe", "runner.py", "--lane", "alpha"],
                                    "create_time": 1_760_000_000.0,
                                }
                            ),
                            FakeProc(
                                {
                                    "pid": 555,
                                    "name": "terminal64.exe",
                                    "cmdline": ["terminal64.exe"],
                                    "create_time": 1_760_000_001.0,
                                }
                            ),
                        ]
                    )
                ),
            },
        )

        with (
            patch.object(watchdog, "psutil", fake_psutil),
            patch.object(watchdog.subprocess, "run") as run_mock,
        ):
            rows = watchdog.list_python_processes()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], 1234)
        self.assertIn("runner.py", rows[0]["command_line"])
        run_mock.assert_not_called()

    def test_refresh_lane_scoreboard_uses_no_window_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "helper.py"
            script_path.write_text("print('ok')\n", encoding="utf-8")
            completed = type("Completed", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})()
            with patch.object(watchdog.subprocess, "run", return_value=completed) as run_mock:
                result = watchdog.refresh_lane_scoreboard(script_path)

        self.assertTrue(result["ok"])
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["creationflags"], watchdog.NO_WINDOW_FLAGS)
        self.assertEqual(kwargs["cwd"], watchdog.ROOT)

    def test_refresh_lane_scoreboard_skips_when_outputs_are_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            script_path = tmp / "build_penetration_lane_scoreboard.py"
            csv_path = tmp / "penetration_lattice_lane_scoreboard.csv"
            md_path = tmp / "penetration_lattice_lane_scoreboard.md"
            script_path.write_text("print('ok')\n", encoding="utf-8")
            csv_path.write_text("lane_id\n", encoding="utf-8")
            md_path.write_text("# ok\n", encoding="utf-8")
            now = time()
            for path in (csv_path, md_path):
                path.touch()
                os.utime(path, (now, now))

            policy = {
                script_path.name: {
                    "outputs": (csv_path, md_path),
                    "min_age_seconds": 120.0,
                }
            }
            with (
                patch.dict(watchdog.REFRESH_POLICY_BY_SCRIPT, policy, clear=False),
                patch.object(watchdog.subprocess, "run") as run_mock,
            ):
                result = watchdog.refresh_lane_scoreboard(script_path)

        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "fresh_outputs")
        run_mock.assert_not_called()

    def test_run_watchdog_can_skip_shared_operator_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            report_json = tmp / "watchdog.json"
            report_md = tmp / "watchdog.md"
            events_jsonl = tmp / "watchdog.jsonl"
            quarantine_json = tmp / "quarantine.json"
            with (
                patch.object(watchdog, "read_registry", return_value=[]),
                patch.object(watchdog, "refresh_lane_scoreboard") as refresh_mock,
                patch.object(watchdog, "load_scoreboard_totals", return_value={}),
                patch.object(watchdog, "load_reset_baselines", return_value={}),
                patch.object(watchdog, "load_quarantine_state", return_value={"updated_at": "", "lanes": {}}),
                patch.object(watchdog, "write_quarantine_state") as _write_quarantine,
                patch.object(watchdog, "write_reports") as _write_reports,
            ):
                rows = watchdog.run_watchdog(
                    tmp / "registry.json",
                    report_json,
                    report_md,
                    events_jsonl,
                    repair=False,
                    lanes_filter=None,
                    force_restart=False,
                    quarantine_state_path=quarantine_json,
                    loop_state_path=None,
                    loop_name="feeder_crypto_canary",
                    loop_started_at="2026-04-14T15:00:00+00:00",
                    refresh_shared_operator_artifacts=False,
                )

        self.assertEqual(rows, [])
        refresh_mock.assert_not_called()

    def test_build_loop_state_payload_counts_statuses(self) -> None:
        args = Namespace(
            registry="configs/penetration_lattice_runner_registry.json",
            report_json="reports/penetration_lattice_runner_watchdog.json",
            report_md="reports/penetration_lattice_runner_watchdog.md",
            events_jsonl="reports/penetration_lattice_runner_watchdog_events.jsonl",
            loop_state_json="reports/watchdog/crypto_watchdog_loop_state.json",
            loop_name="crypto_watchdog",
            repair=True,
            force_restart=False,
            loop=True,
            interval_seconds=30.0,
            lanes=["lane_a", "lane_b"],
        )
        payload = watchdog.build_loop_state_payload(
            loop_name="crypto_watchdog",
            status="ok",
            args=args,
            loop_started_at="2026-04-12T18:33:10+00:00",
            cycle_started_at="2026-04-12T18:33:13+00:00",
            cycle_completed_at="2026-04-12T18:33:14+00:00",
            consecutive_failures=0,
            rows=[
                {"name": "lane_a", "status": "ok"},
                {"name": "lane_b", "status": "stale"},
                {"name": "lane_c", "status": "stale"},
            ],
        )

        self.assertEqual(payload["loop_name"], "crypto_watchdog")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["rows_total"], 3)
        self.assertEqual(payload["status_counts"], {"ok": 1, "stale": 2})
        self.assertEqual(payload["lanes"], ["lane_a", "lane_b"])
        self.assertEqual(payload["loop_started_at"], "2026-04-12T18:33:10+00:00")

    def test_write_loop_state_records_last_error(self) -> None:
        args = Namespace(
            registry="configs/penetration_lattice_runner_registry.json",
            report_json="reports/penetration_lattice_runner_watchdog.json",
            report_md="reports/penetration_lattice_runner_watchdog.md",
            events_jsonl="reports/penetration_lattice_runner_watchdog_events.jsonl",
            loop_state_json="reports/watchdog/crypto_watchdog_loop_state.json",
            loop_name="crypto_watchdog",
            repair=True,
            force_restart=False,
            loop=True,
            interval_seconds=30.0,
            lanes=["lane_a"],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "loop_state.json"
            watchdog.write_loop_state(
                path,
                loop_name="crypto_watchdog",
                status="error",
                args=args,
                loop_started_at="2026-04-12T18:33:10+00:00",
                cycle_started_at="2026-04-12T18:33:13+00:00",
                cycle_completed_at="2026-04-12T18:33:14+00:00",
                consecutive_failures=2,
                error=RuntimeError("boom"),
            )
            payload = watchdog.load_json(path)

        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["consecutive_failures"], 2)
        self.assertEqual(payload["loop_started_at"], "2026-04-12T18:33:10+00:00")
        self.assertEqual(payload["last_error"]["type"], "RuntimeError")
        self.assertEqual(payload["last_error"]["message"], "boom")

    def test_build_recent_incidents_only_includes_status_changes(self) -> None:
        previous_payload = {
            "rows": [
                {"name": "lane_a", "status": "ok"},
                {"name": "lane_b", "status": "erroring"},
                {"name": "lane_c", "status": "ok"},
            ]
        }
        status_rows = [
            {"name": "lane_a", "status": "ok", "reasons": []},
            {"name": "lane_b", "status": "ok", "reasons": ["runner recovered"], "heartbeat_age_seconds": 4.2},
            {"name": "lane_c", "status": "stale", "reasons": ["heartbeat_age=300.0s"], "heartbeat_age_seconds": 300.0, "source_tick_lag_seconds": 190.0},
            {"name": "lane_d", "status": "ok", "reasons": []},
        ]

        incidents = watchdog.build_recent_incidents(previous_payload, status_rows)

        self.assertEqual([row["lane"] for row in incidents], ["lane_b", "lane_c"])
        self.assertEqual(incidents[0]["old_status"], "erroring")
        self.assertEqual(incidents[0]["new_status"], "ok")
        self.assertEqual(incidents[1]["old_status"], "ok")
        self.assertEqual(incidents[1]["new_status"], "stale")

    def test_disabled_lane_reports_paused(self) -> None:
        lane = {
            "name": "live_btcusd_exc2_tight_941779",
            "enabled": False,
            "pause_note": "paused_for_audit",
            "state_path": "reports/unused.json",
            "event_path": "reports/unused.jsonl",
        }
        with (
            patch.object(watchdog, "load_json", return_value={}),
            patch.object(watchdog, "heartbeat_from_state", return_value=(None, None, "missing")),
            patch.object(watchdog, "matching_processes", return_value=[]),
        ):
            row = watchdog.summarize_lane(lane, [], {}, {})
        self.assertEqual(row["status"], "paused")
        self.assertIn("paused_for_audit", row["reasons"])

    def test_broker_gap_reason_triggers_over_threshold(self) -> None:
        lane = {"name": "live_rearm_941777", "broker_gap_alert_usd": 25}
        total = {"realized_gap_usd": "-103.55", "realized_usd": "-27.39", "modeled_realized_usd": "76.16"}
        reason = watchdog.broker_gap_reason(lane, total)
        self.assertEqual(reason, "broker_gap=-103.55 broker=-27.39 modeled=+76.16")

    def test_load_scoreboard_totals_reads_total_rows_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scoreboard.csv"
            path.write_text(
                "\n".join(
                    [
                        "lane_id,lane_type,symbol,realized_gap_usd,net_usd",
                        "live_a,live,EURUSD,-1.0,2.0",
                        "live_a,live,TOTAL,-5.0,-1.0",
                        "shadow_x,shadow,TOTAL,0.0,3.0",
                    ]
                ),
                encoding="utf-8",
            )
            rows = watchdog.load_scoreboard_totals(path)
        self.assertEqual(set(rows.keys()), {"live_a", "shadow_x"})
        self.assertEqual(rows["live_a"]["symbol"], "TOTAL")

    def test_conflicting_process_marks_lane_conflict(self) -> None:
        lane = {
            "name": "live_btcusd_exc2_tight_941779",
            "state_path": "reports/penetration_lattice_shadow_btcusd_exc2_tight_state.json",
            "event_path": "reports/penetration_lattice_shadow_btcusd_exc2_tight_events.jsonl",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "reports/penetration_lattice_shadow_btcusd_exc2_tight_state.json",
            ],
        }
        processes = [
            {
                "pid": 69008,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_crypto_shadow.py --state-path reports/penetration_lattice_shadow_btcusd_exc2_tight_state.json --event-path reports/penetration_lattice_shadow_btcusd_exc2_tight_events.jsonl",
            },
            {
                "pid": 71508,
                "command_line": "python.exe scripts/live_penetration_lattice_crypto_shadow.py --state-path reports/penetration_lattice_shadow_btcusd_exc2_tight_state.json --event-path reports/penetration_lattice_shadow_btcusd_exc2_tight_events.jsonl",
            },
        ]
        with (
            patch.object(watchdog, "load_json", return_value={"runner": {}}),
            patch.object(watchdog, "heartbeat_from_state", return_value=("2026-04-11T03:14:37+00:00", 5.0, "state.updated_at")),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "conflict")
        self.assertEqual(row["process_ids"], [69008])
        self.assertEqual(row["conflicting_process_ids"], [71508])
        self.assertIn("conflicting_processes=71508", row["reasons"])

    def test_conflict_match_substrings_marks_unified_writer_conflict(self) -> None:
        lane = {
            "name": "unified_shadow_10symbol",
            "state_path": "reports/unified_shadow_btcusd_state.json",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_unified_shadow.py",
                "configs/universal_10symbol_rearm.json",
            ],
            "conflict_match_substrings": [
                "configs/universal_10symbol_rearm.json",
                "--state-dir reports/",
            ],
        }
        processes = [
            {
                "pid": 11908,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_unified_shadow.py --config configs/universal_10symbol_rearm.json --state-dir reports/ --poll-seconds 5",
            },
            {
                "pid": 61600,
                "command_line": "python.exe scripts/live_penetration_lattice_unified_shadow.py --config configs/universal_10symbol_rearm.json --state-dir reports/ --poll-seconds 5",
            },
        ]
        with (
            patch.object(watchdog, "load_json", return_value={"runner": {}}),
            patch.object(watchdog, "heartbeat_from_state", return_value=("2026-04-11T03:37:29+00:00", 5.0, "state.updated_at")),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "conflict")
        self.assertEqual(row["process_ids"], [11908])
        self.assertEqual(row["conflicting_process_ids"], [61600])

    def test_duplicate_matching_processes_mark_lane_conflict(self) -> None:
        lane = {
            "name": "shadow_coinbase_experimental_rotation_bb_rsi",
            "state_path": "reports/rotation_bb_rsi_shadow_state.json",
            "event_path": "reports/rotation_bb_rsi_shadow_events.jsonl",
            "process_match_substrings": ["scripts/live_rotation_bb_rsi_shadow.py"],
        }
        processes = [
            {
                "pid": 25396,
                "command_line": "python.exe scripts/live_rotation_bb_rsi_shadow.py --products RAVE-USD BAL-USD",
            },
            {
                "pid": 44832,
                "command_line": "python.exe scripts/live_rotation_bb_rsi_shadow.py --poll-seconds 30",
            },
        ]
        payload = {"runner": {}, "state": {"closes": 3}}
        with (
            patch.object(watchdog, "load_json", return_value=payload),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-12T02:26:48+00:00", 5.0, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "conflict")
        self.assertEqual(row["process_ids"], [25396, 44832])
        self.assertIn("duplicate_matching_processes=25396,44832", row["reasons"])

    def test_matching_processes_ignores_watchdog_loop(self) -> None:
        lane_substrings = ["nzdusd_m15_asym"]
        processes = [
            {
                "pid": 60101,
                "command_line": "python.exe scripts/watch_penetration_lattice_runners.py --repair --loop --lanes shadow_nzdusd_m15_asym shadow_gbpusd_m15_asym --loop-name shadow_watchdog",
            },
            {
                "pid": 60102,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_crypto_shadow.py --state-path reports/penetration_lattice_shadow_nzdusd_m15_asym_state.json --event-path reports/penetration_lattice_shadow_nzdusd_m15_asym_events.jsonl --symbol NZDUSD --timeframe M15",
            },
        ]
        matches = watchdog.matching_processes(processes, lane_substrings)
        self.assertEqual([int(proc["pid"]) for proc in matches], [60102])

    def test_summarize_lane_skips_watchdog_loop_arg_drift(self) -> None:
        lane = {
            "name": "shadow_nzdusd_m15_asym",
            "kind": "shadow_fx",
            "poll_seconds": 30,
            "state_path": "reports/penetration_lattice_shadow_nzdusd_m15_asym_state.json",
            "process_match_substrings": ["nzdusd_m15_asym"],
            "restart_args": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "--symbol",
                "NZDUSD",
                "--timeframe",
                "M15",
                "--state-path",
                "reports/penetration_lattice_shadow_nzdusd_m15_asym_state.json",
                "--event-path",
                "reports/penetration_lattice_shadow_nzdusd_m15_asym_events.jsonl",
            ],
        }
        processes = [
            {
                "pid": 70101,
                "command_line": "python.exe scripts/watch_penetration_lattice_runners.py --repair --loop --lanes shadow_nzdusd_m15_asym shadow_gbpusd_m15_asym --loop-name shadow_watchdog",
            }
        ]
        with (
            patch.object(watchdog, "load_json", return_value={"runner": {}, "symbols": {"NZDUSD": {"open_tickets": []}}}),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-14T17:05:50+00:00", 2.0, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "missing_process")
        self.assertEqual(row["arg_drift_process_ids"], [])
        self.assertFalse(any("arg_drift" in reason for reason in row["reasons"]))

    def test_runtime_arg_drift_marks_lane_degraded(self) -> None:
        lane = {
            "name": "live_ethusd_m5_warp_941784",
            "kind": "live_crypto",
            "poll_seconds": 1,
            "state_path": "reports/penetration_lattice_live_ethusd_m5_warp_state.json",
            "event_path": "reports/penetration_lattice_live_ethusd_m5_warp_events.jsonl",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "reports/penetration_lattice_live_ethusd_m5_warp_state.json",
            ],
            "restart_args": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "--direct-live",
                "--symbol",
                "ETHUSD",
                "--timeframe",
                "M5",
                "--poll-seconds",
                "1",
                "--state-path",
                "reports/penetration_lattice_live_ethusd_m5_warp_state.json",
                "--event-path",
                "reports/penetration_lattice_live_ethusd_m5_warp_events.jsonl",
            ],
        }
        processes = [
            {
                "pid": 6500,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_crypto_shadow.py --symbol ETHUSD --timeframe M5 --state-path reports\\penetration_lattice_live_ethusd_m5_warp_state.json --event-path reports\\penetration_lattice_live_ethusd_m5_warp_events.jsonl",
            }
        ]
        with (
            patch.object(watchdog, "load_json", return_value={"runner": {}, "symbols": {"ETHUSD": {"open_tickets": []}}}),
            patch.object(watchdog, "heartbeat_from_state", return_value=("2026-04-14T17:05:50+00:00", 2.0, "state.updated_at")),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "arg_drift")
        self.assertEqual(row["arg_drift_process_ids"], [6500])
        self.assertTrue(any("missing --direct-live" in reason for reason in row["reasons"]))
        self.assertTrue(any("missing --poll-seconds=1" in reason for reason in row["reasons"]))

    def test_runtime_arg_drift_allows_extra_args_when_required_args_match(self) -> None:
        lane = {
            "name": "live_solusd_m5_warp_941783",
            "kind": "live_crypto",
            "poll_seconds": 1,
            "state_path": "reports/penetration_lattice_live_solusd_m5_warp_state.json",
            "event_path": "reports/penetration_lattice_live_solusd_m5_warp_events.jsonl",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "reports/penetration_lattice_live_solusd_m5_warp_state.json",
            ],
            "restart_args": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "--direct-live",
                "--symbol",
                "SOLUSD",
                "--timeframe",
                "M5",
                "--poll-seconds",
                "1",
                "--state-path",
                "reports/penetration_lattice_live_solusd_m5_warp_state.json",
                "--event-path",
                "reports/penetration_lattice_live_solusd_m5_warp_events.jsonl",
            ],
        }
        processes = [
            {
                "pid": 2588,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_crypto_shadow.py --direct-live --symbol SOLUSD --timeframe M5 --poll-seconds 1 --state-path reports/penetration_lattice_live_solusd_m5_warp_state.json --event-path reports/penetration_lattice_live_solusd_m5_warp_events.jsonl --fresh-start --debug-runtime-check 1",
            }
        ]
        with (
            patch.object(watchdog, "load_json", return_value={"runner": {}, "symbols": {"SOLUSD": {"open_tickets": []}}}),
            patch.object(watchdog, "heartbeat_from_state", return_value=("2026-04-14T17:05:50+00:00", 2.0, "state.updated_at")),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["arg_drift_process_ids"], [])
        self.assertFalse(any("arg_drift" in reason for reason in row["reasons"]))

    def test_runner_exception_loop_marks_lane_erroring(self) -> None:
        lane = {
            "name": "shadow_coinbase_experimental_rave_supreme_god_killer",
            "kind": "shadow_coinbase_spot",
            "state_path": "reports/supreme_god_killer_state.json",
            "event_path": "reports/supreme_god_killer_events.jsonl",
            "process_match_substrings": ["scripts/live_supreme_god_killer_shadow.py"],
        }
        payload = {
            "runner": {
                "consecutive_exceptions": 11,
                "last_exception_at": "2026-04-12T02:41:49.616879+00:00",
                "last_exception_type": "CoinbaseAdvancedClientError",
                "last_exception_message": "HTTP 400 /api/v3/brokerage/market/products/RAVE-USD/candles: start must not be in the future",
                "last_successful_run_at": "2026-04-12T02:36:46.666747+00:00",
            }
        }
        processes = [
            {
                "pid": 31276,
                "command_line": "python.exe scripts/live_supreme_god_killer_shadow.py",
            }
        ]
        with (
            patch.object(watchdog, "load_json", return_value=payload),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-12T02:41:49+00:00", 5.0, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "erroring")
        self.assertTrue(any("runner_erroring=11" in reason for reason in row["reasons"]))

    def test_loop_bootstrap_grace_marks_missing_lane_starting(self) -> None:
        lane = {
            "name": "unified_shadow_10symbol",
            "kind": "shadow_unified",
            "poll_seconds": 5,
            "stale_after_seconds": 60,
            "state_path": "reports/unified_shadow_btcusd_state.json",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_unified_shadow.py",
                "configs/universal_10symbol_rearm.json",
            ],
        }
        payload = {
            "runner": {},
            "symbols": {
                "BTCUSD": {
                    "last_bar_time": 1776049200,
                    "open_tickets": [1, 2],
                }
            },
        }
        with (
            patch.object(watchdog, "load_json", return_value=payload),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-13T00:07:36+00:00", 6.0, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
            patch.object(watchdog, "utc_now", return_value=watchdog.parse_iso("2026-04-13T00:07:42+00:00")),
        ):
            row = watchdog.summarize_lane(
                lane,
                [],
                {},
                {},
                loop_started_at="2026-04-13T00:07:35+00:00",
            )
        self.assertEqual(row["status"], "starting")
        self.assertTrue(any("loop_bootstrap_grace=7.0s/15.0s" in reason for reason in row["reasons"]))

    def test_loop_bootstrap_grace_does_not_mask_stale_lane(self) -> None:
        lane = {
            "name": "unified_shadow_10symbol",
            "kind": "shadow_unified",
            "poll_seconds": 5,
            "stale_after_seconds": 60,
            "state_path": "reports/unified_shadow_btcusd_state.json",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_unified_shadow.py",
                "configs/universal_10symbol_rearm.json",
            ],
        }
        payload = {
            "runner": {},
            "symbols": {
                "BTCUSD": {
                    "last_bar_time": 1776049200,
                    "open_tickets": [1, 2],
                }
            },
        }
        with (
            patch.object(watchdog, "load_json", return_value=payload),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-13T00:06:03+00:00", 97.2, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
            patch.object(watchdog, "utc_now", return_value=watchdog.parse_iso("2026-04-13T00:07:42+00:00")),
        ):
            row = watchdog.summarize_lane(
                lane,
                [],
                {},
                {},
                loop_started_at="2026-04-13T00:07:35+00:00",
            )
        self.assertEqual(row["status"], "stale")
        self.assertIn("no_matching_process", row["reasons"])
        self.assertTrue(any("heartbeat_age=97.2s" in reason for reason in row["reasons"]))

    def test_recent_rate_limit_stats_surface_in_watchdog_row(self) -> None:
        lane = {
            "name": "shadow_coinbase_experimental_rave_rsi_exit_champion",
            "kind": "shadow_coinbase_spot",
            "state_path": "reports/rave_rsi_exit_champion_state.json",
            "event_path": "reports/rave_rsi_exit_champion_events.jsonl",
            "process_match_substrings": ["scripts/live_rave_rsi_exit_champion.py"],
        }
        payload = {"runner": {}}
        processes = [
            {
                "pid": 76176,
                "command_line": "python.exe scripts/live_rave_rsi_exit_champion.py",
            }
        ]
        with (
            patch.object(watchdog, "load_json", return_value=payload),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-12T05:14:18+00:00", 5.0, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
            patch.object(
                watchdog,
                "recent_rate_limit_stats",
                return_value={
                    "total": 3,
                    "live_fetch": 2,
                    "chunk": 1,
                    "last_at": "2026-04-12T05:13:42+00:00",
                },
            ),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["rate_limit_skip_count_30m"], 3)
        self.assertEqual(row["rate_limit_skip_live_fetch_30m"], 2)
        self.assertEqual(row["rate_limit_skip_chunk_30m"], 1)
        self.assertTrue(any("rate_limit_skips_30m=3 live=2 chunk=1" in reason for reason in row["reasons"]))

    def test_current_symbol_tick_info_reuses_existing_mt5_session(self) -> None:
        tick = type("FakeTick", (), {"time": 1775978407, "time_msc": 1775978407405})()
        with (
            patch.object(watchdog.mt5, "initialize", return_value=True) as initialize_mock,
            patch.object(watchdog.mt5, "shutdown") as shutdown_mock,
            patch.object(watchdog.mt5, "symbol_select", return_value=True) as symbol_select_mock,
            patch.object(watchdog.mt5, "symbol_info_tick", return_value=tick) as symbol_info_tick_mock,
            patch.object(watchdog.time, "time", return_value=1775978407.4),
        ):
            row = watchdog.current_symbol_tick_info("BTCUSD", mt5_session_ready=True)

        self.assertEqual(row["symbol"], "BTCUSD")
        self.assertEqual(row["tick_msc"], 1775978407405)
        self.assertAlmostEqual(row["tick_age_seconds"], 0.4, places=1)
        self.assertTrue(row["is_fresh"])
        initialize_mock.assert_not_called()
        shutdown_mock.assert_not_called()
        symbol_select_mock.assert_called_once_with("BTCUSD", True)
        symbol_info_tick_mock.assert_called_once_with("BTCUSD")

    def test_run_watchdog_batches_mt5_session_once_for_crypto_cycle(self) -> None:
        lane = {
            "name": "shadow_btcusd_m15_warp",
            "kind": "shadow_crypto",
            "state_path": "reports/penetration_lattice_shadow_btcusd_m15_warp_state.json",
            "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py"],
        }
        captured_mt5_session_ready: list[bool] = []

        def fake_summarize_lane(*args, **kwargs):
            captured_mt5_session_ready.append(bool(kwargs.get("mt5_session_ready")))
            return {
                "name": lane["name"],
                "status": "ok",
                "reasons": [],
                "process_ids": [],
                "conflicting_process_ids": [],
                "heartbeat_at": "2026-04-14T06:00:00+00:00",
                "heartbeat_age_seconds": 5.0,
                "last_bar_time": 0,
                "open_count": 0,
                "state_path": lane["state_path"],
                "event_path": "reports/penetration_lattice_shadow_btcusd_m15_warp_events.jsonl",
            }

        with (
            patch.object(watchdog, "read_registry", return_value=[lane]),
            patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
            patch.object(watchdog, "load_scoreboard_totals", return_value={}),
            patch.object(watchdog, "load_reset_baselines", return_value={}),
            patch.object(watchdog, "load_quarantine_state", return_value={"lanes": {}}),
            patch.object(watchdog, "active_quarantine_entries", return_value={}),
            patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_fx_graduation_rows", return_value={}),
            patch.object(watchdog, "list_python_processes", return_value=[]),
            patch.object(watchdog.mt5_terminal_guard, "initialize_mt5", return_value=(True, {})) as initialize_mock,
            patch.object(watchdog.mt5, "shutdown") as shutdown_mock,
            patch.object(watchdog, "summarize_lane", side_effect=fake_summarize_lane),
            patch.object(watchdog, "write_reports"),
        ):
            rows = watchdog.run_watchdog(
                Path("registry.json"),
                Path("report.json"),
                Path("report.md"),
                Path("events.jsonl"),
                False,
                None,
                False,
            )

        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(captured_mt5_session_ready, [True])
        initialize_mock.assert_called_once_with(mt5_module=watchdog.mt5)
        shutdown_mock.assert_called_once_with()

    def test_run_watchdog_emits_summary_checkpoint_events(self) -> None:
        lane = {
            "name": "shadow_nzdusd_m15_asym",
            "kind": "shadow_fx",
            "state_path": "reports/penetration_lattice_shadow_nzdusd_m15_asym_state.json",
            "restart_args": ["scripts/live_penetration_lattice_tick_shadow.py"],
        }

        emitted_events: list[str] = []

        def capture_append_startup_event(*_args: object, **kwargs: object) -> None:
            emitted = kwargs.get("event")
            if isinstance(emitted, str):
                emitted_events.append(emitted)

        def fake_summarize_lane(*_args: object, **kwargs: object) -> dict[str, object]:
            self.assertFalse(bool(kwargs.get("mt5_session_ready")))
            return {
                "name": lane["name"],
                "status": "ok",
                "reasons": [],
                "process_ids": [],
                "conflicting_process_ids": [],
                "heartbeat_at": "2026-04-14T06:00:00+00:00",
                "heartbeat_age_seconds": 5.0,
                "last_bar_time": 0,
                "open_count": 0,
                "state_path": lane["state_path"],
                "event_path": "reports/penetration_lattice_shadow_nzdusd_m15_asym_events.jsonl",
            }

        with (
            patch.object(watchdog, "read_registry", return_value=[lane]),
            patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
            patch.object(watchdog, "load_scoreboard_totals", return_value={}),
            patch.object(watchdog, "load_reset_baselines", return_value={}),
            patch.object(watchdog, "load_quarantine_state", return_value={"lanes": {}}),
            patch.object(watchdog, "active_quarantine_entries", return_value={}),
            patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_fx_graduation_rows", return_value={}),
            patch.object(watchdog, "list_python_processes", return_value=[]),
            patch.object(watchdog, "append_startup_event", side_effect=capture_append_startup_event),
            patch.object(watchdog, "write_quarantine_state"),
            patch.object(watchdog, "write_reports"),
            patch.object(watchdog, "summarize_lane", side_effect=fake_summarize_lane),
        ):
            rows = watchdog.run_watchdog(
                Path("registry.json"),
                Path("report.json"),
                Path("report.md"),
                Path("events.jsonl"),
                False,
                None,
                False,
            )

        self.assertEqual(rows[0]["status"], "ok")
        self.assertIn("run_watchdog_state_loaded", emitted_events)
        self.assertIn("run_watchdog_summary_begin", emitted_events)
        self.assertIn("run_watchdog_summary_enter", emitted_events)
        self.assertIn("run_watchdog_summary_exit", emitted_events)
        self.assertNotIn("run_watchdog_summary_exception", emitted_events)

    def test_run_watchdog_summary_exception_includes_active_lane_context(self) -> None:
        lanes = [
            {
                "name": "shadow_nzdusd_m15_asym",
                "kind": "shadow_fx",
                "state_path": "reports/penetration_lattice_shadow_nzdusd_m15_asym_state.json",
                "restart_args": ["scripts/live_penetration_lattice_tick_shadow.py"],
            },
            {
                "name": "shadow_nzdusd_m15_warp",
                "kind": "shadow_fx",
                "state_path": "reports/penetration_lattice_shadow_nzdusd_m15_warp_state.json",
                "restart_args": ["scripts/live_penetration_lattice_tick_shadow.py"],
            },
        ]

        emitted_events: list[dict[str, object]] = []

        def capture_append_startup_event(*_args: object, **kwargs: object) -> None:
            event = kwargs.get("event")
            if isinstance(event, str) and event == "run_watchdog_summary_exception":
                emitted_events.append({k: v for k, v in kwargs.items() if k in {"event", "lane_name", "lane_index"}})

        def fake_summarize_lane(*_args: object, **_kwargs: object) -> dict[str, object]:
            if _summarize_count["count"] == 1:
                _summarize_count["count"] += 1
                return {
                    "name": lanes[0]["name"],
                    "status": "ok",
                    "reasons": [],
                    "process_ids": [],
                    "conflicting_process_ids": [],
                    "heartbeat_at": "2026-04-14T06:00:00+00:00",
                    "heartbeat_age_seconds": 5.0,
                    "last_bar_time": 0,
                    "open_count": 0,
                    "state_path": lanes[0]["state_path"],
                    "event_path": "reports/penetration_lattice_shadow_nzdusd_m15_asym_events.jsonl",
                }
            raise RuntimeError("summary failure")

        _summarize_count = {"count": 0}

        with (
            patch.object(watchdog, "read_registry", return_value=lanes),
            patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
            patch.object(watchdog, "load_scoreboard_totals", return_value={}),
            patch.object(watchdog, "load_reset_baselines", return_value={}),
            patch.object(watchdog, "load_quarantine_state", return_value={"lanes": {}}),
            patch.object(watchdog, "active_quarantine_entries", return_value={}),
            patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_fx_graduation_rows", return_value={}),
            patch.object(watchdog, "list_python_processes", return_value=[]),
            patch.object(watchdog, "append_startup_event", side_effect=capture_append_startup_event),
            patch.object(watchdog, "write_quarantine_state"),
            patch.object(watchdog, "write_reports"),
            patch.object(watchdog, "summarize_lane", side_effect=fake_summarize_lane),
        ):
            with self.assertRaises(RuntimeError):
                watchdog.run_watchdog(
                    Path("registry.json"),
                    Path("report.json"),
                    Path("report.md"),
                    Path("events.jsonl"),
                    False,
                    None,
                    False,
                )

        self.assertEqual(len(emitted_events), 1)
        self.assertEqual(emitted_events[0].get("lane_name"), "shadow_nzdusd_m15_asym")
        self.assertEqual(emitted_events[0].get("lane_index"), 0)

    def test_main_loop_initializes_mt5_once_and_shuts_down(self) -> None:
        args = Namespace(
            registry="configs/penetration_lattice_runner_registry.json",
            report_json="reports/watchdog.json",
            report_md="reports/watchdog.md",
            events_jsonl="reports/watchdog_events.jsonl",
            loop_state_json="reports/watchdog_loop_state.json",
            quarantine_state_json="",
            loop_name="crypto_watchdog",
            repair=False,
            force_restart=False,
            loop=True,
            interval_seconds=30.0,
            skip_shared_operator_refresh=False,
            lanes=None,
        )

        with (
            patch.object(watchdog, "parse_args", return_value=args),
            patch.object(
                watchdog,
                "acquire_loop_lock",
                return_value=(True, {"pid": 1, "loop_name": "crypto_watchdog", "create_time": 0.0}),
            ),
            patch.object(watchdog, "release_loop_lock"),
            patch.object(watchdog, "write_loop_state"),
            patch.object(
                watchdog,
                "read_registry",
                return_value=[
                    {"name": "shadow_btcusd_m15_warp", "kind": "shadow_crypto", "state_path": "reports/state.json"},
                ],
            ) as read_registry_mock,
            patch.object(watchdog.mt5_terminal_guard, "initialize_mt5", return_value=(True, {})) as initialize_mock,
            patch.object(watchdog.mt5, "shutdown") as shutdown_mock,
            patch.object(watchdog, "run_watchdog", side_effect=SystemExit(0)) as run_watchdog_mock,
        ):
            with self.assertRaises(SystemExit):
                watchdog.main()

        initialize_mock.assert_called_once_with(mt5_module=watchdog.mt5)
        run_watchdog_mock.assert_called_once()
        read_registry_mock.assert_called_once()
        shutdown_mock.assert_called_once_with()

        call_kwargs = run_watchdog_mock.call_args.kwargs
        self.assertTrue(bool(call_kwargs.get("mt5_session_ready")))

    def test_run_watchdog_skips_mt5_session_for_non_crypto_cycle(self) -> None:
        lane = {
            "name": "shadow_usdjpy_gap2",
            "kind": "shadow_fx",
            "state_path": "reports/shadow_usdjpy_gap2_state.json",
            "restart_args": ["scripts/live_penetration_lattice_tick_shadow.py"],
        }
        captured_mt5_session_ready: list[bool] = []

        def fake_summarize_lane(*args, **kwargs):
            captured_mt5_session_ready.append(bool(kwargs.get("mt5_session_ready")))
            return {
                "name": lane["name"],
                "status": "ok",
                "reasons": [],
                "process_ids": [],
                "conflicting_process_ids": [],
                "heartbeat_at": "2026-04-14T06:00:00+00:00",
                "heartbeat_age_seconds": 5.0,
                "last_bar_time": 0,
                "open_count": 0,
                "state_path": lane["state_path"],
                "event_path": "reports/shadow_usdjpy_gap2_events.jsonl",
            }

        with (
            patch.object(watchdog, "read_registry", return_value=[lane]),
            patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
            patch.object(watchdog, "load_scoreboard_totals", return_value={}),
            patch.object(watchdog, "load_reset_baselines", return_value={}),
            patch.object(watchdog, "load_quarantine_state", return_value={"lanes": {}}),
            patch.object(watchdog, "active_quarantine_entries", return_value={}),
            patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_fx_graduation_rows", return_value={}),
            patch.object(watchdog, "list_python_processes", return_value=[]),
            patch.object(watchdog.mt5_terminal_guard, "initialize_mt5", return_value=(False, {})) as initialize_mock,
            patch.object(watchdog.mt5, "shutdown") as shutdown_mock,
            patch.object(watchdog, "summarize_lane", side_effect=fake_summarize_lane),
            patch.object(watchdog, "write_reports"),
        ):
            rows = watchdog.run_watchdog(
                Path("registry.json"),
                Path("report.json"),
                Path("report.md"),
                Path("events.jsonl"),
                False,
                None,
                False,
            )

        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(captured_mt5_session_ready, [False])
        initialize_mock.assert_not_called()
        shutdown_mock.assert_not_called()

    def test_source_tick_stale_reason_marks_live_lane_stale(self) -> None:
        lane = {
            "name": "live_btcusd_exc2_tight_941779",
            "kind": "live_crypto",
            "poll_seconds": 60,
            "state_path": "reports/penetration_lattice_shadow_btcusd_exc2_tight_state.json",
            "event_path": "reports/penetration_lattice_shadow_btcusd_exc2_tight_events.jsonl",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "reports/penetration_lattice_shadow_btcusd_exc2_tight_state.json",
            ],
        }
        payload = {
            "metadata": {"tick_native": True, "direct_live": True},
            "runner": {},
            "symbols": {
                "BTCUSD": {
                    "last_tick_msc": 1775958777584,
                    "open_tickets": [],
                }
            },
        }
        processes = [
            {
                "pid": 64536,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_crypto_shadow.py --state-path reports/penetration_lattice_shadow_btcusd_exc2_tight_state.json",
            }
        ]
        with (
            patch.object(watchdog, "load_json", return_value=payload),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-12T01:53:03+00:00", 5.0, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
            patch.object(
                watchdog,
                "current_symbol_tick_info",
                return_value={
                    "symbol": "BTCUSD",
                    "tick_time": 1775969595,
                    "tick_msc": 1775969595275,
                    "tick_age_seconds": 0.5,
                    "is_fresh": True,
                },
            ),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "stale")
        self.assertTrue(any("source_tick_lag=" in reason for reason in row["reasons"]))
        self.assertEqual(row["source_tick_symbol"], "BTCUSD")
        self.assertAlmostEqual(row["source_tick_lag_seconds"], 10817.7, places=1)
        self.assertEqual(row["source_tick_threshold_seconds"], 180.0)
        self.assertEqual(row["source_tick_state_msc"], 1775958777584)
        self.assertEqual(row["source_tick_live_msc"], 1775969595275)
        self.assertAlmostEqual(row["source_tick_live_age_seconds"], 0.5, places=1)

    def test_source_tick_progress_exposes_telemetry_without_marking_stale(self) -> None:
        lane = {
            "name": "live_btcusd_m5_warp_probation_941780",
            "kind": "live_crypto",
            "poll_seconds": 30,
            "state_path": "reports/penetration_lattice_live_btcusd_m5_warp_state.json",
            "event_path": "reports/penetration_lattice_live_btcusd_m5_warp_events.jsonl",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "reports/penetration_lattice_live_btcusd_m5_warp_state.json",
            ],
        }
        payload = {
            "metadata": {"tick_native": True, "direct_live": True},
            "runner": {},
            "symbols": {
                "BTCUSD": {
                    "last_tick_msc": 1775970856938,
                    "open_tickets": [],
                }
            },
        }
        processes = [
            {
                "pid": 37516,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_crypto_shadow.py --state-path reports/penetration_lattice_live_btcusd_m5_warp_state.json",
            }
        ]
        with (
            patch.object(watchdog, "load_json", return_value=payload),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-12T02:14:22+00:00", 5.0, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
            patch.object(
                watchdog,
                "current_symbol_tick_info",
                return_value={
                    "symbol": "BTCUSD",
                    "tick_time": 1775970892,
                    "tick_msc": 1775970892801,
                    "tick_age_seconds": 0.4,
                    "is_fresh": True,
                },
            ),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "ok")
        self.assertFalse(any("source_tick_lag=" in reason for reason in row["reasons"]))
        self.assertEqual(row["source_tick_symbol"], "BTCUSD")
        self.assertAlmostEqual(row["source_tick_lag_seconds"], 35.9, places=1)
        self.assertEqual(row["source_tick_threshold_seconds"], 120.0)
        self.assertEqual(row["source_tick_state_msc"], 1775970856938)
        self.assertEqual(row["source_tick_live_msc"], 1775970892801)
        self.assertAlmostEqual(row["source_tick_live_age_seconds"], 0.4, places=1)

    def test_source_tick_stale_reason_marks_shadow_crypto_lane_stale(self) -> None:
        lane = {
            "name": "shadow_ethusd_exc2_tight",
            "kind": "shadow_crypto",
            "poll_seconds": 60,
            "state_path": "reports/penetration_lattice_shadow_ethusd_exc2_tight_state.json",
            "event_path": "reports/penetration_lattice_shadow_ethusd_exc2_tight_events.jsonl",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "reports/penetration_lattice_shadow_ethusd_exc2_tight_state.json",
            ],
        }
        payload = {
            "metadata": {"tick_native": True, "direct_live": False},
            "runner": {},
            "symbols": {
                "ETHUSD": {
                    "last_tick_msc": 1775967300354,
                    "open_tickets": [],
                }
            },
        }
        processes = [
            {
                "pid": 60244,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_crypto_shadow.py --state-path reports/penetration_lattice_shadow_ethusd_exc2_tight_state.json",
            }
        ]
        with (
            patch.object(watchdog, "load_json", return_value=payload),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-12T04:15:01+00:00", 5.0, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
            patch.object(
                watchdog,
                "current_symbol_tick_info",
                return_value={
                    "symbol": "ETHUSD",
                    "tick_time": 1775978102,
                    "tick_msc": 1775978102137,
                    "tick_age_seconds": 0.4,
                    "is_fresh": True,
                },
            ),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "stale")
        self.assertTrue(any("source_tick_lag=" in reason for reason in row["reasons"]))
        self.assertEqual(row["source_tick_symbol"], "ETHUSD")
        self.assertAlmostEqual(row["source_tick_lag_seconds"], 10801.8, places=1)
        self.assertEqual(row["source_tick_threshold_seconds"], 180.0)
        self.assertEqual(row["source_tick_state_msc"], 1775967300354)
        self.assertEqual(row["source_tick_live_msc"], 1775978102137)
        self.assertAlmostEqual(row["source_tick_live_age_seconds"], 0.4, places=1)

    def test_source_tick_stale_recurrence_marks_lane_stale_recurrence(self) -> None:
        lane = {
            "name": "shadow_ethusd_exc2_tight",
            "kind": "shadow_crypto",
            "poll_seconds": 60,
            "state_path": "reports/penetration_lattice_shadow_ethusd_exc2_tight_state.json",
            "event_path": "reports/penetration_lattice_shadow_ethusd_exc2_tight_events.jsonl",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "reports/penetration_lattice_shadow_ethusd_exc2_tight_state.json",
            ],
        }
        payload = {
            "metadata": {"tick_native": True, "direct_live": False},
            "runner": {},
            "symbols": {
                "ETHUSD": {
                    "last_tick_msc": 1775967300354,
                    "open_tickets": [],
                }
            },
        }
        processes = [
            {
                "pid": 60244,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_crypto_shadow.py --state-path reports/penetration_lattice_shadow_ethusd_exc2_tight_state.json",
            }
        ]
        with (
            patch.object(watchdog, "load_json", return_value=payload),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-12T04:15:01+00:00", 5.0, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
            patch.object(
                watchdog,
                "current_symbol_tick_info",
                return_value={
                    "symbol": "ETHUSD",
                    "tick_time": 1775978102,
                    "tick_msc": 1775978102137,
                    "tick_age_seconds": 0.4,
                    "is_fresh": True,
                },
            ),
            patch.object(watchdog, "utc_now", return_value=watchdog.parse_iso("2026-04-12T04:40:00+00:00")),
        ):
            row = watchdog.summarize_lane(
                lane,
                processes,
                {},
                {},
                reset_baselines={
                    "shadow_ethusd_exc2_tight": {
                        "reset_at": "2026-04-12T04:38:08+00:00",
                        "reset_type": "stale_tick_repair",
                    }
                },
            )
        self.assertEqual(row["status"], "stale_recurrence")
        self.assertTrue(row["source_tick_recurrence"])
        self.assertEqual(row["source_tick_recurrence_reset_at"], "2026-04-12T04:38:08+00:00")
        self.assertAlmostEqual(row["source_tick_recurrence_age_seconds"], 112.0, places=1)
        self.assertTrue(any("source_tick_recurrence" in reason for reason in row["reasons"]))

    def test_source_tick_stale_reason_marks_unified_shadow_lane_stale(self) -> None:
        lane = {
            "name": "unified_shadow_10symbol",
            "kind": "shadow_unified",
            "poll_seconds": 5,
            "state_path": "reports/unified_shadow_btcusd_state.json",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_unified_shadow.py",
                "configs/universal_10symbol_rearm.json",
            ],
        }
        payload = {
            "metadata": {"tick_native": True, "direct_live": False, "unified_shadow": True},
            "runner": {},
            "symbols": {
                "BTCUSD": {
                    "last_tick_msc": 1775967685560,
                    "open_tickets": [],
                }
            },
        }
        processes = [
            {
                "pid": 75044,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_unified_shadow.py --config configs/universal_10symbol_rearm.json --state-dir reports/ --poll-seconds 5",
            }
        ]
        with (
            patch.object(watchdog, "load_json", return_value=payload),
            patch.object(
                watchdog,
                "heartbeat_from_state",
                return_value=("2026-04-12T04:21:34+00:00", 5.0, "state.updated_at"),
            ),
            patch.object(watchdog, "event_tail_exception", return_value=None),
            patch.object(
                watchdog,
                "current_symbol_tick_info",
                return_value={
                    "symbol": "BTCUSD",
                    "tick_time": 1775978407,
                    "tick_msc": 1775978407405,
                    "tick_age_seconds": 0.4,
                    "is_fresh": True,
                },
            ),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {})
        self.assertEqual(row["status"], "stale")
        self.assertTrue(any("source_tick_lag=" in reason for reason in row["reasons"]))
        self.assertEqual(row["source_tick_symbol"], "BTCUSD")
        self.assertAlmostEqual(row["source_tick_lag_seconds"], 10721.8, places=1)
        self.assertEqual(row["source_tick_threshold_seconds"], 120.0)
        self.assertEqual(row["source_tick_state_msc"], 1775967685560)
        self.assertEqual(row["source_tick_live_msc"], 1775978407405)
        self.assertAlmostEqual(row["source_tick_live_age_seconds"], 0.4, places=1)

    def test_run_watchdog_records_clean_forward_reset_for_source_tick_repair(self) -> None:
        lane = {
            "name": "shadow_btcusd_m5_warp",
            "kind": "shadow_crypto",
            "state_path": "reports/penetration_lattice_shadow_btcusd_m5_warp_state.json",
            "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py"],
        }
        stale_row = {
            "name": "shadow_btcusd_m5_warp",
            "status": "stale",
            "reasons": ["source_tick_lag=10820.6s>120.0s state_tick=1775967501947 live_tick=1775978322535"],
            "process_ids": [16928],
            "conflicting_process_ids": [],
            "heartbeat_at": "2026-04-12T04:18:25.196781+00:00",
            "heartbeat_age_seconds": 18.6,
            "last_bar_time": 1775967300,
            "open_count": 2,
            "state_path": str(Path("C:/repo/reports/penetration_lattice_shadow_btcusd_m5_warp_state.json")),
            "event_path": str(Path("C:/repo/reports/penetration_lattice_shadow_btcusd_m5_warp_events.jsonl")),
        }
        with (
            patch.object(watchdog, "read_registry", return_value=[lane]),
            patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
            patch.object(watchdog, "load_scoreboard_totals", return_value={}),
            patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
            patch.object(watchdog, "list_python_processes", return_value=[]),
            patch.object(watchdog, "summarize_lane", return_value=stale_row.copy()),
            patch.object(watchdog, "stop_process") as stop_process,
            patch.object(watchdog, "record_reset_baseline", return_value={"reset_at": "2026-04-12T04:31:00+00:00"}) as record_reset,
            patch.object(watchdog, "start_lane", return_value={"started_pid": 62428, "stdout_path": "a", "stderr_path": "b"}),
            patch.object(watchdog, "append_jsonl"),
            patch.object(watchdog, "write_reports"),
        ):
            rows = watchdog.run_watchdog(
                Path("registry.json"),
                Path("report.json"),
                Path("report.md"),
                Path("events.jsonl"),
                True,
                None,
                False,
            )

        stop_process.assert_called_once_with(16928)
        record_reset.assert_called_once()
        self.assertEqual(rows[0]["repair_action"], "restart")
        self.assertEqual(rows[0]["repair_started_pid"], 62428)

    def test_run_watchdog_refreshes_repair_launch_contract_from_registry(self) -> None:
        stale_lane = {
            "name": "live_btcusd_m15_warp_941781",
            "kind": "live_crypto",
            "state_path": "reports/penetration_lattice_live_btcusd_m15_warp_state.json",
            "restart_args": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "--symbol",
                "BTCUSD",
                "--step",
                "75",
            ],
        }
        refreshed_lane = {
            "name": "live_btcusd_m15_warp_941781",
            "kind": "live_crypto",
            "state_path": "reports/penetration_lattice_live_btcusd_m15_warp_state.json",
            "restart_args": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "--symbol",
                "BTCUSD",
                "--step",
                "75",
                "--proven-step-ceiling",
                "75",
                "--max-entry-spread-ratio",
                "15.0",
            ],
        }
        missing_row = {
            "name": stale_lane["name"],
            "status": "missing_process",
            "reasons": ["no_matching_process"],
            "process_ids": [],
            "conflicting_process_ids": [],
            "heartbeat_at": "",
            "heartbeat_age_seconds": None,
            "last_bar_time": 0,
            "open_count": 0,
            "state_path": stale_lane["state_path"],
            "event_path": "reports/penetration_lattice_live_btcusd_m15_warp_events.jsonl",
        }
        launched_contracts: list[dict[str, object]] = []
        registry_reads = {"count": 0}

        def fake_read_registry(_path: Path) -> list[dict[str, object]]:
            registry_reads["count"] += 1
            return [stale_lane] if registry_reads["count"] == 1 else [refreshed_lane]

        def fake_start_lane(lane: dict[str, object]) -> dict[str, object]:
            launched_contracts.append(dict(lane))
            return {"started_pid": 33540, "stdout_path": "a", "stderr_path": "b"}

        with (
            patch.object(watchdog, "read_registry", side_effect=fake_read_registry),
            patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
            patch.object(watchdog, "load_scoreboard_totals", return_value={}),
            patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_fx_graduation_rows", return_value={}),
            patch.object(watchdog, "load_reset_baselines", return_value={}),
            patch.object(watchdog, "load_quarantine_state", return_value={"updated_at": "", "lanes": {}}),
            patch.object(watchdog, "active_quarantine_entries", return_value={}),
            patch.object(watchdog, "list_python_processes", return_value=[]),
            patch.object(watchdog, "summarize_lane", return_value=missing_row.copy()),
            patch.object(watchdog, "start_lane", side_effect=fake_start_lane),
            patch.object(watchdog, "append_jsonl"),
            patch.object(watchdog, "write_quarantine_state"),
            patch.object(watchdog, "write_reports"),
        ):
            rows = watchdog.run_watchdog(
                Path("registry.json"),
                Path("report.json"),
                Path("report.md"),
                Path("events.jsonl"),
                True,
                None,
                False,
                Path("quarantine.json"),
                None,
                "crypto_watchdog",
            )

        self.assertEqual(registry_reads["count"], 2)
        self.assertEqual(rows[0]["repair_action"], "restart")
        self.assertTrue(rows[0]["repair_launch_contract_refreshed"])
        self.assertEqual(len(launched_contracts), 1)
        self.assertIn("--proven-step-ceiling", launched_contracts[0]["restart_args"])
        self.assertIn("--max-entry-spread-ratio", launched_contracts[0]["restart_args"])
        spread_flag_index = launched_contracts[0]["restart_args"].index("--max-entry-spread-ratio")
        self.assertEqual(launched_contracts[0]["restart_args"][spread_flag_index + 1], "15.0")

    def test_run_watchdog_quarantines_restart_storm_lane(self) -> None:
        lane = {
            "name": "shadow_coinbase_experimental_rave_apex_champion",
            "kind": "shadow_coinbase_spot",
            "state_path": "reports/rave_apex_state.json",
            "restart_args": ["scripts/live_rave_apex_champion_shadow.py"],
        }
        stale_row = {
            "name": lane["name"],
            "status": "missing_process",
            "reasons": ["no_matching_process"],
            "process_ids": [],
            "conflicting_process_ids": [],
            "heartbeat_at": "",
            "heartbeat_age_seconds": None,
            "last_bar_time": 0,
            "open_count": 0,
            "state_path": str(Path("C:/repo/reports/rave_apex_state.json")),
            "event_path": str(Path("C:/repo/reports/rave_apex_events.jsonl")),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            quarantine_path = Path(tmpdir) / "shadow_quarantine_state.json"
            with (
                patch.object(watchdog, "read_registry", return_value=[lane]),
                patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
                patch.object(watchdog, "load_scoreboard_totals", return_value={}),
                patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
                patch.object(watchdog, "load_reset_baselines", return_value={}),
                patch.object(watchdog, "list_python_processes", return_value=[]),
                patch.object(watchdog, "summarize_lane", return_value=stale_row.copy()),
                patch.object(watchdog, "recent_restart_count", return_value=3),
                patch.object(watchdog, "start_lane") as start_lane,
                patch.object(watchdog, "append_jsonl"),
                patch.object(watchdog, "write_reports"),
            ):
                rows = watchdog.run_watchdog(
                    Path("registry.json"),
                    Path("report.json"),
                    Path("report.md"),
                    Path("events.jsonl"),
                    True,
                    None,
                    False,
                    quarantine_path,
                    "shadow_watchdog",
                )
            quarantine_state = watchdog.load_json(quarantine_path)

        start_lane.assert_not_called()
        self.assertEqual(rows[0]["status"], "quarantined")
        self.assertEqual(rows[0]["repair_action"], "quarantine")
        self.assertIn("shadow_coinbase_experimental_rave_apex_champion", quarantine_state["lanes"])

    def test_run_watchdog_defers_live_arg_drift_repair_when_positions_open(self) -> None:
        lane = {
            "name": "live_ethusd_m5_warp_941784",
            "kind": "live_crypto",
            "state_path": "reports/penetration_lattice_live_ethusd_m5_warp_state.json",
            "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py", "--direct-live"],
        }
        drift_row = {
            "name": lane["name"],
            "status": "arg_drift",
            "reasons": ["arg_drift pid=6500 missing --poll-seconds=1"],
            "process_ids": [6500],
            "conflicting_process_ids": [],
            "arg_drift_process_ids": [6500],
            "arg_drift_details": [{"pid": 6500, "issues": ["missing --poll-seconds=1"]}],
            "heartbeat_at": "2026-04-14T17:05:50+00:00",
            "heartbeat_age_seconds": 2.0,
            "last_bar_time": 1776200000,
            "open_count": 3,
            "state_path": str(Path("C:/repo/reports/penetration_lattice_live_ethusd_m5_warp_state.json")),
            "event_path": str(Path("C:/repo/reports/penetration_lattice_live_ethusd_m5_warp_events.jsonl")),
        }
        with (
            patch.object(watchdog, "read_registry", return_value=[lane]),
            patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
            patch.object(watchdog, "load_scoreboard_totals", return_value={}),
            patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_reset_baselines", return_value={}),
            patch.object(watchdog, "load_quarantine_state", return_value={"updated_at": "", "lanes": {}}),
            patch.object(watchdog, "list_python_processes", return_value=[]),
            patch.object(watchdog, "summarize_lane", return_value=drift_row.copy()),
            patch.object(watchdog, "start_lane") as start_lane,
            patch.object(watchdog, "stop_process") as stop_process,
            patch.object(watchdog, "append_jsonl"),
            patch.object(watchdog, "write_reports"),
            patch.object(watchdog, "write_quarantine_state"),
        ):
            rows = watchdog.run_watchdog(
                Path("registry.json"),
                Path("report.json"),
                Path("report.md"),
                Path("events.jsonl"),
                True,
                None,
                False,
                Path("quarantine.json"),
                "crypto_watchdog",
            )

        self.assertEqual(rows[0]["repair_action"], "defer_arg_drift_open_positions")
        self.assertIn("repair_suppressed_open_positions=3", rows[0]["reasons"])
        stop_process.assert_not_called()
        start_lane.assert_not_called()

    def test_run_watchdog_marks_quarantined_contract_drift_pending(self) -> None:
        lane = {
            "name": "shadow_xrpusd_m15_hh_breakout_v1",
            "kind": "shadow_crypto",
            "state_path": "reports/penetration_lattice_shadow_xrpusd_m15_hh_breakout_v1_state.json",
            "restart_args": ["scripts/live_penetration_lattice_shadow.py", "--shared-price-max-age-ms", "1000"],
        }
        quarantined_row = {
            "name": lane["name"],
            "status": "quarantined",
            "reasons": ["quarantined_until=2026-04-17T23:47:41+00:00 reason=restart_storm=4/4 within 1800s"],
            "process_ids": [44116],
            "conflicting_process_ids": [],
            "heartbeat_at": "2026-04-17T23:27:42+00:00",
            "heartbeat_age_seconds": 4.0,
            "last_bar_time": 1776468460,
            "open_count": 0,
            "state_path": str(Path("C:/repo/reports/penetration_lattice_shadow_xrpusd_m15_hh_breakout_v1_state.json")),
            "event_path": str(Path("C:/repo/reports/penetration_lattice_shadow_xrpusd_m15_hh_breakout_v1_events.jsonl")),
        }
        quarantine_state = {
            "updated_at": "2026-04-17T23:27:42+00:00",
            "lanes": {
                lane["name"]: {
                    "reason": "restart_storm=4/4 within 1800s",
                    "quarantined_until": "2026-04-17T23:47:41+00:00",
                    "restart_count_window": 4,
                }
            },
        }
        refresh_info = {
            "used_refresh": True,
            "refresh_status": "ok",
            "contract_changed": True,
            "restart_args_changed": True,
            "state_path_changed": False,
            "enabled_changed": False,
            "restart_group_changed": False,
            "kind_changed": False,
        }
        with (
            patch.object(watchdog, "read_registry", return_value=[lane]),
            patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
            patch.object(watchdog, "load_scoreboard_totals", return_value={}),
            patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_reset_baselines", return_value={}),
            patch.object(watchdog, "load_quarantine_state", return_value=quarantine_state),
            patch.object(watchdog, "refresh_lane_contract", return_value=(lane, refresh_info)),
            patch.object(watchdog, "list_python_processes", return_value=[]),
            patch.object(watchdog, "summarize_lane", return_value=quarantined_row.copy()),
            patch.object(watchdog, "start_lane") as start_lane,
            patch.object(watchdog, "append_jsonl"),
            patch.object(watchdog, "write_reports"),
            patch.object(watchdog, "write_quarantine_state"),
        ):
            rows = watchdog.run_watchdog(
                Path("registry.json"),
                Path("report.json"),
                Path("report.md"),
                Path("events.jsonl"),
                True,
                None,
                False,
                Path("quarantine.json"),
                "crypto_watchdog",
            )

        self.assertEqual(rows[0]["status"], "quarantined")
        self.assertEqual(rows[0]["repair_action"], "quarantined_contract_drift_pending")
        self.assertTrue(rows[0]["repair_launch_contract_refreshed"])
        self.assertTrue(rows[0]["repair_pending_restart_args_changed"])
        self.assertIn("quarantined_contract_drift_pending", rows[0]["reasons"])
        start_lane.assert_not_called()

    def test_run_watchdog_skips_repairs_for_starting_lane(self) -> None:
        lane = {
            "name": "shadow_coinbase_experimental_rave_crown_jewel",
            "kind": "shadow_coinbase_spot",
            "state_path": "reports/rave_crown_jewel_state.json",
            "restart_args": ["scripts/live_rave_crown_jewel_shadow.py"],
        }
        starting_row = {
            "name": lane["name"],
            "status": "starting",
            "reasons": ["loop_bootstrap_grace=7.0s/15.0s"],
            "process_ids": [],
            "conflicting_process_ids": [],
            "heartbeat_at": "2026-04-13T00:05:55+00:00",
            "heartbeat_age_seconds": 104.8,
            "last_bar_time": 0,
            "open_count": 0,
            "state_path": str(Path("C:/repo/reports/rave_crown_jewel_state.json")),
            "event_path": str(Path("C:/repo/reports/rave_crown_jewel_events.jsonl")),
        }
        with (
            patch.object(watchdog, "read_registry", return_value=[lane]),
            patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
            patch.object(watchdog, "load_scoreboard_totals", return_value={}),
            patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_reset_baselines", return_value={}),
            patch.object(watchdog, "list_python_processes", return_value=[]),
            patch.object(watchdog, "summarize_lane", return_value=starting_row.copy()),
            patch.object(watchdog, "start_lane") as start_lane,
            patch.object(watchdog, "append_jsonl"),
            patch.object(watchdog, "write_reports"),
        ):
            rows = watchdog.run_watchdog(
                Path("registry.json"),
                Path("report.json"),
                Path("report.md"),
                Path("events.jsonl"),
                True,
                None,
                False,
            )

        start_lane.assert_not_called()
        self.assertEqual(rows[0]["status"], "starting")
        self.assertNotIn("repair_action", rows[0])

    def test_run_watchdog_restarts_shared_group_once(self) -> None:
        lanes = [
            {
                "name": "shadow_coinbase_arbusd_rsi7",
                "kind": "shadow_coinbase_spot",
                "state_path": "reports/coinbase_rsi_shadow_arbusd_state.json",
                "restart_args": ["scripts/live_coinbase_rsi_bundle_shadow.py", "--config-path", "configs/coinbase_rsi_bundle_shadow.json"],
                "restart_group": "shadow_coinbase_rsi_bundle_v1",
            },
            {
                "name": "shadow_coinbase_prlusd_rsi7",
                "kind": "shadow_coinbase_spot",
                "state_path": "reports/coinbase_rsi_shadow_prlusd_state.json",
                "restart_args": ["scripts/live_coinbase_rsi_bundle_shadow.py", "--config-path", "configs/coinbase_rsi_bundle_shadow.json"],
                "restart_group": "shadow_coinbase_rsi_bundle_v1",
            },
        ]
        stale_rows = [
            {
                "name": "shadow_coinbase_arbusd_rsi7",
                "status": "missing_process",
                "reasons": ["no_matching_process"],
                "process_ids": [],
                "conflicting_process_ids": [],
                "heartbeat_at": "",
                "heartbeat_age_seconds": None,
                "last_bar_time": 0,
                "open_count": 0,
                "state_path": "reports/coinbase_rsi_shadow_arbusd_state.json",
                "event_path": "reports/coinbase_rsi_shadow_arbusd_events.jsonl",
            },
            {
                "name": "shadow_coinbase_prlusd_rsi7",
                "status": "missing_process",
                "reasons": ["no_matching_process"],
                "process_ids": [],
                "conflicting_process_ids": [],
                "heartbeat_at": "",
                "heartbeat_age_seconds": None,
                "last_bar_time": 0,
                "open_count": 0,
                "state_path": "reports/coinbase_rsi_shadow_prlusd_state.json",
                "event_path": "reports/coinbase_rsi_shadow_prlusd_events.jsonl",
            },
        ]
        with (
            patch.object(watchdog, "read_registry", return_value=lanes),
            patch.object(watchdog, "refresh_lane_scoreboard", return_value={"ok": True}),
            patch.object(watchdog, "load_scoreboard_totals", return_value={}),
            patch.object(watchdog, "load_combined_forward_review_rows", return_value={}),
            patch.object(watchdog, "load_reset_baselines", return_value={}),
            patch.object(watchdog, "list_python_processes", return_value=[]),
            patch.object(watchdog, "summarize_lane", side_effect=[row.copy() for row in stale_rows]),
            patch.object(watchdog, "recent_restart_count", return_value=0),
            patch.object(watchdog, "start_lane", return_value={"started_pid": 64128, "stdout_path": "a", "stderr_path": "b"}) as start_lane,
            patch.object(watchdog, "append_jsonl"),
            patch.object(watchdog, "write_reports"),
        ):
            rows = watchdog.run_watchdog(
                Path("registry.json"),
                Path("report.json"),
                Path("report.md"),
                Path("events.jsonl"),
                True,
                None,
                False,
            )

        start_lane.assert_called_once()
        self.assertEqual(rows[0]["repair_action"], "restart")
        self.assertEqual(rows[1]["repair_action"], "restart_group_deferred")
        self.assertIn("restart_group=shadow_coinbase_rsi_bundle_v1 already_restarted_this_cycle", rows[1]["reasons"])


if __name__ == "__main__":
    unittest.main()
