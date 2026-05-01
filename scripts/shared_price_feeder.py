#!/usr/bin/env python3
"""Shared Price Feeder — Single-process price cache for all lattice lanes.

Subscribes to all symbols used by live + shadow lanes.
Polls MT5 every 250ms and writes atomic JSON updates.
All lanes read from the cache instead of polling MT5 independently.

Benefits:
- 10-20x reduction in MT5 polling overhead
- Consistent prices across all lanes
- Lower latency (250ms vs 1000ms+ individual polls)

Usage:
    python scripts/shared_price_feeder.py

Lanes read prices via:
    from scripts.shared_price_feeder import read_price
    price = read_price("EURUSD", max_age_ms=1000)
"""
import json
import os
import time
import signal
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# MT5 import with graceful fallback
try:
    import MetaTrader5 as mt5
    HAS_MT5 = True
except ImportError:
    HAS_MT5 = False
    mt5 = None  # type: ignore[assignment]

try:
    import mt5_terminal_guard
except Exception:
    mt5_terminal_guard = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "reports" / "shared_price_cache.json"
TICK_CACHE_PATH = ROOT / "reports" / "shared_tick_cache.json"
HEARTBEAT_PATH = ROOT / "reports" / "shared_price_feeder_heartbeat.json"

# All symbols used by active lanes
SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "NZDUSD",
    "USDJPY",
    "BTCUSD",
    "ETHUSD",
    "SOLUSD",
    "XRPUSD",
]

POLL_INTERVAL_MS = 250
MAX_STALE_MS = 1000
TICK_HISTORY_RETENTION_MS = 180_000
MAX_TICK_HISTORY_PER_SYMBOL = 2_048
COPY_TICKS_ALL = getattr(mt5, "COPY_TICKS_ALL", 0) if HAS_MT5 else 0
ATOMIC_WRITE_ATTEMPTS = 12
ATOMIC_WRITE_RETRY_BASE_SECONDS = 0.05
WRITE_WARNING_COOLDOWN_SECONDS = 5.0

# Global shutdown flag
_shutting_down = False
_last_write_warning_at: dict[str, float] = {}


def _signal_handler(signum, frame):
    global _shutting_down
    _shutting_down = True
    print(f"\n[feeder] Received signal {signum}, shutting down gracefully...")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2) + "\n"
    last_error = None
    for attempt in range(ATOMIC_WRITE_ATTEMPTS):
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(ATOMIC_WRITE_RETRY_BASE_SECONDS * (attempt + 1))
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    if last_error is not None:
        raise last_error


def _best_effort_atomic_write(path: Path, data: dict, *, label: str) -> bool:
    """Keep the feeder alive on transient Windows file-lock races."""
    try:
        _atomic_write(path, data)
        return True
    except OSError as exc:
        now = time.monotonic()
        last_warned_at = _last_write_warning_at.get(label, 0.0)
        if (now - last_warned_at) >= WRITE_WARNING_COOLDOWN_SECONDS:
            print(f"[feeder] WARN: {label} write skipped after retries: {exc}")
            _last_write_warning_at[label] = now
        return False


def read_cached_price(symbol: str, max_age_ms: int = MAX_STALE_MS) -> dict | None:
    """Read price from shared cache only.

    Args:
        symbol: Symbol name (e.g., "EURUSD")
        max_age_ms: Maximum acceptable cache age in milliseconds

    Returns:
        {"bid": float, "ask": float, "ts": str} or None
    """
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            entry = cache.get(symbol)
            if entry:
                ts = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))
                age_ms = (datetime.now(timezone.utc) - ts).total_seconds() * 1000
                if age_ms < max_age_ms:
                    return entry
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            pass
    return None


def read_cached_ticks_since(
    symbol: str,
    last_tick_msc: int,
    *,
    max_age_ms: int = MAX_STALE_MS,
    lookback_seconds: int = 120,
) -> list[dict] | None:
    """Read recent tick history from the shared cache when it fully covers the request."""
    if not TICK_CACHE_PATH.exists():
        return None
    try:
        cache = json.loads(TICK_CACHE_PATH.read_text(encoding="utf-8"))
        history = cache.get(symbol) or []
        if not history:
            return None
        latest_tick_msc = int(history[-1].get("time_msc", 0) or 0)
        now_msc = int(datetime.now(timezone.utc).timestamp() * 1000)
        if latest_tick_msc <= 0 or (now_msc - latest_tick_msc) >= int(max_age_ms):
            return None
        oldest_tick_msc = int(history[0].get("time_msc", 0) or 0)
        if int(last_tick_msc or 0) > 0:
            if latest_tick_msc <= int(last_tick_msc):
                return []
            if oldest_tick_msc > int(last_tick_msc):
                return None
            return [tick for tick in history if int(tick.get("time_msc", 0) or 0) > int(last_tick_msc)]
        start_msc = now_msc - (max(1, int(lookback_seconds)) * 1000)
        if oldest_tick_msc > start_msc:
            return None
        return [tick for tick in history if int(tick.get("time_msc", 0) or 0) >= start_msc]
    except (json.JSONDecodeError, KeyError, ValueError, OSError, TypeError):
        return None


def _normalize_tick(tick) -> dict:
    return {
        "time": int(getattr(tick, "time", 0) or 0),
        "time_msc": int(getattr(tick, "time_msc", 0) or 0),
        "bid": float(getattr(tick, "bid", 0.0) or 0.0),
        "ask": float(getattr(tick, "ask", 0.0) or 0.0),
        "last": float(getattr(tick, "last", 0.0) or 0.0),
        "flags": int(getattr(tick, "flags", 0) or 0),
        "volume": int(getattr(tick, "volume", 0) or 0),
        "volume_real": float(getattr(tick, "volume_real", 0.0) or 0.0),
    }


def _copy_ticks_since(symbol: str, last_tick_msc: int) -> list[dict]:
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    if int(last_tick_msc or 0) > 0:
        start = datetime.fromtimestamp(max(0, int(last_tick_msc // 1000) - 1), tz=timezone.utc)
    else:
        start = now - timedelta(milliseconds=max(POLL_INTERVAL_MS * 4, 1000))
    ticks = mt5.copy_ticks_range(symbol, start, now, COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return []
    out: list[dict] = []
    for tick in ticks:
        tick_msc = int(tick["time_msc"])
        if int(last_tick_msc or 0) > 0 and tick_msc <= int(last_tick_msc):
            continue
        out.append(
            {
                "time": int(tick["time"]),
                "time_msc": tick_msc,
                "bid": float(tick["bid"]),
                "ask": float(tick["ask"]),
                "last": float(tick["last"]),
                "flags": int(tick["flags"]),
                "volume": int(tick["volume"]),
                "volume_real": float(tick["volume_real"]),
            }
        )
    return out


def _append_tick_history(history: list[dict], new_ticks: list[dict], *, now_msc: int) -> list[dict]:
    merged = list(history or [])
    last_seen_msc = int(merged[-1].get("time_msc", 0) or 0) if merged else 0
    for tick in new_ticks:
        tick_msc = int(tick.get("time_msc", 0) or 0)
        if tick_msc <= last_seen_msc:
            continue
        merged.append(tick)
        last_seen_msc = tick_msc
    cutoff_msc = int(now_msc) - TICK_HISTORY_RETENTION_MS
    merged = [tick for tick in merged if int(tick.get("time_msc", 0) or 0) >= cutoff_msc]
    if len(merged) > MAX_TICK_HISTORY_PER_SYMBOL:
        merged = merged[-MAX_TICK_HISTORY_PER_SYMBOL:]
    return merged


def read_price(symbol: str, max_age_ms: int = MAX_STALE_MS) -> dict | None:
    """Read price from shared cache. Falls back to MT5 direct if stale."""
    cached = read_cached_price(symbol, max_age_ms=max_age_ms)
    if cached is not None:
        return cached

    # Fallback to direct MT5
    if HAS_MT5:
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            return {"bid": tick.bid, "ask": tick.ask, "ts": utc_now_iso()}

    return None


def main() -> int:
    global _shutting_down

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print(f"[feeder] Shared Price Feeder starting")
    print(f"[feeder] Symbols: {SYMBOLS}")
    print(f"[feeder] Poll interval: {POLL_INTERVAL_MS}ms")
    print(f"[feeder] Cache: {CACHE_PATH}")

    if not HAS_MT5 or mt5_terminal_guard is None:
        print("[feeder] ERROR: MetaTrader5 not available")
        return 1

    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1

    print(f"[feeder] MT5 initialized, starting feed loop")

    # Initialize cache
    cache = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    tick_cache = {}
    if TICK_CACHE_PATH.exists():
        try:
            tick_cache = json.loads(TICK_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    cycle = 0
    while not _shutting_down:
        cycle += 1
        cycle_start = time.time()
        now_msc = int(datetime.now(timezone.utc).timestamp() * 1000)

        updated = 0
        tick_history_updated = 0
        tick_cache_dirty = False
        for symbol in SYMBOLS:
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                normalized_tick = _normalize_tick(tick)
                cache[symbol] = {
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "ts": utc_now_iso(),
                    "volume": tick.volume,
                    "spread": round(tick.ask - tick.bid, 5),
                }
                updated += 1
                existing_history = list(tick_cache.get(symbol) or [])
                last_tick_msc = int(existing_history[-1].get("time_msc", 0) or 0) if existing_history else 0
                new_ticks = _copy_ticks_since(symbol, last_tick_msc)
                if not new_ticks and int(normalized_tick.get("time_msc", 0) or 0) > last_tick_msc:
                    new_ticks = [normalized_tick]
                next_history = _append_tick_history(existing_history, new_ticks, now_msc=now_msc)
                if next_history != existing_history:
                    tick_cache[symbol] = next_history
                    tick_history_updated += len(new_ticks)
                    tick_cache_dirty = True

        # Write cache atomically
        if updated > 0:
            _best_effort_atomic_write(CACHE_PATH, cache, label="shared_price_cache")
        if tick_cache_dirty:
            _best_effort_atomic_write(TICK_CACHE_PATH, tick_cache, label="shared_tick_cache")

        # Write heartbeat
        heartbeat = {
            "feeder_pid": __import__("os").getpid(),
            "heartbeat_at": utc_now_iso(),
            "cycle": cycle,
            "symbols_updated": updated,
            "symbols_total": len(SYMBOLS),
            "cache_path": str(CACHE_PATH),
            "tick_cache_path": str(TICK_CACHE_PATH),
            "tick_history_retention_ms": TICK_HISTORY_RETENTION_MS,
            "tick_history_updates": tick_history_updated,
        }
        _best_effort_atomic_write(HEARTBEAT_PATH, heartbeat, label="shared_price_feeder_heartbeat")

        # Throttle to poll interval
        elapsed_ms = (time.time() - cycle_start) * 1000
        sleep_ms = max(0, POLL_INTERVAL_MS - elapsed_ms)
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

        # Log every 100 cycles (~25 seconds)
        if cycle % 100 == 0:
            print(f"[feeder] Cycle {cycle}: {updated}/{len(SYMBOLS)} symbols updated")

    print(f"[feeder] Shutting down after {cycle} cycles")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
