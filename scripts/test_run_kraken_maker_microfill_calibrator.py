from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_kraken_maker_microfill_calibrator as cal


class KrakenMakerMicrofillCalibratorTests(unittest.TestCase):
    def test_buy_fill_proxy_when_best_bid_trades_below_order(self) -> None:
        initial = cal.BookTop(bid=1.00, ask=1.02, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:00+00:00")
        current = cal.BookTop(bid=0.99, ask=1.02, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:01+00:00")

        result, reason = cal.infer_post_only_fill_proxy("buy", 1.00, initial, current)

        self.assertEqual(result, "probable_queue_depletion_fill_proxy")
        self.assertEqual(reason, "best_bid_traded_below_order")

    def test_sell_fill_proxy_when_best_ask_lifts_above_order(self) -> None:
        initial = cal.BookTop(bid=1.00, ask=1.02, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:00+00:00")
        current = cal.BookTop(bid=1.00, ask=1.03, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:01+00:00")

        result, reason = cal.infer_post_only_fill_proxy("sell", 1.02, initial, current)

        self.assertEqual(result, "probable_queue_depletion_fill_proxy")
        self.assertEqual(reason, "best_ask_lifted_above_order")

    def test_inside_spread_buy_does_not_false_positive_from_lower_bid(self) -> None:
        initial = cal.BookTop(bid=1.00, ask=1.10, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:00+00:00")
        current = cal.BookTop(bid=1.00, ask=1.10, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:01+00:00")

        result, reason = cal.infer_post_only_fill_proxy("buy", 1.05, initial, current)

        self.assertEqual(result, "unfilled_active")
        self.assertEqual(reason, "bid_order_still_working")

    def test_inside_spread_sell_does_not_false_positive_from_higher_ask(self) -> None:
        initial = cal.BookTop(bid=1.00, ask=1.10, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:00+00:00")
        current = cal.BookTop(bid=1.00, ask=1.10, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:01+00:00")

        result, reason = cal.infer_post_only_fill_proxy("sell", 1.05, initial, current)

        self.assertEqual(result, "unfilled_active")
        self.assertEqual(reason, "ask_order_still_working")

    def test_maker_price_at_offset_preserves_post_only_side(self) -> None:
        book = cal.BookTop(bid=1.00, ask=1.10, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:00+00:00")

        self.assertAlmostEqual(cal.maker_price_at_offset("buy", book, 0.50), 1.05)
        self.assertAlmostEqual(cal.maker_price_at_offset("sell", book, 0.50), 1.05)
        self.assertLess(cal.maker_price_at_offset("buy", book, 0.99), book.ask)
        self.assertGreater(cal.maker_price_at_offset("sell", book, 0.99), book.bid)

    def test_offset_key_is_stable(self) -> None:
        self.assertEqual(cal.offset_key("btr-usd", "SELL", 0.5), "BTR-USD|sell|0.5000")

    def test_maker_price_at_tickback_uses_passive_legal_side(self) -> None:
        book = cal.BookTop(bid=1.00, ask=1.10, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:00+00:00")

        self.assertAlmostEqual(cal.maker_price_at_tickback("buy", book, 0.01, 0), 1.00)
        self.assertAlmostEqual(cal.maker_price_at_tickback("buy", book, 0.01, 2), 0.98)
        self.assertAlmostEqual(cal.maker_price_at_tickback("sell", book, 0.01, 0), 1.10)
        self.assertAlmostEqual(cal.maker_price_at_tickback("sell", book, 0.01, 2), 1.12)

    def test_apply_order_price_bounds_supports_profit_floor(self) -> None:
        self.assertAlmostEqual(cal.apply_order_price_bounds(1.05, min_order_price=1.08), 1.08)
        self.assertAlmostEqual(cal.apply_order_price_bounds(1.05, max_order_price=1.02), 1.02)
        self.assertAlmostEqual(cal.apply_order_price_bounds(1.05, min_order_price=0.0, max_order_price=0.0), 1.05)

    def test_tickback_key_is_stable(self) -> None:
        self.assertEqual(cal.tickback_key("btr-usd", "SELL", 2), "BTR-USD|sell|2")

    def test_spread_decay_marks_unfilled(self) -> None:
        initial = cal.BookTop(bid=1.00, ask=1.10, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:00+00:00")
        current = cal.BookTop(bid=1.04, ask=1.06, bid_size=100.0, ask_size=100.0, ts_utc="2026-01-01T00:00:01+00:00")

        result, _ = cal.infer_post_only_fill_proxy("buy", 1.00, initial, current)

        self.assertEqual(result, "spread_decay_unfilled")

    def test_summarize_events(self) -> None:
        rows = [
            {"action": "microfill_calibration_trial", "product_id": "HOUSE-USD", "side": "buy", "result": "probable_queue_depletion_fill_proxy"},
            {
                "action": "microfill_calibration_trial",
                "product_id": "HOUSE-USD",
                "side": "sell",
                "price_offset_frac": 0.5,
                "price_offset_key": "HOUSE-USD|sell|0.5000",
                "result": "unfilled_timeout",
            },
            {
                "action": "microfill_calibration_trial",
                "product_id": "HOUSE-USD",
                "side": "buy",
                "tick_back": 1,
                "tick_back_key": "HOUSE-USD|buy|1",
                "result": "hard_cross_fill_proxy",
            },
        ]

        summary = cal.summarize_events(rows)

        self.assertEqual(summary["trials"], 3)
        self.assertEqual(summary["fill_like_trials"], 2)
        self.assertEqual(summary["fill_like_rate"], 0.666667)
        self.assertEqual(summary["by_product"]["HOUSE-USD"]["unfilled_timeout"], 1)
        self.assertEqual(summary["by_product_side"]["HOUSE-USD|buy"]["probable_queue_depletion_fill_proxy"], 1)
        self.assertEqual(summary["by_product_side_offset"]["HOUSE-USD|sell|0.5000"]["unfilled_timeout"], 1)
        self.assertEqual(summary["by_product_side_tick_offset"]["HOUSE-USD|buy|1"]["hard_cross_fill_proxy"], 1)

    def test_parse_price_offset_fracs_dedupes_and_clamps(self) -> None:
        self.assertEqual(cal.parse_price_offset_fracs("0,0.5,0.50001,2,bad"), [0.0, 0.5, 0.99])

    def test_parse_price_tick_backs_dedupes(self) -> None:
        self.assertEqual(cal.parse_price_tick_backs("0,1,1.0,2,bad,-1"), [0, 1, 2])

    def test_write_summary_reads_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "action": "microfill_calibration_trial",
                        "product_id": "BTR-USD",
                        "side": "buy",
                        "result": "hard_cross_fill_proxy",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            summary_path = root / "summary.json"

            summary = cal.write_summary(summary_path, events)

            self.assertTrue(summary_path.exists())
            self.assertEqual(summary["fill_like_rate"], 1.0)

    def test_load_opportunity_products_filters_and_limits_board_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "opps.json"
            path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {"product_id": "SKIP-USD", "playbook": "maker_harvest", "mer": 9.0, "spread_bps": 50.0},
                            {"product_id": "FOLKS-USD", "playbook": "maker_harvest", "mer": 9.0, "spread_bps": 140.0},
                            {"product_id": "OTHER-USD", "playbook": "other", "mer": 9.0, "spread_bps": 140.0},
                            {"product_id": "BTR-USD", "playbook": "maker_harvest", "mer": 3.7, "spread_bps": 105.0},
                            {"product_id": "HOUSE-USD", "playbook": "maker_harvest", "mer": 6.0, "spread_bps": 600.0},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            products = cal.load_opportunity_products(
                path,
                top_products=2,
                min_mer=3.5,
                min_spread_bps=100.0,
                playbook="maker_harvest",
            )

            self.assertEqual(products, ["FOLKS-USD", "BTR-USD"])

    def test_products_for_cycle_uses_board_with_explicit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            args = type(
                "Args",
                (),
                {
                    "products": ["HOUSE-USD"],
                    "priority_products": [],
                    "product_source": "opportunity-board",
                    "opportunity_board_path": missing,
                    "top_products": 3,
                    "min_mer": 3.5,
                    "min_spread_bps": 100.0,
                    "playbook": "maker_harvest",
                },
            )()

            self.assertEqual(cal.products_for_cycle(args), ["HOUSE-USD"])

    def test_products_for_cycle_prioritizes_runner_targets_before_board_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "opps.json"
            path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {"product_id": "BTR-USD", "playbook": "maker_harvest", "mer": 3.7, "spread_bps": 105.0},
                            {"product_id": "FOLKS-USD", "playbook": "maker_harvest", "mer": 9.0, "spread_bps": 140.0},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            args = type(
                "Args",
                (),
                {
                    "products": ["HOUSE-USD"],
                    "priority_products": ["KSM-USD", "BTR-USD"],
                    "product_source": "opportunity-board",
                    "opportunity_board_path": path,
                    "top_products": 3,
                    "min_mer": 1.0,
                    "min_spread_bps": 25.0,
                    "playbook": "maker_harvest",
                },
            )()

            self.assertEqual(cal.products_for_cycle(args), ["KSM-USD", "BTR-USD", "FOLKS-USD", "HOUSE-USD"])


if __name__ == "__main__":
    unittest.main()
