#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_crypto_m15_warp_readiness as readiness


class CryptoM15WarpReadinessTests(unittest.TestCase):
    def test_eth_and_xrp_classification(self) -> None:
        eth_state = {
            "metadata": {"step": 5.0, "shared_price_max_age_ms": 1000},
            "runner": {"heartbeat_at": "2026-04-14T05:00:00+00:00", "started_at": "2026-04-14T03:00:00+00:00"},
            "symbols": {"ETHUSD": {"realized_closes": 29, "realized_net_usd": 557.78, "anchor_resets": 0, "open_tickets": [1] * 9, "max_open_total": 14, "anchor": 2244.82}},
        }
        xrp_state = {
            "metadata": {"step": 0.01},
            "runner": {"heartbeat_at": "2026-04-14T05:00:00+00:00", "started_at": "2026-04-14T04:00:00+00:00"},
            "symbols": {"XRPUSD": {"realized_closes": 1, "realized_net_usd": 14.16, "anchor_resets": 13, "open_tickets": [1, 2], "max_open_total": 3, "anchor": 1.3483}},
        }
        ltc_state = {
            "metadata": {"step": 0.15},
            "runner": {"heartbeat_at": "2026-04-14T05:00:00+00:00"},
            "symbols": {"LTCUSD": {"realized_closes": 0, "realized_net_usd": 0.0, "anchor_resets": 0, "open_tickets": [1, 2], "max_open_total": 4, "anchor": 54.91}},
        }

        def fake_load_json(path: Path) -> dict:
            name = path.name
            if name == "penetration_lattice_runner_registry.json":
                return {
                    "lanes": [
                        {"name": "shadow_ethusd_m15_warp"},
                        {"name": "shadow_xrpusd_m15_warp_v2"},
                        {"name": "live_ltcusd_m15_warp_941894"},
                    ]
                }
            if name == "watchdog_groups.json":
                return {
                    "groups": {
                        "feeder_crypto_m15_canary": {"lanes": ["shadow_ethusd_m15_warp", "shadow_xrpusd_m15_warp_v2"]},
                        "crypto_watchdog": {"lanes": ["live_ltcusd_m15_warp_941894"]},
                    }
                }
            if name == "penetration_lattice_shadow_ethusd_m15_warp_state.json":
                return eth_state
            if name == "penetration_lattice_shadow_xrpusd_m15_warp_state.json":
                return xrp_state
            if name == "penetration_lattice_live_ltcusd_m15_warp_state.json":
                return ltc_state
            if name == "penetration_lattice_shadow_ltcusd_m15_warp_state.json":
                return ltc_state
            return {}

        with patch.object(readiness, "load_json", side_effect=fake_load_json):
            eth_row = readiness.classify_candidate(readiness.CANDIDATES[0])
            xrp_row = readiness.classify_candidate(readiness.CANDIDATES[2])
            ltc_row = readiness.classify_candidate(readiness.CANDIDATES[3])

        self.assertEqual(eth_row["readiness"], "shadow_collecting")
        self.assertEqual(eth_row["progress_label"], "29/50 closes")
        self.assertEqual(xrp_row["readiness"], "unstable_resets")
        self.assertEqual(xrp_row["lane_name"], "shadow_xrpusd_m15_warp_v2")
        self.assertEqual(xrp_row["next_gate"], "retune_step_before_scale_claims")
        self.assertTrue(ltc_row["in_registry"])
        self.assertEqual(ltc_row["lane_name"], "live_ltcusd_m15_warp_941894")
        self.assertEqual(ltc_row["role"], "blind_live_probe")
        self.assertTrue(str(ltc_row["visibility"]).startswith("registry_watchdog"))

    def test_blind_live_rows_replace_stale_manual_only_current_read(self) -> None:
        blind_live_state = {
            "metadata": {"step": 0.15},
            "runner": {"heartbeat_at": "2026-04-17T19:00:00+00:00", "started_at": "2026-04-17T18:00:00+00:00"},
            "symbols": {"LTCUSD": {"realized_closes": 0, "realized_net_usd": 0.0, "anchor_resets": 0, "open_tickets": [], "max_open_total": 4, "anchor": 56.7}},
        }

        def fake_load_json(path: Path) -> dict:
            name = path.name
            if name == "penetration_lattice_runner_registry.json":
                return {"lanes": [{"name": "live_ltcusd_m15_warp_941894"}]}
            if name == "watchdog_groups.json":
                return {"groups": {"crypto_watchdog": {"lanes": ["live_ltcusd_m15_warp_941894"]}}}
            if name == "penetration_lattice_live_ltcusd_m15_warp_state.json":
                return blind_live_state
            return {}

        with patch.object(readiness, "load_json", side_effect=fake_load_json):
            payload = readiness.build_payload()

        current_read = payload["current_read"]
        self.assertTrue(any("registry-backed blind live probes" in line for line in current_read))
        self.assertFalse(any("LTCUSD" in line and "manual-only probes outside the registry/watchdog reporting path" in line for line in current_read))

    def test_prefers_tick_source_from_symbol_bucket(self) -> None:
        state = {
            "metadata": {"step": 5.0},
            "runner": {
                "heartbeat_at": "2026-04-14T05:00:00+00:00",
                "tick_history_source": "legacy_source",
                "latest_tick_source": "legacy_source",
                "tick_history_source_by_symbol": {
                    "ETHUSD": {"last": "shared_tick_cache", "counts": {"shared_tick_cache": 2}}
                },
                "latest_tick_source_by_symbol": {
                    "ETHUSD": {"last": "symbol_info_tick", "counts": {"symbol_info_tick": 4}}
                },
            },
            "symbols": {"ETHUSD": {"realized_closes": 2, "realized_net_usd": 80.0, "anchor_resets": 0, "open_tickets": [], "max_open_total": 8, "anchor": 2244.82}},
        }

        with patch.object(readiness, "load_json", return_value=state):
            row = readiness.classify_candidate(readiness.CANDIDATES[0])

        self.assertEqual(row["tick_history_source"], "shared_tick_cache")
        self.assertEqual(row["latest_tick_source"], "symbol_info_tick")
        self.assertEqual(row["latest_tick_append_source"], "")

    def test_prefers_v2_path_precedence_when_present(self) -> None:
        v1_state = {
            "runner": {"heartbeat_at": "2026-04-14T05:12:00+00:00"},
            "symbols": {"SOLUSD": {"realized_closes": 0, "anchor_resets": 0, "open_tickets": [1], "anchor": 84.99}},
            "metadata": {"step": 1.0},
        }
        v2_state = {
            "runner": {"heartbeat_at": "2026-04-14T05:09:17+00:00"},
            "symbols": {"SOLUSD": {"realized_closes": 0, "anchor_resets": 4, "open_tickets": [], "anchor": 85.81}},
            "metadata": {"step": 0.2},
        }

        def fake_load_json(path: Path) -> dict:
            name = path.name
            if name == "penetration_lattice_runner_registry.json":
                return {"lanes": [{"name": "shadow_solusd_m15_warp_v2"}]}
            if name == "watchdog_groups.json":
                return {"groups": {"feeder_crypto_m15_canary": {"lanes": ["shadow_solusd_m15_warp_v2"]}}}
            if name == "penetration_lattice_shadow_solusd_m15_warp_state.json":
                return v1_state
            if name == "penetration_lattice_shadow_solusd_m15_warp_v2_state.json":
                return v2_state
            return {}

        with patch.object(readiness, "load_json", side_effect=fake_load_json):
            row = readiness.classify_candidate(readiness.CANDIDATES[1])

        self.assertEqual(row["step"], 0.2)
        self.assertEqual(row["lane_name"], "shadow_solusd_m15_warp_v2")
        self.assertEqual(row["state_source"], "penetration_lattice_shadow_solusd_m15_warp_v2_state.json")
        self.assertIn("source=penetration_lattice_shadow_solusd_m15_warp_v2_state.json", row["visibility"])


if __name__ == "__main__":
    unittest.main()
