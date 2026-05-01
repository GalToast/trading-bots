#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_organism_state_report as organism


class BuildOrganismStateReportTests(unittest.TestCase):
    def test_triage_action_maps_forward_statuses(self) -> None:
        self.assertEqual(organism.triage_action("holding_up"), "keep")
        self.assertEqual(organism.triage_action("holding_up_in_position"), "keep")
        self.assertEqual(organism.triage_action("lagging"), "review_demote")
        self.assertEqual(organism.triage_action("lagging_in_position"), "review_demote")
        self.assertEqual(organism.triage_action("seeded_negative"), "watch_seed_negative")
        self.assertEqual(organism.triage_action("seeded_positive"), "watch_seed_positive")
        self.assertEqual(organism.triage_action("seeded_in_position"), "wait_first_close")

    def test_build_payload_merges_execution_and_watchdog_truth(self) -> None:
        original_exec = organism.EXECUTION_REPORT_JSON
        original_watchdog = organism.WATCHDOG_REPORT_JSON
        original_watchdog_groups = organism.WATCHDOG_GROUPS_CONFIG
        original_runner_registry = organism.RUNNER_REGISTRY_JSON
        original_btc_concentration = organism.BTC_CONCENTRATION_JSON
        try:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                exec_path = tmp / "execution.json"
                watchdog_path = tmp / "watchdog.json"
                watchdog_groups_path = tmp / "watchdog_groups.json"
                runner_registry_path = tmp / "runner_registry.json"
                concentration_path = tmp / "btc_concentration.json"
                exec_path.write_text(
                    """
{
  "rows": [
    {
      "lane": "live_rearm_941777",
      "kind": "live_fx",
      "watchdog_status": "ok",
      "notes": "broker_scope_outside_lane=USDJPY:39, fx_grad=live progress=graduated(100.0%) next=next_good_session_window",
      "probable_missed_open": false,
      "suspected_missed_open": false,
      "fx_graduation": {
        "readiness": "live",
        "progress_label": "graduated",
        "next_gate": "next_good_session_window"
      },
      "forward_review": {}
    },
    {
      "lane": "live_btcusd_m5_warp_probation_941780",
      "kind": "live_crypto",
      "watchdog_status": "",
      "notes": "historical_only",
      "probable_missed_open": false,
      "suspected_missed_open": false,
      "forward_review": {}
    },
    {
      "lane": "shadow_coinbase_experimental_rotation_bb_rsi",
      "kind": "shadow_coinbase_spot",
      "watchdog_status": "ok",
      "notes": "forward=lagging realized=-0.80 closes=43",
      "probable_missed_open": false,
      "suspected_missed_open": false,
      "forward_review": {
        "forward_status": "lagging",
        "realized_net_usd": "-0.7982",
        "realized_delta_usd": "-3.0876",
        "realized_closes": "43",
        "open_count": "0"
      }
    }
  ]
}
""".strip(),
                    encoding="utf-8",
                )
                watchdog_path.write_text(
                    """
{
  "recent_incidents": [
    {
      "lane": "shadow_fx_m15_micro_eurusd_bar",
      "old_status": "stale",
      "new_status": "ok",
      "heartbeat_age_seconds": 7.9,
      "reasons": []
    }
  ],
  "rows": [
    {
      "name": "live_rearm_941777",
      "status": "ok",
      "scoreboard_total": {
        "realized_usd": "193.35",
        "floating_usd": "598.21",
        "net_usd": "791.56",
        "closes": "155",
        "open_count": "80"
      }
    }
  ]
}
""".strip(),
                    encoding="utf-8",
                )
                runner_registry_path.write_text(
                    """
{
  "lanes": [
    {
      "name": "live_btcusd_m5_warp_probation_941780",
      "kind": "live_crypto",
      "enabled": false,
      "pause_note": "paused_for_test"
    }
  ]
}
""".strip(),
                    encoding="utf-8",
                )
                concentration_path.write_text(
                    """
{
  "summary": {
    "combined_floating_usd": "-7735.14",
    "combined_net_usd": "-5219.37",
    "combined_realized_usd": "2515.77",
    "combined_open_count": 83,
    "triggered_thresholds": ["m5_no_compression"]
  }
}
""".strip(),
                    encoding="utf-8",
                )
                watchdog_groups_path.write_text('{"groups": {}}', encoding="utf-8")
                organism.EXECUTION_REPORT_JSON = exec_path
                organism.WATCHDOG_REPORT_JSON = watchdog_path
                organism.WATCHDOG_GROUPS_CONFIG = watchdog_groups_path
                organism.RUNNER_REGISTRY_JSON = runner_registry_path
                organism.BTC_CONCENTRATION_JSON = concentration_path

                payload = organism.build_payload()
        finally:
            organism.EXECUTION_REPORT_JSON = original_exec
            organism.WATCHDOG_REPORT_JSON = original_watchdog
            organism.WATCHDOG_GROUPS_CONFIG = original_watchdog_groups
            organism.RUNNER_REGISTRY_JSON = original_runner_registry
            organism.BTC_CONCENTRATION_JSON = original_btc_concentration

        self.assertEqual(payload["summary"]["watchdog_non_ok_count"], 0)
        self.assertEqual(payload["summary"]["live_lane_count"], 1)
        self.assertEqual(payload["summary"]["paused_live_lane_count"], 1)
        self.assertEqual(payload["summary"]["forward_triage_count"], 1)
        self.assertEqual(payload["summary"]["gate_watch_count"], 1)
        self.assertEqual(payload["summary"]["btc_concentration_triggers"], ["m5_no_compression"])
        self.assertEqual(payload["live_risks"][0]["lane"], "combined_btc_live_concentration")
        self.assertEqual(payload["live_lanes"][0]["lane"], "live_rearm_941777")
        self.assertEqual(payload["live_lanes"][0]["net_usd"], "791.56")
        self.assertEqual(payload["paused_live_lanes"][0]["lane"], "live_btcusd_m5_warp_probation_941780")
        self.assertEqual(payload["paused_live_lanes"][0]["pause_note"], "paused_for_test")
        self.assertEqual(payload["live_risks"][1]["lane"], "live_rearm_941777")
        self.assertEqual(payload["forward_triage"][0]["action"], "review_demote")
        self.assertEqual(payload["gate_watch"][0]["status"], "live")

    def test_render_markdown_includes_forward_triage_action_note(self) -> None:
        markdown = organism.render_markdown(
            {
                "generated_at": "2026-04-14T06:22:00+00:00",
                "summary": {
                    "watchdog_non_ok_count": 0,
                    "execution_probable_missed_open_count": 0,
                    "execution_suspected_missed_open_count": 0,
                    "live_lane_count": 1,
                    "paused_live_lane_count": 1,
                    "forward_triage_count": 1,
                    "gate_watch_count": 1,
                    "btc_concentration_triggers": ["m5_no_compression"],
                    "btc_combined_floating_usd": "-7735.14",
                    "btc_combined_net_usd": "-5219.37",
                },
                "live_lanes": [
                    {
                        "lane": "live_rearm_941777",
                        "realized_usd": "193.35",
                        "floating_usd": "598.21",
                        "net_usd": "791.56",
                        "closes": "155",
                        "open_count": "80",
                        "watchdog_status": "ok",
                        "notes": "fx_grad=live",
                    }
                ],
                "paused_live_lanes": [
                    {
                        "lane": "live_btcusd_m5_warp_probation_941780",
                        "realized_usd": "",
                        "floating_usd": "",
                        "net_usd": "",
                        "closes": 0,
                        "open_count": 0,
                        "watchdog_status": "paused",
                        "pause_note": "paused_for_test",
                    }
                ],
                "live_risks": [],
                "gate_watch": [
                    {
                        "lane": "shadow_ethusd_m15_warp",
                        "status": "shadow_collecting",
                        "progress": "29/50 shadow closes",
                        "next_gate": "reach_50_closes_positive_reset_free",
                        "notes": "crypto_grad=shadow_collecting",
                    }
                ],
                "forward_triage": [
                    {
                        "lane": "shadow_coinbase_experimental_rotation_bb_rsi",
                        "forward_status": "lagging",
                        "action": "review_demote",
                        "realized_net_usd": "-0.7982",
                        "realized_delta_usd": "-3.0876",
                        "closes": "43",
                        "open_count": "0",
                        "notes": "forward=lagging realized=-0.80 closes=43",
                    }
                ],
                "recent_incidents": [],
            }
        )

        self.assertIn("# Organism State", markdown)
        self.assertIn("m5_no_compression", markdown)
        self.assertIn("review_demote", markdown)
        self.assertIn("shadow_ethusd_m15_warp", markdown)
        self.assertIn("live_rearm_941777", markdown)
        self.assertIn("Paused / Disabled Live Lanes", markdown)
        self.assertIn("paused_for_test", markdown)


if __name__ == "__main__":
    unittest.main()
