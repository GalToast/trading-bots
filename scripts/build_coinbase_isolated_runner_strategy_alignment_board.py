#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import multi_coin_isolated_runner as runner


ALLOCATOR_PATH = REPORTS / "coinbase_isolated_sleeve_allocator.json"
CLAIM_AUDIT_PATH = REPORTS / "coinbase_momentum_claim_audit.json"
JSON_PATH = REPORTS / "coinbase_isolated_runner_strategy_alignment_board.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_strategy_alignment_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def claim_audit_map() -> dict[str, dict[str, Any]]:
    rows = load_json(CLAIM_AUDIT_PATH).get("rows", [])
    return {row["coin"]: row for row in rows}


def parse_board_strategy(row: dict[str, Any], claim_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    strategy = row["strategy"]
    coin = row["coin"]
    if strategy.startswith("mom_"):
        return {
            "family": "momentum",
            "lookback": int(strategy.split("_", 1)[1]),
            "descriptor": strategy,
        }
    if strategy == "momentum_registry_validation":
        claim = claim_map.get(coin, {})
        return {
            "family": "momentum",
            "lookback": claim.get("optimized_best_lookback"),
            "descriptor": strategy,
        }
    if strategy == "range_breakout_shadow":
        return {
            "family": "range_breakout",
            "lookback": None,
            "descriptor": strategy,
        }
    if strategy == "rsi_mean_reversion_active":
        return {
            "family": "rsi_mean_reversion",
            "lookback": None,
            "descriptor": strategy,
        }
    return {
        "family": strategy,
        "lookback": None,
        "descriptor": strategy,
    }


def parse_runner_strategy(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "family": cfg["strategy"],
        "lookback": cfg.get("lookback"),
        "descriptor": cfg["strategy"],
    }


def quote_path(path: Path) -> str:
    return f"\"{path}\""


def runner_command(coin: str, max_cycles: int) -> str:
    stem = coin.lower().replace("-", "").replace("_", "")
    state_path = REPORTS / f"multi_coin_isolated_runner_{stem}_state.json"
    event_path = REPORTS / f"multi_coin_isolated_runner_{stem}_events.jsonl"
    return (
        f"python scripts/multi_coin_isolated_runner.py --total-cash 48 --coins {coin} "
        f"--state-path {quote_path(state_path)} --event-path {quote_path(event_path)} --max-cycles {max_cycles}"
    )


def compare_row(row: dict[str, Any], runner_cfg: dict[str, Any] | None, claim_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    board = parse_board_strategy(row, claim_map)
    runner_parsed = parse_runner_strategy(runner_cfg) if runner_cfg else {"family": "", "lookback": None, "descriptor": ""}

    if runner_cfg is None:
        alignment_status = "missing_from_runner"
        smoke_admission = "blocked_until_config_added"
        note = "board-approved sleeve is not present in the current isolated runner config"
    elif board["family"] != runner_parsed["family"]:
        alignment_status = "family_mismatch"
        smoke_admission = "do_not_count_as_sleeve_book_proof"
        note = "runner family diverges from the board-approved sleeve family"
    elif board["family"] == "momentum" and board["lookback"] is not None and runner_parsed["lookback"] != board["lookback"]:
        alignment_status = "lookback_mismatch"
        smoke_admission = "family_probe_only"
        note = "runner is testing the right family but the wrong momentum lookback for the approved sleeve"
    elif row["strategy"] == "momentum_registry_validation":
        alignment_status = "registry_aligned"
        smoke_admission = "direct_sleeve_book_proof"
        note = "runner momentum config matches the validated registry momentum lane closely enough to treat a bounded smoke as direct sleeve proof"
    else:
        alignment_status = "direct_aligned"
        smoke_admission = "direct_sleeve_book_proof"
        note = "runner family and key params align with the board-approved sleeve"

    return {
        "coin": row["coin"],
        "board_strategy": row["strategy"],
        "runner_strategy": runner_cfg["strategy"] if runner_cfg else "",
        "runner_lookback": runner_cfg.get("lookback") if runner_cfg else None,
        "runner_tp_pct": runner_cfg.get("tp_pct") if runner_cfg else None,
        "runner_sl_pct": runner_cfg.get("sl_pct") if runner_cfg else None,
        "runner_max_hold": runner_cfg.get("max_hold") if runner_cfg else None,
        "sleeve_rank": row["sleeve_rank"],
        "launch_wave": row["launch_wave"],
        "alignment_status": alignment_status,
        "smoke_admission": smoke_admission,
        "note": note,
        "supervised_command": runner_command(row["coin"], 10),
    }


def build_payload() -> dict[str, Any]:
    allocator = load_json(ALLOCATOR_PATH)
    claim_map = claim_audit_map()
    runner_map = {cfg["coin"]: cfg for cfg in runner.COIN_CONFIGS}
    rows = [
        compare_row(row, runner_map.get(row["coin"]), claim_map)
        for row in allocator.get("primary_sleeves", [])
    ]
    rows.sort(key=lambda row: row["sleeve_rank"])

    direct = [row["coin"] for row in rows if row["smoke_admission"] == "direct_sleeve_book_proof"]
    probes = [row["coin"] for row in rows if row["smoke_admission"] == "family_probe_only"]
    blocked = [row["coin"] for row in rows if row["smoke_admission"] == "do_not_count_as_sleeve_book_proof"]

    return {
        "generated_at": utc_now_iso(),
        "runner_path": str(SCRIPTS / "multi_coin_isolated_runner.py"),
        "allocator_path": str(ALLOCATOR_PATH),
        "leadership_read": [
            "Restart durability is now proven, but that is not the same thing as strategy-book alignment.",
            "The current isolated runner can be used as a direct sleeve-book smoke only where its built-in family and key params actually match the approved sleeve.",
            "Right now TRU is the only clean direct proof lane, A8 and CFG are momentum-family probes with lookback drift, and RAVE/NOM/SUP/BAL should not be counted as proof for the approved sleeve book until the runner config is rewritten.",
        ],
        "summary": {
            "primary_sleeves_reviewed": len(rows),
            "direct_sleeve_book_proofs": len(direct),
            "family_probe_only": len(probes),
            "blocked_due_to_family_mismatch": len(blocked),
            "direct_proof_coins": direct,
            "family_probe_coins": probes,
            "blocked_coins": blocked,
        },
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Strategy Alignment Board",
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
            f"- Direct sleeve-book proofs: `{', '.join(payload['summary']['direct_proof_coins']) or 'none'}`",
            f"- Momentum-family probes only: `{', '.join(payload['summary']['family_probe_coins']) or 'none'}`",
            f"- Blocked from sleeve-book proof: `{', '.join(payload['summary']['blocked_coins']) or 'none'}`",
            "",
            "## Rows",
            "",
            "| Coin | Board Strategy | Runner Strategy | Alignment | Smoke Admission | Note |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        runner_desc = row["runner_strategy"]
        if row["runner_lookback"] is not None:
            runner_desc += f" lb={row['runner_lookback']}"
        lines.append(
            f"| {row['coin']} | {row['board_strategy']} | {runner_desc} | {row['alignment_status']} | {row['smoke_admission']} | {row['note']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
