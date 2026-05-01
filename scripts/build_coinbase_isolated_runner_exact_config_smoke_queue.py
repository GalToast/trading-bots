#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

MANIFEST_PATH = REPORTS / "coinbase_isolated_runner_sleeve_smoke_manifest.json"
ALLOCATOR_PATH = REPORTS / "coinbase_isolated_sleeve_allocator.json"
FIX_VERIFICATION_PATH = REPORTS / "coinbase_isolated_runner_fix_verification.json"
PRIMARY_ALIGNMENT_PATH = REPORTS / "coinbase_primary_runner_alignment_board.json"

JSON_PATH = REPORTS / "coinbase_isolated_runner_exact_config_smoke_queue.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_exact_config_smoke_queue.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def pretty_lane(coin: str, strategy: str) -> str:
    return f"{coin} {strategy}".strip()


def smoke_phase(row: dict[str, Any], sleeve_row: dict[str, Any], alignment_row: dict[str, Any]) -> tuple[str, int]:
    proof_class = str(row.get("proof_class") or "")
    strategy = str(row.get("board_strategy") or "")
    launch_wave = str(sleeve_row.get("launch_wave") or "")
    alignment_status = str(alignment_row.get("alignment_status") or "")

    if proof_class == "exact_config_smoke" and "shadow" not in strategy:
        return ("batch_1_exact_standalone", 0)
    if proof_class == "exact_config_smoke" and alignment_status == "aligned_config_legacy_runtime_present":
        return ("batch_1_exact_cleanup_last", 2)
    if proof_class == "exact_config_smoke":
        return ("batch_1_exact_shadow", 1)
    if launch_wave == "launch_now":
        return ("batch_2_inferred_launch_now", 3)
    return ("batch_3_inferred_optional", 4)


def queue_decision(row: dict[str, Any], sleeve_row: dict[str, Any], alignment_row: dict[str, Any]) -> str:
    proof_class = str(row.get("proof_class") or "")
    launch_wave = str(sleeve_row.get("launch_wave") or "")
    alignment_status = str(alignment_row.get("alignment_status") or "")
    if proof_class == "exact_config_smoke" and alignment_status == "aligned_config_legacy_runtime_present":
        return "run_after_exact_batch_once_legacy_runtime_is_retired"
    if proof_class == "exact_config_smoke":
        return "run_now"
    if launch_wave == "launch_now":
        return "run_after_exact_batch"
    return "optional_after_exact_and_launch_now_batches"


def rationale(row: dict[str, Any], sleeve_row: dict[str, Any], alignment_row: dict[str, Any]) -> str:
    proof_class = str(row.get("proof_class") or "")
    coin = str(row.get("coin") or "")
    strategy = str(row.get("board_strategy") or "")
    launch_wave = str(sleeve_row.get("launch_wave") or "")
    blocker = str(alignment_row.get("blocker") or "")
    reason = str(sleeve_row.get("reason") or "")

    if proof_class == "exact_config_smoke" and "shadow" not in strategy:
        return (
            f"{coin} is the cleanest first smoke because it is exact-config, standalone, "
            f"and not waiting on same-coin stack governance. {reason}".strip()
        )
    if proof_class == "exact_config_smoke" and "legacy" in blocker:
        return (
            f"{coin} is still worth an exact-config smoke, but it should trail the other exact rows "
            f"because saved runtime still points to the legacy lane. {blocker}".strip()
        )
    if proof_class == "exact_config_smoke":
        return (
            f"{coin} stays in the first batch because the override config is exact for the approved sleeve. "
            f"{reason}".strip()
        )
    if launch_wave == "launch_now":
        return (
            f"{coin} remains important, but the config row is reconstructed rather than exact, so it should "
            f"follow the first exact-config batch. {blocker}".strip()
        )
    return (
        f"{coin} is already a live anchor or lower-urgency inferred lane, so an override-config smoke is "
        f"useful but not the first proof priority. {reason}".strip()
    )


def build_payload() -> dict[str, Any]:
    manifest = load_json(MANIFEST_PATH)
    allocator = load_json(ALLOCATOR_PATH)
    fix_verification = load_json(FIX_VERIFICATION_PATH)
    primary_alignment = load_json(PRIMARY_ALIGNMENT_PATH)

    sleeve_rows = {
        str(row.get("coin") or ""): row
        for row in list(allocator.get("primary_sleeves") or [])
    }
    alignment_rows = {
        str(row.get("coin") or ""): row
        for row in list(primary_alignment.get("rows") or [])
    }

    queue_rows: list[dict[str, Any]] = []
    for row in list(manifest.get("rows") or []):
        coin = str(row.get("coin") or "")
        sleeve_row = sleeve_rows.get(coin, {})
        alignment_row = alignment_rows.get(coin, {})
        phase, phase_rank = smoke_phase(row, sleeve_row, alignment_row)
        queue_rows.append(
            {
                "coin": coin,
                "board_strategy": str(row.get("board_strategy") or ""),
                "runner_strategy": str(row.get("runner_strategy") or ""),
                "proof_class": str(row.get("proof_class") or ""),
                "config_status": str(row.get("config_status") or ""),
                "launch_wave": str(sleeve_row.get("launch_wave") or ""),
                "sleeve_rank": int(sleeve_row.get("sleeve_rank") or 99),
                "same_coin_stack_policy": str(sleeve_row.get("same_coin_stack_policy") or ""),
                "alignment_status": str(alignment_row.get("alignment_status") or ""),
                "runtime_blocker": str(alignment_row.get("blocker") or ""),
                "phase": phase,
                "phase_rank": phase_rank,
                "queue_decision": queue_decision(row, sleeve_row, alignment_row),
                "smoke_command": str(row.get("smoke_command") or ""),
                "supervised_command": str(row.get("supervised_command") or ""),
                "rationale": rationale(row, sleeve_row, alignment_row),
            }
        )

    queue_rows.sort(
        key=lambda row: (
            int(row["phase_rank"]) if row.get("phase_rank") is not None else 99,
            int(row["sleeve_rank"]) if row.get("sleeve_rank") is not None else 99,
            str(row.get("coin") or ""),
        )
    )
    for index, row in enumerate(queue_rows, start=1):
        row["queue_rank"] = index

    first = queue_rows[0] if queue_rows else {}
    exact_batch = [row["coin"] for row in queue_rows if str(row.get("proof_class") or "") == "exact_config_smoke"]
    inferred_batch = [row["coin"] for row in queue_rows if str(row.get("proof_class") or "") == "inferred_config_smoke"]

    summary = {
        "verification_verdict": str(fix_verification.get("verification_verdict") or ""),
        "queue_rows": len(queue_rows),
        "exact_config_batch_size": len(exact_batch),
        "inferred_follow_on_batch_size": len(inferred_batch),
        "first_smoke_candidate": pretty_lane(
            str(first.get("coin") or ""),
            str(first.get("board_strategy") or ""),
        ),
    }

    leadership_read = [
        "The restart drills already cleared the isolated runner for controlled smoke, so config evidence now decides the proof order.",
        "TRU-USD should lead the first batch because it is the cleanest exact-config standalone lane, while NOM-USD, SUP-USD, and BAL-USD are exact but breakout-shadow rows that belong after it.",
        "A8-USD and CFG-USD still matter, but they should follow the exact-config batch because their override rows are reconstructed rather than directly saved. RAVE-USD is already live enough that an override smoke is optional, not urgent.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "manifest_path": str(MANIFEST_PATH),
        "allocator_path": str(ALLOCATOR_PATH),
        "fix_verification_path": str(FIX_VERIFICATION_PATH),
        "primary_alignment_path": str(PRIMARY_ALIGNMENT_PATH),
        "leadership_read": leadership_read,
        "summary": summary,
        "rows": queue_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Exact Config Smoke Queue",
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
            f"- Verification verdict: `{payload['summary']['verification_verdict']}`",
            f"- Queue rows: `{payload['summary']['queue_rows']}`",
            f"- Exact-config first batch: `{payload['summary']['exact_config_batch_size']}`",
            f"- Inferred follow-on batch: `{payload['summary']['inferred_follow_on_batch_size']}`",
            f"- First smoke candidate: `{payload['summary']['first_smoke_candidate']}`",
            "",
            "## Queue",
            "",
            "| Rank | Coin | Strategy | Proof Class | Phase | Decision | Alignment |",
            "| ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {queue_rank} | {coin} | {board_strategy} | {proof_class} | {phase} | {queue_decision} | {alignment_status} |".format(
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
