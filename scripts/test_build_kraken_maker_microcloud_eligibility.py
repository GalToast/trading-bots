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

import build_kraken_maker_microcloud_eligibility as microcloud


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def opportunity_payload(product_id: str = "HOT-USD") -> dict:
    return {
        "rows": [
            {
                "product_id": product_id,
                "playbook": "maker_harvest",
                "mer": 1.5,
                "spread_bps": 20.0,
                "machinegun_score": 12.0,
            }
        ]
    }


def radar_payload(product_id: str = "HOT-USD", min_notional: float = 5.0) -> dict:
    return {
        "rows": [
            {
                "product_id": product_id,
                "min_notional_usd": min_notional,
                "cost_min": min_notional,
                "spread_bps": 22.0,
            }
        ]
    }


def calibration_payload(product_id: str = "HOT-USD") -> dict:
    return {
        "by_product": {},
        "by_product_side": {
            f"{product_id}|buy": {"probable_queue_depletion_fill_proxy": 8, "unfilled_timeout": 2},
            f"{product_id}|sell": {"probable_queue_depletion_fill_proxy": 7, "unfilled_timeout": 3},
        },
        "by_product_side_offset": {
            f"{product_id}|buy|0.0000": {"probable_queue_depletion_fill_proxy": 7, "unfilled_timeout": 3},
            f"{product_id}|sell|0.0000": {"probable_queue_depletion_fill_proxy": 6, "unfilled_timeout": 4},
        },
        "by_product_side_tick_offset": {},
    }


class KrakenMakerMicrocloudEligibilityTests(unittest.TestCase):
    def test_slice_math_applies_fee_before_min_notional(self) -> None:
        option = microcloud.slice_option(
            quote_usd=25.0,
            slice_count=5,
            maker_fee_bps=25.0,
            min_notional_usd=5.0,
        )

        self.assertEqual(option["slice_quote_usd"], 5.0)
        self.assertEqual(option["post_fee_order_notional_usd"], 4.9875)
        self.assertFalse(option["min_notional_valid"])
        self.assertEqual(option["min_notional_shortfall_usd"], 0.0125)

    def test_two_slice_l1_candidate_does_not_overclaim_true_microcloud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opp = root / "opp.json"
            radar = root / "radar.json"
            calibration = root / "calibration.json"
            write_json(opp, opportunity_payload())
            write_json(radar, radar_payload(min_notional=5.0))
            write_json(calibration, calibration_payload())

            payload = microcloud.build_payload(
                opportunity_path=opp,
                radar_path=radar,
                microfill_summary_path=calibration,
                quote_usd=25.0,
                maker_fee_bps=25.0,
                slice_counts=[2, 5],
                min_entry_rate=0.10,
                min_exit_rate=0.25,
                min_offset_rate=0.10,
                min_offset_samples=6,
            )

        row = payload["rows"][0]
        self.assertTrue(row["telemetry_only_l1_two_slice_candidate"])
        self.assertFalse(row["true_5x_microcloud_launchable"])
        self.assertIn("five_slice_min_notional_fails", row["blockers"])
        self.assertIn("tickback_microfill_calibration_missing", row["blockers"])
        self.assertEqual(payload["summary"]["verdict"], "telemetry_only_l1_candidates_found")
        self.assertFalse(payload["launch_contract"]["candidate_command_emitted"])

    def test_true_microcloud_allows_contract_when_tickback_evidence_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opp = root / "opp.json"
            radar = root / "radar.json"
            calibration = root / "calibration.json"
            payload = calibration_payload()
            payload["by_product_side_tick_offset"] = {
                "HOT-USD|buy|1": {"probable_queue_depletion_fill_proxy": 7, "unfilled_timeout": 3},
                "HOT-USD|buy|2": {"probable_queue_depletion_fill_proxy": 7, "unfilled_timeout": 3},
                "HOT-USD|sell|1": {"probable_queue_depletion_fill_proxy": 7, "unfilled_timeout": 3},
                "HOT-USD|sell|2": {"probable_queue_depletion_fill_proxy": 7, "unfilled_timeout": 3},
            }
            write_json(opp, opportunity_payload())
            write_json(radar, radar_payload(min_notional=1.0))
            write_json(calibration, payload)

            result = microcloud.build_payload(
                opportunity_path=opp,
                radar_path=radar,
                microfill_summary_path=calibration,
                quote_usd=25.0,
                maker_fee_bps=25.0,
                slice_counts=[2, 5],
            )

        row = result["rows"][0]
        self.assertTrue(row["tickback_calibration_available"])
        self.assertTrue(row["true_5x_microcloud_launchable"])
        self.assertTrue(result["launch_contract"]["true_microcloud_ab_allowed"])
        self.assertNotIn("runner_microcloud_behavior_not_implemented", result["summary"]["global_blockers"])

    def test_missing_microfill_blocks_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opp = root / "opp.json"
            radar = root / "radar.json"
            calibration = root / "calibration.json"
            write_json(opp, opportunity_payload())
            write_json(radar, radar_payload(min_notional=1.0))
            write_json(calibration, {"by_product_side": {}, "by_product_side_offset": {}})

            payload = microcloud.build_payload(
                opportunity_path=opp,
                radar_path=radar,
                microfill_summary_path=calibration,
                quote_usd=25.0,
                maker_fee_bps=25.0,
                slice_counts=[2, 5],
            )

        row = payload["rows"][0]
        self.assertFalse(row["telemetry_only_l1_two_slice_candidate"])
        self.assertIn("base_buy_sell_microfill_gate_fails", row["blockers"])
        self.assertIn("l1_offset_microfill_gate_fails", row["blockers"])

    def test_write_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = microcloud.build_payload(
                opportunity_path=root / "missing-opp.json",
                radar_path=root / "missing-radar.json",
                microfill_summary_path=root / "missing-calibration.json",
            )
            json_path = root / "eligibility.json"
            md_path = root / "eligibility.md"

            microcloud.write_reports(payload, json_path, md_path)

            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("no_opportunity_rows", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
