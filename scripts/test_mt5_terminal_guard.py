#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import mt5_terminal_guard as guard

TEST_MT5_LOGIN = 100001


class FakeMt5:
    def __init__(
        self,
        *,
        login: int = TEST_MT5_LOGIN,
        server: str = "Hugosway-Demo",
        terminal_path: str = r"C:\Program Files\Hugosway\Hugosway PRO5 Terminal",
        trade_allowed: bool = True,
        initialize_ok: bool = True,
    ) -> None:
        self.login = login
        self.server = server
        self.terminal_path = terminal_path
        self.trade_allowed = trade_allowed
        self.initialize_ok = initialize_ok
        self.initialize_kwargs: dict[str, object] | None = None
        self.shutdown_called = False

    def initialize(self, **kwargs):
        self.initialize_kwargs = dict(kwargs)
        return self.initialize_ok

    def account_info(self):
        return SimpleNamespace(login=self.login, server=self.server)

    def terminal_info(self):
        return SimpleNamespace(path=self.terminal_path, trade_allowed=self.trade_allowed, connected=True)

    def shutdown(self):
        self.shutdown_called = True

    def last_error(self):
        return (1, "ok")


class Mt5TerminalGuardTests(unittest.TestCase):
    def test_initialize_mt5_uses_env_contract_and_accepts_matching_identity(self) -> None:
        fake_mt5 = FakeMt5()
        with mock.patch.dict(
            os.environ,
            {
                "MT5_LOGIN": str(TEST_MT5_LOGIN),
                "MT5_PASSWORD": "secret",
                "MT5_SERVER": "Hugosway-Demo",
                "MT5_TERMINAL_PATH": r"C:\Program Files\Hugosway\Hugosway PRO5 Terminal\terminal64.exe",
            },
            clear=False,
        ):
            ok, payload = guard.initialize_mt5(mt5_module=fake_mt5, require_trade_allowed=True)

        self.assertTrue(ok)
        self.assertEqual(fake_mt5.initialize_kwargs["login"], TEST_MT5_LOGIN)
        self.assertEqual(fake_mt5.initialize_kwargs["server"], "Hugosway-Demo")
        self.assertEqual(fake_mt5.initialize_kwargs["path"], r"C:\Program Files\Hugosway\Hugosway PRO5 Terminal\terminal64.exe")
        self.assertEqual(payload["reason"], "ok")
        self.assertTrue(payload["identity_ok"])
        self.assertEqual(payload["contract"]["binding_mode"], "path_pinned")

    def test_initialize_mt5_rejects_terminal_path_mismatch(self) -> None:
        fake_mt5 = FakeMt5(terminal_path=r"C:\Wrong Terminal")
        with mock.patch.dict(
            os.environ,
            {
                "MT5_LOGIN": str(TEST_MT5_LOGIN),
                "MT5_PASSWORD": "secret",
                "MT5_SERVER": "Hugosway-Demo",
                "MT5_TERMINAL_PATH": r"C:\Program Files\Hugosway\Hugosway PRO5 Terminal\terminal64.exe",
            },
            clear=False,
        ):
            ok, payload = guard.initialize_mt5(mt5_module=fake_mt5, require_trade_allowed=True)

        self.assertFalse(ok)
        self.assertEqual(payload["reason"], "identity_mismatch")
        self.assertIn("terminal_path_mismatch", payload["identity_mismatches"])
        self.assertTrue(fake_mt5.shutdown_called)


if __name__ == "__main__":
    unittest.main()
