#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_hungry_hippo_first_proof_launch_packet_board as board


class BuildHungryHippoFirstProofLaunchPacketBoardTests(unittest.TestCase):
    def test_build_payload_makes_only_first_lane_launchable(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {
                        "symbol": "AUDUSD",
                        "config_path": "configs/audusd.json",
                        "watchdog_group": "fx_watchdog",
                        "runner_family": "tick_shadow",
                        "state_path": "reports/audusd_state.json",
                        "event_path": "reports/audusd_events.jsonl",
                        "runtime_state": "not_launched_yet",
                        "validation_verdict": "pass",
                        "enabled": False,
                        "pause_note": "parked",
                    },
                    {
                        "symbol": "USDCAD",
                        "config_path": "configs/usdcad.json",
                        "watchdog_group": "fx_watchdog",
                        "runner_family": "tick_shadow",
                        "state_path": "reports/usdcad_state.json",
                        "event_path": "reports/usdcad_events.jsonl",
                        "runtime_state": "not_launched_yet",
                        "validation_verdict": "pass",
                        "enabled": False,
                        "pause_note": "parked",
                    },
                    {
                        "symbol": "XRPUSD",
                        "config_path": "configs/xrpusd.json",
                        "watchdog_group": "crypto_watchdog",
                        "runner_family": "tick_crypto_shadow",
                        "state_path": "reports/xrpusd_state.json",
                        "event_path": "reports/xrpusd_events.jsonl",
                        "runtime_state": "not_launched_yet",
                        "validation_verdict": "pass",
                        "enabled": False,
                        "pause_note": "parked",
                    },
                ]
            },
            {
                "summary": {
                    "starter_candidate_symbol": "USDCAD",
                    "starter_next_symbol": "XRPUSD",
                    "current_max_honest_active_lanes": 0,
                },
                "rows": [
                    {"blocker_reason": "slot1 proof"},
                    {"blocker_reason": "slot2 still unresolved"},
                ],
            },
            {
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "scope": "shadow_candidate",
                        "runner_family": "tick_shadow",
                        "config_path": "configs/usdcad.json",
                        "verdict": "pass",
                        "enabled": False,
                    },
                    {
                        "symbol": "XRPUSD",
                        "scope": "shadow_candidate",
                        "runner_family": "tick_crypto_shadow",
                        "config_path": "configs/xrpusd.json",
                        "verdict": "pass",
                        "enabled": False,
                    },
                    {
                        "symbol": "AUDUSD",
                        "scope": "shadow_candidate",
                        "runner_family": "tick_shadow",
                        "config_path": "configs/audusd.json",
                        "verdict": "pass",
                        "enabled": False,
                    },
                ]
            },
        )

        self.assertEqual(payload["summary"]["launch_now_symbols"], ["USDCAD"])
        self.assertEqual(payload["summary"]["hold_symbols"], ["XRPUSD", "AUDUSD"])
        self.assertEqual(payload["summary"]["watch_only_symbols"], ["AUDUSD"])
        self.assertIn("Only `['USDCAD']` is launchable", payload["leadership_read"][1])
        self.assertIn("`['AUDUSD']`", payload["leadership_read"][2])
        self.assertEqual(payload["rows"][0]["launch_readiness"], "launch_now")
        self.assertEqual(payload["rows"][1]["launch_readiness"], "hold_until_prior_gate_clears")
        self.assertEqual(payload["rows"][2]["launch_readiness"], "watch_only_outside_current_unlock_ladder")
        self.assertEqual(payload["rows"][2]["packet_role"], "watch_only_outside_current_unlock_ladder")
        self.assertEqual(payload["rows"][1]["rollout_blocker"], "slot2 still unresolved")

    def test_build_payload_blocks_validation_fail_even_for_starter(self) -> None:
        payload = board.build_payload(
            {"rows": []},
            {
                "summary": {
                    "starter_candidate_symbol": "USDCAD",
                    "starter_next_symbol": "XRPUSD",
                    "current_max_honest_active_lanes": 0,
                },
                "rows": [
                    {"blocker_reason": "slot1 proof"},
                    {"blocker_reason": "slot2 unresolved"},
                ],
            },
            {
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "scope": "shadow_candidate",
                        "runner_family": "tick_shadow",
                        "config_path": "configs\\hungry_hippo_usdcad_m15_breakout_shadow.json",
                        "verdict": "fail",
                        "enabled": True,
                    }
                ]
            },
        )

        self.assertEqual(payload["summary"]["launch_now_symbols"], [])
        self.assertEqual(payload["summary"]["blocked_validation_symbols"], ["USDCAD"])
        self.assertEqual(payload["rows"][0]["launch_readiness"], "blocked_validation_fail")
        self.assertEqual(payload["rows"][0]["runtime_state"], "excluded_from_forward_watch")
        self.assertEqual(payload["rows"][0]["config_path"], "configs\\hungry_hippo_usdcad_m15_breakout_shadow.json")

    def test_build_payload_marks_started_starter_as_already_started(self) -> None:
        payload = board.build_payload(
            {
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "config_path": "configs/usdcad.json",
                        "watchdog_group": "fx_watchdog",
                        "runner_family": "tick_crypto_shadow",
                        "state_path": "reports/usdcad_state.json",
                        "event_path": "reports/usdcad_events.jsonl",
                        "runtime_state": "launched_waiting_first_close",
                        "validation_verdict": "pass",
                        "enabled": True,
                        "pause_note": "",
                        "current_open_count": 10,
                    }
                ]
            },
            {
                "summary": {
                    "starter_candidate_symbol": "USDCAD",
                    "starter_next_symbol": "XRPUSD",
                    "current_max_honest_active_lanes": 0,
                },
                "rows": [
                    {"blocker_reason": "slot1 proof"},
                    {"blocker_reason": "slot2 unresolved"},
                ],
            },
            {
                "rows": [
                    {
                        "symbol": "USDCAD",
                        "scope": "shadow_candidate",
                        "runner_family": "tick_crypto_shadow",
                        "config_path": "configs/usdcad.json",
                        "verdict": "pass",
                        "enabled": True,
                    }
                ]
            },
        )

        self.assertEqual(payload["summary"]["launch_now_symbols"], [])
        self.assertEqual(payload["rows"][0]["launch_readiness"], "already_started")
        self.assertIn("`['none']`", payload["leadership_read"][1])

    def test_render_markdown_mentions_packet(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T00:00:00+00:00",
                "leadership_read": ["a"],
                "summary": {
                    "launch_now_symbols": ["USDCAD"],
                    "hold_symbols": ["XRPUSD", "AUDUSD"],
                    "watch_only_symbols": ["AUDUSD"],
                    "blocked_validation_symbols": [],
                    "starter_candidate_symbol": "USDCAD",
                    "starter_next_symbol": "XRPUSD",
                    "current_max_honest_active_lanes": 0,
                },
                "rows": [
                    {
                        "launch_order": 1,
                        "symbol": "USDCAD",
                        "packet_role": "starter_candidate",
                        "launch_readiness": "launch_now",
                        "watchdog_group": "fx_watchdog",
                        "config_path": "configs/usdcad.json",
                        "state_path": "reports/usdcad_state.json",
                        "event_path": "reports/usdcad_events.jsonl",
                        "runtime_state": "not_launched_yet",
                        "validation_verdict": "pass",
                        "rollout_blocker": "proof",
                        "next_action": "start",
                    }
                ],
                "watch_steps": [],
                "no_go_rules": [],
            }
        )

        self.assertIn("Hungry Hippo First-Proof Launch Packet Board", markdown)
        self.assertIn("launch_now_symbols: `['USDCAD']`", markdown)
        self.assertIn("watch_only_symbols: `['AUDUSD']`", markdown)
        self.assertIn("1. USDCAD", markdown)


if __name__ == "__main__":
    unittest.main()
