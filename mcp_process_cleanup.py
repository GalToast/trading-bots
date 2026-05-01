#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency fallback
    psutil = None

ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from process_singleton import acquire_singleton  # type: ignore  # noqa: E402


DEFAULT_REPORT_JSON = ROOT / "reports" / "watchdog" / "mcp_process_cleanup_report.json"
DEFAULT_EVENTS_JSONL = ROOT / "reports" / "watchdog" / "mcp_process_cleanup_events.jsonl"
DEFAULT_LOCK_PATH = ROOT / "reports" / "watchdog" / "mcp_process_cleanup.lock"
DEFAULT_MIN_AGE_SECONDS = 120.0
EXPLICIT_PARENT_RE = re.compile(r"--parent-pid(?:=|\s+)(\d+)", re.IGNORECASE)

FAMILY_RULES: dict[str, tuple[str, ...]] = {
    "chrome_devtools": ("chrome-devtools-mcp",),
    "playwright": ("@playwright/mcp",),
}


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    ppid: int
    name: str
    cmdline: list[str]
    cmdline_text: str
    create_time: float
    age_seconds: float
    family: str | None
    explicit_parent_pid: int | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil is not None:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False
    return True


def normalize_cmdline(cmdline: list[str] | tuple[str, ...] | None) -> list[str]:
    return [str(token or "").strip() for token in list(cmdline or []) if str(token or "").strip()]


def classify_family(cmdline_text: str) -> str | None:
    lowered = str(cmdline_text or "").lower()
    for family, needles in FAMILY_RULES.items():
        if any(needle in lowered for needle in needles):
            return family
    return None


def parse_explicit_parent_pid(cmdline_text: str) -> int | None:
    match = EXPLICIT_PARENT_RE.search(str(cmdline_text or ""))
    if not match:
        return None
    try:
        value = int(match.group(1))
    except Exception:
        return None
    return value if value > 0 else None


def snapshot_from_record(record: dict[str, Any], *, now_ts: float) -> ProcessSnapshot:
    pid = int(record.get("pid") or 0)
    ppid = int(record.get("ppid") or 0)
    name = str(record.get("name") or "")
    cmdline = normalize_cmdline(record.get("cmdline"))
    cmdline_text = " ".join(cmdline)
    create_time = float(record.get("create_time") or 0.0)
    age_seconds = max(0.0, now_ts - create_time) if create_time > 0 else 0.0
    family = classify_family(cmdline_text)
    return ProcessSnapshot(
        pid=pid,
        ppid=ppid,
        name=name,
        cmdline=cmdline,
        cmdline_text=cmdline_text,
        create_time=create_time,
        age_seconds=age_seconds,
        family=family,
        explicit_parent_pid=parse_explicit_parent_pid(cmdline_text),
    )


def list_process_snapshots(*, now_ts: float | None = None) -> list[ProcessSnapshot]:
    if psutil is None:
        raise RuntimeError("psutil is required for MCP cleanup")
    now_ts = float(now_ts or utc_now().timestamp())
    rows: list[ProcessSnapshot] = []
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
        try:
            info = proc.info or {}
        except Exception:
            continue
        try:
            row = snapshot_from_record(
                {
                    "pid": info.get("pid"),
                    "ppid": info.get("ppid"),
                    "name": info.get("name"),
                    "cmdline": info.get("cmdline"),
                    "create_time": info.get("create_time"),
                },
                now_ts=now_ts,
            )
        except Exception:
            continue
        if row.pid > 0:
            rows.append(row)
    return rows


def find_instance_root_pid(pid: int, targeted_by_pid: dict[int, ProcessSnapshot]) -> int:
    current = targeted_by_pid[pid]
    visited = {pid}
    while current.ppid in targeted_by_pid:
        parent = targeted_by_pid[current.ppid]
        if parent.family != current.family or parent.pid in visited:
            break
        visited.add(parent.pid)
        current = parent
    return current.pid


def build_cleanup_plan(
    processes: list[ProcessSnapshot],
    *,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    process_alive_fn: Callable[[int], bool] = process_alive,
) -> dict[str, Any]:
    by_pid = {proc.pid: proc for proc in processes if proc.pid > 0}
    targeted = {proc.pid: proc for proc in processes if proc.family is not None}
    instances: dict[int, dict[str, Any]] = {}

    for proc in targeted.values():
        root_pid = find_instance_root_pid(proc.pid, targeted)
        inst = instances.setdefault(
            root_pid,
            {
                "root_pid": root_pid,
                "family": proc.family,
                "member_pids": [],
                "member_count": 0,
                "root_create_time": 0.0,
                "root_age_seconds": 0.0,
                "root_cmdline": "",
                "owner_pid": 0,
                "owner_alive": False,
                "owner_name": "",
                "owner_cmdline": "",
                "reasons": [],
                "killable": False,
            },
        )
        inst["member_pids"].append(proc.pid)

    for root_pid, inst in instances.items():
        root = targeted[root_pid]
        owner = by_pid.get(root.ppid)
        owner_pid = int(root.ppid or 0)
        owner_alive = process_alive_fn(owner_pid) if owner_pid > 0 else False
        reasons: list[str] = []

        for pid in list(inst["member_pids"]):
            proc = targeted[pid]
            explicit_parent_pid = int(proc.explicit_parent_pid or 0)
            if explicit_parent_pid > 0 and not process_alive_fn(explicit_parent_pid) and proc.age_seconds >= min_age_seconds:
                reasons.append(f"explicit_parent_dead:{explicit_parent_pid}")

        if root.age_seconds >= min_age_seconds and not owner_alive:
            reasons.append("owner_missing")

        inst["member_pids"] = sorted(set(int(pid) for pid in inst["member_pids"]))
        inst["member_count"] = len(inst["member_pids"])
        inst["root_create_time"] = float(root.create_time)
        inst["root_age_seconds"] = round(root.age_seconds, 1)
        inst["root_cmdline"] = root.cmdline_text
        inst["owner_pid"] = owner_pid
        inst["owner_alive"] = owner_alive
        inst["owner_name"] = owner.name if owner else ""
        inst["owner_cmdline"] = owner.cmdline_text if owner else ""
        inst["reasons"] = sorted(set(reasons))
        inst["killable"] = bool(inst["reasons"])

    duplicates_by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for inst in instances.values():
        owner_pid = int(inst.get("owner_pid") or 0)
        if not inst.get("owner_alive") or owner_pid <= 0:
            continue
        key = (str(inst.get("family") or ""), owner_pid)
        duplicates_by_key.setdefault(key, []).append(inst)

    for grouped in duplicates_by_key.values():
        if len(grouped) <= 1:
            continue
        grouped_sorted = sorted(grouped, key=lambda item: (float(item["root_create_time"]), int(item["root_pid"])), reverse=True)
        keep = grouped_sorted[0]
        for inst in grouped_sorted[1:]:
            if float(inst["root_age_seconds"]) < min_age_seconds:
                continue
            reasons = set(str(item) for item in inst.get("reasons") or [])
            reasons.add(f"duplicate_instance_for_owner:{keep['root_pid']}")
            inst["reasons"] = sorted(reasons)
            inst["killable"] = True

    targets: list[dict[str, Any]] = []
    target_pids: list[int] = []
    for inst in sorted(instances.values(), key=lambda item: int(item["root_pid"])):
        if not inst.get("killable"):
            continue
        inst_targets = sorted(set(int(pid) for pid in inst.get("member_pids") or []))
        target_pids.extend(inst_targets)
        targets.append(
            {
                "root_pid": int(inst["root_pid"]),
                "family": str(inst["family"] or ""),
                "target_pids": inst_targets,
                "reasons": list(inst.get("reasons") or []),
                "owner_pid": int(inst.get("owner_pid") or 0),
                "owner_alive": bool(inst.get("owner_alive")),
            }
        )

    return {
        "matched_process_count": len(targeted),
        "instance_count": len(instances),
        "instances": sorted(instances.values(), key=lambda item: int(item["root_pid"])),
        "targets": targets,
        "target_pids": sorted(set(target_pids)),
        "min_age_seconds": float(min_age_seconds),
    }


def terminate_processes(pids: list[int]) -> list[dict[str, Any]]:
    if psutil is None:
        raise RuntimeError("psutil is required for MCP cleanup")
    results: list[dict[str, Any]] = []
    for pid in sorted({int(pid) for pid in pids if int(pid) > 0}):
        try:
            proc = psutil.Process(pid)
        except Exception:
            results.append({"pid": pid, "status": "missing"})
            continue
        try:
            proc.terminate()
            proc.wait(timeout=3)
            results.append({"pid": pid, "status": "terminated"})
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=3)
                results.append({"pid": pid, "status": "killed"})
            except Exception as exc:
                results.append({"pid": pid, "status": "failed", "error": str(exc)})
    return results


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def run_cleanup(
    *,
    dry_run: bool,
    min_age_seconds: float,
    json_out: Path,
    events_jsonl: Path | None,
) -> dict[str, Any]:
    if psutil is None:
        raise RuntimeError("psutil is required for MCP cleanup")
    now = utc_now()
    processes = list_process_snapshots(now_ts=now.timestamp())
    plan = build_cleanup_plan(processes, min_age_seconds=min_age_seconds)
    actions: list[dict[str, Any]] = []
    if plan["target_pids"] and not dry_run:
        actions = terminate_processes(list(plan["target_pids"]))

    payload = {
        "ts_utc": now.isoformat(),
        "dry_run": bool(dry_run),
        "matched_process_count": int(plan["matched_process_count"]),
        "instance_count": int(plan["instance_count"]),
        "target_count": len(plan["targets"]),
        "target_pids": list(plan["target_pids"]),
        "targets": list(plan["targets"]),
        "actions": actions,
        "instances": list(plan["instances"]),
        "min_age_seconds": float(min_age_seconds),
    }
    write_json(json_out, payload)
    if events_jsonl is not None:
        append_jsonl(
            events_jsonl,
            {
                "ts_utc": payload["ts_utc"],
                "action": "mcp_cleanup_run",
                "dry_run": payload["dry_run"],
                "matched_process_count": payload["matched_process_count"],
                "target_count": payload["target_count"],
                "target_pids": payload["target_pids"],
                "actions": payload["actions"],
            },
        )
    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cleanup stale or duplicate MCP node helper processes.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be cleaned without terminating anything.")
    parser.add_argument("--min-age-seconds", type=float, default=DEFAULT_MIN_AGE_SECONDS)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--events-jsonl", type=Path, default=DEFAULT_EVENTS_JSONL)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    with acquire_singleton(
        args.lock_path,
        scope="mcp_process_cleanup",
        metadata={"script": str(Path(__file__).name)},
    ) as lease:
        if not lease.acquired:
            print(
                json.dumps(
                    {
                        "status": "skipped_duplicate_launch",
                        "owner_pid": lease.owner_pid,
                        "lock_path": str(args.lock_path),
                    },
                    sort_keys=True,
                )
            )
            return 0
        payload = run_cleanup(
            dry_run=bool(args.dry_run),
            min_age_seconds=float(args.min_age_seconds),
            json_out=Path(args.json_out),
            events_jsonl=Path(args.events_jsonl) if args.events_jsonl else None,
        )
    print(
        json.dumps(
            {
                "status": "ok",
                "dry_run": payload["dry_run"],
                "matched_process_count": payload["matched_process_count"],
                "target_count": payload["target_count"],
                "target_pids": payload["target_pids"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
