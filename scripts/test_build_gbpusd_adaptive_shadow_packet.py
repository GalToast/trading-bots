from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_gbpusd_adaptive_shadow_packet as packet


class GbpusdAdaptiveShadowPacketTests(unittest.TestCase):
    def test_packet_is_defined_and_targets_dedicated_lane(self) -> None:
        payload = packet.build_payload()

        self.assertEqual(payload["status"], "packet_defined_waiting_launch")
        self.assertTrue(payload["summary"]["packet_defined"])
        self.assertFalse(payload["summary"]["runtime_present"])
        self.assertEqual(payload["summary"]["forward_gate"], "waiting_first_launch")

        proposed = payload["packet_contract"]
        self.assertEqual(proposed["lane_name"], "shadow_gbpusd_m15_trend_harvest_v1")
        self.assertEqual(proposed["raw_close_alpha"], 0.5)
        self.assertEqual(proposed["raw_rearm_variant"], "rearm_lvl2_exc1")
        self.assertEqual(proposed["raw_sell_gap"], 1)
        self.assertEqual(proposed["raw_buy_gap"], 3)
        self.assertTrue(any(arg == "--fresh-start" for arg in proposed["command"]))
        self.assertTrue(any(arg == "--adaptive-overlay-autopilot" for arg in proposed["command"]))

    def test_packet_keeps_reference_runtime_visible(self) -> None:
        payload = packet.build_payload()
        checks = {row["check_id"]: row for row in payload["contract_checks"]}

        self.assertEqual(payload["reference_runtime_lane"]["lane_name"], "shadow_gbpusd_m15_asym")
        self.assertEqual(checks["directional_asymmetry"]["status"], "pass")
        self.assertEqual(checks["buy_gap"]["status"], "pass")
        self.assertEqual(checks["rearm_variant"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
