#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import watch_spot_microstructure_lab as mod


class WatchSpotMicrostructureLabTests(unittest.TestCase):
    def test_write_state_persists_json_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            payload = {"runner": {"pid": 123}, "updated_at": "2026-04-12T00:00:00+00:00"}
            mod.write_state(state_path, payload)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved, payload)

    @patch("watch_spot_microstructure_lab.run_step")
    def test_refresh_once_runs_analysis_then_dashboard(self, run_step_mock) -> None:
        run_step_mock.side_effect = [
            {"script": "analyze_spot_microstructure_sync.py", "stdout": "a"},
            {"script": "analyze_predatory_signal_alignment.py", "stdout": "b"},
            {"script": "analyze_rave_v2_execution_truth.py", "stdout": "c"},
            {"script": "build_empirical_execution_snapshot.py", "stdout": "d"},
            {"script": "build_spot_microstructure_lab_dashboard.py", "stdout": "e"},
        ]
        payload = mod.refresh_once()
        self.assertEqual(payload["analysis"]["script"], "analyze_spot_microstructure_sync.py")
        self.assertEqual(payload["alignment"]["script"], "analyze_predatory_signal_alignment.py")
        self.assertEqual(payload["execution_truth"]["script"], "analyze_rave_v2_execution_truth.py")
        self.assertEqual(payload["empirical_snapshot"]["script"], "build_empirical_execution_snapshot.py")
        self.assertEqual(payload["dashboard"]["script"], "build_spot_microstructure_lab_dashboard.py")
        self.assertEqual(run_step_mock.call_count, 5)


if __name__ == "__main__":
    unittest.main()
