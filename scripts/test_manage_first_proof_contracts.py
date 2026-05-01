#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import manage_first_proof_contracts as manager


class ManageFirstProofContractsTests(unittest.TestCase):
    def test_resolve_symbols_from_all_ready_queue_rows(self) -> None:
        queue_payload = {
            "rows": [
                {
                    "symbol": "AUDUSD",
                    "proposal_status": "proposal_ready_for_launch_contract",
                    "next_action_class": "formalize_first_seat_proof_contract",
                },
                {
                    "symbol": "XRPUSD",
                    "proposal_status": "proposal_ready_for_launch_contract",
                    "next_action_class": "formalize_first_seat_proof_contract",
                },
                {
                    "symbol": "NZDUSD",
                    "proposal_status": "proposal_ready",
                    "next_action_class": "formalize_queue_contract",
                },
            ]
        }
        args = SimpleNamespace(all_ready=True, symbol=[])

        symbols = manager.resolve_symbols(args, queue_payload)

        self.assertEqual(symbols, ["AUDUSD", "XRPUSD"])

    def test_build_contract_merges_queue_and_first_proof_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "configs" / "hungry_hippo_audusd_m15_breakout_shadow.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "name": "shadow_audusd_m15_hh_breakout_v1",
                        "watchdog_group": "fx_watchdog",
                        "restart_args": ["scripts/live_penetration_lattice_tick_shadow.py", "--symbol", "AUDUSD"],
                        "state_path": "reports/penetration_lattice_shadow_audusd_m15_hh_breakout_v1_state.json",
                        "event_path": "reports/penetration_lattice_shadow_audusd_m15_hh_breakout_v1_events.jsonl",
                        "enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            old_root = manager.ROOT
            manager.ROOT = root
            try:
                contract = manager.build_contract(
                    "AUDUSD",
                    queue_payload={
                        "rows": [
                            {
                                "symbol": "AUDUSD",
                                "task_id": "audusd_first_live_seat_proof_contract",
                                "title": "Launch the AUDUSD first live-seat proof contract",
                                "proposal_status": "proposal_ready_for_launch_contract",
                                "next_action_class": "formalize_first_seat_proof_contract",
                            }
                        ]
                    },
                    first_proof_payload={
                        "rows": [
                            {
                                "symbol": "AUDUSD",
                                "config_path": "configs\\hungry_hippo_audusd_m15_breakout_shadow.json",
                                "packet_role": "watch_only_outside_current_unlock_ladder",
                                "launch_readiness": "watch_only_outside_current_unlock_ladder",
                                "runtime_state": "not_launched_yet",
                                "rollout_blocker": "outside ladder",
                                "next_action": "keep parked",
                                "validation_verdict": "pass",
                            }
                        ]
                    },
                )
            finally:
                manager.ROOT = old_root

        self.assertEqual(contract["symbol"], "AUDUSD")
        self.assertEqual(contract["task_id"], "audusd_first_live_seat_proof_contract")
        self.assertEqual(contract["packet_role"], "watch_only_outside_current_unlock_ladder")
        self.assertEqual(contract["watchdog_group"], "fx_watchdog")
        self.assertEqual(contract["lane"]["name"], "shadow_audusd_m15_hh_breakout_v1")

    def test_upsert_registry_and_watchdog_membership(self) -> None:
        registry = {"lanes": []}
        watchdog = {"groups": {"fx_watchdog": {"label": "FX", "lanes": []}}}
        lane = {
            "name": "shadow_audusd_m15_hh_breakout_v1",
            "watchdog_group": "fx_watchdog",
            "restart_args": ["scripts/live_penetration_lattice_tick_shadow.py", "--symbol", "AUDUSD"],
            "enabled": False,
            "pause_note": "parked_first_launch_contract_2026_04_16",
        }

        registry_changed = manager.upsert_registry_lane(registry, lane, enabled_override=True)
        watchdog_changed = manager.ensure_watchdog_membership(watchdog, "fx_watchdog", "shadow_audusd_m15_hh_breakout_v1")

        self.assertTrue(registry_changed)
        self.assertTrue(watchdog_changed)
        self.assertEqual(registry["lanes"][0]["name"], "shadow_audusd_m15_hh_breakout_v1")
        self.assertTrue(registry["lanes"][0]["enabled"])
        self.assertEqual(registry["lanes"][0]["pause_note"], "")
        self.assertIn("shadow_audusd_m15_hh_breakout_v1", watchdog["groups"]["fx_watchdog"]["lanes"])

    def test_blocked_launch_symbols_respects_packet_readiness(self) -> None:
        blocked = manager.blocked_launch_symbols(
            [
                {"symbol": "AUDUSD", "launch_readiness": "watch_only_outside_current_unlock_ladder"},
                {"symbol": "USDCAD", "launch_readiness": "already_started"},
                {"symbol": "GBPUSD", "launch_readiness": "launch_now"},
            ]
        )

        self.assertEqual(blocked, ["AUDUSD"])


if __name__ == "__main__":
    unittest.main()
