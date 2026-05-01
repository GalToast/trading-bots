#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_kraken_maker_validate_only_probe as probe


class FakeKrakenClient:
    def __init__(self) -> None:
        self.orders: list[dict] = []

    def asset_pairs(self) -> dict:
        return {
            "HOUSEUSD": {
                "altname": "HOUSEUSD",
                "wsname": "HOUSE/USD",
                "ordermin": "1000",
                "costmin": "0.5",
                "pair_decimals": 7,
                "lot_decimals": 8,
                "status": "online",
            }
        }

    def ticker(self, rest_pairs: list[str]) -> dict:
        if rest_pairs != ["HOUSEUSD"]:
            raise AssertionError(rest_pairs)
        return {"HOUSEUSD": {"b": ["0.0021000"], "a": ["0.0022000"], "c": ["0.0021500"]}}

    def add_order(self, **kwargs) -> dict:
        self.orders.append(kwargs)
        return {"descr": {"order": "buy 4761.90476191 HOUSEUSD @ limit 0.0021"}}


class KrakenMakerValidateOnlyProbeTests(unittest.TestCase):
    def test_build_validate_order_plan_uses_post_only_quote_cap_shape(self) -> None:
        pair = probe.build_pair_map(FakeKrakenClient().asset_pairs())["HOUSE-USD"]

        plan = probe.build_validate_order_plan(
            product_id="HOUSE-USD",
            pair=pair,
            bid=0.0021,
            ask=0.0022,
            max_quote_usd=10.0,
            min_quote_cushion=1.02,
        )

        self.assertEqual(plan.product_id, "HOUSE-USD")
        self.assertEqual(plan.rest_pair, "HOUSEUSD")
        self.assertEqual(plan.side, "buy")
        self.assertTrue(plan.post_only)
        self.assertTrue(plan.validate_only)
        self.assertLessEqual(plan.quote_usd, 10.0 * 1.001)
        self.assertGreaterEqual(plan.quote_usd, 0.5)

    def test_build_validate_order_plan_supports_quote_native_btc_cap(self) -> None:
        pair = probe.build_pair_map(
            {
                "COMPXBT": {
                    "altname": "COMPXBT",
                    "wsname": "COMP/XBT",
                    "ordermin": "0.01",
                    "costmin": "0.00001",
                    "pair_decimals": 7,
                    "lot_decimals": 8,
                    "status": "online",
                }
            }
        )["COMP-BTC"]

        plan = probe.build_validate_order_plan(
            product_id="COMP-BTC",
            pair=pair,
            bid=0.0002828,
            ask=0.0002846,
            max_quote_usd=9.0,
            max_quote_amount=0.000118,
            min_quote_cushion=1.02,
        )

        self.assertEqual(plan.quote_currency, "BTC")
        self.assertLessEqual(plan.quote_amount, 0.000118 * 1.001)
        self.assertEqual(plan.quote_usd, 0.0)

    def test_run_probe_calls_add_order_validate_true_and_writes_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            client = FakeKrakenClient()

            payload = probe.run_probe(
                client=client,  # type: ignore[arg-type]
                products=["HOUSE-USD"],
                event_path=event_path,
                max_quote_usd=10.0,
                min_quote_cushion=1.02,
            )

            self.assertEqual(payload["summary"]["validated"], 1)
            self.assertEqual(len(client.orders), 1)
            self.assertTrue(client.orders[0]["validate"])
            self.assertTrue(client.orders[0]["post_only"])
            self.assertEqual(client.orders[0]["side"], "buy")
            row = json.loads(event_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["action"], "kraken_validate_order")
            self.assertTrue(row["ok"])
            self.assertEqual(row["status"], "validated")
            self.assertTrue(row["validate_only"])

    def test_run_probe_blocks_non_usd_without_explicit_allow(self) -> None:
        class BtcClient(FakeKrakenClient):
            def asset_pairs(self) -> dict:
                return {
                    "COMPXBT": {
                        "altname": "COMPXBT",
                        "wsname": "COMP/XBT",
                        "ordermin": "0.01",
                        "costmin": "0.00001",
                        "pair_decimals": 7,
                        "lot_decimals": 8,
                        "status": "online",
                    }
                }

        with tempfile.TemporaryDirectory() as tmp:
            payload = probe.run_probe(
                client=BtcClient(),  # type: ignore[arg-type]
                products=["COMP-BTC"],
                event_path=Path(tmp) / "events.jsonl",
                max_quote_usd=9.0,
                max_quote_amount=0.000118,
                min_quote_cushion=1.02,
            )

            self.assertEqual(payload["summary"]["validated"], 0)
            self.assertEqual(payload["results"][0]["status"], "non_usd_quote_requires_explicit_allow")

    def test_dry_run_does_not_call_add_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeKrakenClient()

            payload = probe.run_probe(
                client=client,  # type: ignore[arg-type]
                products=["HOUSE-USD"],
                event_path=Path(tmp) / "events.jsonl",
                max_quote_usd=10.0,
                min_quote_cushion=1.02,
                dry_run=True,
            )

            self.assertEqual(payload["summary"]["validated"], 1)
            self.assertEqual(client.orders, [])
            self.assertEqual(payload["results"][0]["status"], "dry_run_validated_locally")

    def test_infer_recent_products_prefers_active_then_recent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state.json"
            event_path = root / "events.jsonl"
            state_path.write_text(
                json.dumps({"state": {"active_positions": {"BTR-USD": {"product_id": "BTR-USD"}}}}),
                encoding="utf-8",
            )
            event_path.write_text(
                "\n".join(
                    [
                        json.dumps({"action": "close_maker_shadow", "product_id": "HOUSE-USD"}),
                        json.dumps({"action": "open_maker_shadow", "product_id": "FOLKS-USD"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            products = probe.infer_recent_products(state_path=state_path, event_path=event_path, limit=3)

            self.assertEqual(products, ["BTR-USD", "FOLKS-USD", "HOUSE-USD"])


if __name__ == "__main__":
    unittest.main()
