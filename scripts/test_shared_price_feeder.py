#!/usr/bin/env python3
"""Tests for shared price feeder."""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT / "scripts"))

import shared_price_feeder as feeder


class TestAtomicWrite(unittest.TestCase):
    def test_writes_json(self):
        test_path = ROOT / "reports" / "test_atomic_write.json"
        feeder._atomic_write(test_path, {"a": 1, "b": 2})
        data = json.loads(test_path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"a": 1, "b": 2})
        test_path.unlink(missing_ok=True)

    def test_is_atomic(self):
        """File should exist at target path, not .tmp."""
        test_path = ROOT / "reports" / "test_atomic_write2.json"
        tmp_path = test_path.with_suffix(".json.tmp")
        feeder._atomic_write(test_path, {"x": 99})
        self.assertTrue(test_path.exists())
        self.assertFalse(tmp_path.exists())
        test_path.unlink(missing_ok=True)

    def test_retries_transient_replace_lock(self):
        test_path = ROOT / "reports" / "test_atomic_write_retry.json"
        original_replace = feeder.os.replace
        calls = {"count": 0}

        def flaky_replace(src, dst):
            calls["count"] += 1
            if calls["count"] < 3:
                raise PermissionError("[WinError 5] Access is denied")
            return original_replace(src, dst)

        with mock.patch("shared_price_feeder.os.replace", side_effect=flaky_replace):
            feeder._atomic_write(test_path, {"retry": True})

        data = json.loads(test_path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"retry": True})
        self.assertEqual(calls["count"], 3)
        test_path.unlink(missing_ok=True)

    @mock.patch("shared_price_feeder.print")
    def test_best_effort_atomic_write_swallows_persistent_lock(self, mock_print):
        test_path = ROOT / "reports" / "test_atomic_write_best_effort.json"

        with mock.patch("shared_price_feeder._atomic_write", side_effect=PermissionError("[WinError 5] Access is denied")):
            ok = feeder._best_effort_atomic_write(test_path, {"ignored": True}, label="shared_price_cache")

        self.assertFalse(ok)
        mock_print.assert_called_once()


class TestReadPrice(unittest.TestCase):
    def setUp(self):
        """Create a fresh cache with known data."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.temp_dir.name) / "shared_price_cache.json"
        self.tick_cache_path = Path(self.temp_dir.name) / "shared_tick_cache.json"
        self.cache_patch = mock.patch.object(feeder, "CACHE_PATH", self.cache_path)
        self.tick_cache_patch = mock.patch.object(feeder, "TICK_CACHE_PATH", self.tick_cache_path)
        self.cache_patch.start()
        self.tick_cache_patch.start()
        self.test_cache = {
            "EURUSD": {
                "bid": 1.1234,
                "ask": 1.1235,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
            "GBPUSD": {
                "bid": 1.3456,
                "ask": 1.3457,
                "ts": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
            },
        }
        feeder._atomic_write(feeder.CACHE_PATH, self.test_cache)
        now = datetime.now(timezone.utc)
        self.test_tick_cache = {
            "EURUSD": [
                {
                    "time": int((now - timedelta(milliseconds=900)).timestamp()),
                    "time_msc": int((now - timedelta(milliseconds=900)).timestamp() * 1000),
                    "bid": 1.1233,
                    "ask": 1.1234,
                    "last": 0.0,
                    "flags": 0,
                    "volume": 0,
                    "volume_real": 0.0,
                },
                {
                    "time": int((now - timedelta(milliseconds=300)).timestamp()),
                    "time_msc": int((now - timedelta(milliseconds=300)).timestamp() * 1000),
                    "bid": 1.1234,
                    "ask": 1.1235,
                    "last": 0.0,
                    "flags": 0,
                    "volume": 0,
                    "volume_real": 0.0,
                },
            ],
            "GBPUSD": [
                {
                    "time": int((now - timedelta(seconds=5)).timestamp()),
                    "time_msc": int((now - timedelta(seconds=5)).timestamp() * 1000),
                    "bid": 1.3456,
                    "ask": 1.3457,
                    "last": 0.0,
                    "flags": 0,
                    "volume": 0,
                    "volume_real": 0.0,
                }
            ],
        }
        feeder._atomic_write(feeder.TICK_CACHE_PATH, self.test_tick_cache)

    def tearDown(self):
        """Clean up test cache."""
        self.cache_patch.stop()
        self.tick_cache_patch.stop()
        self.temp_dir.cleanup()

    @mock.patch("shared_price_feeder.HAS_MT5", False)
    def test_reads_fresh_cache(self):
        """Should return price when cache is fresh."""
        price = feeder.read_price("EURUSD", max_age_ms=1000)
        self.assertIsNotNone(price)
        self.assertAlmostEqual(price["bid"], 1.1234, places=4)
        self.assertAlmostEqual(price["ask"], 1.1235, places=4)

    @mock.patch("shared_price_feeder.HAS_MT5", True)
    @mock.patch("shared_price_feeder.mt5")
    def test_read_cached_price_never_falls_back_to_mt5(self, mock_mt5):
        """Cache-only reads should not touch MT5 when the cache is stale or missing."""
        price = feeder.read_cached_price("BTCUSD", max_age_ms=1000)
        self.assertIsNone(price)
        mock_mt5.symbol_info_tick.assert_not_called()

    @mock.patch("shared_price_feeder.HAS_MT5", False)
    def test_stale_cache_returns_none(self):
        """Should return None when cache is too old."""
        price = feeder.read_price("GBPUSD", max_age_ms=1000)
        # GBPUSD entry is 5 seconds old, max_age is 1 second
        self.assertIsNone(price)

    @mock.patch("shared_price_feeder.HAS_MT5", False)
    def test_missing_symbol_returns_none(self):
        """Should return None for symbol not in cache."""
        price = feeder.read_price("BTCUSD", max_age_ms=1000)
        self.assertIsNone(price)

    def test_reads_cached_ticks_since_when_covered(self):
        ticks = feeder.read_cached_ticks_since("EURUSD", self.test_tick_cache["EURUSD"][0]["time_msc"], max_age_ms=1000, lookback_seconds=120)
        self.assertIsNotNone(ticks)
        self.assertEqual(len(ticks), 1)
        self.assertAlmostEqual(ticks[0]["bid"], 1.1234, places=4)

    def test_cached_ticks_since_returns_empty_when_no_new_ticks_and_cache_is_fresh(self):
        last_tick_msc = self.test_tick_cache["EURUSD"][-1]["time_msc"]
        ticks = feeder.read_cached_ticks_since("EURUSD", last_tick_msc, max_age_ms=1000, lookback_seconds=120)
        self.assertEqual(ticks, [])

    def test_cached_ticks_since_returns_none_when_history_does_not_cover_request(self):
        uncovered_last_tick_msc = self.test_tick_cache["EURUSD"][0]["time_msc"] - 500
        ticks = feeder.read_cached_ticks_since("EURUSD", uncovered_last_tick_msc, max_age_ms=1000, lookback_seconds=120)
        self.assertIsNone(ticks)

    def test_cached_ticks_since_returns_none_when_history_is_stale(self):
        ticks = feeder.read_cached_ticks_since("GBPUSD", 0, max_age_ms=1000, lookback_seconds=1)
        self.assertIsNone(ticks)

    @mock.patch("shared_price_feeder.HAS_MT5", True)
    @mock.patch("shared_price_feeder.mt5")
    def test_fallback_to_mt5(self, mock_mt5):
        """Should fall back to MT5 when cache is stale."""
        mock_tick = mock.Mock()
        mock_tick.bid = 74000.0
        mock_tick.ask = 74100.0
        mock_mt5.symbol_info_tick.return_value = mock_tick

        price = feeder.read_price("BTCUSD", max_age_ms=1000)
        self.assertIsNotNone(price)
        self.assertEqual(price["bid"], 74000.0)
        self.assertEqual(price["ask"], 74100.0)

    def test_cache_file_not_exists(self):
        """Should handle missing cache file gracefully."""
        if feeder.CACHE_PATH.exists():
            feeder.CACHE_PATH.unlink(missing_ok=True)
        price = feeder.read_price("EURUSD", max_age_ms=1000)
        # Without MT5, should return None
        # With MT5, should fall back to direct call
        # Either way, should not crash
        self.assertIsInstance(price, (dict, type(None)))


class TestMainMt5Guard(unittest.TestCase):
    def setUp(self):
        self.original_shutting_down = feeder._shutting_down
        feeder._shutting_down = False

    def tearDown(self):
        feeder._shutting_down = self.original_shutting_down

    @mock.patch("shared_price_feeder.signal.signal")
    @mock.patch("shared_price_feeder.print")
    def test_main_returns_guard_failure_when_mt5_identity_check_fails(self, mock_print, _mock_signal):
        fake_guard = SimpleNamespace(
            initialize_mt5=mock.Mock(return_value=(False, {"reason": "identity_mismatch"})),
            failure_summary=mock.Mock(return_value="MT5 connection guard failed: terminal_path_mismatch"),
        )

        with mock.patch.object(feeder, "HAS_MT5", True), \
             mock.patch.object(feeder, "mt5_terminal_guard", fake_guard):
            result = feeder.main()

        self.assertEqual(result, 1)
        fake_guard.initialize_mt5.assert_called_once_with(mt5_module=feeder.mt5)
        fake_guard.failure_summary.assert_called_once()
        mock_print.assert_any_call("MT5 connection guard failed: terminal_path_mismatch")

    @mock.patch("shared_price_feeder.signal.signal")
    @mock.patch("shared_price_feeder.print")
    @mock.patch("shared_price_feeder.time.sleep")
    @mock.patch("shared_price_feeder._best_effort_atomic_write")
    def test_main_uses_mt5_guard_before_feed_loop(self, mock_best_effort_write, mock_sleep, mock_print, _mock_signal):
        fake_guard = SimpleNamespace(
            initialize_mt5=mock.Mock(return_value=(True, {"reason": "ok"})),
            failure_summary=mock.Mock(),
        )
        fake_tick = SimpleNamespace(bid=1.1, ask=1.2, volume=3, time=1, time_msc=1000, last=0.0, flags=0, volume_real=0.0)
        fake_mt5 = SimpleNamespace(
            symbol_info_tick=mock.Mock(return_value=fake_tick),
            shutdown=mock.Mock(),
        )

        def stop_after_first_sleep(_seconds):
            feeder._shutting_down = True

        mock_sleep.side_effect = stop_after_first_sleep

        with mock.patch.object(feeder, "HAS_MT5", True), \
             mock.patch.object(feeder, "mt5", fake_mt5), \
             mock.patch.object(feeder, "mt5_terminal_guard", fake_guard), \
             mock.patch.object(feeder, "SYMBOLS", ["EURUSD"]), \
             mock.patch.object(feeder, "_copy_ticks_since", return_value=[]):
            result = feeder.main()

        self.assertEqual(result, 0)
        fake_guard.initialize_mt5.assert_called_once_with(mt5_module=fake_mt5)
        fake_mt5.symbol_info_tick.assert_called_once_with("EURUSD")
        fake_mt5.shutdown.assert_called_once()
        self.assertGreaterEqual(mock_best_effort_write.call_count, 2)
        self.assertFalse(fake_guard.failure_summary.called)

    @mock.patch("shared_price_feeder.signal.signal")
    @mock.patch("shared_price_feeder.print")
    @mock.patch("shared_price_feeder.time.sleep")
    @mock.patch("shared_price_feeder._best_effort_atomic_write")
    def test_main_survives_best_effort_write_failures(self, mock_best_effort_write, mock_sleep, _mock_print, _mock_signal):
        fake_guard = SimpleNamespace(
            initialize_mt5=mock.Mock(return_value=(True, {"reason": "ok"})),
            failure_summary=mock.Mock(),
        )
        fake_tick = SimpleNamespace(bid=1.1, ask=1.2, volume=3, time=1, time_msc=1000, last=0.0, flags=0, volume_real=0.0)
        fake_mt5 = SimpleNamespace(
            symbol_info_tick=mock.Mock(return_value=fake_tick),
            shutdown=mock.Mock(),
        )

        def stop_after_first_sleep(_seconds):
            feeder._shutting_down = True

        mock_sleep.side_effect = stop_after_first_sleep
        mock_best_effort_write.side_effect = [False, False, True]

        with mock.patch.object(feeder, "HAS_MT5", True), \
             mock.patch.object(feeder, "mt5", fake_mt5), \
             mock.patch.object(feeder, "mt5_terminal_guard", fake_guard), \
             mock.patch.object(feeder, "SYMBOLS", ["EURUSD"]), \
             mock.patch.object(feeder, "_copy_ticks_since", return_value=[]):
            result = feeder.main()

        self.assertEqual(result, 0)
        fake_mt5.shutdown.assert_called_once()
        self.assertGreaterEqual(mock_best_effort_write.call_count, 2)


if __name__ == "__main__":
    unittest.main()
