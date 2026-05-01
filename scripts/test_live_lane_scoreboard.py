#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_penetration_lane_scoreboard as scoreboard


class LiveLaneScoreboardTests(unittest.TestCase):
    def test_lane_specs_include_live_btcusd_m15_warp(self) -> None:
        spec = next(
            (row for row in scoreboard.LANES if row.lane_id == "live_btcusd_m15_warp_941781"),
            None,
        )
        self.assertIsNotNone(spec)
        self.assertEqual(spec.lane_type, "live")
        self.assertEqual(spec.live_magic, 941781)
        self.assertEqual(spec.live_prefix, "PLIVE-WARP")
        self.assertEqual(spec.state_path.name, "penetration_lattice_live_btcusd_m15_warp_state.json")
        self.assertEqual(spec.exec_log_path.name, "penetration_lattice_live_btcusd_m15_warp_exec_events.jsonl")

    def test_lane_specs_include_live_btcusd_m5_warp_probation(self) -> None:
        spec = next(
            (row for row in scoreboard.LANES if row.lane_id == "live_btcusd_m5_warp_probation_941780"),
            None,
        )
        self.assertIsNotNone(spec)
        self.assertEqual(spec.lane_type, "live")
        self.assertEqual(spec.live_magic, 941780)
        self.assertEqual(spec.live_prefix, "PLIVE-BTCM5")
        self.assertEqual(spec.state_path.name, "penetration_lattice_live_btcusd_m5_warp_state.json")
        self.assertEqual(spec.exec_log_path.name, "penetration_lattice_live_btcusd_m5_warp_exec_events.jsonl")

    def test_first_log_timestamp_reads_first_valid_ts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "lane.jsonl"
            path.write_text(
                "\n".join(
                    [
                        "",
                        '{"junk": true}',
                        '{"ts_utc":"2026-04-10T21:03:10.576466+00:00","action":"reconcile_gap_detected"}',
                    ]
                ),
                encoding="utf-8",
            )
            ts = scoreboard.first_log_timestamp(path)
        self.assertIsNotNone(ts)
        self.assertEqual(ts.isoformat(), "2026-04-10T21:03:10.576466+00:00")

    def test_summarize_live_lane_uses_broker_realized_not_modeled_realized(self) -> None:
        spec = scoreboard.LaneSpec(
            lane_id="live_demo",
            lane_type="live",
            state_path=Path("unused.json"),
            exec_log_path=Path("unused.jsonl"),
            live_magic=941779,
            live_prefix="PLIVE-DEMO",
        )
        state = {
            "updated_at": "2026-04-10T23:40:00+00:00",
            "symbols": {
                "EURUSD": {"realized_net_usd": 2.0, "realized_closes": 5},
                "GBPUSD": {"realized_net_usd": 1.0, "realized_closes": 3},
            },
        }
        positions = [
            SimpleNamespace(symbol="EURUSD", profit=1.2),
            SimpleNamespace(symbol="GBPUSD", profit=-0.4),
        ]
        deals = [
            SimpleNamespace(symbol="EURUSD", profit=0.0, swap=0.0, commission=-0.05, fee=0.0, entry=getattr(scoreboard.mt5, "DEAL_ENTRY_IN", 0), magic=941779, comment="PLIVE-DEMO-B"),
            SimpleNamespace(symbol="EURUSD", profit=0.30, swap=0.0, commission=0.0, fee=0.0, entry=getattr(scoreboard.mt5, "DEAL_ENTRY_OUT", 1), magic=941779, comment="PLIVE-DEMO-exit"),
            SimpleNamespace(symbol="GBPUSD", profit=0.0, swap=0.0, commission=-0.05, fee=0.0, entry=getattr(scoreboard.mt5, "DEAL_ENTRY_IN", 0), magic=941779, comment="PLIVE-DEMO-S"),
            SimpleNamespace(symbol="GBPUSD", profit=-0.10, swap=0.0, commission=0.0, fee=0.0, entry=getattr(scoreboard.mt5, "DEAL_ENTRY_OUT", 1), magic=941779, comment="PLIVE-DEMO-exit"),
        ]

        with (
            patch.object(scoreboard, "live_positions_by_magic", return_value=positions),
            patch.object(scoreboard, "live_deals_for_lane", return_value=(deals, scoreboard.parse_iso_utc("2026-04-10T21:00:00+00:00"))),
        ):
            rows = scoreboard.summarize_live_lane(spec, state)

        eur = next(row for row in rows if row["symbol"] == "EURUSD")
        gbp = next(row for row in rows if row["symbol"] == "GBPUSD")
        total = next(row for row in rows if row["symbol"] == "TOTAL")

        self.assertEqual(eur["realized_basis"], "broker")
        self.assertAlmostEqual(eur["realized_usd"], 0.25, places=3)
        self.assertAlmostEqual(eur["modeled_realized_usd"], 2.0, places=3)
        self.assertAlmostEqual(eur["realized_gap_usd"], -1.75, places=3)
        self.assertEqual(eur["closes"], 1)
        self.assertAlmostEqual(eur["net_usd"], 1.45, places=3)

        self.assertAlmostEqual(gbp["realized_usd"], -0.15, places=3)
        self.assertAlmostEqual(gbp["modeled_realized_usd"], 1.0, places=3)
        self.assertAlmostEqual(gbp["realized_gap_usd"], -1.15, places=3)
        self.assertEqual(gbp["closes"], 1)
        self.assertAlmostEqual(gbp["net_usd"], -0.55, places=3)

        self.assertAlmostEqual(total["realized_usd"], 0.10, places=3)
        self.assertAlmostEqual(total["modeled_realized_usd"], 3.0, places=3)
        self.assertAlmostEqual(total["realized_gap_usd"], -2.90, places=3)
        self.assertEqual(total["closes"], 2)
        self.assertEqual(total["open_count"], 2)

    def test_live_deals_for_lane_accepts_prefix_when_history_magic_is_zero(self) -> None:
        spec = scoreboard.LaneSpec(
            lane_id="live_demo",
            lane_type="live",
            state_path=Path("unused.json"),
            exec_log_path=Path("unused.jsonl"),
            live_magic=941779,
            live_prefix="PLIVE-BTC",
        )
        broker_deals = [
            SimpleNamespace(magic=0, comment="PLIVE-BTC-exit"),
            SimpleNamespace(magic=941779, comment="PLIVE-BTC-B"),
            SimpleNamespace(magic=123456, comment="PLIVE-BTC-nope"),
            SimpleNamespace(magic=0, comment="OTHER-LANE"),
        ]

        with (
            patch.object(scoreboard, "session_started_at", return_value=scoreboard.parse_iso_utc("2026-04-10T21:00:00+00:00")),
            patch.object(scoreboard.mt5, "history_deals_get", return_value=broker_deals),
        ):
            deals, started_at = scoreboard.live_deals_for_lane(spec, {})

        self.assertEqual(started_at.isoformat(), "2026-04-10T21:00:00+00:00")
        self.assertEqual([deal.comment for deal in deals], ["PLIVE-BTC-exit", "PLIVE-BTC-B"])

    def test_exact_logged_deals_resolves_deal_tickets_from_exec_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "exec.jsonl"
            log_path.write_text(
                "\n".join(
                    [
                        '{"event":{"symbol":"BTCUSD"},"result":{"attempts":[{"deal":12434}]}}',
                        '{"event":{"symbol":"BTCUSD"},"result":{"attempts":[{"deal":12434},{"deal":12435}]}}',
                    ]
                ),
                encoding="utf-8",
            )
            spec = scoreboard.LaneSpec(
                lane_id="live_demo",
                lane_type="live",
                state_path=Path("unused.json"),
                exec_log_path=log_path,
                live_magic=941779,
                live_prefix="PLIVE-BTC",
            )
            history_map = {
                12434: [SimpleNamespace(ticket=12434, symbol="BTCUSD", entry=1, comment="PLIVE-BTC-exit", profit=-17.32, commission=0.0, swap=0.0, fee=0.0)],
                12435: [SimpleNamespace(ticket=12435, symbol="BTCUSD", entry=1, comment="PLIVE-BTC-exit", profit=-17.32, commission=0.0, swap=0.0, fee=0.0)],
            }

            with patch.object(scoreboard.mt5, "history_deals_get", side_effect=lambda ticket=None, *args, **kwargs: history_map.get(ticket, [])):
                deals = scoreboard.exact_logged_deals(spec)

        self.assertEqual([deal.ticket for deal in deals], [12434, 12435])

    def test_summarize_shadow_lane_keeps_modeled_realized(self) -> None:
        spec = scoreboard.LaneSpec(
            lane_id="shadow_demo",
            lane_type="shadow",
            state_path=Path("unused.json"),
        )
        state = {
            "updated_at": "2026-04-10T23:41:00+00:00",
            "symbols": {
                "BTCUSD": {
                    "realized_net_usd": -12.5,
                    "realized_closes": 4,
                    "open_tickets": [],
                }
            },
        }

        with patch.object(scoreboard, "synthetic_shadow_floating", return_value=0.0):
            rows = scoreboard.summarize_shadow_lane(spec, state)

        btc = next(row for row in rows if row["symbol"] == "BTCUSD")
        self.assertEqual(btc["realized_basis"], "modeled")
        self.assertAlmostEqual(btc["realized_usd"], -12.5, places=3)
        self.assertAlmostEqual(btc["modeled_realized_usd"], -12.5, places=3)
        self.assertAlmostEqual(btc["realized_gap_usd"], 0.0, places=3)


if __name__ == "__main__":
    unittest.main()
