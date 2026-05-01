#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

QUEUE_PATH = REPORTS / "coinbase_isolated_runner_exact_config_smoke_queue.json"
DRY_PROBE_PATH = REPORTS / "coinbase_isolated_runner_exact_config_dry_probe.json"
SUPERVISED_PROBE_PATHS = [
    REPORTS / "coinbase_isolated_runner_supervised_probe.json",
    REPORTS / "coinbase_isolated_runner_supervised_probe_truusd_3cycles.json",
    REPORTS / "coinbase_isolated_runner_supervised_probe_supusd.json",
    REPORTS / "coinbase_isolated_runner_supervised_probe_supusd_3cycles.json",
]

JSON_PATH = REPORTS / "coinbase_isolated_runner_override_path_proof_board.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_override_path_proof_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_payload() -> dict[str, Any]:
    queue = load_json(QUEUE_PATH)
    dry_probe = load_json(DRY_PROBE_PATH)
    supervised_probes = [load_json(path) for path in SUPERVISED_PROBE_PATHS if path.exists()]

    dry_rows = {str(row.get("coin") or ""): row for row in list(dry_probe.get("rows") or [])}
    supervised_summaries: dict[str, dict[str, Any]] = {}
    for payload in supervised_probes:
        summary = dict(payload.get("summary") or {})
        coin = str(summary.get("target_coin") or "")
        if not coin:
            continue
        current = supervised_summaries.get(coin)
        if current is None or int(summary.get("max_cycles") or 1) > int(current.get("max_cycles") or 1):
            supervised_summaries[coin] = summary
    supervised_targets = [coin for coin in supervised_summaries.keys() if coin]

    rows: list[dict[str, Any]] = []
    for row in list(queue.get("rows") or []):
        coin = str(row.get("coin") or "")
        if str(row.get("proof_class") or "") != "exact_config_smoke":
            continue

        dry_row = dry_rows.get(coin, {})
        queue_decision = str(row.get("queue_decision") or "")
        status = "pending"
        next_action = "hold"
        read = ""

        supervised_summary = supervised_summaries.get(coin, {})
        if supervised_summary:
            max_cycles = int(supervised_summary.get("max_cycles") or 1)
            if str(supervised_summary.get("status") or "") == "probe_pass" and str(supervised_summary.get("position") or "") == "flat":
                if max_cycles >= 3:
                    status = "clean_across_multiple_windows_waiting_for_signal"
                    next_action = "shift_longer_window_to_next_coin"
                    read = "Dry probe plus multiple bounded supervised windows passed cleanly, but the lane still stayed flat with no signal."
                else:
                    status = "operationally_clean_waiting_for_signal"
                    next_action = "rerun_with_longer_supervised_window"
                    read = "Dry probe and bounded supervised probe both passed, but the first live-style window stayed flat with no signal."
            else:
                status = "supervised_probe_active"
                next_action = "observe_current_probe"
                read = "Bounded supervised probe is already active for this exact-config lane."
        elif queue_decision == "run_after_exact_batch_once_legacy_runtime_is_retired":
            status = "blocked_by_legacy_runtime"
            next_action = "retire_legacy_runtime_then_probe"
            read = "The override path is exact, but governance still blocks this lane until the legacy runtime trail is retired."
        elif str(dry_row.get("status") or "") == "probe_pass" and coin == "NOM-USD":
            status = "dry_clean_defer_for_overlap"
            next_action = "defer_to_parallel_nom_lane_then_supervise"
            read = "Dry probe passed, but NOM is being deferred here to avoid colliding with the existing parallel NOM lane."
        elif str(dry_row.get("status") or "") == "probe_pass":
            status = "dry_clean_ready_for_supervised_probe"
            next_action = "run_next_supervised_probe"
            read = "Dry probe passed cleanly and there is no stronger local blocker left for the next supervised run."
        else:
            status = "dry_probe_missing_or_failed"
            next_action = "repair_dry_path_first"
            read = "This lane should not move to supervised proof until the dry override path is green."

        rows.append(
            {
                "coin": coin,
                "queue_rank": int(row.get("queue_rank") or 0),
                "board_strategy": str(row.get("board_strategy") or ""),
                "queue_decision": queue_decision,
                "dry_probe_status": str(dry_row.get("status") or ""),
                "supervised_probe_status": str(supervised_summary.get("status") or ""),
                "supervised_probe_position": str(supervised_summary.get("position") or ""),
                "supervised_probe_max_cycles": int(supervised_summary.get("max_cycles") or 0),
                "status": status,
                "next_action": next_action,
                "read": read,
            }
        )

    rows.sort(key=lambda row: int(row.get("queue_rank") or 99))
    next_row = next((row for row in rows if row["next_action"] == "rerun_with_longer_supervised_window"), {})
    if not next_row:
        next_row = next((row for row in rows if row["next_action"] == "run_next_supervised_probe"), {})
    deferred_row = next((row for row in rows if row["next_action"] == "defer_to_parallel_nom_lane_then_supervise"), {})

    leadership_read = [
        "The exact-config override path is now clearly separated into dry-path proof and bounded supervised proof.",
        "TRU-USD and SUP-USD now both have clean 3-cycle reruns with no signals, which strengthens the path operationally but does not create a reason to force deployment from empty windows.",
        "NOM-USD is deferred only to avoid overlap with the existing parallel NOM lane, and BAL-USD stays blocked until legacy runtime cleanup finishes.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "queue_path": str(QUEUE_PATH),
        "dry_probe_path": str(DRY_PROBE_PATH),
        "supervised_probe_paths": [str(path) for path in SUPERVISED_PROBE_PATHS if path.exists()],
        "leadership_read": leadership_read,
        "summary": {
            "exact_rows": len(rows),
            "supervised_probe_targets": supervised_targets,
            "next_supervised_target": str(next_row.get("coin") or ""),
            "next_supervised_strategy": str(next_row.get("board_strategy") or ""),
            "deferred_next_target": str(deferred_row.get("coin") or ""),
            "deferred_next_strategy": str(deferred_row.get("board_strategy") or ""),
        },
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Override Path Proof Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Exact rows: `{payload['summary']['exact_rows']}`",
            f"- Current supervised targets: `{', '.join(payload['summary']['supervised_probe_targets'])}`",
            f"- Next supervised target: `{payload['summary']['next_supervised_target']}`",
            f"- Next supervised strategy: `{payload['summary']['next_supervised_strategy']}`",
            "",
            "## Rows",
            "",
            "| Rank | Coin | Strategy | Status | Next Action |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            f"| {row['queue_rank']} | {row['coin']} | {row['board_strategy']} | {row['status']} | {row['next_action']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
