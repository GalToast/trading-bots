#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest.mock import patch

import analyze_rave_v2_execution_truth as mod


class AnalyzeRaveV2ExecutionTruthTests(unittest.TestCase):
    @patch("analyze_rave_v2_execution_truth.load_jsonl")
    @patch("analyze_rave_v2_execution_truth.load_json")
    def test_build_report_detects_startup_backfill_only_without_explicit_phase(self, load_json_mock, load_jsonl_mock) -> None:
        load_json_mock.return_value = {"started_at": "2026-04-12T13:27:35+00:00"}
        load_jsonl_mock.return_value = [
            {"action": "open", "ts_utc": "2026-04-12T13:27:38+00:00"},
            {"action": "close", "ts_utc": "2026-04-12T13:27:39+00:00"},
            {"action": "open", "ts_utc": "2026-04-12T13:28:10+00:00"},
        ]
        payload = mod.build_report()
        truth = payload["execution_truth"]
        self.assertEqual(truth["provenance"], "startup_backfill_only")
        self.assertEqual(truth["forward_event_count"], 0)
        self.assertIn("startup replay artifacts", truth["warning"])

    @patch("analyze_rave_v2_execution_truth.load_jsonl")
    @patch("analyze_rave_v2_execution_truth.load_json")
    def test_build_report_prefers_explicit_phase_labels(self, load_json_mock, load_jsonl_mock) -> None:
        load_json_mock.return_value = {"started_at": "2026-04-12T13:27:35+00:00"}
        load_jsonl_mock.return_value = [
            {"action": "open", "ts_utc": "2026-04-12T13:27:38+00:00", "phase": "startup_backfill"},
            {"action": "open", "ts_utc": "2026-04-12T14:27:38+00:00", "phase": "live_forward"},
        ]
        payload = mod.build_report()
        truth = payload["execution_truth"]
        self.assertEqual(truth["provenance"], "mixed")
        self.assertEqual(truth["explicit_phase_coverage_pct"], 100.0)
        self.assertEqual(truth["forward_event_count"], 1)


if __name__ == "__main__":
    unittest.main()
