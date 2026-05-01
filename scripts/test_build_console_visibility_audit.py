#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_console_visibility_audit as audit


class ConsoleVisibilityAuditTests(unittest.TestCase):
    def test_payload_emits_quiet_ready_verdict(self) -> None:
        payload = audit.build_payload()

        self.assertEqual(payload["quiet_launch_verdict"], "repo_background_launchers_quiet_ready")
        self.assertGreaterEqual(len(payload["findings"]), 6)
        self.assertIn("WSH hidden wrappers", payload["leadership_read"][0])

    def test_refresh_finding_points_at_hidden_start_process(self) -> None:
        findings = {finding["title"]: finding for finding in audit.build_findings()}
        refresh = findings["Supervisor board refresh now launches builders through a hidden PowerShell host"]

        self.assertEqual(refresh["status"], "patched_quiet")
        self.assertGreater(refresh["line"], 0)

    def test_no_window_finding_has_source_lines(self) -> None:
        findings = {finding["title"]: finding for finding in audit.build_findings()}
        helper = findings["Repo PowerShell helper calls now opt into CREATE_NO_WINDOW"]

        for ref in helper["files"]:
            self.assertGreater(ref["line"], 0)

    def test_launcher_finding_points_at_create_no_window(self) -> None:
        findings = {finding["title"]: finding for finding in audit.build_findings()}
        launcher = findings["Background watchdog launcher hops now use CreateNoWindow process starts"]

        for ref in launcher["files"][:4]:
            self.assertGreater(ref["line"], 0)

    def test_task_wrapper_finding_points_at_hidden_launcher_source(self) -> None:
        findings = {finding["title"]: finding for finding in audit.build_findings()}
        wrapper = findings["Scheduled watchdog and supervisor tasks now launch through WSH hidden wrappers"]

        for ref in wrapper["files"]:
            self.assertGreater(ref["line"], 0)


if __name__ == "__main__":
    unittest.main()
