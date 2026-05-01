#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import watch_penetration_lattice_runners as watchdog


class WatchdogCoinbaseForwardReviewTests(unittest.TestCase):
    def test_forward_review_reason_surfaces_for_coinbase_rsi_lane(self) -> None:
        lane = {
            "name": "shadow_coinbase_prlusd_rsi7",
            "kind": "shadow_coinbase_spot",
            "state_path": "reports/coinbase_rsi_shadow_prlusd_state.json",
            "event_path": "reports/coinbase_rsi_shadow_prlusd_events.jsonl",
            "process_match_substrings": [
                "scripts/live_coinbase_rsi_shadow.py",
                "reports/coinbase_rsi_shadow_prlusd_state.json",
            ],
        }
        processes = [
            {
                "pid": 76136,
                "command_line": "python.exe scripts/live_coinbase_rsi_shadow.py --product-id PRL-USD --state-path reports/coinbase_rsi_shadow_prlusd_state.json --event-path reports/coinbase_rsi_shadow_prlusd_events.jsonl",
            }
        ]
        forward_rows = {
            "shadow_coinbase_prlusd_rsi7": {
                "lane_name": "shadow_coinbase_prlusd_rsi7",
                "forward_status": "lagging_in_position",
                "realized_net_usd": "-0.3157",
                "realized_closes": "5",
            }
        }
        with (
            patch.object(watchdog, "load_json", return_value={"runner": {}}),
            patch.object(watchdog, "heartbeat_from_state", return_value=("2026-04-11T18:15:00+00:00", 10.0, "state.updated_at")),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, forward_rows)
        self.assertEqual(row["status"], "ok")
        self.assertIn("forward=lagging_in_position realized=-0.32 closes=5", row["reasons"])

    def test_forward_review_reason_surfaces_for_burst_lane(self) -> None:
        lane = {
            "name": "shadow_coinbase_burst_roundrobin_best",
            "kind": "shadow_coinbase_spot",
            "state_path": "reports/shadow_coinbase_burst_roundrobin_best_state.json",
            "event_path": "reports/shadow_coinbase_burst_roundrobin_best_events.jsonl",
            "process_match_substrings": [
                "scripts/burst_fade_roundrobin_shadow.py",
                "reports/shadow_coinbase_burst_roundrobin_best_state.json",
            ],
        }
        processes = [
            {
                "pid": 77432,
                "command_line": "python.exe scripts/burst_fade_roundrobin_shadow.py --state-path reports/shadow_coinbase_burst_roundrobin_best_state.json --event-path reports/shadow_coinbase_burst_roundrobin_best_events.jsonl",
            }
        ]
        forward_rows = {
            "shadow_coinbase_burst_roundrobin_best": {
                "lane_name": "shadow_coinbase_burst_roundrobin_best",
                "forward_status": "seeded_positive",
                "realized_net_usd": "673.4468",
                "closes": "186",
            }
        }
        with (
            patch.object(watchdog, "load_json", return_value={"runner": {}}),
            patch.object(watchdog, "heartbeat_from_state", return_value=("2026-04-11T18:45:00+00:00", 10.0, "state.updated_at")),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, forward_rows)
        self.assertEqual(row["status"], "ok")
        self.assertIn("forward=seeded_positive closes=186", row["reasons"])

    def test_forward_review_reason_surfaces_for_shadow_crypto_candidate(self) -> None:
        lane = {
            "name": "shadow_btcusd_h1_step30",
            "kind": "shadow_crypto_candidate",
            "state_path": "reports/penetration_lattice_shadow_btcusd_h1_step30_state.json",
            "event_path": "reports/penetration_lattice_shadow_btcusd_h1_step30_events.jsonl",
            "process_match_substrings": [
                "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                "reports/penetration_lattice_shadow_btcusd_h1_step30_state.json",
            ],
        }
        processes = [
            {
                "pid": 80321,
                "command_line": "python.exe scripts/live_penetration_lattice_tick_crypto_shadow.py --symbol BTCUSD --timeframe H1 --state-path reports/penetration_lattice_shadow_btcusd_h1_step30_state.json --event-path reports/penetration_lattice_shadow_btcusd_h1_step30_events.jsonl",
            }
        ]
        forward_rows = {
            "shadow_btcusd_h1_step30": {
                "lane_name": "shadow_btcusd_h1_step30",
                "forward_status": "holding_up_in_position",
                "realized_net_usd": "12.5000",
                "closes": "7",
            }
        }
        with (
            patch.object(watchdog, "load_json", return_value={"runner": {}}),
            patch.object(watchdog, "heartbeat_from_state", return_value=("2026-04-13T04:00:00+00:00", 10.0, "state.updated_at")),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, forward_rows)
        self.assertEqual(row["status"], "ok")
        self.assertIn("forward=holding_up_in_position realized=+12.50 closes=7", row["reasons"])

    def test_ratio_proof_readiness_reason_surfaces_for_cfg_sleeve(self) -> None:
        lane = {
            "name": "shadow_coinbase_cfgbtc_ratio_sleeve",
            "kind": "shadow_coinbase_spot",
            "state_path": "reports/cfg_btc_synthetic_sleeve_shadow_state.json",
            "event_path": "reports/cfg_btc_synthetic_sleeve_shadow_events.jsonl",
            "process_match_substrings": [
                "scripts/cfg_eth_ratio_lattice_shadow.py",
                "reports/cfg_btc_synthetic_sleeve_shadow_state.json",
            ],
        }
        processes = [
            {
                "pid": 64664,
                "command_line": "python.exe scripts/cfg_eth_ratio_lattice_shadow.py --pair CFG/BTC --state-path reports/cfg_btc_synthetic_sleeve_shadow_state.json",
            }
        ]
        forward_rows = {
            "shadow_coinbase_cfgbtc_ratio_sleeve": {
                "lane_name": "shadow_coinbase_cfgbtc_ratio_sleeve",
                "forward_status": "seeded_in_position",
                "realized_net_usd": "0.0000",
                "realized_closes": "0",
            }
        }
        proof_rows = {
            "shadow_coinbase_cfgbtc_ratio_sleeve": {
                "lane_name": "shadow_coinbase_cfgbtc_ratio_sleeve",
                "role": "scale_up",
                "current_gate": "waiting_first_close",
                "deployment_posture": "shadow_only_scale_up",
            }
        }
        with (
            patch.object(watchdog, "load_json", return_value={"runner": {}}),
            patch.object(watchdog, "heartbeat_from_state", return_value=("2026-04-13T17:40:00+00:00", 10.0, "state.updated_at")),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, forward_rows, proof_rows)
        self.assertEqual(row["status"], "ok")
        self.assertIn("proof_role=scale_up gate=waiting_first_close posture=shadow_only_scale_up", row["reasons"])

    def test_fx_graduation_reason_surfaces_for_gbp_shadow_lane(self) -> None:
        lane = {
            "name": "shadow_gbpusd_tick_forward",
            "kind": "shadow_fx",
            "state_path": "reports/shadow_gbpusd_tick_forward_state.json",
            "event_path": "reports/shadow_gbpusd_tick_forward_events.jsonl",
            "process_match_substrings": [
                "scripts/shadow_gbpusd_tick_forward.py",
                "reports/shadow_gbpusd_tick_forward_state.json",
            ],
        }
        processes = [
            {
                "pid": 61888,
                "command_line": "python.exe scripts/shadow_gbpusd_tick_forward.py --state-path reports/shadow_gbpusd_tick_forward_state.json --event-path reports/shadow_gbpusd_tick_forward_events.jsonl",
            }
        ]
        fx_rows = {
            "shadow_gbpusd_tick_forward": {
                "lane_name": "shadow_gbpusd_tick_forward",
                "readiness": "shadow_proof_positive",
                "progress_label": "3/20 durable closes",
                "progress_pct": "15.0%",
                "next_gate": "accumulate_20_plus_clean_closes",
            }
        }
        with (
            patch.object(watchdog, "load_json", return_value={"runner": {}}),
            patch.object(watchdog, "heartbeat_from_state", return_value=("2026-04-13T19:30:00+00:00", 10.0, "state.updated_at")),
            patch.object(watchdog, "event_tail_exception", return_value=None),
        ):
            row = watchdog.summarize_lane(lane, processes, {}, {}, {}, fx_rows)
        self.assertEqual(row["status"], "ok")
        self.assertIn(
            "fx_grad=shadow_proof_positive progress=3/20 durable closes(15.0%) next=accumulate_20_plus_clean_closes",
            row["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
