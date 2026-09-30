"""Primitive logging / JSONL helpers for the MT5 worker (leaf module)."""
from __future__ import annotations
from .config import REPO_ROOT  # noqa: F401
from datetime import datetime
import json
import os
import subprocess


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    safe = msg.encode('ascii', 'ignore').decode('ascii')
    line = f"[{ts}] {safe}"
    try:
        print(line, flush=True)
    except OSError:
        try:
            fallback_path = os.path.join(REPO_ROOT, "mt5_canonical_worker_out.log")
            with open(fallback_path, "a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
        except OSError:
            pass

def get_process_command_line(pid):
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\"; if ($p) {{ $p.CommandLine }}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""

def append_jsonl_record(path, payload):
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception as exc:
        log(f"  TRADE_BEHAVIOR_LOG_FAIL reason=append error={exc}")
