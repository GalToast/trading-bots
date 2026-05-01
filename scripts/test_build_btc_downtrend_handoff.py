#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_btc_downtrend_handoff as handoff


class BtcDowntrendHandoffTests(unittest.TestCase):
    def test_handoff_proposes_shadow_only_sell_biased_candidate(self) -> None:
        payload = handoff.build_payload()
        proposed = payload["proposed_downtrend_shape"]

        self.assertEqual(payload["status"], "handoff_ready")
        self.assertEqual(payload["summary"]["regime_signal_read"]["action_bias"], "SELL")
        self.assertEqual(proposed["shape_id"], "btcusd_m15_bounce_down_v1")
        self.assertEqual(proposed["posture"], "shadow_only_candidate")
        self.assertLess(proposed["sell_step_coeff"], proposed["buy_step_coeff"])
        self.assertEqual(proposed["alpha"], 0.3)


if __name__ == "__main__":
    unittest.main()
