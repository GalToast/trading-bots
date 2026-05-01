#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_nzdusd_transfer_probe as probe


class NzdusdTransferProbeTests(unittest.TestCase):
    def test_probe_detects_research_only_runtime_with_rearm_override(self) -> None:
        payload = probe.build_payload()
        checks = {item["check_id"]: item for item in payload["conformity_checks"]}

        self.assertEqual(payload["status"], "research_only_monitoring_with_override")
        self.assertTrue(payload["summary"]["runtime_present"])
        self.assertEqual(payload["summary"]["forward_gate"], "waiting_first_close")
        self.assertEqual(checks["directional_asymmetry"]["status"], "pass")
        self.assertEqual(checks["alpha"]["status"], "pass")
        self.assertEqual(checks["sell_gap"]["status"], "pass")
        self.assertEqual(checks["buy_gap"]["status"], "pass")
        self.assertEqual(checks["rearm_variant"]["status"], "warn")

    def test_probe_keeps_transfer_constraints_visible(self) -> None:
        payload = probe.build_payload()
        constraints = payload["target_transfer"]["constraints"]

        self.assertIn("Do not import GBPUSD side-gap asymmetry by default.", constraints)
        self.assertIn("Require realism or forward-proof promotion before any live claim.", constraints)


if __name__ == "__main__":
    unittest.main()
