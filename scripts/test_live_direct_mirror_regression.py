#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_penetration_lattice_mirror as mirror
import live_penetration_lattice_shadow as shadow


class LiveDirectMirrorRegressionTests(unittest.TestCase):
    def test_run_direct_live_exec_skips_source_event_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_state_path = root / "source_state.json"
            source_event_path = root / "source_events.jsonl"
            exec_state_path = root / "exec_state.json"
            exec_log_path = root / "exec_events.jsonl"

            source_state_path.write_text(json.dumps({"symbols": {}}), encoding="utf-8")
            source_event_path.write_text(json.dumps({"action": "open_ticket", "symbol": "BTCUSD"}) + "\n", encoding="utf-8")

            exec_state = {"offset": 0, "positions": []}
            with (
                patch.object(shadow.live_mirror, "process_event", side_effect=AssertionError("source event replay must stay disabled")) as process_event,
                patch.object(shadow.live_mirror, "reconcile_from_source_state") as reconcile_from_source_state,
                patch.object(shadow.live_mirror, "save_state") as save_state,
            ):
                shadow.run_direct_live_exec(
                    exec_state,
                    source_state_path=source_state_path,
                    source_event_path=source_event_path,
                    exec_state_path=exec_state_path,
                    exec_log_path=exec_log_path,
                    allowed_symbols={"BTCUSD"},
                    live_magic=941779,
                    attached_live_magics=[],
                    live_comment_prefix="PLIVE-BTC",
                    live_volume=0.01,
                )

            self.assertFalse(process_event.called)
            reconcile_from_source_state.assert_called_once()
            self.assertFalse(reconcile_from_source_state.call_args.kwargs["flatten_tracked_extras"])
            save_state.assert_called_once()
            self.assertEqual(exec_state["offset"], source_event_path.stat().st_size)

    def test_reconcile_preserves_duplicate_same_level_positions_by_count(self) -> None:
        payload = {
            "symbols": {
                "EURUSD": {
                    "open_tickets": [
                        {"direction": "BUY", "entry_price": 1.12345},
                        {"direction": "BUY", "entry_price": 1.12345},
                    ]
                }
            }
        }
        state = {"positions": [], "reconcile_gap_keys": [], "reconcile_retry_after": {}}

        opened: list[int] = []

        def fake_try_reconcile_open(positions, symbol, direction, entry_level, *_args, **_kwargs):
            ticket = 1000 + len(opened)
            opened.append(ticket)
            positions.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "entry_level": entry_level,
                    "live_ticket": ticket,
                    "comment": "TEST-B",
                    "position_comment": "TEST-B",
                    "opened_at": f"2026-04-10T00:00:0{len(opened)}Z",
                }
            )
            return True

        with (
            patch.object(mirror, "load_json", return_value=payload),
            patch.object(mirror, "broker_position_exists", return_value=True),
            patch.object(mirror, "try_reconcile_open", side_effect=fake_try_reconcile_open),
            patch.object(mirror, "append_jsonl"),
        ):
            mirror.reconcile_from_source_state(
                state,
                source_state_path=Path("unused.json"),
                allowed_symbols={"EURUSD"},
                log_path=Path("unused.log"),
                live_magic=941777,
                attached_live_magics=[941785],
                comment_prefix="PLIVE-LATTICE",
                live_volume=0.01,
            )

        self.assertEqual(opened, [1000, 1001])
        self.assertEqual(len(state["positions"]), 2)
        self.assertEqual([p["live_ticket"] for p in state["positions"]], [1000, 1001])

    def test_reconcile_tolerates_harmless_float_noise(self) -> None:
        payload = {
            "symbols": {
                "USDJPY": {
                    "open_tickets": [
                        {"direction": "SELL", "entry_price": 159.26125173611112},
                    ]
                }
            }
        }
        state = {
            "positions": [
                {
                    "symbol": "USDJPY",
                    "direction": "SELL",
                    "entry_level": 159.261252,
                    "live_ticket": 45912643,
                    "comment": "PLIVE-LATTICE-S",
                    "position_comment": "PLIVE-LATTICE-S",
                    "opened_at": "2026-04-10T19:01:04Z",
                }
            ],
            "reconcile_gap_keys": [],
            "reconcile_retry_after": {},
        }

        with (
            patch.object(mirror, "load_json", return_value=payload),
            patch.object(mirror, "broker_position_exists", return_value=True),
            patch.object(mirror, "try_reconcile_open") as try_reconcile_open,
            patch.object(mirror, "close_live_position") as close_live_position,
            patch.object(mirror, "append_jsonl"),
        ):
            mirror.reconcile_from_source_state(
                state,
                source_state_path=Path("unused.json"),
                allowed_symbols={"USDJPY"},
                log_path=Path("unused.log"),
                live_magic=941777,
                attached_live_magics=[941785],
                comment_prefix="PLIVE-LATTICE",
                live_volume=0.01,
            )

        try_reconcile_open.assert_not_called()
        close_live_position.assert_not_called()
        self.assertEqual(len(state["positions"]), 1)
        self.assertEqual(state["reconcile_gap_keys"], [])

    def test_reconcile_replaces_tracked_ticket_missing_broker_side(self) -> None:
        payload = {
            "symbols": {
                "BTCUSD": {
                    "open_tickets": [
                        {"direction": "BUY", "entry_price": 72741.59},
                    ]
                }
            }
        }
        state = {
            "positions": [
                {
                    "symbol": "BTCUSD",
                    "direction": "BUY",
                    "entry_level": 72741.59,
                    "live_ticket": 999001,
                    "comment": "PLIVE-BTC-B",
                    "position_comment": "PLIVE-BTC-B",
                    "opened_at": "2026-04-10T23:00:00Z",
                }
            ],
            "reconcile_gap_keys": [],
            "reconcile_retry_after": {},
        }

        opened: list[int] = []

        def fake_try_reconcile_open(positions, symbol, direction, entry_level, *_args, **_kwargs):
            ticket = 999100 + len(opened)
            opened.append(ticket)
            positions.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "entry_level": entry_level,
                    "live_ticket": ticket,
                    "comment": "PLIVE-BTC-B",
                    "position_comment": "PLIVE-BTC-B",
                    "opened_at": "2026-04-10T23:01:00Z",
                }
            )
            return True

        with (
            patch.object(mirror, "load_json", return_value=payload),
            patch.object(mirror, "broker_position_exists", return_value=False),
            patch.object(mirror, "try_reconcile_open", side_effect=fake_try_reconcile_open),
            patch.object(mirror, "append_jsonl"),
        ):
            mirror.reconcile_from_source_state(
                state,
                source_state_path=Path("unused.json"),
                allowed_symbols={"BTCUSD"},
                log_path=Path("unused.log"),
                live_magic=941779,
                attached_live_magics=[],
                comment_prefix="PLIVE-BTC",
                live_volume=0.01,
            )

        self.assertEqual(opened, [999100])
        self.assertEqual(len(state["positions"]), 1)
        self.assertEqual(state["positions"][0]["live_ticket"], 999100)

    def test_reconcile_accepts_attached_magic_as_alive_broker_inventory(self) -> None:
        payload = {
            "symbols": {
                "BTCUSD": {
                    "open_tickets": [
                        {"direction": "SELL", "entry_price": 74913.5},
                    ]
                }
            }
        }
        state = {
            "positions": [
                {
                    "symbol": "BTCUSD",
                    "direction": "SELL",
                    "entry_level": 74913.5,
                    "live_ticket": 45920015,
                    "broker_magic": 941785,
                    "comment": "PLSHADOW-S15-S",
                    "position_comment": "PLSHADOW-S15-S",
                    "opened_at": "2026-04-17T00:50:04Z",
                }
            ],
            "reconcile_gap_keys": [],
            "reconcile_retry_after": {},
        }

        with (
            patch.object(mirror, "load_json", return_value=payload),
            patch.object(mirror, "broker_position_exists", return_value=True) as broker_position_exists,
            patch.object(mirror, "try_reconcile_open") as try_reconcile_open,
            patch.object(mirror, "append_jsonl"),
        ):
            mirror.reconcile_from_source_state(
                state,
                source_state_path=Path("unused.json"),
                allowed_symbols={"BTCUSD"},
                log_path=Path("unused.log"),
                live_magic=941781,
                attached_live_magics=[941785, 941786],
                comment_prefix="PLIVE-BTC",
                live_volume=0.01,
            )

        try_reconcile_open.assert_not_called()
        broker_position_exists.assert_called_once_with(45920015, live_magic=941781, attached_live_magics=[941785, 941786])
        self.assertEqual(len(state["positions"]), 1)

    def test_reconcile_defers_stale_market_entry_until_price_is_near_level(self) -> None:
        payload = {
            "symbols": {
                "BTCUSD": {
                    "open_tickets": [
                        {"direction": "SELL", "entry_price": 73056.59},
                    ],
                    "reconcile_open_max_drift_px": 10.0,
                }
            }
        }
        state = {"positions": [], "reconcile_gap_keys": [], "reconcile_retry_after": {}}

        with (
            patch.object(mirror, "load_json", return_value=payload),
            patch.object(mirror, "current_market_price", return_value={"ok": True, "price": 72833.79, "tick_time_raw": 1775875921}),
            patch.object(mirror, "send_market_order") as send_market_order,
            patch.object(mirror, "append_jsonl"),
        ):
            mirror.reconcile_from_source_state(
                state,
                source_state_path=Path("unused.json"),
                allowed_symbols={"BTCUSD"},
                log_path=Path("unused.log"),
                live_magic=941779,
                attached_live_magics=[],
                comment_prefix="PLIVE-BTC",
                live_volume=0.01,
            )

        send_market_order.assert_not_called()
        self.assertEqual(state["positions"], [])
        self.assertEqual(len(state["reconcile_gap_keys"]), 1)
        self.assertIn("BTCUSD|SELL|73056.59000|slot=0", state["reconcile_gap_keys"][0])
        self.assertIn("BTCUSD|SELL|73056.59000|slot=0", state["reconcile_retry_after"])

    def test_reconcile_preserves_extra_tracked_positions_when_flatten_disabled(self) -> None:
        payload = {"symbols": {"BTCUSD": {"open_tickets": []}}}
        state = {
            "positions": [
                {
                    "symbol": "BTCUSD",
                    "direction": "SELL",
                    "entry_level": 72000.0,
                    "live_ticket": 45910001,
                    "comment": "PLIVE-BTCM5-S",
                    "position_comment": "PLIVE-BTCM5-S",
                    "opened_at": "2026-04-13T19:10:00Z",
                }
            ],
            "reconcile_gap_keys": [],
            "reconcile_retry_after": {},
        }

        with (
            patch.object(mirror, "load_json", return_value=payload),
            patch.object(mirror, "broker_position_exists", return_value=True),
            patch.object(mirror, "close_live_position") as close_live_position,
            patch.object(mirror, "append_jsonl") as append_jsonl,
        ):
            mirror.reconcile_from_source_state(
                state,
                source_state_path=Path("unused.json"),
                allowed_symbols={"BTCUSD"},
                log_path=Path("unused.log"),
                flatten_tracked_extras=False,
                live_magic=941780,
                attached_live_magics=[],
                comment_prefix="PLIVE-BTCM5",
                live_volume=0.01,
            )

        close_live_position.assert_not_called()
        self.assertEqual(len(state["positions"]), 1)
        logged_actions = [call.args[1]["action"] for call in append_jsonl.call_args_list]
        self.assertIn("reconcile_preserve_extra_tracked_position", logged_actions)

    def test_reconcile_does_not_reopen_stale_missing_rearm_ticket(self) -> None:
        payload = {
            "symbols": {
                "BTCUSD": {
                    "open_tickets": [
                        {
                            "direction": "BUY",
                            "entry_price": 72831.59,
                            "from_rearm": True,
                            "opened_time": 1775872800,
                        },
                    ],
                    "reconcile_open_max_drift_px": 400.0,
                }
            }
        }
        state = {"positions": [], "reconcile_gap_keys": [], "reconcile_retry_after": {}}

        with (
            patch.object(mirror, "load_json", return_value=payload),
            patch.object(mirror, "send_market_order") as send_market_order,
            patch.object(mirror, "append_jsonl"),
            patch("live_penetration_lattice_mirror.time.time", return_value=1775887236.0),
        ):
            mirror.reconcile_from_source_state(
                state,
                source_state_path=Path("unused.json"),
                allowed_symbols={"BTCUSD"},
                log_path=Path("unused.log"),
                live_magic=941779,
                attached_live_magics=[],
                comment_prefix="PLIVE-BTC",
                live_volume=0.01,
            )

        send_market_order.assert_not_called()
        self.assertEqual(state["positions"], [])
        self.assertEqual(len(state["reconcile_gap_keys"]), 1)
        self.assertIn("BTCUSD|BUY|72831.59000|slot=0", state["reconcile_gap_keys"][0])
        self.assertIn("BTCUSD|BUY|72831.59000|slot=0", state["reconcile_retry_after"])


if __name__ == "__main__":
    unittest.main()
