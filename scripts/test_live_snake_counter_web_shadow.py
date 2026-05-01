#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_snake_counter_web_shadow as live_shadow
from backtest_snake_counter_web import SnakeContract, SnakeTicket


class LiveSnakeCounterWebShadowTests(unittest.TestCase):
    def test_state_roundtrip_preserves_ticket_metadata(self) -> None:
        payload = {
            "open_tickets": [
                {
                    "direction": "BUY",
                    "entry_price": 1.2345,
                    "opened_time": 123,
                    "ticket_kind": "hedge",
                    "live_ticket": 456,
                    "position_comment": "hedge-row",
                    "pair_id": 7,
                }
            ]
        }
        state = live_shadow.SnakeShadowState.from_payload(payload, symbol="EURUSD")
        ticket = state.open_tickets[0]
        self.assertEqual(ticket.ticket_kind, "hedge")
        self.assertEqual(ticket.live_ticket, 456)
        self.assertEqual(ticket.position_comment, "hedge-row")
        self.assertEqual(ticket.pair_id, 7)

    def test_build_contract_carries_hedge_fields(self) -> None:
        namespace = type(
            "Args",
            (),
            {
                "symbol": "GBPUSD",
                "timeframe": "M1",
                "step_pips": 0.03,
                "retrace_steps": 1,
                "hold_frontier": 0,
                "rebase_on_flat": True,
                "max_open_per_side": 600,
                "controller_mode": "static",
                "portfolio_close_mode": "float_zero",
                "hedge_mode": "same_level",
                "hedge_trigger_depth": 4,
                "min_harvest_profit_usd": 0.25,
                "variant_label": "",
            },
        )()
        contract = live_shadow.build_contract(namespace, pip_px=0.0001)
        self.assertEqual(contract.hedge_mode, "same_level")
        self.assertEqual(contract.hedge_trigger_depth, 4)
        self.assertEqual(contract.hedge_profit_threshold_steps, 0)
        self.assertEqual(contract.min_harvest_profit_usd, 0.25)
        self.assertIn("_hedgesame_level_", contract.variant_label)

    def test_same_level_hedge_opens_opposite_ticket_live(self) -> None:
        contract = SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=10,
            controller_mode="static",
            portfolio_close_mode="none",
            hedge_mode="same_level",
            hedge_trigger_depth=4,
            hedge_profit_threshold_steps=0,
            variant_label="test",
        )
        state = live_shadow.SnakeShadowState(symbol="GBPUSD")
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            live_shadow.maybe_open_ticket(
                state=state,
                contract=contract,
                direction="SELL",
                level=1,
                entry_price=1.101,
                tick={"time": 1, "time_msc": 1000, "bid": 1.1009, "ask": 1.1011},
                event_path=event_path,
                spread_px=0.0002,
                step_px=0.001,
                divisor=1,
                max_entry_spread_ratio=0.0,
                spread_ratio_history=[],
                liquidity_gap_spread_multiplier=0.0,
                liquidity_gap_spread_lookback=0,
                liquidity_gap_spread_floor_ratio=0.0,
                direct_exec=None,
            )
        self.assertEqual(len(state.open_tickets), 2)
        self.assertEqual([ticket.ticket_kind for ticket in state.open_tickets], ["core", "hedge"])
        self.assertEqual([ticket.direction for ticket in state.open_tickets], ["SELL", "BUY"])

    def test_depth_threshold_hedge_waits_for_core_depth(self) -> None:
        contract = SnakeContract(
            symbol="EURUSD",
            timeframe="M1",
            step_px=0.001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=10,
            controller_mode="static",
            portfolio_close_mode="none",
            hedge_mode="depth_threshold",
            hedge_trigger_depth=4,
            hedge_profit_threshold_steps=0,
            variant_label="test",
        )
        state = live_shadow.SnakeShadowState(
            symbol="EURUSD",
            open_tickets=[
                SnakeTicket(direction="SELL", entry_price=1.101, opened_time=1, ticket_kind="core"),
                SnakeTicket(direction="SELL", entry_price=1.102, opened_time=2, ticket_kind="core"),
                SnakeTicket(direction="SELL", entry_price=1.103, opened_time=3, ticket_kind="core"),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            live_shadow.maybe_open_ticket(
                state=state,
                contract=contract,
                direction="SELL",
                level=4,
                entry_price=1.104,
                tick={"time": 4, "time_msc": 4000, "bid": 1.1039, "ask": 1.1041},
                event_path=event_path,
                spread_px=0.0002,
                step_px=0.001,
                divisor=1,
                max_entry_spread_ratio=0.0,
                spread_ratio_history=[],
                liquidity_gap_spread_multiplier=0.0,
                liquidity_gap_spread_lookback=0,
                liquidity_gap_spread_floor_ratio=0.0,
                direct_exec=None,
            )
        self.assertEqual(len(state.open_tickets), 5)
        self.assertEqual([ticket.ticket_kind for ticket in state.open_tickets[-2:]], ["core", "hedge"])

    def test_run_once_collapses_direct_live_backlog_to_latest_tick(self) -> None:
        contract = SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=10,
            controller_mode="static",
            portfolio_close_mode="float_zero",
            hedge_mode="same_level",
            hedge_trigger_depth=4,
            hedge_profit_threshold_steps=0,
            variant_label="test",
        )
        state = live_shadow.SnakeShadowState(symbol="GBPUSD", anchor=1.1, last_price=1.1, last_tick_msc=0)
        processed: list[int] = []

        def capture_process_tick(**kwargs) -> None:
            processed.append(int(kwargs["tick"]["time_msc"]))

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            event_path = Path(tmpdir) / "events.jsonl"
            runner = {"pid": 1, "heartbeat_at": "", "status": "ok"}
            metadata = {"symbol": "GBPUSD"}
            ticks = [
                {"time_msc": 1000, "time": 1, "bid": 1.1, "ask": 1.1002},
                {"time_msc": 2000, "time": 2, "bid": 1.101, "ask": 1.1012},
            ]
            with mock.patch.object(live_shadow, "load_ticks_since_with_source", return_value=(ticks, "mock_history")), mock.patch.object(
                live_shadow, "load_latest_tick", return_value=(None, "mock_latest")
            ), mock.patch.object(live_shadow, "process_tick", side_effect=capture_process_tick), mock.patch.object(
                live_shadow.live_mirror, "broker_live_positions", return_value=[]
            ):
                exec_log_path = Path(tmpdir) / "exec.jsonl"
                live_shadow.run_once(
                    state=state,
                    contract=contract,
                    symbol_info=type("Info", (), {"point": 0.00001, "digits": 5})(),
                    state_path=state_path,
                    event_path=event_path,
                    metadata=metadata,
                    runner=runner,
                    shared_price_max_age_ms=0,
                    session_gate=False,
                    max_entry_spread_ratio=0.0,
                    liquidity_gap_spread_multiplier=0.0,
                    liquidity_gap_spread_lookback=0,
                    liquidity_gap_spread_floor_ratio=0.0,
                    require_live_admissibility=False,
                    direct_exec={"live_magic": 1, "live_comment_prefix": "T", "live_volume": 0.01, "log_path": exec_log_path},
                )
        self.assertEqual(processed, [2000])

    def test_apply_closes_skips_live_harvests_below_profit_buffer(self) -> None:
        contract = SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=10,
            controller_mode="static",
            portfolio_close_mode="none",
            hedge_mode="none",
            hedge_trigger_depth=4,
            hedge_profit_threshold_steps=0,
            variant_label="test",
            min_harvest_profit_usd=0.2,
        )
        ticket = SnakeTicket(direction="BUY", entry_price=1.1000, opened_time=1)
        state = live_shadow.SnakeShadowState(symbol="GBPUSD", open_tickets=[ticket])
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            with mock.patch.object(live_shadow, "research_unit_pnl_usd", return_value=0.1):
                live_shadow.apply_closes(
                    state=state,
                    contract=contract,
                    symbol_info=type("Info", (), {"point": 0.00001, "digits": 5})(),
                    price=1.101,
                    spread_px=0.0001,
                    tick={"time": 1, "time_msc": 1000, "bid": 1.1009, "ask": 1.1011},
                    event_path=event_path,
                    direct_exec={"live_magic": 1, "live_comment_prefix": "T", "live_volume": 0.01},
                )
        self.assertEqual(len(state.open_tickets), 1)
        self.assertEqual(state.realized_closes, 0)

    def test_apply_closes_allows_funded_rescue_pair_when_net_non_negative(self) -> None:
        contract = SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=10,
            controller_mode="static",
            portfolio_close_mode="funded_rescue",
            hedge_mode="none",
            hedge_trigger_depth=4,
            hedge_profit_threshold_steps=0,
            variant_label="test",
            positive_only_closes=True,
        )
        best_ticket = SnakeTicket(direction="BUY", entry_price=1.1000, opened_time=1)
        worst_ticket = SnakeTicket(direction="BUY", entry_price=1.1005, opened_time=2)
        state = live_shadow.SnakeShadowState(symbol="GBPUSD", open_tickets=[best_ticket, worst_ticket])
        closed_actions: list[str] = []

        def fake_projected_close_pnl_usd(**kwargs):
            ticket = kwargs["ticket"]
            return 0.30 if ticket is best_ticket else -0.10

        def fake_close_ticket(**kwargs) -> None:
            closed_actions.append(str(kwargs["action"]))

        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            with mock.patch.object(live_shadow, "projected_close_pnl_usd", side_effect=fake_projected_close_pnl_usd), mock.patch.object(
                live_shadow, "close_ticket", side_effect=fake_close_ticket
            ):
                live_shadow.apply_closes(
                    state=state,
                    contract=contract,
                    symbol_info=type("Info", (), {"point": 0.00001, "digits": 5})(),
                    price=1.101,
                    spread_px=0.0001,
                    tick={"time": 1, "time_msc": 1000, "bid": 1.1009, "ask": 1.1011},
                    event_path=event_path,
                    direct_exec={"live_magic": 1, "live_comment_prefix": "T", "live_volume": 0.01},
                )

        self.assertIn("close_ticket_funded_rescue", closed_actions)

    def test_apply_closes_skips_live_close_when_executable_fill_is_negative(self) -> None:
        contract = SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.00001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=10,
            controller_mode="static",
            portfolio_close_mode="none",
            hedge_mode="none",
            hedge_trigger_depth=4,
            hedge_profit_threshold_steps=0,
            variant_label="test",
            min_harvest_profit_usd=0.0,
        )
        ticket = SnakeTicket(direction="BUY", entry_price=1.1002, opened_time=1)
        state = live_shadow.SnakeShadowState(symbol="GBPUSD", open_tickets=[ticket])
        info = type("Info", (), {"point": 0.00001, "digits": 5, "currency_profit": "USD", "trade_contract_size": 100000})()
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            live_shadow.apply_closes(
                state=state,
                contract=contract,
                symbol_info=info,
                price=1.1003,
                spread_px=0.0006,
                tick={"time": 1, "time_msc": 1000, "bid": 1.1000, "ask": 1.1006},
                event_path=event_path,
                direct_exec={"live_magic": 1, "live_comment_prefix": "T", "live_volume": 0.01},
            )
        self.assertEqual(len(state.open_tickets), 1)
        self.assertEqual(state.realized_closes, 0)

    def test_projected_close_pnl_uses_executable_side_for_direct_live(self) -> None:
        state = live_shadow.SnakeShadowState(symbol="GBPUSD")
        ticket = SnakeTicket(direction="SELL", entry_price=1.1005, opened_time=1)
        info = type("Info", (), {"point": 0.00001, "digits": 5, "currency_profit": "USD", "trade_contract_size": 100000})()
        pnl = live_shadow.projected_close_pnl_usd(
            state=state,
            ticket=ticket,
            symbol_info=info,
            mid_price=1.1000,
            spread_px=0.0004,
            tick={"bid": 1.0998, "ask": 1.1002},
            direct_exec={"live_magic": 1},
        )
        self.assertAlmostEqual(pnl, 0.3, places=6)

    def test_sync_state_to_broker_positions_clears_phantom_open_tickets(self) -> None:
        state = live_shadow.SnakeShadowState(
            symbol="GBPUSD",
            open_tickets=[
                SnakeTicket(
                    direction="BUY",
                    entry_price=1.2345,
                    opened_time=123,
                    ticket_kind="core",
                    live_ticket=999,
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            with mock.patch.object(live_shadow.live_mirror, "broker_live_positions", return_value=[]):
                changed = live_shadow.sync_state_to_broker_positions(
                    state=state,
                    event_path=event_path,
                    live_magic=123,
                )
        self.assertTrue(changed)
        self.assertEqual(state.open_tickets, [])

    def test_sync_state_to_broker_positions_rehydrates_and_rebuilds_realized_state(self) -> None:
        state = live_shadow.SnakeShadowState(
            symbol="GBPUSD",
            open_tickets=[
                SnakeTicket(
                    direction="BUY",
                    entry_price=1.2345,
                    opened_time=123,
                    ticket_kind="core",
                    live_ticket=111,
                )
            ],
            realized_net_usd=12.5,
            gross_positive_booked_usd=4.0,
            realized_closes=7,
            wins=3,
            max_open_total=1,
            max_open_sell=0,
            max_open_buy=1,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            log_path = Path(tmpdir) / "exec.jsonl"
            log_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                        {
                            "symbol": "GBPUSD",
                            "result": {
                                "attempts": [{"deal": 9001}],
                                "broker_fill": {
                                    "symbol": "GBPUSD",
                                    "entry": int(live_shadow.mt5.DEAL_ENTRY_OUT),
                                    "profit": 2.2,
                                    "commission": -0.2,
                                        "swap": 0.0,
                                        "fee": 0.0,
                                        "comment": "TS-1",
                                        "magic": 941999,
                                        "time": 111,
                                        "time_msc": 222,
                                    },
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "symbol": "GBPUSD",
                                "result": {
                                    "attempts": [{"deal": 9002}],
                                    "broker_fill": {
                                        "symbol": "GBPUSD",
                                        "entry": int(live_shadow.mt5.DEAL_ENTRY_OUT),
                                        "profit": -0.4,
                                        "commission": 0.0,
                                        "swap": 0.0,
                                        "fee": 0.0,
                                        "comment": "TS-1",
                                        "magic": 941999,
                                        "time": 333,
                                        "time_msc": 444,
                                    },
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(live_shadow.mt5, "history_deals_get", return_value=[]), mock.patch.object(
                live_shadow.live_mirror,
                "broker_live_positions",
                return_value=[
                    {
                        "symbol": "GBPUSD",
                        "direction": "SELL",
                        "ticket": 321,
                        "price_open": 1.2,
                        "comment": "PSNAKE",
                        "time": 1234,
                    }
                ],
            ):
                changed = live_shadow.sync_state_to_broker_positions(
                    state=state,
                    event_path=event_path,
                    live_magic=941999,
                    direct_exec={
                        "live_magic": 941999,
                        "log_path": log_path,
                        "live_comment_prefix": "TS",
                        "max_open_per_side": 3,
                    },
                )
        self.assertTrue(changed)
        self.assertEqual(len(state.open_tickets), 1)
        self.assertEqual(state.open_tickets[0].live_ticket, 321)
        self.assertAlmostEqual(state.realized_net_usd, 1.6)
        self.assertAlmostEqual(state.gross_positive_booked_usd, 2.0)
        self.assertEqual(state.realized_closes, 2)
        self.assertEqual(state.wins, 1)
        self.assertEqual(state.max_open_total, 6)
        self.assertEqual(state.max_open_sell, 1)
        self.assertEqual(state.max_open_buy, 1)

    def test_run_once_marks_live_contract_friction_invalid_before_processing(self) -> None:
        contract = SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.000003,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=10,
            controller_mode="static",
            portfolio_close_mode="float_zero",
            hedge_mode="same_level",
            hedge_trigger_depth=4,
            hedge_profit_threshold_steps=0,
            variant_label="test",
        )
        state = live_shadow.SnakeShadowState(symbol="GBPUSD", anchor=1.1, last_price=1.1, last_tick_msc=0)
        processed: list[int] = []

        def capture_process_tick(**kwargs) -> None:
            processed.append(int(kwargs["tick"]["time_msc"]))

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            event_path = Path(tmpdir) / "events.jsonl"
            runner = {"pid": 1, "heartbeat_at": "", "status": "ok"}
            metadata = {"symbol": "GBPUSD"}
            live_tick = {"time_msc": 3000, "time": 3, "bid": 1.10000, "ask": 1.10008}
            with mock.patch.object(live_shadow, "load_ticks_since_with_source", return_value=([], "mock_history")), mock.patch.object(
                live_shadow, "load_latest_tick", return_value=(live_tick, "mock_latest")
            ), mock.patch.object(live_shadow, "process_tick", side_effect=capture_process_tick), mock.patch.object(
                live_shadow.live_mirror, "broker_live_positions", return_value=[]
            ):
                exec_log_path = Path(tmpdir) / "exec.jsonl"
                live_shadow.run_once(
                    state=state,
                    contract=contract,
                    symbol_info=type("Info", (), {"point": 0.00001, "digits": 5})(),
                    state_path=state_path,
                    event_path=event_path,
                    metadata=metadata,
                    runner=runner,
                    shared_price_max_age_ms=0,
                    session_gate=False,
                    max_entry_spread_ratio=0.3,
                    liquidity_gap_spread_multiplier=0.0,
                    liquidity_gap_spread_lookback=0,
                    liquidity_gap_spread_floor_ratio=0.0,
                    require_live_admissibility=True,
                    direct_exec={
                        "live_magic": 1,
                        "live_comment_prefix": "T",
                        "live_volume": 0.01,
                        "log_path": exec_log_path,
                    },
                )
            saved = live_shadow.json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(processed, [])
        self.assertEqual(runner["status"], "live_contract_friction_invalid")
        self.assertGreater(runner["live_admissibility_spread_to_step_ratio"], 0.3)
        self.assertEqual(saved["runner"]["status"], "live_contract_friction_invalid")

    def test_maybe_open_ticket_blocks_liquidity_gap_spread(self) -> None:
        contract = SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=10,
            controller_mode="static",
            portfolio_close_mode="none",
            hedge_mode="none",
            hedge_trigger_depth=4,
            hedge_profit_threshold_steps=0,
            variant_label="test",
        )
        state = live_shadow.SnakeShadowState(symbol="GBPUSD")
        spread_history = [0.10, 0.10, 0.12, 0.11]
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            live_shadow.maybe_open_ticket(
                state=state,
                contract=contract,
                direction="SELL",
                level=1,
                entry_price=1.101,
                tick={"time": 1, "time_msc": 1000, "bid": 1.1009, "ask": 1.1014},
                event_path=event_path,
                spread_px=0.0005,
                step_px=0.001,
                divisor=1,
                max_entry_spread_ratio=0.0,
                spread_ratio_history=spread_history,
                liquidity_gap_spread_multiplier=2.0,
                liquidity_gap_spread_lookback=4,
                liquidity_gap_spread_floor_ratio=0.15,
                direct_exec=None,
            )
            events = event_path.read_text(encoding="utf-8")
        self.assertEqual(len(state.open_tickets), 0)
        self.assertIn("open_blocked_wide_spread", events)
        self.assertIn("liquidity_gap", events)

    def test_run_once_marks_live_contract_friction_invalid_via_liquidity_gap(self) -> None:
        contract = SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=10,
            controller_mode="static",
            portfolio_close_mode="float_zero",
            hedge_mode="same_level",
            hedge_trigger_depth=4,
            hedge_profit_threshold_steps=0,
            variant_label="test",
        )
        state = live_shadow.SnakeShadowState(symbol="GBPUSD", anchor=1.1, last_price=1.1, last_tick_msc=0)
        processed: list[int] = []

        def capture_process_tick(**kwargs) -> None:
            processed.append(int(kwargs["tick"]["time_msc"]))

        ticks = [
            {"time_msc": 1000, "time": 1, "bid": 1.10000, "ask": 1.10001},
            {"time_msc": 2000, "time": 2, "bid": 1.10000, "ask": 1.10001},
            {"time_msc": 3000, "time": 3, "bid": 1.10000, "ask": 1.10001},
            {"time_msc": 4000, "time": 4, "bid": 1.10000, "ask": 1.10001},
        ]
        live_tick = {"time_msc": 5000, "time": 5, "bid": 1.10000, "ask": 1.10050}
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            event_path = Path(tmpdir) / "events.jsonl"
            runner = {"pid": 1, "heartbeat_at": "", "status": "ok"}
            metadata = {"symbol": "GBPUSD"}
            with mock.patch.object(live_shadow, "load_ticks_since_with_source", return_value=(ticks, "mock_history")), mock.patch.object(
                live_shadow, "load_latest_tick", return_value=(live_tick, "mock_latest")
            ), mock.patch.object(live_shadow, "process_tick", side_effect=capture_process_tick), mock.patch.object(
                live_shadow.live_mirror, "broker_live_positions", return_value=[]
            ):
                exec_log_path = Path(tmpdir) / "exec.jsonl"
                live_shadow.run_once(
                    state=state,
                    contract=contract,
                    symbol_info=type("Info", (), {"point": 0.00001, "digits": 5})(),
                    state_path=state_path,
                    event_path=event_path,
                    metadata=metadata,
                    runner=runner,
                    shared_price_max_age_ms=0,
                    session_gate=False,
                    max_entry_spread_ratio=0.0,
                    liquidity_gap_spread_multiplier=2.0,
                    liquidity_gap_spread_lookback=4,
                    liquidity_gap_spread_floor_ratio=0.15,
                    require_live_admissibility=True,
                    direct_exec={
                        "live_magic": 1,
                        "live_comment_prefix": "T",
                        "live_volume": 0.01,
                        "log_path": exec_log_path,
                    },
                )
            saved = live_shadow.json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(processed, [])
        self.assertEqual(runner["status"], "live_contract_friction_invalid")
        self.assertEqual(runner["live_admissibility_spread_block_mode"], "liquidity_gap")
        self.assertEqual(saved["runner"]["live_admissibility_spread_block_mode"], "liquidity_gap")

    def test_reconcile_state_with_broker_clears_stale_direct_live_inventory_when_broker_flat(self) -> None:
        state = live_shadow.SnakeShadowState(
            symbol="GBPUSD",
            open_tickets=[
                SnakeTicket(direction="BUY", entry_price=1.2, opened_time=1, live_ticket=111, position_comment="A"),
                SnakeTicket(direction="SELL", entry_price=1.3, opened_time=2, live_ticket=222, position_comment="B"),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            with mock.patch.object(live_shadow.live_mirror, "broker_live_positions", return_value=[]):
                changed = live_shadow.reconcile_state_with_broker(
                    state=state,
                    event_path=event_path,
                    direct_exec={"live_magic": 941795},
                )
            events = event_path.read_text(encoding="utf-8")
        self.assertTrue(changed)
        self.assertEqual(state.open_tickets, [])
        self.assertIn("broker_flat_cleared_stale_state", events)

    def test_reconcile_state_with_broker_rehydrates_missing_live_positions(self) -> None:
        state = live_shadow.SnakeShadowState(symbol="EURUSD")
        broker_rows = [
            {
                "symbol": "EURUSD",
                "direction": "BUY",
                "ticket": 333,
                "price_open": 1.1781,
                "comment": "PSNAKE-EURUSD-B",
                "time": 123,
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "events.jsonl"
            with mock.patch.object(live_shadow.live_mirror, "broker_live_positions", return_value=broker_rows):
                changed = live_shadow.reconcile_state_with_broker(
                    state=state,
                    event_path=event_path,
                    direct_exec={"live_magic": 941796},
                )
        self.assertTrue(changed)
        self.assertEqual(len(state.open_tickets), 1)
        self.assertEqual(state.open_tickets[0].live_ticket, 333)
        self.assertEqual(state.open_tickets[0].entry_price, 1.1781)

    def test_run_once_blocks_fresh_direct_live_start_when_broker_carry_exists(self) -> None:
        contract = SnakeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.001,
            retrace_steps=1,
            hold_frontier=0,
            rebase_on_flat=True,
            max_open_per_side=10,
            controller_mode="static",
            portfolio_close_mode="float_zero",
            hedge_mode="same_level",
            hedge_trigger_depth=4,
            hedge_profit_threshold_steps=0,
            variant_label="test",
        )
        state = live_shadow.SnakeShadowState(symbol="GBPUSD")
        processed: list[int] = []

        def capture_process_tick(**kwargs) -> None:
            processed.append(int(kwargs["tick"]["time_msc"]))

        broker_rows = [
            {
                "symbol": "GBPUSD",
                "direction": "BUY",
                "ticket": 555,
                "price_open": 1.2510,
                "comment": "PSNAKE-GBPUSD-B",
                "time": 123,
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            event_path = Path(tmpdir) / "events.jsonl"
            runner = {"pid": 1, "heartbeat_at": "", "status": "ok"}
            metadata = {"symbol": "GBPUSD"}
            with mock.patch.object(
                live_shadow.live_mirror,
                "broker_live_positions",
                return_value=broker_rows,
            ), mock.patch.object(
                live_shadow,
                "process_tick",
                side_effect=capture_process_tick,
            ):
                live_shadow.run_once(
                    state=state,
                    contract=contract,
                    symbol_info=type("Info", (), {"point": 0.00001, "digits": 5})(),
                    state_path=state_path,
                    event_path=event_path,
                    metadata=metadata,
                    runner=runner,
                    shared_price_max_age_ms=0,
                    session_gate=False,
                    max_entry_spread_ratio=0.0,
                    liquidity_gap_spread_multiplier=0.0,
                    liquidity_gap_spread_lookback=0,
                    liquidity_gap_spread_floor_ratio=0.0,
                    require_live_admissibility=False,
                    direct_exec={
                        "live_magic": 941795,
                        "live_comment_prefix": "PSNAKE-GBPUSD",
                        "live_volume": 0.01,
                        "block_on_prestart_open_carry": True,
                    },
                )
            saved = live_shadow.json.loads(state_path.read_text(encoding="utf-8"))
            events = event_path.read_text(encoding="utf-8")
        self.assertEqual(processed, [])
        self.assertEqual(state.open_tickets, [])
        self.assertEqual(runner["status"], "pre_start_open_carry_blocked")
        self.assertEqual(saved["runner"]["status"], "pre_start_open_carry_blocked")
        self.assertIn("pre_start_open_carry_blocked", events)


if __name__ == "__main__":
    unittest.main()
