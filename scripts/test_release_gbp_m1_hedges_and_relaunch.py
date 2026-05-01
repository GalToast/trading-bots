#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
OPERATORS_DIR = SCRIPTS_DIR / "operators"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(OPERATORS_DIR) not in sys.path:
    sys.path.insert(0, str(OPERATORS_DIR))

import release_gbp_m1_hedges_and_relaunch as runner


class ReleaseGbpM1HedgesAndRelaunchTests(unittest.TestCase):
    def test_market_closed_detection(self) -> None:
        result = SimpleNamespace(stdout="retcode=10018(TRADE_RETCODE_MARKET_CLOSED)", stderr="")
        self.assertTrue(runner.output_mentions_market_closed(result))

    def test_dry_run_mode_prints_next_commands_without_apply(self) -> None:
        calls: list[list[str]] = []

        def fake_run(argv: list[str], **_: object) -> SimpleNamespace:
            calls.append(list(argv))
            return SimpleNamespace(returncode=0, stdout="mode=dry_run matched_positions=24\n", stderr="")

        with patch("release_gbp_m1_hedges_and_relaunch.run_command", side_effect=fake_run):
            with patch(
                "release_gbp_m1_hedges_and_relaunch.parse_args",
                return_value=SimpleNamespace(
                    apply=False,
                    skip_refresh=False,
                    retry_market_closed_seconds=0.0,
                    max_wait_seconds=None,
                ),
            ):
                rc = runner.main()

        self.assertEqual(rc, 0)
        self.assertEqual(calls, [runner.CLOSE_DRY_RUN])

    def test_apply_mode_returns_market_closed_status_without_relaunch(self) -> None:
        calls: list[list[str]] = []
        results = [
            SimpleNamespace(returncode=0, stdout="mode=dry_run matched_positions=24\n", stderr=""),
            SimpleNamespace(
                returncode=1,
                stdout="close ticket=1 ok=false detail=retcode=10018(TRADE_RETCODE_MARKET_CLOSED) comment=Market closed\n",
                stderr="",
            ),
        ]

        def fake_run(argv: list[str], **_: object) -> SimpleNamespace:
            calls.append(list(argv))
            return results[len(calls) - 1]

        with patch("release_gbp_m1_hedges_and_relaunch.run_command", side_effect=fake_run):
            with patch(
                "release_gbp_m1_hedges_and_relaunch.parse_args",
                return_value=SimpleNamespace(
                    apply=True,
                    skip_refresh=False,
                    retry_market_closed_seconds=0.0,
                    max_wait_seconds=None,
                ),
            ):
                rc = runner.main()

        self.assertEqual(rc, 3)
        self.assertEqual(calls, [runner.CLOSE_DRY_RUN, runner.CLOSE_APPLY])

    def test_apply_mode_retries_market_closed_then_succeeds(self) -> None:
        calls: list[list[str]] = []
        results = [
            SimpleNamespace(returncode=0, stdout="mode=dry_run matched_positions=24\n", stderr=""),
            SimpleNamespace(
                returncode=1,
                stdout="close ticket=1 ok=false detail=retcode=10018(TRADE_RETCODE_MARKET_CLOSED) comment=Market closed\n",
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="mode=dry_run matched_positions=24\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="mode=apply matched_positions=24\npost_apply_remaining_matches=0\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="mode=dry_run matched_positions=0\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="microharvest relaunched\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="hybrid relaunched\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="exec report refreshed\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="dashboard refreshed\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="memory refreshed\n", stderr=""),
        ]

        def fake_run(argv: list[str], **_: object) -> SimpleNamespace:
            calls.append(list(argv))
            return results[len(calls) - 1]

        with patch("release_gbp_m1_hedges_and_relaunch.run_command", side_effect=fake_run):
            with patch("release_gbp_m1_hedges_and_relaunch.time.sleep") as sleep_mock:
                with patch(
                    "release_gbp_m1_hedges_and_relaunch.parse_args",
                    return_value=SimpleNamespace(
                        apply=True,
                        skip_refresh=False,
                        retry_market_closed_seconds=5.0,
                        max_wait_seconds=30.0,
                    ),
                ):
                    rc = runner.main()

        self.assertEqual(rc, 0)
        sleep_mock.assert_called_once_with(5.0)
        self.assertEqual(
            calls,
            [
                runner.CLOSE_DRY_RUN,
                runner.CLOSE_APPLY,
                runner.CLOSE_DRY_RUN,
                runner.CLOSE_APPLY,
                runner.CLOSE_DRY_RUN,
                runner.MICROHARVEST_RELAUNCH,
                runner.HYBRID_RELAUNCH,
                *runner.REFRESH_COMMANDS,
            ],
        )

    def test_apply_mode_market_closed_timeout_skips_relaunch(self) -> None:
        calls: list[list[str]] = []
        results = [
            SimpleNamespace(returncode=0, stdout="mode=dry_run matched_positions=24\n", stderr=""),
            SimpleNamespace(
                returncode=1,
                stdout="close ticket=1 ok=false detail=retcode=10018(TRADE_RETCODE_MARKET_CLOSED) comment=Market closed\n",
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="mode=dry_run matched_positions=24\n", stderr=""),
            SimpleNamespace(
                returncode=1,
                stdout="close ticket=1 ok=false detail=retcode=10018(TRADE_RETCODE_MARKET_CLOSED) comment=Market closed\n",
                stderr="",
            ),
        ]

        def fake_run(argv: list[str], **_: object) -> SimpleNamespace:
            calls.append(list(argv))
            return results[len(calls) - 1]

        monotonic_values = iter([100.0, 100.0, 106.0])

        with patch("release_gbp_m1_hedges_and_relaunch.run_command", side_effect=fake_run):
            with patch("release_gbp_m1_hedges_and_relaunch.time.sleep") as sleep_mock:
                with patch(
                    "release_gbp_m1_hedges_and_relaunch.time.monotonic",
                    side_effect=lambda: next(monotonic_values),
                ):
                    with patch(
                        "release_gbp_m1_hedges_and_relaunch.parse_args",
                        return_value=SimpleNamespace(
                            apply=True,
                            skip_refresh=False,
                            retry_market_closed_seconds=5.0,
                            max_wait_seconds=5.0,
                        ),
                    ):
                        rc = runner.main()

        self.assertEqual(rc, 3)
        sleep_mock.assert_called_once_with(5.0)
        self.assertEqual(
            calls,
            [
                runner.CLOSE_DRY_RUN,
                runner.CLOSE_APPLY,
                runner.CLOSE_DRY_RUN,
                runner.CLOSE_APPLY,
            ],
        )


if __name__ == "__main__":
    unittest.main()
