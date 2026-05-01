#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_alive(pid: int) -> bool:
    """Robust check for process existence on Windows/Linux."""
    if pid <= 0:
        return False
    
    # Try psutil first if available (most robust on Windows)
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass

    # Fallback to os.kill(pid, 0)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError):
        # On Windows, SystemError can be raised by os.kill in some cases
        return False
    return True


def read_lock(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@dataclass
class SingletonLease:
    lock_path: Path
    acquired: bool
    owner_pid: int | None = None

    def __enter__(self) -> SingletonLease:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.acquired:
            try:
                self.lock_path.unlink(missing_ok=True)
            except Exception:
                pass


def acquire_singleton(
    lock_path: Path, *, scope: str = "", metadata: dict[str, Any] | None = None
) -> SingletonLease:
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Try to create the lock file exclusively
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "pid": os.getpid(),
                    "scope": scope,
                    "acquired_at": utc_now_iso(),
                    "metadata": metadata or {},
                },
                f,
                indent=2,
            )
        return SingletonLease(lock_path=lock_path, acquired=True, owner_pid=os.getpid())
    except FileExistsError:
        pass

    # 2. Lock file exists, check if the owner is still alive
    existing = read_lock(lock_path)
    owner_pid = int(existing.get("pid") or 0) if existing else None

    if owner_pid and process_alive(owner_pid):
        # Owner is alive, we cannot acquire the lock
        return SingletonLease(lock_path=lock_path, acquired=False, owner_pid=owner_pid)

    # 3. Owner is dead (or no pid), steal the lock
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        # If we can't delete it, we still can't acquire it safely
        return SingletonLease(lock_path=lock_path, acquired=False, owner_pid=owner_pid)

    # Try acquiring again (recursive call or just retry once)
    # We'll just retry once directly to avoid infinite recursion
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "pid": os.getpid(),
                    "scope": scope,
                    "acquired_at": utc_now_iso(),
                    "metadata": metadata or {},
                },
                f,
                indent=2,
            )
        return SingletonLease(lock_path=lock_path, acquired=True, owner_pid=os.getpid())
    except Exception:
        return SingletonLease(lock_path=lock_path, acquired=False, owner_pid=owner_pid)
