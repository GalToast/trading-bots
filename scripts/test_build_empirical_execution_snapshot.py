#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest.mock import patch

import build_empirical_execution_snapshot as mod


class BuildEmpiricalExecutionSnapshotTests(unittest.TestCase):
    @patch("build_empirical_execution_snapshot.load_json")
    def test_build_snapshot_derives_live_fill_probability(self, load_json_mock) -> None:
        load_json_mock.side_effect = [
            {
                "updated_at": "2026-04-12T00:00:00+00:00",
                "state": {
                    "closes": 9,
                    "position": {"ep": 1.0},
                    "rsi_signals": 10,
                    "realized_net": 12.5,
                    "win_rate": 60.0,
                    "total_volume": 123.0,
                    "total_fees": 1.0,
                },
            },
            {"updated_at": "2026-04-12T00:01:00+00:00", "state": {"realized_closes": 4, "realized_net_usd": 2.0, "signals_generated": 30}},
            {"analysis": {"aligned_event_rows": 7, "follow_seconds": 8.0, "by_product_action": {
                "RAVE-USD::iceberg_sell_reload_detected": {"count": 3, "match_rate_pct": 66.67, "avg_delta_bps": -10.5},
                "RAVE-USD::iceberg_buy_reload_detected": {"count": 2, "match_rate_pct": 50.0, "avg_delta_bps": 5.0},
            }}},
            {"capture_lane": {"analysis": {"sample_count": 100, "significant_kraken_moves": 8}}},
            {"round_trip_cost": {"entry_slippage_bps": -8.0, "exit_slippage_bps": 0.0}},
            {"execution_truth": {"provenance": "startup_backfill_only", "forward_event_count": 0, "total_events": 10, "warning": "backfill only"}},
        ]
        payload = mod.build_snapshot()
        model = payload["fill_models"]["rave_live_v2_hybrid_v1"]
        self.assertEqual(model["measured"]["entry_count"], 10)
        self.assertEqual(model["measured"]["fill_prob"], 1.0)
        self.assertEqual(payload["signal_overlays"]["rave_iceberg_sell_reload_v1"]["count"], 3)
        self.assertEqual(model["confidence"], "low")
        self.assertEqual(model["resolved_for_benchmark"]["execution_provenance"], "startup_backfill_only")


if __name__ == "__main__":
    unittest.main()
