from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import scripts.watch_experimental_proof_board as watch


class WatchExperimentalProofBoardTests(unittest.TestCase):
    @mock.patch("scripts.watch_experimental_proof_board.subprocess.run")
    def test_refresh_surfaces_rebuilds_coverage_board_before_experimental_board(self, mock_run) -> None:
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

        watch.refresh_surfaces()

        commands = [call.args[0] for call in mock_run.call_args_list]
        executed = [command[1] for command in commands]
        self.assertEqual(
            executed,
            [
                str(watch.ETH_BOARD_SCRIPT),
                str(watch.SHAPESHIFTER_BOARD_SCRIPT),
                str(watch.COVERAGE_BOARD_SCRIPT),
                str(watch.EXPERIMENTAL_BOARD_SCRIPT),
            ],
        )

    def test_snapshot_from_payload_extracts_proof_fields(self) -> None:
        snapshot = watch.snapshot_from_payload(
            {
                "overall_status": "waiting_market_proof",
                "next_action": "wait",
                "eth_atr": {
                    "total_realized_closes": 0,
                    "total_open_positions": 0,
                    "total_realized_net_usd": 0.0,
                },
                "shapeshifter": {
                    "proof_status": "historical_box_only",
                    "structure_flip_count_since_runner_start": 0,
                    "realized_closes": 12,
                    "phase1_event_coverage_readiness": "stale_or_pre_enrichment_log",
                    "phase1_event_coverage_next_action": "Rebuild against a fresh post-enrichment runtime log.",
                    "phase1_event_covered_field_count": 0,
                    "phase1_event_field_count": 18,
                    "phase1_close_metric_event_count": 0,
                    "phase1_loss_without_first_green_count": 0,
                    "phase1_first_path_verdict": "awaiting_first_trade_path_event",
                    "phase1_market_state_hypothesis_verdict": "insufficient_fresh_path_evidence",
                },
            }
        )

        self.assertEqual(snapshot["overall_status"], "waiting_market_proof")
        self.assertEqual(snapshot["eth_total_realized_closes"], 0)
        self.assertEqual(snapshot["shapeshifter_proof_status"], "historical_box_only")
        self.assertEqual(snapshot["shapeshifter_realized_closes"], 12)
        self.assertEqual(snapshot["shapeshifter_phase1_event_coverage_readiness"], "stale_or_pre_enrichment_log")
        self.assertEqual(
            snapshot["shapeshifter_phase1_event_coverage_next_action"],
            "Rebuild against a fresh post-enrichment runtime log.",
        )
        self.assertEqual(snapshot["shapeshifter_phase1_event_covered_field_count"], 0)
        self.assertEqual(snapshot["shapeshifter_phase1_close_metric_event_count"], 0)
        self.assertEqual(snapshot["shapeshifter_phase1_first_path_verdict"], "awaiting_first_trade_path_event")
        self.assertEqual(
            snapshot["shapeshifter_phase1_market_state_hypothesis_verdict"],
            "insufficient_fresh_path_evidence",
        )

    def test_diff_messages_detects_eth_and_shapeshifter_progress(self) -> None:
        previous = {
            "overall_status": "waiting_market_proof",
            "eth_total_realized_closes": 0,
            "eth_total_open_positions": 0,
            "shapeshifter_structure_flip_count_since_runner_start": 0,
            "shapeshifter_proof_status": "historical_box_only",
            "shapeshifter_phase1_event_coverage_readiness": "stale_or_pre_enrichment_log",
            "shapeshifter_phase1_event_covered_field_count": 0,
            "shapeshifter_phase1_close_metric_event_count": 0,
            "shapeshifter_phase1_first_path_verdict": "awaiting_first_trade_path_event",
            "shapeshifter_phase1_market_state_hypothesis_verdict": "insufficient_fresh_path_evidence",
        }
        current = {
            "overall_status": "new_runtime_proof_available",
            "eth_total_realized_closes": 1,
            "eth_total_open_positions": 2,
            "shapeshifter_structure_flip_count_since_runner_start": 1,
            "shapeshifter_proof_status": "structure_flip_observed",
            "shapeshifter_phase1_event_coverage_readiness": "post_enrichment_runtime_log",
            "shapeshifter_phase1_event_covered_field_count": 3,
            "shapeshifter_phase1_close_metric_event_count": 1,
            "shapeshifter_phase1_first_path_verdict": "never_green_toxic_continuation",
            "shapeshifter_phase1_market_state_hypothesis_verdict": "repricing_or_toxic_flow_risk",
        }

        messages = watch.diff_messages(previous, current)

        self.assertTrue(any("overall_status" in message for message in messages))
        self.assertTrue(any("eth_total_realized_closes" in message for message in messages))
        self.assertTrue(any("structure_flip_count_since_runner_start" in message for message in messages))
        self.assertTrue(any("shapeshifter_proof_status" in message for message in messages))
        self.assertTrue(any("shapeshifter_phase1_event_coverage_readiness" in message for message in messages))
        self.assertTrue(any("shapeshifter_phase1_event_covered_field_count" in message for message in messages))
        self.assertTrue(any("shapeshifter_phase1_close_metric_event_count" in message for message in messages))
        self.assertTrue(any("shapeshifter_phase1_first_path_verdict" in message for message in messages))
        self.assertTrue(any("shapeshifter_phase1_market_state_hypothesis_verdict" in message for message in messages))

    def test_diff_messages_ignores_missing_previous_phase1_coverage_key_when_value_is_zero(self) -> None:
        previous = {
            "overall_status": "waiting_market_proof",
            "eth_total_realized_closes": 0,
            "eth_total_open_positions": 0,
            "shapeshifter_structure_flip_count_since_runner_start": 0,
            "shapeshifter_proof_status": "historical_box_only",
            "shapeshifter_phase1_event_coverage_readiness": "stale_or_pre_enrichment_log",
        }
        current = {
            "overall_status": "waiting_market_proof",
            "eth_total_realized_closes": 0,
            "eth_total_open_positions": 0,
            "shapeshifter_structure_flip_count_since_runner_start": 0,
            "shapeshifter_proof_status": "historical_box_only",
            "shapeshifter_phase1_event_coverage_readiness": "stale_or_pre_enrichment_log",
            "shapeshifter_phase1_event_covered_field_count": 0,
            "shapeshifter_phase1_close_metric_event_count": 0,
            "shapeshifter_phase1_first_path_verdict": "awaiting_first_trade_path_event",
            "shapeshifter_phase1_market_state_hypothesis_verdict": "insufficient_fresh_path_evidence",
        }

        messages = watch.diff_messages(previous, current)

        self.assertFalse(any("shapeshifter_phase1_event_covered_field_count" in message for message in messages))

    def test_proof_arrived_recognizes_transition_statuses(self) -> None:
        self.assertTrue(watch.proof_arrived({"overall_status": "new_runtime_proof_available"}))
        self.assertTrue(watch.proof_arrived({"overall_status": "new_eth_forward_sample_available"}))
        self.assertFalse(watch.proof_arrived({"overall_status": "waiting_market_proof"}))

    def test_format_switchboard_message_summarizes_changes(self) -> None:
        message = watch.format_switchboard_message(
            [
                "overall_status waiting_market_proof -> new_runtime_proof_available",
                "eth_total_realized_closes 0 -> 1",
            ],
            {
                "overall_status": "new_runtime_proof_available",
                "eth_total_realized_closes": 1,
                "eth_total_open_positions": 2,
                "shapeshifter_structure_flip_count_since_runner_start": 1,
                "shapeshifter_proof_status": "structure_flip_observed",
                "shapeshifter_phase1_event_coverage_readiness": "stale_or_pre_enrichment_log",
                "shapeshifter_phase1_event_coverage_next_action": "Rebuild against a fresh post-enrichment runtime log.",
                "shapeshifter_phase1_event_covered_field_count": 0,
                "shapeshifter_phase1_event_field_count": 18,
                "shapeshifter_phase1_close_metric_event_count": 1,
                "shapeshifter_phase1_loss_without_first_green_count": 1,
                "shapeshifter_phase1_first_path_verdict": "never_green_toxic_continuation",
                "shapeshifter_phase1_market_state_hypothesis_verdict": "repricing_or_toxic_flow_risk",
            },
        )

        self.assertIn("status=new_runtime_proof_available", message)
        self.assertIn("ETH closes=1", message)
        self.assertIn("Changes:", message)
        self.assertIn("shapeshifter phase1 readiness=stale_or_pre_enrichment_log", message)
        self.assertIn("shapeshifter phase1 coverage=0/18", message)
        self.assertIn("shapeshifter phase1 close_metrics=1", message)
        self.assertIn("shapeshifter first_path=never_green_toxic_continuation", message)
        self.assertIn("shapeshifter market_state=repricing_or_toxic_flow_risk", message)
        self.assertIn("fresh post-enrichment runtime log", message)

    @mock.patch("scripts.watch_experimental_proof_board.subprocess.run")
    def test_post_switchboard_message_uses_switchboard_cli(self, mock_run) -> None:
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

        watch.post_switchboard_message(sender="@codex_fx", content="hello")

        command = mock_run.call_args.args[0]
        self.assertEqual(command[1], str(watch.SWITCHBOARD_CLI_SCRIPT))
        self.assertEqual(command[2], "post")
        self.assertIn("--sender", command)
        self.assertIn("@codex_fx", command)
        self.assertIn("hello", command)

    @mock.patch("scripts.watch_experimental_proof_board.write_monitor_state")
    @mock.patch("scripts.watch_experimental_proof_board.load_json")
    @mock.patch("scripts.watch_experimental_proof_board.refresh_surfaces")
    def test_run_once_prints_alert_change(self, _mock_refresh, mock_load_json, _mock_write_state) -> None:
        payload = {
            "generated_at": "2026-04-16T02:50:00+00:00",
            "overall_status": "new_runtime_proof_available",
            "next_action": "review flip",
            "eth_atr": {
                "total_realized_closes": 0,
                "total_open_positions": 0,
                "total_realized_net_usd": 0.0,
            },
            "shapeshifter": {
                "proof_status": "structure_flip_observed",
                "structure_flip_count_since_runner_start": 1,
                "realized_closes": 12,
                "phase1_event_coverage_readiness": "stale_or_pre_enrichment_log",
                "phase1_event_coverage_next_action": "Rebuild against a fresh post-enrichment runtime log.",
                "phase1_event_covered_field_count": 0,
                "phase1_event_field_count": 18,
                "phase1_close_metric_event_count": 0,
                "phase1_loss_without_first_green_count": 0,
                "phase1_first_path_verdict": "awaiting_first_trade_path_event",
                "phase1_market_state_hypothesis_verdict": "insufficient_fresh_path_evidence",
            },
        }
        mock_load_json.return_value = payload
        previous = {
            "overall_status": "waiting_market_proof",
            "eth_total_realized_closes": 0,
            "eth_total_open_positions": 0,
            "shapeshifter_structure_flip_count_since_runner_start": 0,
            "shapeshifter_proof_status": "historical_box_only",
            "shapeshifter_phase1_event_covered_field_count": 0,
            "shapeshifter_phase1_close_metric_event_count": 0,
            "shapeshifter_phase1_first_path_verdict": "awaiting_first_trade_path_event",
            "shapeshifter_phase1_market_state_hypothesis_verdict": "insufficient_fresh_path_evidence",
        }

        buf = io.StringIO()
        with redirect_stdout(buf):
            _payload, changes, snapshot = watch.run_once(previous_snapshot=previous, quiet=False)
        text = buf.getvalue()

        self.assertEqual(snapshot["overall_status"], "new_runtime_proof_available")
        self.assertTrue(any("overall_status" in change for change in changes))
        self.assertIn("ALERT change", text)
        self.assertIn("phase1_readiness=stale_or_pre_enrichment_log", text)
        self.assertIn("phase1_first_path=awaiting_first_trade_path_event", text)
        self.assertIn("phase1_market_state=insufficient_fresh_path_evidence", text)
        self.assertIn("shapeshifter_phase1_next_action", text)


if __name__ == "__main__":
    unittest.main()
