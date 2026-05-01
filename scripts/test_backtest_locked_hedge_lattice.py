#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_locked_hedge_lattice as study


class LockedHedgeLatticeTests(unittest.TestCase):
    def test_open_locked_base_supports_locked_spread(self) -> None:
        tickets = study._open_locked_base(anchor=1.1000, step_px=0.0001, mode="locked_spread", bar_time=1)
        self.assertEqual(len(tickets), 2)
        self.assertEqual((tickets[0].direction, tickets[0].entry_price), ("SELL", 1.1))
        self.assertEqual((tickets[1].direction, tickets[1].entry_price), ("BUY", 1.1001))
        self.assertTrue(all(ticket.ticket_kind == "locked" for ticket in tickets))

    def test_open_locked_base_supports_full_hedge(self) -> None:
        tickets = study._open_locked_base(anchor=1.1000, step_px=0.0001, mode="full_hedge", bar_time=1)
        self.assertEqual(len(tickets), 2)
        self.assertEqual((tickets[0].direction, tickets[0].entry_price), ("BUY", 1.1))
        self.assertEqual((tickets[1].direction, tickets[1].entry_price), ("SELL", 1.1))

    def test_build_contracts_crosses_modes_and_reanchor_values(self) -> None:
        original_symbol_info = study.mt5.symbol_info
        try:
            study.mt5.symbol_info = lambda symbol: SimpleNamespace(point=0.00001, digits=5)  # type: ignore[assignment]
            args = SimpleNamespace(
                symbols=["GBPUSD"],
                timeframe="M1",
                step_pips=[0.5],
                modes=["locked_spread", "full_hedge"],
                oscillation_trigger_steps=[1],
                oscillation_close_steps=[1, 2],
                max_oscillation_per_side_values=[8],
                reanchor_threshold_steps=[6, 10],
            )
            contracts = study.build_contracts(args)
        finally:
            study.mt5.symbol_info = original_symbol_info  # type: ignore[assignment]
        self.assertEqual(len(contracts), 8)
        labels = {contract.variant_label for contract in contracts}
        self.assertIn("locked_locked_spread_step0.5pip_trigger1_close1_osc8_reanchor6", labels)
        self.assertIn("locked_full_hedge_step0.5pip_trigger1_close2_osc8_reanchor10", labels)

    def test_simulate_contract_harvests_and_reanchors(self) -> None:
        bars = [
            {"time": 1, "open": 1.1000, "high": 1.1000, "low": 1.1000, "close": 1.1000},
            {"time": 2, "open": 1.1000, "high": 1.1004, "low": 1.1000, "close": 1.1004},
            {"time": 3, "open": 1.1004, "high": 1.1004, "low": 1.1000, "close": 1.1000},
            {"time": 4, "open": 1.1000, "high": 1.1012, "low": 1.1000, "close": 1.1012},
        ]
        contract = study.LockedHedgeContract(
            symbol="GBPUSD",
            timeframe="M1",
            step_px=0.0001,
            mode="locked_spread",
            oscillation_trigger_steps=1,
            oscillation_close_steps=1,
            max_oscillation_per_side=8,
            reanchor_threshold_steps=10,
            variant_label="test",
        )
        info = SimpleNamespace(
            point=0.00001,
            digits=5,
            spread=10,
            currency_profit="USD",
            trade_contract_size=100000,
        )
        row = study.simulate_contract(contract, bars, info)
        self.assertGreater(row["gross_positive_booked_usd"], 0.0)
        self.assertGreaterEqual(row["realized_closes"], 1)
        self.assertGreaterEqual(row["max_open_total"], 2)


if __name__ == "__main__":
    unittest.main()
