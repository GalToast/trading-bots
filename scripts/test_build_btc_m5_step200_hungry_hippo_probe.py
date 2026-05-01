#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_btc_m5_step200_hungry_hippo_probe as probe


class BuildBtcM5Step200HungryHippoProbeTests(unittest.TestCase):
    def test_build_probe_payload_preserves_step200_baseline_and_shadow_only(self) -> None:
        salvage_payload = {
            "lanes": [
                {
                    "lane": "shadow_btcusd_m5_warp_step200",
                    "verdict": "salvage_probe_candidate",
                    "realized_net_usd": 139.96,
                    "realized_closes": 2,
                    "avg_per_close": 69.98,
                    "total_resets": 0,
                    "open_positions": 2,
                    "max_open_per_side": 60,
                    "alpha": 1.0,
                }
            ]
        }
        state_payload = {
            "metadata": {
                "raw_rearm_variant": "rearm_lvl2_exc1",
                "raw_rearm_cooldown_bars": 0,
                "raw_sell_gap": 1,
                "raw_buy_gap": 1,
            },
            "runner": {"heartbeat_at": "2026-04-14T04:36:36.133472+00:00"},
            "symbols": {
                "BTCUSD": {
                    "anchor": 74067.43,
                    "realized_closes": 2,
                    "realized_net_usd": 139.96,
                    "open_tickets": [{"x": 1}, {"x": 2}],
                    "rearm_tokens": [{"x": 1}],
                }
            },
        }
        btc_live_payload = {
            "regime": {"control_mode": "bounce_reversal"},
            "escape_hatch": {
                "tier1_breakeven": {"max_bars": 12, "max_loss": 5.0},
                "tier2_extreme": {"cut_count": 2, "max_loss_per_position": 10.0},
            },
        }

        payload = probe.build_probe_payload(salvage_payload, state_payload, btc_live_payload)

        config = payload["probe_config"]
        self.assertEqual(config["name"], "shadow_btcusd_m5_hungry_hippo_step200_v1")
        self.assertEqual(config["kind"], "shadow_crypto")
        self.assertFalse(config["enabled"])
        self.assertIn("--step", config["restart_args"])
        self.assertIn("200", config["restart_args"])
        self.assertIn("--escape-hatch", config["restart_args"])
        self.assertEqual(payload["baseline_evidence"]["realized_closes"], 2)

    def test_render_markdown_mentions_shadow_only_and_success_gate(self) -> None:
        payload = {
            "generated_at": "2026-04-15T00:00:00+00:00",
            "leadership_read": ["shadow only"],
            "baseline_evidence": {
                "anchor": 1.0,
                "realized_closes": 2,
                "realized_net_usd": 3.0,
                "open_tickets": 1,
                "rearm_tokens": 1,
                "heartbeat_at": "x",
            },
            "probe_hypothesis": {
                "what_is_preserved": ["a"],
                "what_is_new": ["b"],
                "success_gate": "collect more closes",
            },
            "probe_config": {
                "kind": "shadow_crypto",
                "enabled": False,
                "watchdog_group": "crypto_watchdog",
                "hungry_hippo_metadata": {
                    "risk_notes": "risk",
                    "validation_status": "shadow_only",
                },
            },
        }

        markdown = probe.render_markdown(payload)
        self.assertIn("BTC M5 Step200 Hungry Hippo Probe", markdown)
        self.assertIn("shadow only", markdown)
        self.assertIn("collect more closes", markdown)
        self.assertIn("shadow_crypto", markdown)


if __name__ == "__main__":
    unittest.main()
