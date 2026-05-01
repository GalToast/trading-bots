#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

PROOF_BOARD_PATH = REPORTS / "coinbase_isolated_runtime_proof_board.json"

JSON_PATH = REPORTS / "coinbase_isolated_runtime_launch_manifest.json"
MD_PATH = REPORTS / "coinbase_isolated_runtime_launch_manifest.md"


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug_for_coin(coin: str) -> str:
    return str(coin).replace("-", "").replace("_", "").lower()


def build_rows() -> list[dict[str, Any]]:
    proof_board = load_json(PROOF_BOARD_PATH)
    rows: list[dict[str, Any]] = []
    for row in list(proof_board.get("rows") or []):
        coin = str(row.get("coin") or "")
        slug = slug_for_coin(coin)
        state_path = ROOT / "reports" / f"multi_coin_portfolio_{slug}_state.json"
        event_path = ROOT / "reports" / f"multi_coin_portfolio_{slug}_events.jsonl"
        launch_command = (
            f"python scripts/multi_coin_portfolio.py --coins {coin} --starting-cash 48 "
            f"--state-path \"{state_path}\" --event-path \"{event_path}\""
        )
        smoke_command = (
            f"{launch_command} --poll-seconds 5 --max-loops 1"
        )
        supervised_command = (
            f"{launch_command} --poll-seconds 30 --max-loops 12"
        )
        pre_launch = ""
        if str(row.get("proof_phase") or "") == "artifact_cleanup_then_runtime":
            pre_launch = "retire or ignore the stale lane artifact before treating new runtime state as canonical"
        elif str(row.get("proof_phase") or "") == "replace_legacy_runtime":
            pre_launch = "keep the old runtime file for audit history, but stop treating it as the breakout proof source"

        rows.append(
            {
                "fix_order": int(row.get("fix_order") or 0),
                "coin": coin,
                "strategy": str(row.get("strategy") or ""),
                "proof_phase": str(row.get("proof_phase") or ""),
                "state_path": str(state_path),
                "event_path": str(event_path),
                "pre_launch_action": pre_launch,
                "launch_command": launch_command,
                "smoke_command": smoke_command,
                "supervised_command": supervised_command,
                "success_gate": str(row.get("success_gate") or ""),
            }
        )
    rows.sort(key=lambda row: (int(row.get("fix_order") or 99), str(row.get("coin") or "")))
    return rows


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": [
            "This manifest turns the isolated runtime-proof queue into concrete single-coin launch commands using the existing multi_coin_portfolio runner.",
            "Each proof lane gets its own state and event files so isolated runs do not overwrite the shared multi-coin runner artifacts.",
            "Use these commands to persist the first clean runtime trail for each promoted lane; they are proof runs, not a final portfolio architecture verdict.",
        ],
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runtime Launch Manifest",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Order | Coin | Strategy | Proof Phase | State Path | Event Path | Pre-Launch Action | Smoke Command | Supervised Command |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {fix_order} | {coin} | {strategy} | {proof_phase} | {state_path} | {event_path} | {pre_launch_action} | `{smoke_command}` | `{supervised_command}` |".format(
                **row
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
