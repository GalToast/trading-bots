#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCRIPTS = ROOT / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_coinbase_primary_runner_alignment_board as alignment_builder
import build_coinbase_runner_truth_split_board as truth_builder

ALIGNMENT_PATH = REPORTS / "coinbase_primary_runner_alignment_board.json"
TRUTH_SPLIT_PATH = REPORTS / "coinbase_runner_truth_split_board.json"
ALLOCATOR_PATH = REPORTS / "coinbase_isolated_sleeve_allocator.json"
RESERVE_PATH = REPORTS / "coinbase_reserve_activation_board.json"

JSON_PATH = REPORTS / "coinbase_runner_remediation_queue.json"
MD_PATH = REPORTS / "coinbase_runner_remediation_queue.md"

PHASE_RANK = {
    "monitor_live_anchor": 0,
    "clear_wave_1_blocker": 1,
    "clear_wave_1_persist_gap": 2,
    "clear_wave_2_runtime_gap": 3,
    "clear_wave_2_runtime_refresh": 4,
    "clear_wave_2_backfill_refresh": 5,
    "clear_wave_2_missing_backfill": 6,
    "clear_wave_2_persist_gap": 7,
    "clear_reserve_dependency": 8,
}


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


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_rows() -> list[dict[str, Any]]:
    alignment = alignment_builder.build_payload()
    truth_split = truth_builder.build_payload()
    allocator = load_json(ALLOCATOR_PATH)
    reserve = load_json(RESERVE_PATH)

    allocator_rows = {str(row.get("coin") or ""): row for row in list(allocator.get("primary_sleeves") or []) if row.get("coin")}
    reserve_rows = {str(row.get("coin") or ""): row for row in list(reserve.get("rows") or []) if row.get("coin")}
    truth_rows = {str(row.get("coin") or ""): row for row in list(truth_split.get("rows") or []) if row.get("coin")}

    rows: list[dict[str, Any]] = []
    for row in list(alignment.get("rows") or []):
        coin = str(row.get("coin") or "")
        allocator_row = allocator_rows.get(coin) or {}
        truth_row = truth_rows.get(coin) or {}
        launch_wave = str(allocator_row.get("launch_wave") or "")
        sleeve_rank = int(allocator_row.get("sleeve_rank") or row.get("sleeve_rank") or 0)
        alignment_status = str(row.get("alignment_status") or "")
        truth_status = str(truth_row.get("truth_status") or "")
        phase = "clear_wave_2_persist_gap"
        fix_order = 99
        fix_type = "persist_runtime_state"

        if coin == "RAVE-USD":
            phase = "monitor_live_anchor"
            fix_order = 1
            fix_type = "refresh_saved_runtime_after_close"
        elif launch_wave == "launch_now" and alignment_status == "family_aligned_but_old_lane_still_present":
            phase = "clear_wave_1_blocker"
            fix_order = 2
            fix_type = "retire_stale_lane_artifact"
        elif launch_wave == "launch_now":
            phase = "clear_wave_1_persist_gap"
            fix_order = 3
            fix_type = "persist_primary_runtime_state"
        elif truth_status == "source_aligned_saved_runtime_missing" and launch_wave == "launch_after_wave_1":
            phase = "clear_wave_2_runtime_gap"
            fix_order = 4 if coin == "NOM-USD" else 5 if coin == "SUP-USD" else 7
            fix_type = "persist_primary_runtime_state"
        elif truth_status == "source_aligned_saved_runtime_stale":
            phase = "clear_wave_2_runtime_refresh"
            fix_order = 6
            fix_type = "replace_legacy_runtime_state"
        elif truth_status == "source_fixed_saved_backfill_stale":
            phase = "clear_wave_2_backfill_refresh"
            fix_order = 6
            fix_type = "refresh_saved_backfill_and_runtime"
        elif truth_status == "source_fixed_saved_backfill_missing":
            phase = "clear_wave_2_missing_backfill"
            fix_order = 5
            fix_type = "create_saved_backfill_row"
        else:
            phase = "clear_wave_2_persist_gap"
            fix_order = 7
            fix_type = "persist_primary_runtime_state"

        action = str(row.get("recommended_action") or "")
        if coin == "NOM-USD" and phase == "clear_wave_2_runtime_gap":
            action = "persist NOM breakout runtime so saved state catches up to the already aligned backfill"
        elif coin == "SUP-USD" and phase == "clear_wave_2_runtime_gap":
            action = "persist SUP breakout runtime so the saved trail includes its first aligned runtime proof"
        elif coin == "BAL-USD" and phase == "clear_wave_2_runtime_refresh":
            action = "replace BAL's legacy momentum runtime trail with a breakout runtime artifact"
        elif coin == "SUP-USD" and phase == "clear_wave_2_missing_backfill":
            action = "create saved backfill/runtime artifacts for SUP breakout before calling it aligned"
        success_gate = ""
        if phase == "monitor_live_anchor":
            success_gate = "save the next live close so the aligned anchor stays fresh"
        elif coin == "A8-USD":
            success_gate = "A8 momentum replaces the stale RSI artifact in saved state"
        elif coin in {"CFG-USD", "TRU-USD"}:
            success_gate = f"{coin} gets a saved current runtime state for its board-approved momentum lane"
        elif coin == "NOM-USD":
            success_gate = "NOM gets a saved breakout runtime trail on top of its already aligned source and backfill story"
        elif coin == "SUP-USD":
            success_gate = "SUP gets its first saved breakout runtime proof before anyone cites runtime alignment"
        elif coin == "BAL-USD":
            success_gate = "BAL breakout gets a saved runtime trail that replaces the old momentum artifact"

        rows.append(
            {
                "coin": coin,
                "sleeve_rank": sleeve_rank,
                "launch_wave": launch_wave,
                "alignment_status": alignment_status or truth_status,
                "remediation_phase": phase,
                "fix_order": fix_order,
                "fix_type": fix_type,
                "board_primary_lane": str(row.get("planned_primary_lane") or ""),
                "saved_runner_strategy": str(row.get("saved_runner_strategy") or ""),
                "current_saved_runtime_summary": str(row.get("current_saved_runtime_summary") or ""),
                "blocker": str(row.get("blocker") or ""),
                "recommended_action": action,
                "success_gate": success_gate,
            }
        )

    reserve_nom = reserve_rows.get("NOM-USD") or {}
    reserve_rave = reserve_rows.get("RAVE-USD") or {}
    rows.extend(
        [
            {
                "coin": "NOM-USD",
                "sleeve_rank": 8,
                "launch_wave": "reserve",
                "alignment_status": str(reserve_nom.get("reserve_status") or ""),
                "remediation_phase": "clear_reserve_dependency",
                "fix_order": 8,
                "fix_type": "persist_dual_shadow_runtime",
                "board_primary_lane": str(reserve_nom.get("secondary_lane") or ""),
                "saved_runner_strategy": "",
                "current_saved_runtime_summary": str(reserve_nom.get("runtime_dependency_status") or ""),
                "blocker": str(reserve_nom.get("blocking_reason") or ""),
                "recommended_action": str(reserve_nom.get("recommended_action") or ""),
                "success_gate": "NOM secondary reserve lane has saved dual-shadow runtime proof, not just overlap proof",
            },
            {
                "coin": "RAVE-USD",
                "sleeve_rank": 9,
                "launch_wave": "reserve",
                "alignment_status": str(reserve_rave.get("reserve_status") or ""),
                "remediation_phase": "clear_reserve_dependency",
                "fix_order": 9,
                "fix_type": "restore_and_graduate_secondary_lane",
                "board_primary_lane": str(reserve_rave.get("secondary_lane") or ""),
                "saved_runner_strategy": "",
                "current_saved_runtime_summary": str(reserve_rave.get("runtime_dependency_status") or ""),
                "blocker": str(reserve_rave.get("blocking_reason") or ""),
                "recommended_action": str(reserve_rave.get("recommended_action") or ""),
                "success_gate": "RAVE RSI live lane is restored and the three graduation gaps are closed",
            },
        ]
    )

    rows.sort(
        key=lambda row: (
            PHASE_RANK.get(str(row.get("remediation_phase") or ""), 99),
            int(row.get("fix_order") or 99),
            int(row.get("sleeve_rank") or 99),
            str(row.get("coin") or ""),
        )
    )
    return rows


def build_leadership_read(rows: list[dict[str, Any]]) -> list[str]:
    wave1 = [row["coin"] for row in rows if row["remediation_phase"] in {"clear_wave_1_blocker", "clear_wave_1_persist_gap"}]
    wave2 = [
        row["coin"]
        for row in rows
        if row["remediation_phase"] in {
            "clear_wave_2_runtime_gap",
            "clear_wave_2_runtime_refresh",
            "clear_wave_2_backfill_refresh",
            "clear_wave_2_missing_backfill",
            "clear_wave_2_persist_gap",
        }
    ]
    reserve = [row["coin"] for row in rows if row["remediation_phase"] == "clear_reserve_dependency"]
    return [
        "The next execution work is a remediation queue, not more discovery: clear saved-state blockers in launch order so the runner can honestly match the board.",
        f"Wave 1 fix order is {', '.join(wave1)}: clean the stale A8 RSI artifact first, then persist current runtime state for CFG.",
        f"Wave 2 fix order is {', '.join(wave2)}: persist first runtime proof for NOM and SUP, replace BAL's legacy runtime trail, then persist TRU runtime so saved evidence catches up to source truth.",
        f"Reserve work comes after that: {', '.join(reserve)} still depend on saved dual-lane runtime or graduation proof, not just bench strength.",
    ]


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total_fix_items": len(rows),
        "wave_1_items": sum(1 for row in rows if row["remediation_phase"] in {"clear_wave_1_blocker", "clear_wave_1_persist_gap"}),
        "wave_2_items": sum(
            1
            for row in rows
            if row["remediation_phase"] in {
                "clear_wave_2_runtime_gap",
                "clear_wave_2_runtime_refresh",
                "clear_wave_2_backfill_refresh",
                "clear_wave_2_missing_backfill",
                "clear_wave_2_persist_gap",
            }
        ),
        "reserve_items": sum(1 for row in rows if row["remediation_phase"] == "clear_reserve_dependency"),
    }


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(rows),
        "summary": build_summary(rows),
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Runner Remediation Queue",
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
            f"- Total fix items: `{payload['summary']['total_fix_items']}`",
            f"- Wave 1 items: `{payload['summary']['wave_1_items']}`",
            f"- Wave 2 items: `{payload['summary']['wave_2_items']}`",
            f"- Reserve items: `{payload['summary']['reserve_items']}`",
            "",
            "## Rows",
            "",
            "| Order | Coin | Phase | Fix Type | Board Lane | Saved Runner | Saved Runtime | Recommended Action |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {fix_order} | {coin} | {remediation_phase} | {fix_type} | {board_primary_lane} | {saved_runner_strategy} | {current_saved_runtime_summary} | {recommended_action} |".format(
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
