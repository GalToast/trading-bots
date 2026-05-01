#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_shared_price_feeder_status as feeder_status


class BuildSharedPriceFeederStatusTests(unittest.TestCase):
    def test_build_feeder_groups_reads_shared_history_flag_from_registry(self) -> None:
        registry = [
            {
                "name": "shadow_btcusd_h1_step30",
                "kind": "shadow_crypto_candidate",
                "state_path": "reports/one.json",
                "event_path": "reports/one.jsonl",
                "restart_args": [
                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                    "--shared-price-max-age-ms",
                    "1000",
                ],
            },
            {
                "name": "shadow_btcusd_h1_step50",
                "kind": "shadow_crypto_candidate",
                "state_path": "reports/two.json",
                "event_path": "reports/two.jsonl",
                "restart_args": [
                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                ],
            },
            {
                "name": "shadow_btcusd_m15_warp",
                "kind": "shadow_crypto",
                "state_path": "reports/three.json",
                "event_path": "reports/three.jsonl",
                "restart_args": [
                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                    "--shared-price-max-age-ms",
                    "1000",
                ],
            },
        ]
        groups = {
            "groups": {
                "feeder_crypto_canary": {"lanes": ["shadow_btcusd_h1_step30", "shadow_btcusd_h1_step50"]},
                "feeder_crypto_m15_canary": {"lanes": ["shadow_btcusd_m15_warp"]},
            }
        }
        feeder_groups = feeder_status.build_feeder_groups(registry, groups)
        self.assertEqual(len(feeder_groups), 2)
        self.assertEqual(feeder_groups[0]["group"], "feeder_crypto_canary")
        self.assertEqual(feeder_groups[1]["group"], "feeder_crypto_m15_canary")
        self.assertTrue(feeder_groups[0]["rows"][0]["shared_price_enabled"])
        self.assertEqual(feeder_groups[0]["rows"][0]["shared_price_max_age_ms"], 1000)
        self.assertFalse(feeder_groups[0]["rows"][1]["shared_price_enabled"])
        self.assertEqual(feeder_groups[0]["rows"][1]["shared_price_max_age_ms"], 0)
        self.assertTrue(feeder_groups[1]["rows"][0]["shared_price_enabled"])
        self.assertEqual(feeder_groups[0]["active_shared_rows"], 1)
        self.assertEqual(feeder_groups[0]["enabled_nonshared_rows"], 1)
        self.assertEqual(feeder_groups[0]["disabled_rows"], 0)
        self.assertTrue(feeder_groups[1]["all_active_shared_using_cache"] is False or feeder_groups[1]["all_active_shared_using_cache"] is True)

    def test_build_status_reports_ok_when_heartbeat_is_fresh_and_all_feeder_groups_enabled(self) -> None:
        now = datetime(2026, 4, 14, 2, 20, tzinfo=timezone.utc)
        original_load_json = feeder_status.load_json
        try:
            def fake_load_json(path: Path):
                if path == feeder_status.LAUNCHER_STATE_PATH:
                    return {
                        "status": "running",
                        "wrapper_pid": 999,
                        "child_pid": 123,
                        "launch_mode": "attached",
                        "launcher_started_at": (now - timedelta(seconds=3)).isoformat(),
                    }
                if path == feeder_status.PRICE_FEEDER_WATCHDOG_STATE_PATH:
                    return None
                if path == feeder_status.HEARTBEAT_PATH:
                    return {"heartbeat_at": (now - timedelta(seconds=1)).isoformat(), "cycle": 5}
                if path == feeder_status.PRICE_CACHE_PATH:
                    return {"BTCUSD": {"bid": 74000.0, "ask": 74001.0, "ts": (now - timedelta(milliseconds=250)).isoformat()}}
                if path == feeder_status.TICK_CACHE_PATH:
                    return {"BTCUSD": [{"time_msc": int((now - timedelta(milliseconds=250)).timestamp() * 1000)}]}
                if path == feeder_status.REGISTRY_PATH:
                    return {
                        "lanes": [
                            {
                                "name": "shadow_btcusd_h1_step30",
                                "kind": "shadow_crypto_candidate",
                                "state_path": "reports/one.json",
                                "event_path": "reports/one.jsonl",
                                "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py", "--shared-price-max-age-ms", "1000"],
                            },
                            {
                                "name": "shadow_btcusd_h1_step50",
                                "kind": "shadow_crypto_candidate",
                                "state_path": "reports/two.json",
                                "event_path": "reports/two.jsonl",
                                "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py", "--shared-price-max-age-ms", "1000"],
                            },
                            {
                                "name": "shadow_btcusd_m15_warp",
                                "kind": "shadow_crypto",
                                "state_path": "reports/three.json",
                                "event_path": "reports/three.jsonl",
                                "restart_args": ["scripts/live_penetration_lattice_tick_crypto_shadow.py", "--shared-price-max-age-ms", "1000"],
                            },
                        ]
                    }
                if path == feeder_status.WATCHDOG_GROUPS_PATH:
                    return {
                        "groups": {
                            "feeder_crypto_canary": {"lanes": ["shadow_btcusd_h1_step30", "shadow_btcusd_h1_step50"]},
                            "feeder_crypto_m15_canary": {"lanes": ["shadow_btcusd_m15_warp"]},
                        }
                    }
                if str(path).endswith("reports\\one.json") or str(path).endswith("reports/one.json"):
                    return {"runner": {"pid": 123, "tick_history_source_last": "shared_tick_cache", "latest_tick_source_last": "shared_price_cache"}}
                if str(path).endswith("reports\\two.json") or str(path).endswith("reports/two.json"):
                    return {"runner": {"pid": 456, "tick_history_source_last": "copy_ticks_range", "latest_tick_source_last": "symbol_info_tick"}}
                if str(path).endswith("reports\\three.json") or str(path).endswith("reports/three.json"):
                    return {"runner": {"pid": 789, "tick_history_source_last": "shared_tick_cache", "latest_tick_source_last": "shared_price_cache"}}
                return None
            feeder_status.load_json = fake_load_json  # type: ignore[assignment]
            payload = feeder_status.build_status(now=now)
        finally:
            feeder_status.load_json = original_load_json  # type: ignore[assignment]

        self.assertEqual(payload["status"], "degraded_fallback")
        self.assertFalse(payload["all_active_canaries_using_cache"])
        self.assertEqual(payload["feeder_group_count"], 2)
        self.assertEqual(payload["shared_runtime_group_count"], 2)
        self.assertEqual(payload["shared_enabled_lane_count"], 3)
        self.assertEqual(payload["active_feeder_shared_lane_count"], 3)
        self.assertEqual(payload["active_feeder_noncache_rows"], 1)
        self.assertAlmostEqual(payload["heartbeat_age_seconds"], 1.0, places=3)
        self.assertEqual(payload["tick_cache"]["total_ticks"], 1)
        self.assertEqual(payload["launcher"]["status"], "running")
        self.assertEqual(payload["launcher"]["wrapper_pid"], 999)
        self.assertEqual(payload["launcher"]["child_pid"], 123)
        self.assertAlmostEqual(payload["launcher"]["observed_age_seconds"], 3.0, places=3)
        self.assertEqual(payload["watchdog"]["status"], "")
        self.assertEqual(payload["supervisor"]["mode"], "wrapper")
        self.assertEqual(payload["supervisor"]["status"], "wrapper_running")
        self.assertEqual(payload["feeder_groups"][0]["rows"][0]["tick_history_source_last"], "shared_tick_cache")
        self.assertEqual(payload["feeder_groups"][0]["rows"][1]["latest_tick_source_last"], "symbol_info_tick")
        self.assertEqual(payload["feeder_groups"][1]["rows"][0]["latest_tick_source_last"], "shared_price_cache")
        self.assertEqual(payload["shared_runtime_mode_counts"]["shared_cache_active"], 2)
        self.assertEqual(payload["shared_runtime_mode_counts"]["direct_mt5_fallback"], 1)

    def test_build_shared_runtime_groups_includes_non_feeder_shared_lanes(self) -> None:
        registry = [
            {
                "name": "shadow_btcusd_h1_step30",
                "kind": "shadow_crypto_candidate",
                "state_path": "reports/one.json",
                "event_path": "reports/one.jsonl",
                "restart_args": [
                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                    "--shared-price-max-age-ms",
                    "1000",
                ],
            },
            {
                "name": "hungry_hippo_ethusd_m5_step14_control",
                "kind": "shadow_crypto",
                "state_path": "reports/two.json",
                "event_path": "reports/two.jsonl",
                "restart_args": [
                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                    "--shared-price-max-age-ms",
                    "1000",
                ],
            },
        ]
        groups = {
            "groups": {
                "feeder_crypto_canary": {"label": "Feeder Crypto Canary", "lanes": ["shadow_btcusd_h1_step30"]},
                "crypto_watchdog": {"label": "Crypto", "lanes": ["hungry_hippo_ethusd_m5_step14_control"]},
            }
        }
        original_read_canary_runtime = feeder_status.read_canary_runtime
        try:
            def fake_read_canary_runtime(path: str):
                if path.endswith("one.json"):
                    return {
                        "tick_history_source_last": "shared_tick_cache",
                        "latest_tick_source_last": "shared_price_cache",
                        "latest_tick_append_source_last": "symbol_info_tick",
                    }
                return {
                    "tick_history_source_last": "copy_ticks_range",
                    "latest_tick_source_last": "symbol_info_tick",
                    "latest_tick_append_source_last": "symbol_info_tick",
                }
            feeder_status.read_canary_runtime = fake_read_canary_runtime  # type: ignore[assignment]
            runtime_groups = feeder_status.build_shared_runtime_groups(registry, groups)
        finally:
            feeder_status.read_canary_runtime = original_read_canary_runtime  # type: ignore[assignment]

        self.assertEqual([group["group"] for group in runtime_groups], ["crypto_watchdog", "feeder_crypto_canary"])
        self.assertEqual(runtime_groups[0]["rows"][0]["lane"], "hungry_hippo_ethusd_m5_step14_control")
        self.assertEqual(runtime_groups[0]["rows"][0]["runtime_mode"], "direct_mt5_fallback")
        self.assertEqual(runtime_groups[1]["rows"][0]["runtime_mode"], "shared_cache_active")

    def test_build_status_reports_ok_for_healthy_non_canary_shared_runtime(self) -> None:
        now = datetime(2026, 4, 16, 2, 32, tzinfo=timezone.utc)
        original_load_json = feeder_status.load_json
        try:
            def fake_load_json(path: Path):
                if path == feeder_status.LAUNCHER_STATE_PATH:
                    return {
                        "status": "running",
                        "wrapper_pid": 32336,
                        "child_pid": 41304,
                        "launch_mode": "attached",
                        "launcher_started_at": (now - timedelta(seconds=90)).isoformat(),
                    }
                if path == feeder_status.PRICE_FEEDER_WATCHDOG_STATE_PATH:
                    return None
                if path == feeder_status.HEARTBEAT_PATH:
                    return {
                        "heartbeat_at": (now - timedelta(milliseconds=200)).isoformat(),
                        "feeder_pid": 41304,
                        "cycle": 3778,
                        "symbols_updated": 8,
                        "symbols_total": 8,
                    }
                if path == feeder_status.PRICE_CACHE_PATH:
                    stamp = (now - timedelta(milliseconds=250)).isoformat()
                    return {"ETHUSD": {"bid": 3200.0, "ask": 3200.5, "ts": stamp}}
                if path == feeder_status.TICK_CACHE_PATH:
                    return {"ETHUSD": [{"time_msc": int((now - timedelta(milliseconds=100)).timestamp() * 1000)}]}
                if path == feeder_status.REGISTRY_PATH:
                    return {
                        "lanes": [
                            {
                                "name": "shadow_ethusd_m5_structure_shapeshifter",
                                "kind": "shadow_crypto",
                                "enabled": True,
                                "state_path": "reports/shapeshifter.json",
                                "event_path": "reports/shapeshifter.jsonl",
                                "restart_args": [
                                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                                    "--shared-price-max-age-ms",
                                    "1000",
                                ],
                            }
                        ]
                    }
                if path == feeder_status.WATCHDOG_GROUPS_PATH:
                    return {
                        "groups": {
                            "feeder_crypto_canary": {"lanes": []},
                            "feeder_crypto_m15_canary": {"lanes": []},
                            "crypto_watchdog": {"lanes": ["shadow_ethusd_m5_structure_shapeshifter"]},
                        }
                    }
                if str(path).endswith("reports\\shapeshifter.json") or str(path).endswith("reports/shapeshifter.json"):
                    return {
                        "runner": {
                            "pid": 45108,
                            "tick_history_source_last": "shared_tick_cache",
                            "latest_tick_source_last": "shared_price_cache",
                            "latest_tick_append_source_last": "symbol_info_tick",
                        }
                    }
                return None
            feeder_status.load_json = fake_load_json  # type: ignore[assignment]
            payload = feeder_status.build_status(now=now)
        finally:
            feeder_status.load_json = original_load_json  # type: ignore[assignment]

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["active_feeder_shared_lane_count"], 0)
        self.assertEqual(payload["active_shared_runtime_lane_count"], 1)
        self.assertEqual(payload["shared_cache_active_lane_count"], 1)
        self.assertEqual(payload["direct_mt5_fallback_lane_count"], 0)
        self.assertEqual(payload["supervisor"]["mode"], "wrapper")

    def test_build_status_prefers_live_watchdog_when_wrapper_has_failed(self) -> None:
        now = datetime(2026, 4, 16, 2, 49, tzinfo=timezone.utc)
        original_load_json = feeder_status.load_json
        try:
            def fake_load_json(path: Path):
                if path == feeder_status.LAUNCHER_STATE_PATH:
                    return {
                        "status": "child_exited_unexpected",
                        "wrapper_pid": 16220,
                        "child_pid": 2716,
                        "launch_mode": "spawned",
                        "launcher_finished_at": (now - timedelta(minutes=4)).isoformat(),
                        "auto_restart_reason": "restart_limit_reached",
                    }
                if path == feeder_status.PRICE_FEEDER_WATCHDOG_STATE_PATH:
                    return {
                        "watchdog_status": "ok",
                        "feeder_pid": 38044,
                        "feeder_alive": True,
                        "last_restart": (now - timedelta(minutes=4)).isoformat(),
                        "consecutive_failures": 1,
                        "watchdog_updated_at": (now - timedelta(seconds=8)).isoformat(),
                    }
                if path == feeder_status.HEARTBEAT_PATH:
                    return {
                        "heartbeat_at": (now - timedelta(milliseconds=200)).isoformat(),
                        "feeder_pid": 38044,
                        "cycle": 905,
                        "symbols_updated": 8,
                        "symbols_total": 8,
                    }
                if path == feeder_status.PRICE_CACHE_PATH:
                    stamp = (now - timedelta(milliseconds=150)).isoformat()
                    return {"ETHUSD": {"bid": 3200.0, "ask": 3200.5, "ts": stamp}}
                if path == feeder_status.TICK_CACHE_PATH:
                    return {"ETHUSD": [{"time_msc": int((now - timedelta(milliseconds=50)).timestamp() * 1000)}]}
                if path == feeder_status.REGISTRY_PATH:
                    return {
                        "lanes": [
                            {
                                "name": "shadow_ethusd_m5_structure_shapeshifter",
                                "kind": "shadow_crypto",
                                "enabled": True,
                                "state_path": "reports/shapeshifter.json",
                                "event_path": "reports/shapeshifter.jsonl",
                                "restart_args": [
                                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                                    "--shared-price-max-age-ms",
                                    "1000",
                                ],
                            }
                        ]
                    }
                if path == feeder_status.WATCHDOG_GROUPS_PATH:
                    return {"groups": {"crypto_watchdog": {"lanes": ["shadow_ethusd_m5_structure_shapeshifter"]}}}
                if str(path).endswith("reports\\shapeshifter.json") or str(path).endswith("reports/shapeshifter.json"):
                    return {
                        "runner": {
                            "pid": 45108,
                            "tick_history_source_last": "shared_tick_cache",
                            "latest_tick_source_last": "shared_price_cache",
                            "latest_tick_append_source_last": "symbol_info_tick",
                        }
                    }
                return None
            feeder_status.load_json = fake_load_json  # type: ignore[assignment]
            payload = feeder_status.build_status(now=now)
        finally:
            feeder_status.load_json = original_load_json  # type: ignore[assignment]

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["launcher"]["status"], "child_exited_unexpected")
        self.assertEqual(payload["watchdog"]["status"], "ok")
        self.assertEqual(payload["supervisor"]["mode"], "price_feeder_watchdog")
        self.assertEqual(payload["supervisor"]["status"], "watchdog_ok_wrapper_failed")

    def test_render_markdown_includes_operator_commands(self) -> None:
        text = feeder_status.render_markdown(
            {
                "generated_at": "2026-04-15T18:45:00+00:00",
                "status": "ok",
                "heartbeat_age_seconds": 0.5,
                "supervisor": {
                    "mode": "price_feeder_watchdog",
                    "status": "watchdog_ok_wrapper_failed",
                    "observed_age_seconds": 2.0,
                },
                "launcher": {
                    "present": True,
                    "status": "running",
                    "observed_age_seconds": 1.5,
                },
                "watchdog": {
                    "present": True,
                    "status": "ok",
                    "observed_age_seconds": 2.0,
                },
                "price_cache": {"symbols": 8, "fresh_symbols": 4},
                "tick_cache": {"symbols": 8, "symbols_with_recent_ticks": 4, "total_ticks": 64},
                "feeder_group_count": 2,
                "all_active_canaries_using_cache": True,
                "active_feeder_shared_lane_count": 2,
                "active_feeder_noncache_rows": 0,
                "feeder_enabled_nonshared_rows": 0,
                "feeder_disabled_rows": 0,
                "shared_runtime_group_count": 2,
                "shared_enabled_lane_count": 2,
                "active_shared_runtime_lane_count": 2,
                "shared_runtime_mode_counts": {"shared_cache_active": 1, "direct_mt5_fallback": 1},
                "feeder_groups": [
                    {
                        "group": "feeder_crypto_canary",
                        "rows": [
                            {
                                "lane": "shadow_btcusd_h1_step30",
                                "shared_price_enabled": True,
                                "shared_price_max_age_ms": 1000,
                                "state_path": "reports/one.json",
                            }
                        ],
                    },
                    {
                        "group": "feeder_crypto_m15_canary",
                        "rows": [
                            {
                                "lane": "shadow_btcusd_m15_warp",
                                "shared_price_enabled": True,
                                "shared_price_max_age_ms": 1000,
                                "state_path": "reports/two.json",
                            }
                        ],
                    },
                ],
                "shared_runtime_groups": [
                    {
                        "group": "crypto_watchdog",
                        "rows": [
                            {
                                "lane": "hungry_hippo_ethusd_m5_step14_control",
                                "shared_price_enabled": True,
                                "shared_price_max_age_ms": 1000,
                                "runtime_mode": "direct_mt5_fallback",
                                "tick_history_source_last": "copy_ticks_range",
                                "latest_tick_source_last": "symbol_info_tick",
                                "latest_tick_append_source_last": "symbol_info_tick",
                            }
                        ],
                    },
                    {
                        "group": "feeder_crypto_canary",
                        "rows": [
                            {
                                "lane": "shadow_btcusd_h1_step30",
                                "shared_price_enabled": True,
                                "shared_price_max_age_ms": 1000,
                                "runtime_mode": "shared_cache_active",
                                "tick_history_source_last": "shared_tick_cache",
                                "latest_tick_source_last": "shared_price_cache",
                                "latest_tick_append_source_last": "symbol_info_tick",
                                "state_path": "reports/one.json",
                            }
                        ],
                    },
                ],
            }
        )
        self.assertIn("scripts/operators/start_shared_price_feeder.ps1", text)
        self.assertIn("supervisor_mode", text)
        self.assertIn("price_feeder_watchdog_state.json", text)
        self.assertIn("launcher_status", text)
        self.assertIn("shared_price_feeder_launcher_state.json", text)
        self.assertIn("feeder_crypto_canary", text)
        self.assertIn("crypto_watchdog", text)
        self.assertIn("Runtime Mode", text)
        self.assertIn("active_shared_runtime_lanes", text)
        self.assertIn("direct_mt5_fallback", text)
        self.assertIn("tick_history_fallback", text)


if __name__ == "__main__":
    unittest.main()
