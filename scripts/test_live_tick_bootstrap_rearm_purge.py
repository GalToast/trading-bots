#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_penetration_lattice_tick_crypto_shadow as tick_crypto
import live_penetration_lattice_tick_shadow as tick_fx


class LiveTickBootstrapRearmPurgeTests(unittest.TestCase):
    def test_sync_engine_to_broker_rehydrates_missing_exec_positions_from_broker(self) -> None:
        engine = SimpleNamespace(
            symbol="USDJPY",
            state=SimpleNamespace(
                open_tickets=[],
                realized_net_usd=0.0,
                realized_closes=0,
                rearm_tokens=[],
                max_open_total=0,
            ),
        )
        engine._ticket_level_idx = lambda _direction, _trigger_level: 7
        exec_state = {"positions": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            exec_log_path = Path(tmpdir) / "exec_events.jsonl"
            exec_log_path.write_text("", encoding="utf-8")
            event_path = Path(tmpdir) / "state_events.jsonl"
            with (
                patch.object(
                    tick_crypto.live_mirror,
                    "broker_live_positions",
                    return_value=[
                        {
                            "symbol": "USDJPY",
                            "direction": "SELL",
                            "ticket": 45912848,
                            "price_open": 159.261,
                            "comment": "PLIVE-LATTICE-S",
                            "time": 1776018666,
                        }
                    ],
                ),
                patch.object(tick_crypto, "exact_logged_deals", return_value=[]),
                patch.object(tick_crypto, "append_jsonl") as append_jsonl,
            ):
                summary = tick_crypto.sync_engine_to_broker(
                    engine,
                    exec_state=exec_state,
                    exec_log_path=exec_log_path,
                    event_path=event_path,
                    live_magic=941777,
                )

        self.assertTrue(summary["changed"])
        self.assertEqual(summary["open_count"], 1)
        self.assertEqual(len(exec_state["positions"]), 1)
        self.assertEqual(exec_state["positions"][0]["live_ticket"], 45912848)
        self.assertEqual(exec_state["positions"][0]["direction"], "SELL")
        self.assertAlmostEqual(exec_state["positions"][0]["entry_level"], 159.261, places=6)
        self.assertAlmostEqual(exec_state["positions"][0]["fill_price"], 159.261, places=6)
        self.assertAlmostEqual(exec_state["positions"][0]["broker_price_open"], 159.261, places=6)
        self.assertEqual(len(engine.state.open_tickets), 1)
        self.assertEqual(engine.state.open_tickets[0]["live_ticket"], 45912848)
        self.assertAlmostEqual(engine.state.open_tickets[0]["trigger_level"], 159.261, places=6)
        self.assertAlmostEqual(engine.state.open_tickets[0]["fill_price"], 159.261, places=6)
        append_jsonl.assert_called_once()
        logged = append_jsonl.call_args.args[1]
        self.assertEqual(logged["rehydrated_tickets"], [45912848])
        self.assertEqual(logged["dropped_tracked_tickets"], [])

    def test_sync_engine_to_broker_drops_ghost_tickets_and_realigns_realized(self) -> None:
        engine = SimpleNamespace(
            symbol="BTCUSD",
            state=SimpleNamespace(
                open_tickets=[
                    {"direction": "BUY", "trigger_level": 72606.59, "fill_price": 71697.79, "live_ticket": 0, "level_idx": 9},
                    {"direction": "BUY", "trigger_level": 70716.59, "fill_price": 70714.71, "live_ticket": 45912847, "level_idx": 29},
                ],
                realized_net_usd=553.4,
                realized_closes=42,
                rearm_tokens=[{"direction": "BUY", "level": 72561.59, "level_idx": 10, "armed": True}],
                max_open_total=27,
            ),
        )
        engine._ticket_level_idx = lambda _direction, _trigger_level: 29
        exec_state = {
            "positions": [
                {
                    "symbol": "BTCUSD",
                    "direction": "BUY",
                    "entry_level": 70716.59,
                    "live_ticket": 45912847,
                    "opened_at": "2026-04-12T15:31:07.667917+00:00",
                }
            ]
        }
        deals = [
            {"entry": 0, "profit": 0.0, "commission": -0.05, "swap": 0.0, "fee": 0.0},
            {"entry": 1, "profit": 12.55, "commission": -0.05, "swap": 0.0, "fee": 0.0},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            exec_log_path = Path(tmpdir) / "exec_events.jsonl"
            exec_log_path.write_text("", encoding="utf-8")
            event_path = Path(tmpdir) / "state_events.jsonl"
            with (
                patch.object(
                    tick_crypto.live_mirror,
                    "broker_live_positions",
                    return_value=[
                        {
                            "ticket": 45912847,
                            "symbol": "BTCUSD",
                            "direction": "BUY",
                            "price_open": 70714.71,
                            "comment": "PLIVE-BTC-B",
                            "time": 1776018666,
                        }
                    ],
                ),
                patch.object(tick_crypto, "exact_logged_deals", return_value=deals),
                patch.object(tick_crypto, "append_jsonl") as append_jsonl,
            ):
                summary = tick_crypto.sync_engine_to_broker(
                    engine,
                    exec_state=exec_state,
                    exec_log_path=exec_log_path,
                    event_path=event_path,
                    live_magic=941779,
                )

        self.assertTrue(summary["changed"])
        self.assertEqual(summary["open_count"], 1)
        self.assertEqual(summary["realized_closes"], 1)
        self.assertAlmostEqual(summary["realized_net_usd"], 12.45, places=6)
        self.assertAlmostEqual(exec_state["positions"][0]["fill_price"], 70714.71, places=6)
        self.assertAlmostEqual(exec_state["positions"][0]["broker_price_open"], 70714.71, places=6)
        self.assertEqual(len(engine.state.open_tickets), 1)
        self.assertEqual(engine.state.open_tickets[0]["trigger_level"], 70716.59)
        self.assertEqual(engine.state.open_tickets[0]["fill_price"], 70714.71)
        self.assertEqual(engine.state.open_tickets[0]["live_ticket"], 45912847)
        self.assertEqual(engine.state.rearm_tokens, [])
        append_jsonl.assert_called_once()
        logged = append_jsonl.call_args.args[1]
        self.assertEqual(logged["action"], "direct_live_broker_sync")
        self.assertEqual(logged["old_open_count"], 2)
        self.assertEqual(logged["new_open_count"], 1)
        self.assertEqual(logged["rehydrated_tickets"], [])
        self.assertEqual(logged["dropped_tracked_tickets"], [])

    def test_sync_engine_to_broker_repairs_zero_max_open_total_even_when_state_matches_broker(self) -> None:
        engine = SimpleNamespace(
            symbol="ETHUSD",
            state=SimpleNamespace(
                open_tickets=[],
                realized_net_usd=0.0,
                realized_closes=0,
                rearm_tokens=[],
                max_open_total=0,
            ),
        )
        engine._ticket_level_idx = lambda _direction, _trigger_level: 0
        exec_state = {"positions": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            exec_log_path = Path(tmpdir) / "exec_events.jsonl"
            exec_log_path.write_text("", encoding="utf-8")
            event_path = Path(tmpdir) / "state_events.jsonl"
            with (
                patch.object(tick_crypto.live_mirror, "broker_live_positions", return_value=[]),
                patch.object(tick_crypto, "exact_logged_deals", return_value=[]),
                patch.object(tick_crypto, "append_jsonl") as append_jsonl,
            ):
                summary = tick_crypto.sync_engine_to_broker(
                    engine,
                    exec_state=exec_state,
                    exec_log_path=exec_log_path,
                    event_path=event_path,
                    live_magic=941784,
                )

        self.assertTrue(summary["changed"])
        self.assertEqual(summary["open_count"], 0)
        self.assertEqual(engine.state.max_open_total, 24)
        append_jsonl.assert_called_once()
        logged = append_jsonl.call_args.args[1]
        self.assertEqual(logged["old_max_open_total"], 0)
        self.assertEqual(logged["new_max_open_total"], 24)

    def test_sync_engine_to_broker_rehydrates_attached_magic_inventory(self) -> None:
        engine = SimpleNamespace(
            symbol="BTCUSD",
            state=SimpleNamespace(
                open_tickets=[],
                realized_net_usd=0.0,
                realized_closes=0,
                rearm_tokens=[],
                max_open_total=0,
            ),
        )
        engine._ticket_level_idx = lambda _direction, _trigger_level: 3
        exec_state = {"positions": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            exec_log_path = Path(tmpdir) / "exec_events.jsonl"
            exec_log_path.write_text("", encoding="utf-8")
            event_path = Path(tmpdir) / "state_events.jsonl"
            with (
                patch.object(
                    tick_crypto.live_mirror,
                    "broker_live_positions",
                    return_value=[
                        {
                            "symbol": "BTCUSD",
                            "direction": "SELL",
                            "ticket": 50001,
                            "magic": 941785,
                            "price_open": 74913.5,
                            "comment": "PLSHADOW-S15-S",
                            "time": 1776018666,
                        }
                    ],
                ) as broker_live_positions,
                patch.object(tick_crypto, "exact_logged_deals", return_value=[]),
                patch.object(tick_crypto, "append_jsonl"),
            ):
                summary = tick_crypto.sync_engine_to_broker(
                    engine,
                    exec_state=exec_state,
                    exec_log_path=exec_log_path,
                    event_path=event_path,
                    live_magic=941781,
                    attached_live_magics=[941785, 941786],
                )

        self.assertEqual(summary["open_count"], 1)
        self.assertEqual(exec_state["positions"][0]["broker_magic"], 941785)
        self.assertEqual(engine.state.open_tickets[0]["broker_magic"], 941785)
        broker_live_positions.assert_called_once()
        self.assertEqual(broker_live_positions.call_args.kwargs["attached_live_magics"], [941785, 941786])

    def test_direct_live_action_sink_tracks_open_and_close_from_broker_results(self) -> None:
        exec_state = {"positions": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            exec_log_path = Path(tmpdir) / "exec_events.jsonl"
            exec_log_path.write_text("", encoding="utf-8")
            sink = tick_crypto.build_direct_live_action_sink(
                exec_state=exec_state,
                exec_log_path=exec_log_path,
                live_magic=941779,
                live_comment_prefix="PLIVE-BTC",
                live_volume=0.01,
            )
            with (
                patch.object(
                    tick_crypto.live_mirror,
                    "send_market_order",
                    return_value={
                        "ok": True,
                        "ticket": 45912847,
                        "position_comment": "PLIVE-BTC-B",
                        "broker_position_price_open": 70714.71,
                        "broker_fill": {
                            "ticket": 12517,
                            "entry": 0,
                            "profit": 0.0,
                            "commission": -0.05,
                            "swap": 0.0,
                            "fee": 0.0,
                            "price": 70714.71,
                        },
                    },
                ),
                patch.object(
                    tick_crypto.live_mirror,
                    "close_live_position",
                    return_value={
                        "ok": True,
                        "broker_fill": {
                            "ticket": 12518,
                            "entry": 1,
                            "profit": 12.55,
                            "commission": -0.05,
                            "swap": 0.0,
                            "fee": 0.0,
                            "price": 70800.01,
                        },
                    },
                ),
            ):
                open_result = sink(
                    {
                        "kind": "open",
                        "symbol": "BTCUSD",
                        "direction": "BUY",
                        "trigger_level": 70716.59,
                        "fill_price": 70714.71,
                    }
                )
                self.assertEqual(len(exec_state["positions"]), 1)
                self.assertEqual(exec_state["positions"][0]["live_ticket"], 45912847)
                self.assertAlmostEqual(exec_state["positions"][0]["entry_level"], 70716.59, places=6)
                self.assertAlmostEqual(exec_state["positions"][0]["fill_price"], 70714.71, places=6)
                self.assertAlmostEqual(exec_state["positions"][0]["broker_price_open"], 70714.71, places=6)
                close_result = sink(
                    {
                        "kind": "close",
                        "symbol": "BTCUSD",
                        "direction": "BUY",
                        "trigger_level": 70716.59,
                        "fill_price": 70800.01,
                        "ticket": {"live_ticket": 0},
                    }
                )

            self.assertTrue(open_result["ok"])
            self.assertEqual(open_result["live_ticket"], 45912847)
            self.assertAlmostEqual(open_result["fill_price"], 70714.71, places=6)
            self.assertEqual(len(exec_state["positions"]), 0)
            self.assertTrue(close_result["ok"])
            self.assertAlmostEqual(close_result["fill_price"], 70800.01, places=6)
            self.assertAlmostEqual(close_result["realized_pnl"], 12.5, places=6)
            log_lines = [line for line in exec_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(log_lines), 2)
            self.assertIn('"action": "open_attempt"', log_lines[0])
            self.assertIn('"action": "close_attempt"', log_lines[1])

    def test_fx_run_once_direct_live_uses_action_sink_and_realigns_before_after(self) -> None:
        captured_sinks: list[object] = []
        engine = SimpleNamespace(
            symbol="USDJPY",
            timeframe_name="M1",
            state=SimpleNamespace(last_tick_msc=1776010000000),
        )

        def fake_process_ticks(ticks, **kwargs):
            captured_sinks.append(kwargs.get("action_sink"))
            return len(ticks)

        engine.process_ticks = fake_process_ticks
        runner_status = {"heartbeat_at": None, "last_successful_run_at": None, "consecutive_exceptions": 7}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "fx_state.json"
            event_path = Path(tmpdir) / "fx_events.jsonl"
            direct_exec = {
                "state": {"positions": []},
                "state_path": Path(tmpdir) / "mirror_state.json",
                "log_path": Path(tmpdir) / "mirror_events.jsonl",
                "allowed_symbols": {"USDJPY"},
                "live_magic": 941777,
                "live_comment_prefix": "PLIVE-LATTICE",
                "live_volume": 0.01,
            }
            with (
                patch.object(
                    tick_fx,
                    "load_ticks_since_with_source",
                    return_value=([{"time": 1776010060, "time_msc": 1776010060000, "bid": 159.25, "ask": 159.26}], "copy_ticks_range"),
                ),
                patch.object(tick_fx, "sync_engine_to_broker", side_effect=[{"changed": True}, {"changed": False}]) as sync_engine_to_broker,
                patch.object(tick_fx, "build_direct_live_action_sink", return_value=lambda _req: {"ok": True}) as build_action_sink,
                patch.object(tick_fx, "save_state") as save_state,
                patch.object(tick_fx, "run_direct_live_exec") as run_direct_live_exec,
            ):
                tick_fx.run_once(
                    {"USDJPY": engine},
                    state_path=state_path,
                    event_path=event_path,
                    metadata={"direct_live": True},
                    direct_exec=direct_exec,
                    runner_status=runner_status,
                )

        self.assertEqual(sync_engine_to_broker.call_count, 2)
        build_action_sink.assert_called_once()
        run_direct_live_exec.assert_called_once()
        self.assertEqual(len(captured_sinks), 1)
        self.assertTrue(callable(captured_sinks[0]))
        self.assertEqual(save_state.call_count, 2)
        self.assertEqual(runner_status["consecutive_exceptions"], 0)

    def test_fx_run_once_session_gate_still_syncs_direct_live_inventory(self) -> None:
        engine = SimpleNamespace(
            symbol="GBPUSD",
            timeframe_name="M1",
            state=SimpleNamespace(last_tick_msc=1776010000000),
        )
        runner_status = {"heartbeat_at": None, "last_successful_run_at": None, "consecutive_exceptions": 3}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "fx_state.json"
            event_path = Path(tmpdir) / "fx_events.jsonl"
            direct_exec = {
                "state": {"positions": []},
                "state_path": Path(tmpdir) / "mirror_state.json",
                "log_path": Path(tmpdir) / "mirror_events.jsonl",
                "allowed_symbols": {"GBPUSD"},
                "live_magic": 941777,
                "live_comment_prefix": "PLIVE-LATTICE",
                "live_volume": 0.01,
            }
            with (
                patch.object(tick_fx, "is_good_session", return_value=False),
                patch.object(tick_fx, "sync_engine_to_broker", return_value={"changed": True}) as sync_engine_to_broker,
                patch.object(tick_fx.live_mirror, "save_state") as save_exec_state,
                patch.object(tick_fx, "save_state") as save_state,
                patch.object(tick_fx, "run_direct_live_exec") as run_direct_live_exec,
            ):
                tick_fx.run_once(
                    {"GBPUSD": engine},
                    state_path=state_path,
                    event_path=event_path,
                    metadata={"direct_live": True},
                    direct_exec=direct_exec,
                    runner_status=runner_status,
                    session_gate=True,
                )

        sync_engine_to_broker.assert_called_once()
        save_exec_state.assert_called_once_with(direct_exec["state_path"], direct_exec["state"])
        save_state.assert_called_once()
        run_direct_live_exec.assert_not_called()
        self.assertTrue(runner_status["session_gated"])
        self.assertEqual(runner_status["consecutive_exceptions"], 0)

    def test_crypto_run_once_falls_back_to_live_tick_when_history_is_empty(self) -> None:
        processed: list[dict] = []
        engine = SimpleNamespace(
            symbol="BTCUSD",
            timeframe_name="M5",
            state=SimpleNamespace(last_tick_msc=1775963094945),
        )
        engine.process_ticks = lambda ticks, **_kwargs: processed.extend(ticks) or len(ticks)
        runner_status = {"heartbeat_at": None, "last_successful_run_at": None, "consecutive_exceptions": 9}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "btc_state.json"
            event_path = Path(tmpdir) / "btc_events.jsonl"
            with (
                patch.object(tick_crypto, "load_ticks_since_with_source", return_value=([], "copy_ticks_range")),
                patch.object(
                    tick_crypto,
                    "load_current_tick_with_source",
                    return_value=(
                        {
                            "time": 1775969017,
                            "time_msc": 1775969017081,
                            "bid": 71552.2,
                            "ask": 71744.84,
                            "last": 0.0,
                            "flags": 0,
                            "volume": 0,
                            "volume_real": 0.0,
                        },
                        "symbol_info_tick",
                    ),
                ),
                patch.object(tick_crypto, "append_jsonl") as append_jsonl,
                patch.object(tick_crypto, "save_state") as save_state,
            ):
                tick_crypto.run_once(
                    engine,
                    state_path=state_path,
                    event_path=event_path,
                    metadata={"direct_live": True},
                    direct_exec=None,
                    runner_status=runner_status,
                )

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["time_msc"], 1775969017081)
        self.assertEqual(runner_status["consecutive_exceptions"], 0)
        save_state.assert_called_once()
        append_jsonl.assert_called_once()
        logged = append_jsonl.call_args.args[1]
        self.assertEqual(logged["action"], "tick_history_fallback")
        self.assertEqual(logged["reason"], "symbol_info_tick_newer_than_loaded_history")
        self.assertEqual(logged["live_tick_msc"], 1775969017081)
        self.assertEqual(runner_status["tick_history_source_last"], "copy_ticks_range")
        self.assertEqual(runner_status["latest_tick_source_last"], "symbol_info_tick")
        self.assertEqual(runner_status["latest_tick_append_source_last"], "symbol_info_tick")

    def test_fx_run_once_can_append_shared_price_fallback_tick(self) -> None:
        processed: list[dict] = []
        engine = SimpleNamespace(
            symbol="EURUSD",
            timeframe_name="M1",
            state=SimpleNamespace(last_tick_msc=1775963094945),
        )
        engine.process_ticks = lambda ticks, **_kwargs: processed.extend(ticks) or len(ticks)
        runner_status = {"heartbeat_at": None, "last_successful_run_at": None, "consecutive_exceptions": 4}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "fx_state.json"
            event_path = Path(tmpdir) / "fx_events.jsonl"
            with (
                patch.object(tick_fx, "load_ticks_since_with_source", return_value=([], "copy_ticks_range")),
                patch.object(
                    tick_fx,
                    "load_latest_tick",
                    return_value=(
                        {
                            "time": 1775969017,
                            "time_msc": 1775969017081,
                            "bid": 1.1252,
                            "ask": 1.1253,
                            "last": 0.0,
                            "flags": 0,
                            "volume": 0,
                            "volume_real": 0.0,
                        },
                        "shared_price_cache",
                    ),
                ),
                patch.object(tick_fx, "append_jsonl") as append_jsonl,
                patch.object(tick_fx, "save_state") as save_state,
            ):
                tick_fx.run_once(
                    {"EURUSD": engine},
                    state_path=state_path,
                    event_path=event_path,
                    metadata={"direct_live": False},
                    direct_exec=None,
                    runner_status=runner_status,
                    shared_price_max_age_ms=1000,
                )

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["time_msc"], 1775969017081)
        self.assertEqual(runner_status["consecutive_exceptions"], 0)
        save_state.assert_called_once()
        append_jsonl.assert_called_once()
        logged = append_jsonl.call_args.args[1]
        self.assertEqual(logged["action"], "tick_history_fallback")
        self.assertEqual(logged["reason"], "shared_price_cache_newer_than_loaded_history")
        self.assertEqual(logged["live_tick_msc"], 1775969017081)
        self.assertEqual(runner_status["tick_history_source_by_symbol"]["EURUSD"]["last"], "copy_ticks_range")
        self.assertEqual(runner_status["latest_tick_source_by_symbol"]["EURUSD"]["last"], "shared_price_cache")
        self.assertEqual(runner_status["latest_tick_append_source_by_symbol"]["EURUSD"]["last"], "shared_price_cache")

    def test_crypto_run_once_can_use_shared_tick_history_without_append_fallback(self) -> None:
        processed: list[dict] = []
        shared_tick = {
            "time": 1775969017,
            "time_msc": 1775969017081,
            "bid": 71552.2,
            "ask": 71744.84,
            "last": 0.0,
            "flags": 0,
            "volume": 0,
            "volume_real": 0.0,
        }
        engine = SimpleNamespace(
            symbol="BTCUSD",
            timeframe_name="M5",
            state=SimpleNamespace(last_tick_msc=1775963094945),
        )
        engine.process_ticks = lambda ticks, **_kwargs: processed.extend(ticks) or len(ticks)
        runner_status = {"heartbeat_at": None, "last_successful_run_at": None, "consecutive_exceptions": 2}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "btc_state.json"
            event_path = Path(tmpdir) / "btc_events.jsonl"
            with (
                patch.object(tick_crypto, "load_ticks_since_with_source", return_value=([shared_tick], "shared_tick_cache")),
                patch.object(tick_crypto, "load_current_tick_with_source", return_value=(shared_tick, "shared_price_cache")),
                patch.object(tick_crypto, "append_jsonl") as append_jsonl,
                patch.object(tick_crypto, "save_state") as save_state,
            ):
                tick_crypto.run_once(
                    engine,
                    state_path=state_path,
                    event_path=event_path,
                    metadata={"direct_live": False},
                    direct_exec=None,
                    runner_status=runner_status,
                    shared_price_max_age_ms=1000,
                )

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["time_msc"], 1775969017081)
        self.assertEqual(runner_status["consecutive_exceptions"], 0)
        save_state.assert_called_once()
        append_jsonl.assert_not_called()
        self.assertEqual(runner_status["tick_history_source_last"], "shared_tick_cache")
        self.assertEqual(runner_status["latest_tick_source_last"], "shared_price_cache")

    def test_crypto_fresh_start_arms_from_current_tick(self) -> None:
        engine = SimpleNamespace(
            symbol="BTCUSD",
            timeframe_name="M5",
            state=SimpleNamespace(
                open_tickets=[{"direction": "SELL"}],
                rearm_tokens=[{"direction": "SELL"}],
                rearm_opens=9,
                realized_closes=7,
                realized_net_usd=12.5,
                anchor_resets=2,
                max_open_total=4,
                last_bar_time=0,
                last_tick_time=0,
                last_tick_msc=0,
            ),
        )
        engine.prime = lambda _price, _bar_time: None
        metadata = {"direct_live": True}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "btc_state.json"
            event_path = Path(tmpdir) / "btc_events.jsonl"
            with (
                patch.object(tick_crypto, "load_recent_bars", return_value=[{"time": 1775959800, "close": 72862.76}]),
                patch.object(
                    tick_crypto,
                    "load_latest_tick",
                    return_value=(
                        {
                            "time": 1775959838,
                            "time_msc": 1775959838836,
                            "bid": 72862.70,
                            "ask": 72862.82,
                            "last": 0.0,
                            "flags": 0,
                            "volume": 0,
                            "volume_real": 0.0,
                        },
                        "shared_price_cache",
                    ),
                ),
                patch.object(tick_crypto, "save_state") as save_state,
                patch.object(tick_crypto, "append_jsonl") as append_jsonl,
            ):
                tick_crypto.bootstrap(engine, state_path, event_path, True, metadata)

        self.assertEqual(engine.state.last_tick_time, 1775959838)
        self.assertEqual(engine.state.last_tick_msc, 1775959838836)
        self.assertEqual(engine.state.open_tickets, [])
        self.assertEqual(engine.state.rearm_tokens, [])
        self.assertEqual(engine.state.rearm_opens, 0)
        self.assertEqual(engine.state.realized_closes, 0)
        self.assertEqual(engine.state.realized_net_usd, 0.0)
        self.assertEqual(engine.state.max_open_total, 24)
        save_state.assert_called_once()
        append_jsonl.assert_called_once()
        logged = append_jsonl.call_args.args[1]
        self.assertEqual(logged["action"], "fresh_start_prime")
        self.assertEqual(logged["symbols"], ["BTCUSD"])

    def test_crypto_fresh_start_ignores_existing_state_snapshot(self) -> None:
        engine = SimpleNamespace(
            symbol="BTCUSD",
            timeframe_name="M5",
            state=SimpleNamespace(
                open_tickets=[{"direction": "SELL", "entry_price": 73002.71, "opened_time": 1775948144}],
                rearm_tokens=[],
                rearm_opens=0,
                realized_closes=0,
                realized_net_usd=0.0,
                anchor_resets=0,
                max_open_total=1,
                last_bar_time=0,
                last_tick_time=0,
                last_tick_msc=0,
            ),
        )
        engine.prime = lambda _price, _bar_time: None
        metadata = {"direct_live": True}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "btc_state.json"
            event_path = Path(tmpdir) / "btc_events.jsonl"
            state_path.write_text("{}", encoding="utf-8")
            with (
                patch.object(tick_crypto, "load_compatible_state") as load_compatible_state,
                patch.object(tick_crypto, "load_recent_bars", return_value=[{"time": 1775959800, "close": 72862.76}]),
                patch.object(
                    tick_crypto,
                    "load_latest_tick",
                    return_value=(
                        {
                            "time": 1775959838,
                            "time_msc": 1775959838836,
                            "bid": 72862.70,
                            "ask": 72862.82,
                            "last": 0.0,
                            "flags": 0,
                            "volume": 0,
                            "volume_real": 0.0,
                        },
                        "shared_price_cache",
                    ),
                ),
                patch.object(tick_crypto, "save_state") as save_state,
                patch.object(tick_crypto, "append_jsonl") as append_jsonl,
            ):
                tick_crypto.bootstrap(engine, state_path, event_path, True, metadata)

        load_compatible_state.assert_not_called()
        self.assertEqual(engine.state.last_tick_msc, 1775959838836)
        self.assertEqual(engine.state.open_tickets, [])
        save_state.assert_called_once()
        append_jsonl.assert_called_once()

    def test_crypto_bootstrap_purges_stale_rearm_ticket_from_loaded_state(self) -> None:
        engine = SimpleNamespace(
            symbol="BTCUSD",
            state=SimpleNamespace(
                open_tickets=[
                    {"direction": "BUY", "entry_price": 72966.59, "from_rearm": False, "opened_time": 1775854800},
                    {"direction": "BUY", "entry_price": 72831.59, "from_rearm": True, "opened_time": 1775872800},
                ],
                lattice_started_time=1775872800,
            ),
        )
        metadata = {"direct_live": True}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "btc_state.json"
            event_path = Path(tmpdir) / "btc_events.jsonl"
            state_path.write_text("{}", encoding="utf-8")
            with (
                patch.object(tick_crypto, "load_compatible_state", side_effect=lambda *_args: None),
                patch.object(tick_crypto, "save_state") as save_state,
                patch.object(tick_crypto, "append_jsonl") as append_jsonl,
                patch.object(tick_crypto, "purge_stale_rearm_tickets", wraps=tick_crypto.purge_stale_rearm_tickets),
                patch("tick_penetration_lattice_core.time.time", return_value=1775887236.0),
            ):
                tick_crypto.bootstrap(engine, state_path, event_path, False, metadata)

        self.assertEqual(len(engine.state.open_tickets), 1)
        self.assertFalse(engine.state.open_tickets[0]["from_rearm"])
        save_state.assert_called_once()
        append_jsonl.assert_called_once()
        logged = append_jsonl.call_args.args[1]
        self.assertEqual(logged["action"], "purged_stale_rearm_tickets")
        self.assertEqual(logged["symbols"], ["BTCUSD"])
        self.assertEqual(len(logged["removed"]), 1)

    def test_fx_bootstrap_purges_stale_rearm_ticket_from_loaded_state(self) -> None:
        engine = SimpleNamespace(
            state=SimpleNamespace(
                open_tickets=[
                    {"direction": "SELL", "entry_price": 1.2700, "from_rearm": False, "opened_time": 1775854800},
                    {"direction": "SELL", "entry_price": 1.2715, "from_rearm": True, "opened_time": 1775872800},
                ],
                lattice_started_time=1775872800,
            ),
        )
        engines = {"GBPUSD": engine}
        metadata = {"direct_live": True}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "fx_state.json"
            event_path = Path(tmpdir) / "fx_events.jsonl"
            state_path.write_text("{}", encoding="utf-8")
            with (
                patch.object(tick_fx, "load_compatible_state", side_effect=lambda *_args: None),
                patch.object(tick_fx, "hydrate_tick_histories"),
                patch.object(tick_fx, "save_state") as save_state,
                patch.object(tick_fx, "append_jsonl") as append_jsonl,
                patch("tick_penetration_lattice_core.time.time", return_value=1775887236.0),
            ):
                tick_fx.bootstrap(engines, state_path, event_path, False, metadata)

        self.assertEqual(len(engine.state.open_tickets), 1)
        self.assertFalse(engine.state.open_tickets[0]["from_rearm"])
        save_state.assert_called_once()
        append_jsonl.assert_called_once()
        logged = append_jsonl.call_args.args[1]
        self.assertEqual(logged["action"], "purged_stale_rearm_tickets")
        self.assertEqual(logged["symbols"], ["GBPUSD"])
        self.assertEqual(len(logged["removed"]["GBPUSD"]), 1)


if __name__ == "__main__":
    unittest.main()
