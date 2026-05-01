#!/usr/bin/env python3
from __future__ import annotations

import json
import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"

QUEUE_PATH = REPORTS / "coinbase_isolated_runner_exact_config_smoke_queue.json"
CONFIG_PATH = REPORTS / "coinbase_isolated_runner_sleeve_book_config.json"
RUNNER_PATH = SCRIPTS / "multi_coin_isolated_runner.py"

DEFAULT_JSON_PATH = REPORTS / "coinbase_isolated_runner_supervised_probe.json"
DEFAULT_MD_PATH = REPORTS / "coinbase_isolated_runner_supervised_probe.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def windows_no_window_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def choose_target_row(target_coin: str | None = None) -> dict[str, Any]:
    queue = load_json(QUEUE_PATH)
    for row in list(queue.get("rows") or []):
        coin = str(row.get("coin") or "")
        if target_coin and coin != target_coin:
            continue
        if str(row.get("proof_class") or "") == "exact_config_smoke" and str(row.get("queue_decision") or "") == "run_now":
            return row
    if target_coin:
        raise SystemExit(f"No exact-config supervised probe candidate found for {target_coin}")
    raise SystemExit("No exact-config supervised probe candidate found")


def report_paths(target_coin: str, max_cycles: int = 1) -> tuple[Path, Path]:
    if target_coin == "TRU-USD" and max_cycles == 1:
        return DEFAULT_JSON_PATH, DEFAULT_MD_PATH
    stem = target_coin.lower().replace("-", "").replace("_", "")
    cycle_suffix = f"_{max_cycles}cycles"
    return (
        REPORTS / f"coinbase_isolated_runner_supervised_probe_{stem}{cycle_suffix}.json",
        REPORTS / f"coinbase_isolated_runner_supervised_probe_{stem}{cycle_suffix}.md",
    )


def build_command(coin: str, state_path: Path, event_path: Path, max_cycles: int = 1) -> list[str]:
    return [
        sys.executable,
        str(RUNNER_PATH),
        "--config-path",
        str(CONFIG_PATH),
        "--total-cash",
        "48",
        "--coins",
        coin,
        "--state-path",
        str(state_path),
        "--event-path",
        str(event_path),
        "--max-cycles",
        str(max_cycles),
    ]


def run_probe(row: dict[str, Any], max_cycles: int = 1) -> dict[str, Any]:
    coin = str(row.get("coin") or "")
    stem = coin.lower().replace("-", "").replace("_", "")
    state_path = REPORTS / f"probe_supervised_{stem}_state.json"
    event_path = REPORTS / f"probe_supervised_{stem}_events.jsonl"
    state_path.unlink(missing_ok=True)
    event_path.unlink(missing_ok=True)

    command = build_command(coin, state_path, event_path, max_cycles=max_cycles)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
        creationflags=windows_no_window_creationflags(),
    )
    state = load_json(state_path) if state_path.exists() else {}
    ledger = dict(((state.get("ledgers") or {}).get(coin) or {}))

    position = str(ledger.get("position") or "")
    closes = int(ledger.get("closes") or 0)
    signals = int(ledger.get("signals") or 0)
    status = "probe_pass" if result.returncode == 0 else "probe_fail"

    return {
        "coin": coin,
        "queue_rank": int(row.get("queue_rank") or 0),
        "board_strategy": str(row.get("board_strategy") or ""),
        "proof_class": str(row.get("proof_class") or ""),
        "return_code": result.returncode,
        "status": status,
        "state_path": str(state_path),
        "event_path": str(event_path),
        "state_exists": state_path.exists(),
        "event_exists": event_path.exists(),
        "position": position or "flat",
        "signals": signals,
        "closes": closes,
        "total_equity": float(state.get("total_equity") or 0.0),
        "total_pnl": float(state.get("total_pnl") or 0.0),
        "stdout_tail": (result.stdout or "")[-1200:],
        "stderr_tail": (result.stderr or "")[-1200:],
        "command": command,
    }


def build_payload(target_coin: str | None = None) -> dict[str, Any]:
    return build_payload_for_cycles(target_coin=target_coin, max_cycles=1)


def build_payload_for_cycles(target_coin: str | None = None, max_cycles: int = 1) -> dict[str, Any]:
    row = choose_target_row(target_coin)
    probe = run_probe(row, max_cycles=max_cycles)
    leadership_read = [
        "This supervised probe runs the first exact-config queue lane through the real runner path with bounded cycles and dedicated probe state files.",
        "It is stronger than a dry probe because it exercises the live loop and saved runtime state, but it is still a tiny proof run rather than a deployment verdict.",
        "The right success condition here is operational clarity: the runner should complete cleanly, save probe state, and tell us whether the lane came back active or flat in the short supervised window.",
    ]
    return {
        "generated_at": utc_now_iso(),
        "queue_path": str(QUEUE_PATH),
        "config_path": str(CONFIG_PATH),
        "runner_path": str(RUNNER_PATH),
        "leadership_read": leadership_read,
        "summary": {
            "target_coin": probe["coin"],
            "max_cycles": max_cycles,
            "queue_rank": probe["queue_rank"],
            "status": probe["status"],
            "position": probe["position"],
            "signals": probe["signals"],
            "closes": probe["closes"],
            "total_pnl": probe["total_pnl"],
        },
        "probe": probe,
    }


def write_reports(payload: dict[str, Any], json_path: Path, md_path: Path) -> None:
    save_json(json_path, payload)
    lines = [
        "# Coinbase Isolated Runner Supervised Probe",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    probe = payload["probe"]
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Target coin: `{payload['summary']['target_coin']}`",
            f"- Queue rank: `{payload['summary']['queue_rank']}`",
            f"- Status: `{payload['summary']['status']}`",
            f"- Position: `{payload['summary']['position']}`",
            f"- Signals: `{payload['summary']['signals']}`",
            f"- Closes: `{payload['summary']['closes']}`",
            f"- Total PnL: `{payload['summary']['total_pnl']}`",
            "",
            "## Probe",
            "",
            f"- State file: `{probe['state_path']}`",
            f"- Event file: `{probe['event_path']}`",
            f"- State exists: `{probe['state_exists']}`",
            f"- Event file exists: `{probe['event_exists']}`",
            f"- Return code: `{probe['return_code']}`",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a bounded supervised probe for an exact-config override lane")
    parser.add_argument("--coin", type=str, default=None, help="Specific exact-config coin to probe")
    parser.add_argument("--max-cycles", type=int, default=1, help="Number of live cycles to run")
    args = parser.parse_args()

    payload = build_payload_for_cycles(args.coin, max_cycles=args.max_cycles)
    json_path, md_path = report_paths(payload["summary"]["target_coin"], max_cycles=args.max_cycles)
    write_reports(payload, json_path, md_path)
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")


if __name__ == "__main__":
    main()
