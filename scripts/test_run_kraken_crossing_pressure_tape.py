#!/usr/bin/env python3
from __future__ import annotations

import unittest
import sys
import tempfile
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_kraken_crossing_pressure_tape as tape


class KrakenCrossingPressureTapeTests(unittest.TestCase):
    def test_trial_features_marks_hard_cross_fill_like(self) -> None:
        features = tape.trial_features(
            {
                "result": "hard_cross_fill_proxy",
                "initial_bid": 10,
                "initial_ask": 11,
                "last_bid": 10.5,
                "last_ask": 10.8,
                "initial_spread_bps": 950,
                "last_spread_bps": 280,
                "samples": 3,
                "elapsed_seconds": 4.2,
            }
        )

        self.assertTrue(features["fill_like"])
        self.assertGreater(features["bid_move_bps"], 0)
        self.assertLess(features["spread_change_bps"], 0)

    def test_trial_features_converts_btc_quote_depth_to_usd(self) -> None:
        features = tape.trial_features(
            {
                "result": "unfilled_timeout",
                "side": "buy",
                "initial_bid": 0.0001,
                "initial_ask": 0.00011,
                "initial_bid_size": 2.0,
                "initial_ask_size": 3.0,
                "last_bid": 0.0001,
                "last_ask": 0.00011,
                "last_bid_size": 1.0,
                "last_ask_size": 1.0,
            },
            depth_notional_usd=10.0,
            quote_currency="BTC",
            quote_to_usd=50000.0,
        )

        self.assertEqual(features["quote_currency"], "BTC")
        self.assertEqual(features["initial_bid_depth_quote"], 0.0002)
        self.assertEqual(features["initial_bid_depth_usd"], 10.0)
        self.assertTrue(features["same_side_depth_ok"])

    def test_cycle_record_requires_both_sides_for_two_sided(self) -> None:
        buy = {"result": "hard_cross_fill_proxy", "initial_bid": 10, "initial_ask": 11, "last_bid": 10, "last_ask": 9.9}
        sell = {"result": "unfilled_timeout", "initial_bid": 10, "initial_ask": 11, "last_bid": 10, "last_ask": 11}

        record = tape.cycle_record("GWEI-USD", 0.5, 1, buy, sell)

        self.assertTrue(record["buy_fill_like"])
        self.assertFalse(record["sell_fill_like"])
        self.assertFalse(record["two_sided_fill_like"])

    def test_load_sell_floor_prices_reads_fire_queue_required_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fire_queue.json"
            path.write_text(
                '{"rows":[{"product_id":"HONEY-USD","estimated_required_exit_price":0.00206},'
                '{"product_id":"BAD-USD","estimated_required_exit_price":0}]}',
                encoding="utf-8",
            )

            floors = tape.load_sell_floor_prices(path)

        self.assertEqual(floors, {"HONEY-USD": 0.00206})

    def test_summarize_ranks_two_sided_leaders_first(self) -> None:
        records = [
            {"product_id": "A-USD", "offset_frac": 0.5, "buy_fill_like": True, "sell_fill_like": False, "two_sided_fill_like": False},
            {"product_id": "B-USD", "offset_frac": 0.5, "buy_fill_like": True, "sell_fill_like": True, "two_sided_fill_like": True},
        ]

        summary = tape.summarize(records)

        self.assertEqual(summary["two_sided_records"], 1)
        self.assertEqual(summary["leaders"][0]["key"], "B-USD|0.5000")

    def test_parse_float_csv_accepts_comma_or_repeated_args(self) -> None:
        self.assertEqual(tape.parse_float_csv("0.5,0.75"), [0.5, 0.75])
        self.assertEqual(tape.parse_float_csv(["0.5", "0.75,0.9"]), [0.5, 0.75, 0.9])

    def test_rank_top_spread_products_filters_and_sorts(self) -> None:
        asset_pairs = {
            "WENUSD": {"altname": "WENUSD", "wsname": "WEN/USD", "status": "online", "pair_decimals": 8},
            "BMBUSD": {"altname": "BMBUSD", "wsname": "BMB/USD", "status": "online", "pair_decimals": 8},
            "BMBEUR": {"altname": "BMBEUR", "wsname": "BMB/EUR", "status": "online", "pair_decimals": 8},
        }
        tickers = {
            "WENUSD": {"a": ["11", "1", "1"], "b": ["9", "1", "1"], "c": ["10", "1"], "v": ["1", "100"]},
            "BMBUSD": {"a": ["10.1", "1", "1"], "b": ["9.9", "1", "1"], "c": ["10", "1"], "v": ["1", "200"]},
            "BMBEUR": {"a": ["20", "1", "1"], "b": ["10", "1", "1"], "c": ["15", "1"], "v": ["1", "999"]},
        }

        rows = tape.rank_top_spread_products(
            asset_pairs,
            tickers,
            quote_currencies=["USD"],
            top_products=2,
            min_spread_bps=100.0,
            min_volume_24h=50.0,
        )

        self.assertEqual([row["product_id"] for row in rows], ["WEN-USD", "BMB-USD"])

    def test_rank_radar_heartbeat_products_uses_movement_not_spread_only(self) -> None:
        radar = {
            "rows": [
                {"product_id": "DEAD-USD", "quote_currency": "USD", "signal_state": "live_hot", "velocity_score": 999, "best_short_bps": 0, "spread_bps": 10, "samples": 5},
                {"product_id": "WIDE-USD", "quote_currency": "USD", "signal_state": "live_hot", "velocity_score": 500, "best_short_bps": 50, "spread_bps": 500, "samples": 5},
                {"product_id": "MOVE-USD", "quote_currency": "USD", "signal_state": "building", "velocity_score": 100, "best_short_bps": 40, "spread_bps": 20, "samples": 3},
                {"product_id": "FAST-USD", "quote_currency": "USD", "signal_state": "live_hot", "velocity_score": 200, "best_short_bps": 30, "spread_bps": 30, "samples": 4},
            ]
        }

        rows = tape.rank_radar_heartbeat_products(
            radar,
            quote_currencies=["USD"],
            top_products=3,
            max_spread_bps=250,
            min_best_short_bps=10,
            min_samples=2,
            states=["live_hot", "building"],
        )

        self.assertEqual([row["product_id"] for row in rows], ["FAST-USD", "MOVE-USD"])

    def test_rank_radar_side_heartbeat_requires_entry_and_exit_motion(self) -> None:
        radar = {
            "rows": [
                {"product_id": "ASKONLY-USD", "quote_currency": "USD", "rest_pair": "ASKONLYUSD", "spread_bps": 20, "velocity_score": 10, "best_short_bps": 1},
                {"product_id": "BOTH-USD", "quote_currency": "USD", "rest_pair": "BOTHUSD", "spread_bps": 30, "velocity_score": 20, "best_short_bps": 2},
                {"product_id": "WIDE-USD", "quote_currency": "USD", "rest_pair": "WIDEUSD", "spread_bps": 400, "velocity_score": 999, "best_short_bps": 999},
            ]
        }
        cache = {
            "samples": {
                "ASKONLYUSD": [{"ts": 1, "ask": 10, "bid": 9}, {"ts": 2, "ask": 9.9, "bid": 9}],
                "BOTHUSD": [{"ts": 1, "ask": 10, "bid": 9}, {"ts": 2, "ask": 9.9, "bid": 9.1}],
                "WIDEUSD": [{"ts": 1, "ask": 10, "bid": 9}, {"ts": 2, "ask": 9.8, "bid": 9.2}],
            }
        }

        rows = tape.rank_radar_side_heartbeat_products(
            radar,
            cache,
            quote_currencies=["USD"],
            top_products=5,
            max_spread_bps=250,
            min_ask_down_bps=50,
            min_bid_up_bps=50,
            min_samples=2,
            lookback_seconds=60,
            side_mode="both",
        )

        self.assertEqual([row["product_id"] for row in rows], ["BOTH-USD"])
        self.assertGreater(rows[0]["ask_down_bps"], 50)
        self.assertGreater(rows[0]["bid_up_bps"], 50)

    def test_rank_radar_side_heartbeat_can_require_latest_motion(self) -> None:
        radar = {
            "rows": [
                {"product_id": "STALE-USD", "quote_currency": "USD", "rest_pair": "STALEUSD", "spread_bps": 20, "velocity_score": 99, "best_short_bps": 99},
                {"product_id": "FRESH-USD", "quote_currency": "USD", "rest_pair": "FRESHUSD", "spread_bps": 20, "velocity_score": 50, "best_short_bps": 50},
            ]
        }
        cache = {
            "samples": {
                "STALEUSD": [{"ts": 1, "ask": 10, "bid": 9}, {"ts": 2, "ask": 9.9, "bid": 9}, {"ts": 3, "ask": 9.9, "bid": 9}],
                "FRESHUSD": [{"ts": 1, "ask": 10, "bid": 9}, {"ts": 2, "ask": 10, "bid": 9}, {"ts": 3, "ask": 9.9, "bid": 9}],
            }
        }

        rows = tape.rank_radar_side_heartbeat_products(
            radar,
            cache,
            quote_currencies=["USD"],
            top_products=5,
            max_spread_bps=250,
            min_ask_down_bps=50,
            min_bid_up_bps=0,
            min_latest_ask_down_bps=50,
            min_latest_bid_up_bps=0,
            min_samples=2,
            lookback_seconds=60,
            side_mode="entry",
        )

        self.assertEqual([row["product_id"] for row in rows], ["FRESH-USD"])

    def test_rank_dislocation_lab_products_uses_positive_maker_upper_events(self) -> None:
        lab = {
            "events": [
                {
                    "product_id": "BAD-USD",
                    "rest_pair": "BADUSD",
                    "setup": "deep_washout_100_60s",
                    "entry_ts": 1,
                    "dislocation_bps": 150,
                    "spread_bps": 50,
                    "ask_discount_bps": 100,
                    "marks": {"60": {"net_pct": -0.2, "mfe_net_pct": -0.1, "target_hit": False}},
                },
                {
                    "product_id": "GOOD-USD",
                    "rest_pair": "GOODUSD",
                    "setup": "deep_washout_100_60s",
                    "entry_ts": 2,
                    "dislocation_bps": 200,
                    "spread_bps": 40,
                    "ask_discount_bps": 180,
                    "marks": {"60": {"net_pct": 1.2, "mfe_net_pct": 1.5, "target_hit": True}},
                },
                {
                    "product_id": "GOOD-USD",
                    "rest_pair": "GOODUSD",
                    "setup": "micro_snapback_20_60s",
                    "entry_ts": 3,
                    "dislocation_bps": 30,
                    "spread_bps": 20,
                    "ask_discount_bps": 20,
                    "marks": {"60": {"net_pct": 0.2, "mfe_net_pct": 0.3, "target_hit": True}},
                },
            ]
        }

        rows = tape.rank_dislocation_lab_products(
            lab,
            top_products=5,
            horizon_seconds=60,
            min_net_pct=0.0,
            min_mfe_net_pct=0.0,
            setup_names=["deep_washout_100_60s"],
        )

        self.assertEqual([row["product_id"] for row in rows], ["GOOD-USD"])
        self.assertEqual(rows[0]["setup"], "deep_washout_100_60s")
        self.assertEqual(rows[0]["net_pct"], 1.2)

    def test_rank_dislocation_lab_products_can_require_realized_net(self) -> None:
        lab = {
            "events": [
                {
                    "product_id": "MFE-ONLY-USD",
                    "setup": "snapback_50_60s",
                    "marks": {"60": {"net_pct": -0.1, "mfe_net_pct": 1.4}},
                },
                {
                    "product_id": "REALIZED-USD",
                    "setup": "snapback_50_60s",
                    "marks": {"60": {"net_pct": 0.2, "mfe_net_pct": 0.3}},
                },
            ]
        }

        rows = tape.rank_dislocation_lab_products(
            lab,
            top_products=5,
            horizon_seconds=60,
            min_net_pct=0.0,
            min_mfe_net_pct=0.0,
            setup_names=[],
            score_mode="realized",
        )

        self.assertEqual([row["product_id"] for row in rows], ["REALIZED-USD"])


if __name__ == "__main__":
    unittest.main()
