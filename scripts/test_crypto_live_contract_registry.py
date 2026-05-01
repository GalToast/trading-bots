import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_GROUPS_PATH = ROOT / "configs" / "watchdog_groups.json"


def _lane_by_name(name: str) -> dict:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    for lane in payload["lanes"]:
        if lane["name"] == name:
            return lane
    raise KeyError(name)


class CryptoLiveContractRegistryTests(unittest.TestCase):
    def test_live_crypto_rows_carry_positive_only_close_guards(self) -> None:
        expected = {
            "live_btcusd_m5_warp_probation_941780": {"spread_ratio": "0.3"},
            "live_btcusd_m15_warp_941781": {
                "spread_ratio": "0.0",
                "liquidity_gap_spread_multiplier": "2.5",
                "liquidity_gap_spread_lookback": "60",
                "liquidity_gap_spread_floor_ratio": "1.0",
                "liquidity_gap_spread_max_ratio": "4.0",
            },
            "live_ethusd_m5_warp_941784": {"spread_ratio": "0.3"},
            "live_adausd_m15_warp_941893": {"spread_ratio": "0.9"},
            "live_ltcusd_m15_warp_941894": {
                "spread_ratio": "0.0",
                "liquidity_gap_spread_multiplier": "2.5",
                "liquidity_gap_spread_lookback": "60",
                "liquidity_gap_spread_floor_ratio": "1.0",
            },
            "live_solusd_m5_warp_941783": {"spread_ratio": "0.3"},
            "live_solusd_m15_warp_v2_941891": {"spread_ratio": "0.65"},
            "live_xrpusd_m5_warp_941789": {"spread_ratio": "0.3"},
            "live_xrpusd_m15_hh_breakout_941892": {"spread_ratio": "0.65"},
        }
        for lane_name, contract in expected.items():
            with self.subTest(lane=lane_name):
                args = _lane_by_name(lane_name)["restart_args"]
                self.assertIn("--positive-only-closes", args)
                self.assertIn("--min-positive-close-profit-usd", args)
                self.assertEqual(args[args.index("--min-positive-close-profit-usd") + 1], "1.0")
                self.assertIn("--max-entry-spread-ratio", args)
                self.assertEqual(args[args.index("--max-entry-spread-ratio") + 1], contract["spread_ratio"])
                if "liquidity_gap_spread_multiplier" in contract:
                    self.assertIn("--liquidity-gap-spread-multiplier", args)
                    self.assertEqual(
                        args[args.index("--liquidity-gap-spread-multiplier") + 1],
                        contract["liquidity_gap_spread_multiplier"],
                    )
                    self.assertIn("--liquidity-gap-spread-lookback", args)
                    self.assertEqual(
                        args[args.index("--liquidity-gap-spread-lookback") + 1],
                        contract["liquidity_gap_spread_lookback"],
                    )
                    self.assertIn("--liquidity-gap-spread-floor-ratio", args)
                    self.assertEqual(
                        args[args.index("--liquidity-gap-spread-floor-ratio") + 1],
                        contract["liquidity_gap_spread_floor_ratio"],
                    )
                    if "liquidity_gap_spread_max_ratio" in contract:
                        self.assertIn("--liquidity-gap-spread-max-ratio", args)
                        self.assertEqual(
                            args[args.index("--liquidity-gap-spread-max-ratio") + 1],
                            contract["liquidity_gap_spread_max_ratio"],
                        )

    def test_live_btc_m15_warp_carries_guarded_burst_contract(self) -> None:
        args = _lane_by_name("live_btcusd_m15_warp_941781")["restart_args"]
        self.assertIn("--guard-open-admission", args)
        self.assertIn("--suppress-additional-levels-after-burst", args)
        self.assertIn("--burst-open-threshold", args)
        self.assertEqual(args[args.index("--burst-open-threshold") + 1], "2")
        self.assertIn("--adaptive-overlay-autopilot", args)
        self.assertIn("--max-floating-loss-usd", args)
        self.assertEqual(args[args.index("--max-floating-loss-usd") + 1], "-50.0")

    def test_live_eth_m5_row_matches_step14_control_shape(self) -> None:
        args = _lane_by_name("live_ethusd_m5_warp_5_941890")["restart_args"]
        self.assertIn("--step", args)
        self.assertEqual(args[args.index("--step") + 1], "14")
        self.assertIn("--disable-dynamic-geometry", args)
        self.assertNotIn("--adaptive-overlay-autopilot", args)

    def test_live_eth_m5_row_is_parked_after_negative_control_truth(self) -> None:
        lane = _lane_by_name("live_ethusd_m5_warp_5_941890")
        self.assertFalse(lane["enabled"])
        self.assertEqual(lane["pause_note"], "parked_negative_eth_m5_control_truth_2026_04_17")

    def test_blind_live_crypto_probes_do_not_keep_oversized_inventory_caps(self) -> None:
        expected_caps = {
            "live_solusd_m15_warp_v2_941891": "15",
            "live_adausd_m15_warp_941893": "15",
            "live_ltcusd_m15_warp_941894": "15",
        }
        for lane_name, cap in expected_caps.items():
            with self.subTest(lane=lane_name):
                args = _lane_by_name(lane_name)["restart_args"]
                self.assertIn("--max-open-per-side", args)
                self.assertEqual(args[args.index("--max-open-per-side") + 1], cap)

    def test_watchdog_groups_drop_parked_toxic_alt_rows(self) -> None:
        payload = json.loads(WATCHDOG_GROUPS_PATH.read_text(encoding="utf-8"))
        grouped = payload["groups"]["crypto_watchdog"]["lanes"]
        flat = payload["crypto_watchdog"]["lanes"]
        feeder = payload["groups"]["feeder_crypto_m15_canary"]["lanes"]

        for lane_list in (grouped, flat):
            self.assertNotIn("shadow_xrpusd_m5_warp", lane_list)
            self.assertNotIn("shadow_ethusd_m5_atr_optimized", lane_list)
            self.assertNotIn("shadow_ethusd_m15_atr_optimized", lane_list)
            self.assertNotIn("shadow_ethusd_m15_asymmetric", lane_list)

        self.assertNotIn("shadow_xrpusd_m15_warp_v2", feeder)

    def test_crypto_watchdog_includes_new_live_seats(self) -> None:
        payload = json.loads(WATCHDOG_GROUPS_PATH.read_text(encoding="utf-8"))
        expected = {
            "live_solusd_m15_warp_v2_941891",
            "live_xrpusd_m15_hh_breakout_941892",
            "live_adausd_m15_warp_941893",
            "live_ltcusd_m15_warp_941894",
        }
        self.assertTrue(expected.issubset(set(payload["groups"]["crypto_watchdog"]["lanes"])))
        self.assertTrue(expected.issubset(set(payload["crypto_watchdog"]["lanes"])))

    def test_watchdog_groups_exclude_disabled_registry_rows(self) -> None:
        payload = json.loads(WATCHDOG_GROUPS_PATH.read_text(encoding="utf-8"))
        disabled = {
            lane["name"]
            for lane in json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["lanes"]
            if lane.get("enabled") is False
        }

        for group in payload["groups"].values():
            lanes = set(group["lanes"])
            self.assertTrue(disabled.isdisjoint(lanes))


if __name__ == "__main__":
    unittest.main()
