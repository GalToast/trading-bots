#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_spot_microstructure_lab_dashboard import alignment_match_rate, render_md, runner_health, summarize_recent_events


class BuildSpotMicrostructureLabDashboardTests(unittest.TestCase):
    def test_runner_health_ok_and_stale(self) -> None:
        fresh = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
        stale = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
        self.assertEqual(runner_health({"heartbeat_at": fresh}, stale_after_seconds=10)["status"], "ok")
        self.assertEqual(runner_health({"heartbeat_at": stale}, stale_after_seconds=10)["status"], "stale")

    def test_summarize_recent_events_counts_actions(self) -> None:
        rows = [
            {"action": "a"},
            {"action": "b"},
            {"action": "a"},
            {"action": ""},
        ]
        self.assertEqual(summarize_recent_events(rows), {"a": 2, "b": 1})

    def test_alignment_match_rate_returns_na_for_missing_action(self) -> None:
        self.assertEqual(alignment_match_rate({"by_action": {}}, "fake_floor_pull_detected"), "n/a")

    def test_render_md_separates_session_and_analysis_samples(self) -> None:
        dashboard = {
            "generated_at": "2026-04-12T00:00:00+00:00",
            "capture_lane": {
                "health": {"status": "ok", "heartbeat_age_seconds": 1.0},
                "capture": {"session_sample_count": 12},
                "analysis": {
                    "sample_count": 44,
                    "avg_interval_seconds": 1.0,
                    "avg_diff_usd": 2.0,
                    "significant_kraken_moves": 3,
                    "best_follow_window_samples": 2,
                    "best_follow_hit_rate_pct": 50.0,
                },
            },
            "signal_logger_lane": {
                "health": {"status": "ok", "heartbeat_age_seconds": 2.0},
                "event_counts": {"iceberg_sell_reload_detected": 1},
                "recent_event_mix": {"iceberg_sell_reload_detected": 1},
                "kraken_state": {"last_move_usd": 0.0},
                "alignment": {
                    "aligned_event_rows": 1,
                    "signal_event_rows": 2,
                    "by_action": {
                        "iceberg_buy_reload_detected": {"match_rate_pct": 100.0},
                        "iceberg_sell_reload_detected": {"match_rate_pct": 0.0},
                        "fake_floor_pull_detected": {"match_rate_pct": 0.0},
                    },
                },
            },
            "baseline_anchor": {
                "name": "shadow_coinbase_raveusd_rsi7",
                "health": {"status": "ok"},
                "state": {"realized_net_usd": 1.0, "realized_closes": 2, "signals_generated": 3},
                "execution_truth": {"provenance": "startup_backfill_only", "forward_event_count": 0, "total_events": 35},
            },
            "strict_warp_research": {
                "health": {"status": "not_started"},
                "state": {"realized_net": 0.0, "realized_closes": 0},
            },
            "benchmark_truth": {"omni_v4_salvage_best": {"gate": "baseline", "realized_net_usd": 4.0, "realized_closes": 5}},
        }
        md = render_md(dashboard)
        self.assertIn("session samples `12`; analyzed rows `44`", md)
        self.assertIn("Execution truth provenance: `startup_backfill_only`", md)


if __name__ == "__main__":
    unittest.main()
