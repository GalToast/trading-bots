#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_eth_m5_retuned_shadow_handoff as handoff


class BuildEthM5RetunedShadowHandoffTests(unittest.TestCase):
    def test_build_payload_creates_disabled_retuned_shadow_config(self) -> None:
        payload = handoff.build_payload(
            {
                "name": "hungry_hippo_ethusd_m5_step14_control",
                "kind": "shadow_crypto",
                "state_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_state.json",
                "event_path": "reports/penetration_lattice_shadow_ethusd_m5_step14_control_events.jsonl",
                "poll_seconds": 30,
                "stale_after_seconds": 240,
                "watchdog_group": "crypto_watchdog",
                "restart_args": [
                    "scripts/live_penetration_lattice_tick_crypto_shadow.py",
                    "--symbol",
                    "ETHUSD",
                    "--timeframe",
                    "M5",
                    "--step",
                    "14",
                    "--step-buy",
                    "14",
                    "--step-sell",
                    "14",
                    "--disable-dynamic-geometry",
                    "--max-open-per-side",
                    "12",
                    "--raw-close-alpha",
                    "1.0",
                    "--raw-rearm-variant",
                    "rearm_lvl2_exc1",
                    "--raw-rearm-cooldown-bars",
                    "0",
                    "--raw-sell-gap",
                    "1",
                    "--raw-buy-gap",
                    "1",
                    "--poll-seconds",
                    "30",
                    "--shared-price-max-age-ms",
                    "1000",
                    "--max-floating-loss-usd",
                    "-15.0",
                    "--max-lattice-window-bars",
                    "240",
                    "--state-path",
                    "reports/penetration_lattice_shadow_ethusd_m5_step14_control_state.json",
                    "--event-path",
                    "reports/penetration_lattice_shadow_ethusd_m5_step14_control_events.jsonl",
                ],
                "hungry_hippo_metadata": {
                    "personality": "NO_SESSION_GATE_HARVEST",
                    "guardrails": {
                        "kill_on_reset_storm": True,
                        "max_resets_per_hour": 6,
                        "floating_loss_limit_usd": -15.0,
                        "session_gate": None,
                        "escape_hatch_enabled": False,
                    },
                },
            },
            {"symbols": {"ETHUSD": {"realized_closes": 36, "realized_net_usd": -314.29, "anchor_resets": 0, "open_tickets": []}}},
            {"summary": {"verdict": "blocked_by_negative_expectancy", "avg_per_close": -8.7303}},
            {
                "rows": [
                    {
                        "action": "decide_eth_step14_negative_proof_response_kill_or_launch_retuned_shadow",
                        "machine_truth": {"recommended_retune_step_usd": 3.0, "recommended_min_shadow_closes": 25},
                    }
                ]
            },
            {
                "recommended_option": "Option A",
                "recommended_step_usd": 3.0,
                "alternate_step_usd": 1.4,
                "minimum_proof_closes": 25,
                "kill_option_available": True,
            },
        )

        config = payload["retuned_config"]
        self.assertEqual(config["name"], handoff.OUTPUT_LANE_NAME)
        self.assertFalse(config["enabled"])
        self.assertEqual(config["state_path"], handoff.OUTPUT_STATE_PATH)
        self.assertEqual(config["event_path"], handoff.OUTPUT_EVENT_PATH)
        self.assertIn(Path(handoff.OUTPUT_STATE_PATH).name, config["process_match_substrings"])
        self.assertIn("3.0", config["restart_args"])
        self.assertEqual(payload["retune_candidate"]["minimum_proof_closes"], 25)
        self.assertIn("Do not: do not retune the existing step14 control in place", handoff.render_markdown(payload))

    def test_render_markdown_surfaces_safe_next_move(self) -> None:
        markdown = handoff.render_markdown(
            {
                "generated_at": "2026-04-15T20:55:00+00:00",
                "leadership_read": ["one"],
                "decision_context": {
                    "queue_top_action": "decide_eth_step14_negative_proof_response_kill_or_launch_retuned_shadow",
                    "control_verdict": "blocked_by_negative_expectancy",
                    "control_realized_closes": 36,
                    "control_realized_net_usd": -314.29,
                    "control_avg_per_close": -8.7303,
                    "control_anchor_resets": 0,
                    "control_open_positions": 0,
                },
                "retune_candidate": {
                    "recommended_option": "Option A",
                    "recommended_step_usd": 3.0,
                    "alternate_step_usd": 1.4,
                    "minimum_proof_closes": 25,
                    "kill_option_available": True,
                },
                "retuned_config": {
                    "name": handoff.OUTPUT_LANE_NAME,
                    "enabled": False,
                    "state_path": handoff.OUTPUT_STATE_PATH,
                    "event_path": handoff.OUTPUT_EVENT_PATH,
                    "watchdog_group": "crypto_watchdog",
                    "hungry_hippo_metadata": {"risk_notes": "shadow-only handoff"},
                },
                "launch_discipline": {
                    "must_keep_untouched": ["step14 control"],
                    "must_not_do": ["do not mutate"],
                    "safe_next_move": "launch as a new shadow lane after branch choice",
                },
            }
        )

        self.assertIn("ETH M5 Retuned Shadow Handoff", markdown)
        self.assertIn("Recommended step USD: `3.00`", markdown)
        self.assertIn("Safe next move: `launch as a new shadow lane after branch choice`", markdown)


if __name__ == "__main__":
    unittest.main()
