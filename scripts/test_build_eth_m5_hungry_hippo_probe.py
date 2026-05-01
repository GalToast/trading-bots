#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_eth_m5_hungry_hippo_probe as probe


class BuildEthM5HungryHippoProbeTests(unittest.TestCase):
    def test_build_probe_payload_preserves_step5_and_marks_old_live_as_stale(self) -> None:
        salvage_payload = {
            "lanes": [
                {
                    "lane": "shadow_ethusd_m5_warp_5",
                    "verdict": "strong_salvage_candidate",
                    "realized_net_usd": 157.17,
                    "realized_closes": 20,
                    "avg_per_close": 7.86,
                    "total_resets": 23,
                    "open_positions": 2,
                    "max_open_per_side": 12,
                    "alpha": 1.0,
                },
                {
                    "lane": "live_ethusd_m5_warp",
                    "verdict": "do_not_restore_as_was",
                    "realized_net_usd": -110.54,
                    "realized_closes": 12,
                },
            ]
        }
        shadow_state_payload = {
            "metadata": {
                "raw_rearm_variant": "rearm_lvl2_exc1",
                "raw_rearm_cooldown_bars": 0,
                "raw_sell_gap": 1,
                "raw_buy_gap": 1,
                "shared_price_max_age_ms": 1000,
            },
            "runner": {"heartbeat_at": "2026-04-14T19:28:05.556889+00:00"},
            "symbols": {
                "ETHUSD": {
                    "anchor": 2317.755,
                    "realized_closes": 20,
                    "realized_net_usd": 157.17,
                    "open_tickets": [{"x": 1}, {"x": 2}],
                    "rearm_opens": 6,
                    "anchor_resets": 23,
                }
            },
        }
        live_state_payload = {
            "metadata": {"max_floating_loss_usd": -10.0},
            "runner": {"heartbeat_at": "2026-04-14T19:28:34.339566+00:00"},
            "symbols": {
                "ETHUSD": {
                    "realized_closes": 12,
                    "realized_net_usd": -110.54,
                    "anchor_resets": 20,
                }
            },
        }
        eth_live_payload = {
            "regime": {"control_mode": "mixed_hold"},
            "escape_hatch": {
                "tier1_breakeven": {"max_bars": 15, "max_loss": 3.0},
                "tier2_extreme": {"cut_count": 1, "max_loss_per_position": 5.0},
            },
        }

        payload = probe.build_probe_payload(salvage_payload, shadow_state_payload, live_state_payload, eth_live_payload)

        config = payload["probe_config"]
        self.assertEqual(config["name"], "shadow_ethusd_m5_hungry_hippo_step5_v1")
        self.assertEqual(config["kind"], "shadow_crypto")
        self.assertFalse(config["enabled"])
        self.assertIn("--step", config["restart_args"])
        self.assertIn("5", config["restart_args"])
        self.assertIn("--escape-hatch", config["restart_args"])
        self.assertEqual(payload["failed_live_reference"]["realized_net_usd"], -110.54)
        self.assertIn("superseded", config["hungry_hippo_metadata"]["validation_status"])

    def test_render_markdown_mentions_failed_live_reference(self) -> None:
        payload = {
            "generated_at": "2026-04-15T00:00:00+00:00",
            "leadership_read": ["superseded"],
            "shadow_baseline": {"anchor": 1.0, "realized_closes": 2, "realized_net_usd": 3.0, "open_tickets": 1, "rearm_opens": 1, "anchor_resets": 1, "heartbeat_at": "x"},
            "failed_live_reference": {"realized_closes": 1, "realized_net_usd": -2.0, "anchor_resets": 3, "max_floating_loss_usd": -10.0, "heartbeat_at": "y"},
            "probe_hypothesis": {"what_is_preserved": ["a"], "what_is_new": ["b"], "success_gate": "positive"},
            "probe_config": {"kind": "shadow_crypto", "enabled": False, "watchdog_group": "crypto_watchdog", "hungry_hippo_metadata": {"risk_notes": "risk", "validation_status": "superseded"}},
        }

        markdown = probe.render_markdown(payload)
        self.assertIn("ETH M5 Hungry Hippo Probe", markdown)
        self.assertIn("Failed Live Reference", markdown)
        self.assertIn("superseded", markdown)
        self.assertIn("shadow_crypto", markdown)


if __name__ == "__main__":
    unittest.main()
