from __future__ import annotations

import unittest
from datetime import datetime, timezone

import scripts.build_booked_pnl_breakdown_board as board


class BuildBookedPnlBreakdownBoardTests(unittest.TestCase):
    def test_build_payload_splits_live_shadow_and_coinbase_books(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 5, 55, tzinfo=timezone.utc),
            organism_payload={
                "live_lanes": [
                    {
                        "lane": "live_rearm_941777",
                        "kind": "live_fx",
                        "realized_usd": "724.43",
                        "open_count": "4",
                        "closes": "320",
                        "watchdog_status": "ok",
                        "notes": "live reference",
                    }
                ],
                "forward_triage": [
                    {
                        "lane": "shadow_coinbase_burst_god_mode_live",
                        "kind": "shadow_coinbase_spot",
                        "realized_net_usd": "3049.89",
                        "open_count": "0",
                        "closes": "896",
                        "forward_status": "holding_up",
                        "action": "keep",
                    }
                ],
            },
            execution_payload={
                "rows": [
                    {
                        "lane": "shadow_fx_close_policy_mixed",
                        "kind": "shadow_fx",
                        "pre_start_state_carry_realized_usd": "-95.54",
                        "runner_session_trade_realized_usd": "4.35",
                        "clean_forward_realized_delta_usd": "",
                        "open_count": "11",
                        "close_count": "769",
                        "watchdog_status": "ok",
                    },
                    {
                        "lane": "shadow_solusd_m15_warp_v2",
                        "kind": "shadow_crypto",
                        "pre_start_state_carry_realized_usd": "55.4",
                        "runner_session_trade_realized_usd": "0.0",
                        "clean_forward_realized_delta_usd": "55.4",
                        "open_count": "2",
                        "close_count": "9",
                        "watchdog_status": "ok",
                    },
                ]
            },
        )

        self.assertEqual(payload["summary"]["live_total_booked_usd"], 724.43)
        self.assertEqual(payload["summary"]["shadow_lattice_total_booked_proxy_usd"], -40.14)
        self.assertEqual(payload["summary"]["shadow_coinbase_total_booked_usd"], 3049.89)
        self.assertEqual(payload["summary"]["combined_shadow_total_mixed_basis_usd"], 3009.75)
        self.assertEqual(payload["shadow_lattice"]["fx_bottom"][0]["lane"], "shadow_fx_close_policy_mixed")
        self.assertEqual(payload["shadow_lattice"]["crypto_top"][0]["lane"], "shadow_solusd_m15_warp_v2")
        self.assertEqual(payload["shadow_coinbase"]["top"][0]["lane"], "shadow_coinbase_burst_god_mode_live")

    def test_build_payload_flags_historical_shadow_drag_when_active_book_is_positive(self) -> None:
        payload = board.build_payload(
            now=datetime(2026, 4, 16, 5, 55, tzinfo=timezone.utc),
            organism_payload={
                "live_lanes": [],
                "forward_triage": [
                    {
                        "lane": "shadow_coinbase_cfgbtc_ratio_sleeve",
                        "kind": "shadow_coinbase_spot",
                        "realized_net_usd": "10.0",
                        "open_count": "2",
                        "closes": "9",
                        "forward_status": "bootstrap_negative",
                        "action": "watch_seed_negative",
                    }
                ],
            },
            execution_payload={
                "rows": [
                    {
                        "lane": "shadow_btcusd_h1_step30",
                        "kind": "shadow_crypto_candidate",
                        "pre_start_state_carry_realized_usd": "-100.0",
                        "open_count": "0",
                        "close_count": "89",
                        "watchdog_status": "quarantined",
                    },
                    {
                        "lane": "shadow_solusd_m15_warp_v2",
                        "kind": "shadow_crypto",
                        "pre_start_state_carry_realized_usd": "5.0",
                        "open_count": "2",
                        "close_count": "9",
                        "watchdog_status": "ok",
                    },
                ]
            },
        )

        self.assertEqual(payload["summary"]["shadow_lattice_total_booked_proxy_usd"], -95.0)
        self.assertEqual(payload["summary"]["shadow_lattice_active_total_booked_proxy_usd"], 5.0)
        self.assertEqual(payload["summary"]["combined_shadow_total_mixed_basis_usd"], -85.0)
        self.assertEqual(payload["summary"]["combined_shadow_active_plus_coinbase_mixed_basis_usd"], 15.0)
        self.assertEqual(payload["readiness"], "historical_shadow_drag_dominates_active_book")

    def test_render_markdown_mentions_mixed_basis_warning(self) -> None:
        markdown = board.render_markdown(
            {
                "generated_at": "2026-04-16T05:55:00+00:00",
                "readiness": "shadow_book_negative",
                "summary": {
                    "live_total_booked_usd": 12.34,
                    "live_lane_count": 1,
                    "shadow_lattice_total_booked_proxy_usd": -20.0,
                    "shadow_lattice_active_total_booked_proxy_usd": -2.0,
                    "shadow_fx_total_booked_proxy_usd": -10.0,
                    "shadow_fx_active_total_booked_proxy_usd": 1.0,
                    "shadow_crypto_total_booked_proxy_usd": -10.0,
                    "shadow_crypto_active_total_booked_proxy_usd": -3.0,
                    "shadow_coinbase_total_booked_usd": 5.0,
                    "combined_shadow_total_mixed_basis_usd": -15.0,
                    "combined_shadow_active_plus_coinbase_mixed_basis_usd": 3.0,
                },
                "methodology": {
                    "live": "exact",
                    "shadow_lattice": "proxy",
                    "shadow_coinbase": "exact",
                },
                "read_rules": ["rule one"],
                "live": {
                    "rows": [
                        {"lane": "live_one", "kind": "live_fx", "booked_usd": 12.34, "close_count": 1, "open_count": 0, "watchdog_status": "ok"}
                    ]
                },
                "shadow_lattice": {
                    "fx_top": [],
                    "fx_bottom": [],
                    "crypto_top": [],
                    "crypto_bottom": [],
                },
                "shadow_coinbase": {
                    "top": [],
                    "bottom": [],
                },
            }
        )

        self.assertIn("Booked P/L Breakdown Board", markdown)
        self.assertIn("Mixed-basis combined shadow total", markdown)
        self.assertIn("Live booked P/L", markdown)
        self.assertIn("rule one", markdown)
        self.assertIn("| `live_one` | `live_fx` | `+12.34` | `1` | `0` | `ok` |", markdown)


if __name__ == "__main__":
    unittest.main()
