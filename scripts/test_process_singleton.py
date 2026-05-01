#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import process_singleton as singleton


class ProcessSingletonTests(unittest.TestCase):
    def test_second_acquire_is_blocked_while_lock_owner_is_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "runner.lock"
            first = singleton.acquire_singleton(lock_path, scope="demo")
            self.assertTrue(first.acquired)
            try:
                second = singleton.acquire_singleton(lock_path, scope="demo")
                self.assertFalse(second.acquired)
                self.assertEqual(second.owner_pid, os.getpid())
            finally:
                first.release()

    def test_stale_lock_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "runner.lock"
            lock_path.write_text(
                json.dumps({"pid": 999999, "scope": "demo", "acquired_at": "2026-04-13T00:00:00+00:00"}),
                encoding="utf-8",
            )
            original_process_alive = singleton.process_alive
            try:
                singleton.process_alive = lambda pid: False  # type: ignore[assignment]
                lease = singleton.acquire_singleton(lock_path, scope="demo")
            finally:
                singleton.process_alive = original_process_alive  # type: ignore[assignment]

            self.assertTrue(lease.acquired)
            self.assertEqual(lease.owner_pid, os.getpid())
            payload = singleton.read_lock(lock_path)
            self.assertEqual(payload["pid"], os.getpid())
            lease.release()

    def test_release_keeps_lock_when_current_process_is_not_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "runner.lock"
            lock_path.write_text(
                json.dumps({"pid": os.getpid() + 1, "scope": "other", "acquired_at": "2026-04-13T00:00:00+00:00"}),
                encoding="utf-8",
            )
            lease = singleton.SingletonLease(lock_path=lock_path, acquired=True, owner_pid=os.getpid() + 1)
            lease.release()
            self.assertTrue(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
