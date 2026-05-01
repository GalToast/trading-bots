#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_tiny_live_fire_queue as queue


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class FakeKrakenClient:
    def __init__(self, *, open_orders: bool = False, usd: str = "20.00") -> None:
        self.open_orders = open_orders
        self.usd = usd

    def asset_pairs(self) -> dict[str, Any]:
        return {
            "TRACUSD": {
                "altname": "TRACUSD",
                "wsname": "TRAC/USD",
                "base": "TRAC",
                "quote": "USD",
                "ordermin": "1",
                "costmin": "5",
                "pair_decimals": 4,
                "lot_decimals": 8,
                "status": "online",
            },
            "GLMRUSD": {
                "altname": "GLMRUSD",
                "wsname": "GLMR/USD",
                "base": "GLMR",
                "quote": "USD",
                "ordermin": "1",
                "costmin": "5",
                "pair_decimals": 5,
                "lot_decimals": 8,
                "status": "online",
            },
            "ETHEUR": {
                "altname": "ETHEUR",
                "wsname": "ETH/EUR",
                "ordermin": "0.0001",
                "costmin": "5",
                "pair_decimals": 2,
                "lot_decimals": 8,
                "status": "online",
            },
            "XXBTZUSD": {
                "altname": "XBTUSD",
                "wsname": "XBT/USD",
                "ordermin": "0.00001",
                "costmin": "0.5",
                "pair_decimals": 1,
                "lot_decimals": 8,
                "status": "online",
            },
            "AAVEXBT": {
                "altname": "AAVEXBT",
                "wsname": "AAVE/XBT",
                "ordermin": "0.01",
                "costmin": "0.00001",
                "pair_decimals": 7,
                "lot_decimals": 8,
                "status": "online",
            },
        }

    def ticker(self, rest_pairs: list[str]) -> dict[str, Any]:
        rows = {
            "TRACUSD": {
                "a": ["0.3260", "10", "10"],
                "b": ["0.3259", "10", "10"],
                "c": ["0.3300", "1"],
                "o": "0.3200",
                "h": ["0.3350", "0.3350"],
                "l": ["0.3100", "0.3100"],
                "v": ["10000", "10000"],
                "t": [100, 100],
            },
            "GLMRUSD": {
                "a": ["0.01785", "10", "10"],
                "b": ["0.01740", "10", "10"],
                "c": ["0.01754", "1"],
                "o": "0.01859",
                "h": ["0.01950", "0.01950"],
                "l": ["0.01722", "0.01722"],
                "v": ["8000000", "8000000"],
                "t": [100, 100],
            },
            "XXBTZUSD": {
                "a": ["78000.0", "1", "1"],
                "b": ["77900.0", "1", "1"],
                "c": ["77950.0", "1"],
                "o": "77000.0",
                "h": ["79000.0", "79000.0"],
                "l": ["76000.0", "76000.0"],
                "v": ["100", "100"],
                "t": [100, 100],
            },
            "AAVEXBT": {
                "a": ["0.0030000", "10", "10"],
                "b": ["0.0029990", "10", "10"],
                "c": ["0.0030100", "1"],
                "o": "0.0029000",
                "h": ["0.0031000", "0.0031000"],
                "l": ["0.0028000", "0.0028000"],
                "v": ["10000", "10000"],
                "t": [100, 100],
            },
        }
        return {pair: rows[pair] for pair in rest_pairs if pair in rows}

    def depth(self, rest_pair: str, count: int = 20) -> dict[str, Any]:
        return {
            rest_pair: {
                "bids": [["0.3259", "10", "1"]],
                "asks": [["0.3260", "4", "1"], ["0.3262", "5", "1"]],
            }
        }

    def balance(self) -> dict[str, Any]:
        return {"ZUSD": self.usd, "XXBT": "0.001"}

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, private: bool = False) -> Any:
        if path == "/0/private/OpenOrders":
            return {"open": {"OPEN1": {}}} if self.open_orders else {"open": {}}
        raise AssertionError(path)


class KrakenTinyLiveFireQueueTests(unittest.TestCase):
    def test_latest_validate_evidence_keeps_latest_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            write_jsonl(
                path,
                [
                    {"action": "kraken_validate_order", "product_id": "TRAC-USD", "ok": False, "status": "old"},
                    {"action": "kraken_validate_order", "product_id": "TRAC-USD", "ok": True, "status": "validated"},
                ],
            )

            evidence = queue.latest_validate_evidence([path])

            self.assertTrue(evidence["TRAC-USD"]["ok"])
            self.assertEqual(evidence["TRAC-USD"]["status"], "validated")

    def test_build_payload_blocks_globally_when_live_order_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            validate = Path(tmp) / "events.jsonl"
            write_jsonl(validate, [{"action": "kraken_validate_order", "product_id": "TRAC-USD", "ok": True, "status": "validated"}])

            payload = queue.build_payload(
                client=FakeKrakenClient(open_orders=True),
                quote_usd=9.0,
                max_quote_usd=9.25,
                maker_fee_bps=25.0,
                target_net_pct=0.10,
                entry_improve_ticks=1000,
                entry_offset_fracs=[],
                max_entry_concession_bps=-1.0,
                min_entry_spread_cushion_bps=0.0,
                microfill_summary_paths=[],
                min_entry_microfill_rate=0.0,
                min_entry_microfill_trials=0.0,
                min_exit_microfill_rate=0.0,
                min_exit_microfill_trials=0.0,
                quote_currencies={"USD"},
                max_exit_floor_above_ask_bps=15.0,
                min_volume_24h_usd=100.0,
                min_trades_24h=0.0,
                max_spread_bps=250.0,
                min_ret_24h_bps=-500.0,
                validate_paths=[validate],
                live_entry_paths=[],
                entry_miss_cooldown_minutes=60.0,
                query_private=True,
                block_on_open_orders=True,
                depth_top_n=2,
            )

            self.assertIn("live_open_orders_present", payload["summary"]["global_blockers"])
            self.assertFalse(payload["summary"]["live_probe_allowed"])

    def test_fire_candidate_requires_validate_and_reachable_exit_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            validate = Path(tmp) / "events.jsonl"
            write_jsonl(validate, [{"action": "kraken_validate_order", "product_id": "TRAC-USD", "ok": True, "status": "validated"}])

            payload = queue.build_payload(
                client=FakeKrakenClient(open_orders=False),
                quote_usd=9.0,
                max_quote_usd=9.25,
                maker_fee_bps=25.0,
                target_net_pct=0.10,
                entry_improve_ticks=0,
                entry_offset_fracs=[],
                max_entry_concession_bps=-1.0,
                min_entry_spread_cushion_bps=0.0,
                microfill_summary_paths=[],
                min_entry_microfill_rate=0.0,
                min_entry_microfill_trials=0.0,
                min_exit_microfill_rate=0.0,
                min_exit_microfill_trials=0.0,
                quote_currencies={"USD"},
                max_exit_floor_above_ask_bps=80.0,
                min_volume_24h_usd=100.0,
                min_trades_24h=0.0,
                max_spread_bps=250.0,
                min_ret_24h_bps=-500.0,
                validate_paths=[validate],
                live_entry_paths=[],
                entry_miss_cooldown_minutes=60.0,
                query_private=True,
                block_on_open_orders=True,
                depth_top_n=2,
            )
            rows = {row["product_id"]: row for row in payload["rows"]}

            self.assertEqual(rows["TRAC-USD"]["readiness"], "fire_candidate")
            self.assertIn(rows["GLMR-USD"]["readiness"], {"needs_validate_only", "blocked_exit_floor", "blocked"})
            self.assertTrue(payload["summary"]["live_probe_allowed"])

    def test_crypto_quote_pair_uses_quote_balance_and_usd_equivalent_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            validate = Path(tmp) / "events.jsonl"
            write_jsonl(validate, [{"action": "kraken_validate_order", "product_id": "AAVE-BTC", "ok": True, "status": "validated"}])

            payload = queue.build_payload(
                client=FakeKrakenClient(open_orders=True),
                quote_usd=9.0,
                max_quote_usd=9.25,
                maker_fee_bps=25.0,
                target_net_pct=0.10,
                entry_improve_ticks=0,
                entry_offset_fracs=[],
                max_entry_concession_bps=-1.0,
                min_entry_spread_cushion_bps=0.0,
                microfill_summary_paths=[],
                min_entry_microfill_rate=0.0,
                min_entry_microfill_trials=0.0,
                min_exit_microfill_rate=0.0,
                min_exit_microfill_trials=0.0,
                quote_currencies={"BTC"},
                max_exit_floor_above_ask_bps=80.0,
                min_volume_24h_usd=100.0,
                min_trades_24h=0.0,
                max_spread_bps=250.0,
                min_ret_24h_bps=-500.0,
                validate_paths=[validate],
                live_entry_paths=[],
                entry_miss_cooldown_minutes=60.0,
                query_private=True,
                block_on_open_orders=False,
                depth_top_n=2,
            )
            rows = {row["product_id"]: row for row in payload["rows"]}

            self.assertIn("AAVE-BTC", rows)
            self.assertEqual(rows["AAVE-BTC"]["quote_currency"], "BTC")
            self.assertAlmostEqual(rows["AAVE-BTC"]["estimated_notional_usd"], 9.0, delta=0.05)
            self.assertNotIn("live_open_orders_present", payload["summary"]["global_blockers"])

    def test_recent_live_entry_miss_blocks_product_during_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validate = root / "validate.jsonl"
            live = root / "live.jsonl"
            write_jsonl(validate, [{"action": "kraken_validate_order", "product_id": "AAVE-BTC", "ok": True, "status": "validated"}])
            write_jsonl(
                live,
                [
                    {
                        "action": "live_roundtrip_entry_submitted",
                        "product_id": "AAVE-XBT",
                        "txid": "ORDER1",
                        "ts_utc": queue.utc_now_iso(),
                    },
                    {
                        "action": "live_roundtrip_entry_status",
                        "txid": "ORDER1",
                        "status": "open",
                        "vol_exec": "0.00000000",
                        "ts_utc": queue.utc_now_iso(),
                    },
                    {
                        "action": "live_roundtrip_entry_cancel_requested",
                        "txid": "ORDER1",
                        "txid_chain": ["ORDER1"],
                        "ts_utc": queue.utc_now_iso(),
                    },
                ],
            )

            payload = queue.build_payload(
                client=FakeKrakenClient(open_orders=False),
                quote_usd=9.0,
                max_quote_usd=9.25,
                maker_fee_bps=25.0,
                target_net_pct=0.10,
                entry_improve_ticks=0,
                entry_offset_fracs=[],
                max_entry_concession_bps=-1.0,
                min_entry_spread_cushion_bps=0.0,
                microfill_summary_paths=[],
                min_entry_microfill_rate=0.0,
                min_entry_microfill_trials=0.0,
                min_exit_microfill_rate=0.0,
                min_exit_microfill_trials=0.0,
                quote_currencies={"BTC"},
                max_exit_floor_above_ask_bps=80.0,
                min_volume_24h_usd=100.0,
                min_trades_24h=0.0,
                max_spread_bps=250.0,
                min_ret_24h_bps=-500.0,
                validate_paths=[validate],
                live_entry_paths=[live],
                entry_miss_cooldown_minutes=60.0,
                query_private=True,
                block_on_open_orders=False,
                depth_top_n=2,
            )
            rows = {row["product_id"]: row for row in payload["rows"]}

            self.assertIn("recent_live_entry_miss", rows["AAVE-BTC"]["blockers"])
            self.assertEqual(rows["AAVE-BTC"]["live_entry_outcome"], "entry_canceled_unfilled")

    def test_entry_offset_uses_corrected_microfill_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validate = root / "validate.jsonl"
            microfill = root / "microfill.json"
            write_jsonl(validate, [{"action": "kraken_validate_order", "product_id": "TRAC-USD", "ok": True, "status": "validated"}])
            microfill.write_text(
                json.dumps(
                    {
                        "by_product_side_offset": {
                            "TRAC-USD|buy|0.2500": {
                                "unfilled_timeout": 1,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            payload = queue.build_payload(
                client=FakeKrakenClient(open_orders=False),
                quote_usd=9.0,
                max_quote_usd=9.25,
                maker_fee_bps=25.0,
                target_net_pct=0.10,
                entry_improve_ticks=0,
                entry_offset_fracs=[0.25],
                max_entry_concession_bps=-1.0,
                min_entry_spread_cushion_bps=0.0,
                microfill_summary_paths=[microfill],
                min_entry_microfill_rate=0.01,
                min_entry_microfill_trials=1.0,
                min_exit_microfill_rate=0.0,
                min_exit_microfill_trials=0.0,
                quote_currencies={"USD"},
                max_exit_floor_above_ask_bps=80.0,
                min_volume_24h_usd=100.0,
                min_trades_24h=0.0,
                max_spread_bps=250.0,
                min_ret_24h_bps=-500.0,
                validate_paths=[validate],
                live_entry_paths=[],
                entry_miss_cooldown_minutes=60.0,
                query_private=True,
                block_on_open_orders=True,
                depth_top_n=2,
            )
            rows = [row for row in payload["rows"] if row["product_id"] == "TRAC-USD"]

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["entry_price_model"], "spread_offset_frac")
            self.assertEqual(rows[0]["entry_offset_frac"], 0.25)
            self.assertEqual(rows[0]["entry_microfill_trials"], 1)
            self.assertEqual(rows[0]["entry_microfill_rate"], 0.0)
            self.assertIn("entry_microfill_rate_too_low", rows[0]["blockers"])

    def test_exit_microfill_gate_blocks_low_sell_fillability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validate = root / "validate.jsonl"
            microfill = root / "microfill.json"
            write_jsonl(validate, [{"action": "kraken_validate_order", "product_id": "TRAC-USD", "ok": True, "status": "validated"}])
            microfill.write_text(
                json.dumps(
                    {
                        "by_product_side_offset": {
                            "TRAC-USD|buy|0.2500": {"hard_cross_fill_proxy": 2},
                            "TRAC-USD|sell|0.2500": {"unfilled_timeout": 2},
                        }
                    }
                ),
                encoding="utf-8",
            )

            payload = queue.build_payload(
                client=FakeKrakenClient(open_orders=False),
                quote_usd=9.0,
                max_quote_usd=9.25,
                maker_fee_bps=25.0,
                target_net_pct=0.10,
                entry_improve_ticks=0,
                entry_offset_fracs=[0.25],
                max_entry_concession_bps=-1.0,
                min_entry_spread_cushion_bps=0.0,
                microfill_summary_paths=[microfill],
                min_entry_microfill_rate=0.5,
                min_entry_microfill_trials=2.0,
                min_exit_microfill_rate=0.5,
                min_exit_microfill_trials=2.0,
                quote_currencies={"USD"},
                max_exit_floor_above_ask_bps=80.0,
                min_volume_24h_usd=100.0,
                min_trades_24h=0.0,
                max_spread_bps=250.0,
                min_ret_24h_bps=-500.0,
                validate_paths=[validate],
                live_entry_paths=[],
                entry_miss_cooldown_minutes=60.0,
                query_private=True,
                block_on_open_orders=True,
                depth_top_n=2,
            )
            rows = [row for row in payload["rows"] if row["product_id"] == "TRAC-USD"]

            self.assertEqual(rows[0]["entry_microfill_rate"], 1.0)
            self.assertEqual(rows[0]["exit_microfill_trials"], 2)
            self.assertEqual(rows[0]["exit_microfill_rate"], 0.0)
            self.assertIn("exit_microfill_rate_too_low", rows[0]["blockers"])
            self.assertNotEqual(rows[0]["readiness"], "fire_candidate")

    def test_microfill_offset_stats_normalize_xbt_to_btc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "microfill.json"
            path.write_text(
                json.dumps(
                    {
                        "by_product_side_offset": {
                            "COMP-XBT|buy|0.1000": {
                                "hard_cross_fill_proxy": 1,
                                "unfilled_timeout": 1,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            stats = queue.load_microfill_offset_stats([path])

            self.assertIn("COMP-BTC|buy|0.1000", stats)
            self.assertEqual(stats["COMP-BTC|buy|0.1000"]["trials"], 2)
            self.assertEqual(stats["COMP-BTC|buy|0.1000"]["fill_like"], 1)

    def test_write_reports_outputs_json_csv_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "generated_at": "now",
                "summary": {"usd_pairs_scanned": 0, "readiness_counts": {}, "global_blockers": [], "global_warnings": [], "live_probe_allowed": False, "next_action": "none", "read": "test"},
                "live_exposure": {"usd_free": 0, "open_order_ids": []},
                "rows": [],
            }

            queue.write_reports(payload, json_path=root / "q.json", csv_path=root / "q.csv", md_path=root / "q.md")

            self.assertTrue((root / "q.json").exists())
            self.assertTrue((root / "q.csv").exists())
            self.assertIn("Kraken Tiny Live Fire Queue", (root / "q.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(unittest.main())
