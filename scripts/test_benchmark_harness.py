#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import benchmark_harness as mod


class BenchmarkHarnessTests(unittest.TestCase):
    def test_resolve_fill_model_uses_builtin_model(self) -> None:
        name, model = mod.resolve_fill_model("perfect", empirical_fill_model=None, empirical_path=Path("missing.json"))
        self.assertEqual(name, "perfect")
        self.assertEqual(model["fill_prob"], 1.0)

    def test_resolve_fill_model_reads_empirical_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empirical_execution_snapshot.json"
            path.write_text(
                json.dumps(
                    {
                        "fill_models": {
                            "rave_live_v2_hybrid_v1": {
                                "resolved_for_benchmark": {
                                    "fill_prob": 0.9,
                                    "entry_slippage_bps": 40,
                                    "exit_slippage_bps": 25,
                                    "execution_provenance": "mixed",
                                    "forward_event_count": 3,
                                    "total_events": 12,
                                    "warning": "forward partial",
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            name, model = mod.resolve_fill_model(
                "perfect",
                empirical_fill_model="rave_live_v2_hybrid_v1",
                empirical_path=path,
            )
        self.assertEqual(name, "rave_live_v2_hybrid_v1")
        self.assertEqual(model["fill_prob"], 0.9)
        self.assertEqual(model["entry_slippage_bps"], 40.0)
        self.assertEqual(model["exit_slippage_bps"], 25.0)
        self.assertEqual(model["execution_provenance"], "mixed")
        self.assertEqual(model["forward_event_count"], 3)


if __name__ == "__main__":
    unittest.main()
