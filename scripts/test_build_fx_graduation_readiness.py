#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_fx_graduation_readiness as readiness


class FxGraduationReadinessTests(unittest.TestCase):
    def test_build_live_row_uses_current_alpha_and_audit_posture(self) -> None:
        state = {
            "metadata": {"raw_close_alpha": 0.5, "raw_rearm_cooldown_bars": 12, "session_gate": True},
            "runner": {"heartbeat_at": "2026-04-13T21:12:00+00:00", "session_gated": True, "gated_hour": 23},
            "symbols": {
                "EURUSD": {"realized_closes": 48, "realized_net_usd": 52.89, "open_tickets": [1, 2]},
                "GBPUSD": {"realized_closes": 94, "realized_net_usd": 148.55, "open_tickets": [1, 2, 3, 4]},
            },
        }
        audit_payload = {
            "summary": {
                "revert_is_thin_sample": True,
                "prior_window_alpha": 1.0,
                "prior_window_close_count": 3,
                "prior_window_close_net_usd": -0.27,
                "current_window_close_count": 0,
                "next_gate": "accumulate_post_revert_sample",
            }
        }
        session_gate = {
            "recovered": 3513.21,
            "off_count": 193,
            "good_total": -0.13,
            "off_total": -3513.21,
        }

        with (
            patch.object(readiness, "load_json", side_effect=[state, audit_payload]),
            patch.object(readiness, "load_session_gating_summary", return_value=session_gate),
        ):
            row = readiness.build_live_row()

        self.assertEqual(row["shape"], "raw_stateful_rearm alpha=0.5 cooldown=12")
        self.assertEqual(row["next_gate"], "next_good_session_window")
        self.assertIn("prior alpha=1.0 window was only 3 closes / $-0.27", row["evidence"])
        self.assertIn("off-session FX cost about $3513.21 across 193 trades", row["evidence"])
        self.assertIn("lane is currently idling cleanly under session gate at 23:00 UTC", row["evidence"])
        self.assertIn("provisional_alpha_audit", row["operator_posture"])
        self.assertIn("session_gate_recommended", row["operator_posture"])
        self.assertIn("session_gate_enabled", row["operator_posture"])
        self.assertIn("gated_now=yes", row["operator_posture"])

    def test_build_gbp_row_surfaces_progress_when_durable_proof_exists(self) -> None:
        state = {
            "runner": {"heartbeat_at": "2026-04-13T19:45:00+00:00"},
            "durable_proof": {
                "durable_realized_closes": 31,
                "durable_realized_net_usd": 4.23,
            },
            "symbols": {
                "GBPUSD": {
                    "open_tickets": [{"direction": "SELL"}] * 41,
                    "realized_closes": 31,
                    "realized_net_usd": 4.23,
                    "floating_net_usd": -49.31,
                }
            },
        }
        report_text = "| Marked Net (USD) | $-49.31 |\n"

        with (
            patch.object(readiness, "load_json", return_value=state),
            patch.object(readiness, "load_text", return_value=report_text),
        ):
            row = readiness.build_gbp_row()

        self.assertEqual(row["readiness"], "shadow_proof_positive")
        self.assertEqual(row["gate_status"], "counting_clean_closes")
        self.assertEqual(row["progress_label"], "31/20 durable closes")
        self.assertEqual(row["progress_pct"], "155.0%")
        self.assertEqual(row["lane_status"], "running")
        self.assertIn("41 open SELL", row["operator_posture"])
        self.assertIn("durable proof ledger records 31 tick-native closes for $+4.23", row["evidence"])

    def test_build_gbp_row_marks_negative_forward_sample_as_not_proof_positive(self) -> None:
        state = {
            "runner": {"heartbeat_at": "2026-04-16T02:04:00+00:00"},
            "durable_proof": {
                "durable_realized_closes": 7313,
                "durable_realized_net_usd": -1932.51,
            },
            "symbols": {
                "GBPUSD": {
                    "open_tickets": [{"direction": "SELL"}] * 4,
                    "realized_closes": 7313,
                    "realized_net_usd": -1932.51,
                    "floating_net_usd": 0.88,
                }
            },
        }
        report_text = "| Marked Net (USD) | $-1931.63 |\n"

        with (
            patch.object(readiness, "load_json", return_value=state),
            patch.object(readiness, "load_text", return_value=report_text),
        ):
            row = readiness.build_gbp_row()

        self.assertEqual(row["readiness"], "shadow_net_negative")
        self.assertEqual(row["gate_status"], "net_negative_forward_sample")
        self.assertEqual(row["next_gate"], "decide_kill_or_closure_diagnosis")
        self.assertIn("Do not promote", row["recommendation"])

    def test_build_live_row_handles_partial_provisional_audit(self) -> None:
        state = {
            "metadata": {"raw_close_alpha": 0.5, "raw_rearm_cooldown_bars": 12},
            "runner": {"heartbeat_at": "2026-04-13T21:12:00+00:00"},
            "symbols": {
                "EURUSD": {"realized_closes": 1, "realized_net_usd": 0.5, "open_tickets": []},
                "GBPUSD": {"realized_closes": 1, "realized_net_usd": 0.5, "open_tickets": []},
            },
        }
        audit_payload = {
            "summary": {
                "revert_is_thin_sample": True,
                "prior_window_close_count": 3,
                "current_window_close_count": 0,
            }
        }

        with (
            patch.object(readiness, "load_json", side_effect=[state, audit_payload]),
            patch.object(readiness, "load_session_gating_summary", return_value={}),
        ):
            row = readiness.build_live_row()

        self.assertIn("prior alpha=unknown window was only 3 closes / unknown", row["evidence"])

    def test_build_payload_carries_watch_lead_progress(self) -> None:
        rows = [
            {
                "candidate": "live_rearm_941777 conservative package",
                "readiness": "live",
                "progress_label": "graduated",
                "progress_pct": "100.0%",
                "operator_posture": "running",
                "recommendation": "keep live",
            },
            {
                "candidate": "GBPUSD macro geometry winner",
                "readiness": "shadow_net_negative",
                "progress_label": "31/20 durable closes",
                "progress_pct": "155.0%",
                "operator_posture": "running; 23 open SELL",
                "recommendation": "do not promote",
            },
            {
                "candidate": "EURUSD macro geometry winner",
                "readiness": "rejected_current_regime",
                "progress_label": "failed",
                "progress_pct": "-",
                "operator_posture": "not_running",
                "recommendation": "reject",
            },
            {
                "candidate": "NZDUSD low-step retune",
                "readiness": "rejected_realism",
                "progress_label": "failed",
                "progress_pct": "-",
                "operator_posture": "not_running",
                "recommendation": "reject",
            },
            {
                "candidate": "symbol-specific close-policy map",
                "readiness": "shadow_net_negative",
                "progress_label": "763/20 forward closes",
                "progress_pct": "3815.0%",
                "operator_posture": "running; 10 open",
                "recommendation": "do not promote ungated mixed lane",
            },
            {
                "candidate": "symbol-specific close-policy map + session gate",
                "readiness": "shadow_collecting",
                "progress_label": "armed for next good session",
                "progress_pct": "0.0%",
                "operator_posture": "running; session_gate=on",
                "recommendation": "keep gated proof running",
            },
        ]
        with (
            patch.object(readiness, "build_live_row", return_value=rows[0]),
            patch.object(readiness, "build_gbp_row", return_value=rows[1]),
            patch.object(readiness, "build_eur_row", return_value=rows[2]),
            patch.object(readiness, "build_nzd_row", return_value=rows[3]),
            patch.object(readiness, "build_close_policy_row", return_value=rows[4]),
            patch.object(readiness, "build_close_policy_session_gated_row", return_value=rows[5]),
            patch.object(
                readiness,
                "load_json",
                return_value={
                    "metadata": {"session_gate": True},
                    "runner": {"session_gated": True, "gated_hour": 23},
                },
            ),
            patch.object(
                readiness,
                "load_session_gating_summary",
                return_value={
                    "recovered": 3513.21,
                    "off_count": 193,
                    "good_total": -0.13,
                    "off_total": -3513.21,
                },
            ),
            patch.object(readiness, "utc_now_iso", return_value="2026-04-13T19:55:00+00:00"),
        ):
            payload = readiness.build_payload()

        self.assertEqual(payload["summary"]["live_rows"], 1)
        self.assertEqual(payload["summary"]["shadow_candidate_rows"], 3)
        self.assertEqual(payload["summary"]["rejected_rows"], 2)
        self.assertEqual(payload["summary"]["blocked_rows"], 0)
        self.assertEqual(payload["watch_lead"]["candidate"], "symbol-specific close-policy map + session gate")
        self.assertEqual(payload["watch_lead"]["progress_label"], "armed for next good session")
        self.assertEqual(payload["watch_lead"]["progress_pct"], "0.0%")
        self.assertTrue(any("net-negative" in line for line in payload["current_read"]))
        self.assertTrue(any("ungated mixed EUR/GBP close-policy lane is also net-negative" in line for line in payload["current_read"]))
        self.assertTrue(any("FX session gating is now the clearest execution fix" in line for line in payload["current_read"]))
        self.assertTrue(any("session-gated mixed close-policy proof lane" in line for line in payload["current_read"]))
        self.assertTrue(any("live FX reference lane is already restarted with session gating" in line for line in payload["current_read"]))

    def test_build_close_policy_row_marks_shadow_launch_ready_when_not_running(self) -> None:
        with patch.object(readiness, "load_json", return_value={}):
            row = readiness.build_close_policy_row()

        self.assertEqual(row["lane_name"], "shadow_fx_close_policy_mixed")
        self.assertEqual(row["readiness"], "shadow_launch_ready")
        self.assertEqual(row["gate_status"], "ready_for_shadow_launch")
        self.assertEqual(row["next_gate"], "launch_supervised_shadow_lane")

    def test_build_close_policy_row_surfaces_running_mixed_lane(self) -> None:
        state = {
            "runner": {"heartbeat_at": "2026-04-13T23:01:00+00:00"},
            "symbols": {
                "EURUSD": {"realized_closes": 2, "realized_net_usd": 1.25, "open_tickets": [1]},
                "GBPUSD": {"realized_closes": 3, "realized_net_usd": 2.75, "open_tickets": [1, 2]},
            },
        }

        with patch.object(readiness, "load_json", return_value=state):
            row = readiness.build_close_policy_row()

        self.assertEqual(row["readiness"], "shadow_collecting")
        self.assertEqual(row["lane_status"], "running")
        self.assertEqual(row["progress_label"], "5/20 forward closes")
        self.assertIn("$+4.00", row["evidence"])

    def test_build_close_policy_row_marks_mature_negative_sample(self) -> None:
        state = {
            "runner": {"heartbeat_at": "2026-04-16T02:08:41+00:00"},
            "symbols": {
                "EURUSD": {"realized_closes": 118, "realized_net_usd": -14.72, "open_tickets": [1, 2, 3, 4, 5, 6]},
                "GBPUSD": {"realized_closes": 645, "realized_net_usd": -80.82, "open_tickets": [1, 2, 3, 4]},
            },
        }

        with patch.object(readiness, "load_json", return_value=state):
            row = readiness.build_close_policy_row()

        self.assertEqual(row["readiness"], "shadow_net_negative")
        self.assertEqual(row["gate_status"], "net_negative_forward_sample")
        self.assertEqual(row["next_gate"], "prefer_session_gated_variant_or_new_shape")
        self.assertIn("Do not promote the ungated mixed lane", row["recommendation"])

    def test_build_close_policy_session_gated_row_surfaces_gated_idle_lane(self) -> None:
        state = {
            "metadata": {"session_gate": True},
            "runner": {
                "heartbeat_at": "2026-04-13T23:22:47+00:00",
                "session_gated": True,
                "gated_hour": 23,
            },
            "symbols": {
                "EURUSD": {"realized_closes": 0, "realized_net_usd": 0.0, "open_tickets": []},
                "GBPUSD": {"realized_closes": 0, "realized_net_usd": 0.0, "open_tickets": []},
            },
        }

        with patch.object(readiness, "load_json", return_value=state):
            row = readiness.build_close_policy_session_gated_row()

        self.assertEqual(row["lane_name"], "shadow_fx_close_policy_mixed_session_gated")
        self.assertEqual(row["readiness"], "shadow_collecting")
        self.assertEqual(row["gate_status"], "waiting_good_session_window")
        self.assertEqual(row["progress_label"], "armed for next good session")
        self.assertEqual(row["next_gate"], "first_good_session_ticks")
        self.assertIn("off-session hour 23:00 UTC is being skipped cleanly", row["evidence"])


if __name__ == "__main__":
    unittest.main()
