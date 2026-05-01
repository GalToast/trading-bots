from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kraken_maker_execution_realism_board as board


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def close(
    product: str = "HOUSE-USD",
    *,
    cost: float = 25.0,
    entry_price: float = 1.0,
    exit_price: float = 1.02,
    spread_bps: float = 100.0,
    net: float = 0.25,
    exit_type: str = "maker_fill",
) -> dict:
    qty = (cost - cost * 0.0025) / entry_price
    gross = qty * exit_price
    return {
        "action": "close_maker_shadow",
        "product_id": product,
        "cost_usd": cost,
        "entry_fee": cost * 0.0025,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "exit_fee_bps": 25.0 if exit_type == "maker_fill" else 40.0,
        "exit_type": exit_type,
        "gross_proceeds": gross,
        "net": net,
        "net_pct": net / cost * 100.0,
        "spread_bps": spread_bps,
        "reason": "maker_rent_harvest",
    }


class KrakenMakerExecutionRealismBoardTests(unittest.TestCase):
    def test_wide_spread_maker_profit_can_fail_all_fallback(self) -> None:
        row = close(spread_bps=300.0, net=0.25)
        scenario = board.Scenario("all_fallback", 0.0, 10.0, 5.0)

        replay = board.scenario_trade_net(row, scenario)

        self.assertGreater(replay["actual_net"], 0.0)
        self.assertLess(replay["fallback_net"], 0.0)
        self.assertLess(replay["expected_net"], 0.0)
        self.assertTrue(replay["would_be_red_if_fallback"])

    def test_lane_flags_concentration_and_maker_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            rows = [
                {"action": "open_maker_shadow", "product_id": "HOUSE-USD", "quote_usd": 25.0},
                close("HOUSE-USD", net=0.50, spread_bps=250.0),
                close("HOUSE-USD", net=0.50, spread_bps=250.0),
                close("BTR-USD", net=0.01, spread_bps=40.0),
            ]
            write_jsonl(path, rows)

            payload = board.lane_payload("test", path, board.DEFAULT_SCENARIOS)

            self.assertEqual(payload["closes"], 3)
            self.assertIn("single_product_net_concentration_ge_70pct", payload["blockers"])
            self.assertIn("profit_depends_on_maker_exit_fill", payload["blockers"])
            self.assertEqual(payload["verdict"], "not_live_equivalent_yet")

    def test_build_payload_keeps_shadow_evidence_cap_until_live_fills_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            write_jsonl(path, [close("BTR-USD", net=0.20, spread_bps=10.0) for _ in range(20)])

            payload = board.build_payload(lanes={"tight": path}, scenarios=board.DEFAULT_SCENARIOS)

            self.assertEqual(payload["summary"]["evidence_closeness_cap_pct"], 55.0)
            self.assertIn(payload["summary"]["verdict"], {"shadow_stress_pass_but_live_microfill_needed", "not_close_enough_to_live"})
            self.assertEqual(payload["lanes"][0]["parse_errors"], 0)

    def test_write_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "events.jsonl"
            write_jsonl(path, [close("BTR-USD", net=0.20, spread_bps=10.0) for _ in range(2)])
            payload = board.build_payload(lanes={"tight": path}, scenarios=board.DEFAULT_SCENARIOS)
            json_path = root / "board.json"
            md_path = root / "board.md"

            board.write_reports(payload, json_path=json_path, md_path=md_path)

            self.assertTrue(json_path.exists())
            self.assertIn("Kraken Maker Execution Realism Board", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
