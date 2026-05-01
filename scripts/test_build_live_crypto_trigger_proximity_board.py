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

import build_live_crypto_trigger_proximity_board as proximity


class LiveCryptoTriggerProximityBoardTests(unittest.TestCase):
    def test_build_payload_sorts_by_nearest_gap_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()

            dashboard = {
                "rows": [
                    {
                        "lane": "live_ethusd_m5_warp_5_941890",
                        "status": "ok",
                        "evidence_basis": "thin_live_sample",
                        "operator_posture": "wait_more_sample",
                    },
                    {
                        "lane": "live_solusd_m15_warp_v2_941891",
                        "status": "ok",
                        "evidence_basis": "thin_live_sample",
                        "operator_posture": "wait_more_sample",
                    },
                ]
            }
            (reports / "live_lane_dashboard.json").write_text(json.dumps(dashboard), encoding="utf-8")

            eth_state = {
                "symbols": {
                    "ETHUSD": {
                        "next_buy_level": 100.0,
                        "next_sell_level": 112.0,
                        "base_step_px": 5.0,
                        "max_entry_spread_ratio": 2.0,
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "anchor_resets": 1,
                        "open_tickets": [],
                    }
                }
            }
            sol_state = {
                "symbols": {
                    "SOLUSD": {
                        "next_buy_level": 49.5,
                        "next_sell_level": 50.5,
                        "base_step_px": 0.5,
                        "max_entry_spread_ratio": 1.0,
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "anchor_resets": 2,
                        "open_tickets": [],
                    }
                }
            }
            (reports / "eth_state.json").write_text(json.dumps(eth_state), encoding="utf-8")
            (reports / "sol_state.json").write_text(json.dumps(sol_state), encoding="utf-8")

            (reports / "eth_events.jsonl").write_text(
                json.dumps({"action": "tick_history_fallback", "bid": 108.0, "ask": 109.0, "ts_utc": "2026-04-17T20:00:00+00:00"}) + "\n",
                encoding="utf-8",
            )
            (reports / "sol_events.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"action": "bootstrap_complete"}),
                        json.dumps({"action": "tick_history_fallback", "bid": 50.1, "ask": 50.2, "ts_utc": "2026-04-17T20:00:01+00:00"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            probes = [
                proximity.CryptoProbeContract(
                    lane="live_ethusd_m5_warp_5_941890",
                    symbol="ETHUSD",
                    state_path=reports / "eth_state.json",
                    event_path=reports / "eth_events.jsonl",
                ),
                proximity.CryptoProbeContract(
                    lane="live_solusd_m15_warp_v2_941891",
                    symbol="SOLUSD",
                    state_path=reports / "sol_state.json",
                    event_path=reports / "sol_events.jsonl",
                ),
            ]

            with patch.object(proximity, "REPORTS", reports), patch.object(
                proximity, "LIVE_LANE_DASHBOARD_JSON", reports / "live_lane_dashboard.json"
            ), patch.object(proximity, "CRYPTO_PROBES", probes):
                payload = proximity.build_payload()

        self.assertEqual(payload["watch_order_by_steps"], ["SOLUSD", "ETHUSD"])
        self.assertEqual(payload["summary"]["nearest_symbol"], "SOLUSD")
        self.assertEqual(payload["summary"]["waiting_for_first_fill_count"], 2)
        self.assertEqual(payload["rows"][0]["distance_status"], "within_one_and_half_steps")
        self.assertEqual(payload["rows"][0]["spread_gate_status"], "admissible_now")
        self.assertEqual(payload["rows"][0]["execution_read"], "waiting_for_first_fill")

    def test_crossed_quote_status_is_detected(self) -> None:
        row = proximity.classify_distance_status(buy_gap_steps=-0.1, sell_gap_steps=1.5)
        self.assertEqual(row, ("buy_crossed_now", "BUY"))
        row = proximity.classify_distance_status(buy_gap_steps=1.0, sell_gap_steps=-0.2)
        self.assertEqual(row, ("sell_crossed_now", "SELL"))

    def test_execution_read_distinguishes_pre_first_fill_states(self) -> None:
        self.assertEqual(
            proximity.classify_execution_read(
                close_count=0,
                open_count=0,
                spread_gate_status="admissible_now",
                distance_status="within_three_quarters_step",
            ),
            "waiting_for_first_fill",
        )
        self.assertEqual(
            proximity.classify_execution_read(
                close_count=0,
                open_count=0,
                spread_gate_status="blocked_now",
                distance_status="within_one_and_half_steps",
            ),
            "spread_blocked_before_first_fill",
        )
        self.assertEqual(
            proximity.classify_execution_read(
                close_count=0,
                open_count=0,
                spread_gate_status="admissible_now",
                distance_status="sell_crossed_now",
            ),
            "crossed_waiting_first_fill",
        )

    def test_gap_only_guard_does_not_render_as_hard_block(self) -> None:
        self.assertEqual(
            proximity.classify_spread_gate_status(
                spread_ratio=0.95,
                max_entry_spread_ratio=0.0,
                liquidity_gap_spread_multiplier=2.5,
                liquidity_gap_spread_floor_ratio=1.0,
            ),
            "admissible_now",
        )
        self.assertEqual(
            proximity.classify_spread_gate_status(
                spread_ratio=1.3,
                max_entry_spread_ratio=0.0,
                liquidity_gap_spread_multiplier=2.5,
                liquidity_gap_spread_floor_ratio=1.0,
            ),
            "adaptive_guard_active",
        )
        self.assertEqual(
            proximity.classify_execution_read(
                close_count=0,
                open_count=0,
                spread_gate_status="adaptive_guard_active",
                distance_status="within_one_and_half_steps",
            ),
            "waiting_for_first_fill",
        )

    def test_parked_live_crypto_lane_is_excluded_from_probe_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()

            dashboard = {
                "rows": [
                    {
                        "lane": "live_ethusd_m5_warp_5_941890",
                        "status": "paused",
                        "evidence_basis": "decommissioned_or_parked",
                        "operator_posture": "leave_paused",
                    },
                    {
                        "lane": "live_solusd_m15_warp_v2_941891",
                        "status": "ok",
                        "evidence_basis": "thin_live_sample",
                        "operator_posture": "wait_more_sample",
                    },
                ]
            }
            (reports / "live_lane_dashboard.json").write_text(json.dumps(dashboard), encoding="utf-8")

            eth_state = {
                "symbols": {
                    "ETHUSD": {
                        "next_buy_level": 100.0,
                        "next_sell_level": 112.0,
                        "base_step_px": 5.0,
                        "max_entry_spread_ratio": 2.0,
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "anchor_resets": 1,
                        "open_tickets": [],
                    }
                }
            }
            sol_state = {
                "symbols": {
                    "SOLUSD": {
                        "next_buy_level": 49.5,
                        "next_sell_level": 50.5,
                        "base_step_px": 0.5,
                        "max_entry_spread_ratio": 1.0,
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "anchor_resets": 2,
                        "open_tickets": [],
                    }
                }
            }
            (reports / "eth_state.json").write_text(json.dumps(eth_state), encoding="utf-8")
            (reports / "sol_state.json").write_text(json.dumps(sol_state), encoding="utf-8")
            (reports / "eth_events.jsonl").write_text(
                json.dumps({"action": "tick_history_fallback", "bid": 108.0, "ask": 109.0, "ts_utc": "2026-04-17T20:00:00+00:00"}) + "\n",
                encoding="utf-8",
            )
            (reports / "sol_events.jsonl").write_text(
                json.dumps({"action": "tick_history_fallback", "bid": 50.1, "ask": 50.2, "ts_utc": "2026-04-17T20:00:01+00:00"}) + "\n",
                encoding="utf-8",
            )

            probes = [
                proximity.CryptoProbeContract(
                    lane="live_ethusd_m5_warp_5_941890",
                    symbol="ETHUSD",
                    state_path=reports / "eth_state.json",
                    event_path=reports / "eth_events.jsonl",
                ),
                proximity.CryptoProbeContract(
                    lane="live_solusd_m15_warp_v2_941891",
                    symbol="SOLUSD",
                    state_path=reports / "sol_state.json",
                    event_path=reports / "sol_events.jsonl",
                ),
            ]

            with patch.object(proximity, "REPORTS", reports), patch.object(
                proximity, "LIVE_LANE_DASHBOARD_JSON", reports / "live_lane_dashboard.json"
            ), patch.object(proximity, "CRYPTO_PROBES", probes):
                payload = proximity.build_payload()

        self.assertEqual(payload["watch_order_by_steps"], ["SOLUSD"])
        self.assertEqual(payload["summary"]["probe_count"], 1)
        self.assertEqual([row["symbol"] for row in payload["rows"]], ["SOLUSD"])


if __name__ == "__main__":
    unittest.main()
