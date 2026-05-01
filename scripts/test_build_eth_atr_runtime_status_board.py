#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_eth_atr_runtime_status_board as board


class BuildEthAtrRuntimeStatusBoardTests(unittest.TestCase):
    def test_build_payload_marks_active_pack_as_shadow_only(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = {
                "lanes": [
                    {
                        "name": "shadow_ethusd_m5_atr_optimized",
                        "enabled": True,
                        "watchdog_group": "shadow_watchdog",
                        "state_path": "reports/m5.json",
                        "event_path": "reports/m5.jsonl",
                    },
                    {
                        "name": "shadow_ethusd_m15_atr_optimized",
                        "enabled": True,
                        "watchdog_group": "shadow_watchdog",
                        "state_path": "reports/m15.json",
                        "event_path": "reports/m15.jsonl",
                    },
                    {
                        "name": "shadow_ethusd_m15_asymmetric",
                        "enabled": True,
                        "watchdog_group": "shadow_watchdog",
                        "state_path": "reports/m15_asym.json",
                        "event_path": "reports/m15_asym.jsonl",
                    },
                    {
                        "name": "live_ethusd_m15_warp_graduation_941782",
                        "enabled": False,
                        "kind": "live_crypto",
                        "watchdog_group": "crypto_watchdog",
                        "pause_note": "toxic_sub_atr_0.58x_m15_replaced_by_atr_optimized_lanes_20260415",
                    },
                ]
            }
            execution = {
                "rows": [
                    {"lane": "shadow_ethusd_m5_atr_optimized", "watchdog_status": "ok", "open_count": 1, "close_count": 0, "heartbeat_at": "2026-04-15T23:39:30+00:00"},
                    {"lane": "shadow_ethusd_m15_atr_optimized", "watchdog_status": "ok", "open_count": 1, "close_count": 0, "heartbeat_at": "2026-04-15T23:39:30+00:00"},
                    {"lane": "shadow_ethusd_m15_asymmetric", "watchdog_status": "ok", "open_count": 1, "close_count": 0, "heartbeat_at": "2026-04-15T23:39:30+00:00"},
                ]
            }
            state_payload = {
                "metadata": {
                    "step": 5.0,
                    "timeframe": "M5",
                    "raw_close_alpha": 1.0,
                    "direct_live": False,
                    "mt5_connection": {"identity_ok": True, "reason": "ok"},
                },
                "runner": {
                    "pid": 123,
                    "heartbeat_at": "2026-04-15T23:39:30+00:00",
                    "started_at": "2026-04-15T22:54:22+00:00",
                },
                "symbols": {
                    "ETHUSD": {
                        "symbol": "ETHUSD",
                        "open_tickets": [{"live_ticket": 0}],
                        "realized_closes": 0,
                        "realized_net_usd": 0.0,
                        "anchor_resets": 0,
                    }
                },
                "updated_at": "2026-04-15T23:39:30+00:00",
            }

            (root / "configs").mkdir(parents=True, exist_ok=True)
            (root / "reports").mkdir(parents=True, exist_ok=True)
            (root / "configs" / "penetration_lattice_runner_registry.json").write_text(
                json.dumps(registry),
                encoding="utf-8",
            )
            (root / "reports" / "execution_monitor_report.json").write_text(
                json.dumps(execution),
                encoding="utf-8",
            )
            for name in ("m5.json", "m15.json", "m15_asym.json"):
                (root / "reports" / name).write_text(json.dumps(state_payload), encoding="utf-8")

            with (
                patch.object(board, "ROOT", root),
                patch.object(board, "REPORTS", root / "reports"),
                patch.object(board, "CONFIGS", root / "configs"),
                patch.object(board, "EXECUTION_MONITOR_JSON", root / "reports" / "execution_monitor_report.json"),
                patch.object(board, "RUNNER_REGISTRY_JSON", root / "configs" / "penetration_lattice_runner_registry.json"),
                patch.object(board, "utc_now_iso", return_value="2026-04-15T23:45:00+00:00"),
            ):
                payload = board.build_payload()
                markdown = board.build_markdown(payload)

        self.assertEqual(payload["summary"]["active_shadow_lane_count"], 3)
        self.assertEqual(payload["summary"]["mt5_visible_lane_count"], 0)
        self.assertEqual(payload["summary"]["total_open_shadow_positions"], 3)
        self.assertTrue(all(row["shadow_only"] for row in payload["active_rows"]))
        self.assertIn("not_expected_in_mt5_shadow_only", markdown)
        self.assertIn("> Current runtime generated board.", markdown)
        self.assertIn("It is not the current authority surface for the active optimized ETH shadow pack.", markdown)


if __name__ == "__main__":
    unittest.main()
