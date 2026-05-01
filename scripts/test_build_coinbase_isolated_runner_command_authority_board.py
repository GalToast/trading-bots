#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_isolated_runner_command_authority_board as board


class CoinbaseIsolatedRunnerCommandAuthorityBoardTests(unittest.TestCase):
    def test_payload_emits_expected_verdict(self) -> None:
        payload = board.build_payload()

        self.assertEqual(payload["summary"]["verdict"], "probe_commands_allowed_live_commands_blocked")
        self.assertEqual(len(payload["rows"]), 8)

    def test_default_deploy_helper_is_blocked(self) -> None:
        rows = {row["command_name"]: row for row in board.build_rows()}
        deploy = rows["default_deploy_helper"]

        self.assertEqual(deploy["status"], "blocked")
        self.assertEqual(deploy["authority"], "do_not_launch")

    def test_tru_supervised_probe_is_probe_only(self) -> None:
        rows = {row["command_name"]: row for row in board.build_rows()}
        tru = rows["tru_supervised_probe"]

        self.assertEqual(tru["status"], "allowed_bounded_probe_only")
        self.assertIn("TRU-USD", tru["command"])

    def test_nom_probe_is_deferred(self) -> None:
        rows = {row["command_name"]: row for row in board.build_rows()}
        nom = rows["nom_supervised_probe"]

        self.assertEqual(nom["status"], "deferred")
        self.assertEqual(nom["authority"], "wait_for_handoff")


if __name__ == "__main__":
    unittest.main()
