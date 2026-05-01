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


SLEEVE_ALLOCATOR_PATH = REPORTS / "coinbase_isolated_sleeve_allocator.json"
CLAIM_AUDIT_PATH = REPORTS / "coinbase_momentum_claim_audit.json"
RANGE_SWEEP_PATH = REPORTS / "coinbase_range_breakout_sweep.json"
JSON_PATH = REPORTS / "coinbase_isolated_runner_sleeve_book_config.json"
MD_PATH = REPORTS / "coinbase_isolated_runner_sleeve_book_config.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def current_runner_map() -> dict[str, dict[str, Any]]:
    return {cfg["coin"]: dict(cfg) for cfg in runner.DEFAULT_COIN_CONFIGS}


def claim_map() -> dict[str, dict[str, Any]]:
    return {row["coin"]: row for row in load_json(CLAIM_AUDIT_PATH).get("rows", [])}


def range_map() -> dict[str, dict[str, Any]]:
    return {row["coin"]: row for row in load_json(RANGE_SWEEP_PATH).get("coin_rows", [])}


def parse_lookback(strategy: str) -> int | None:
    if strategy.startswith("mom_"):
        return int(strategy.split("_", 1)[1])
    return None


def build_config_row(row: dict[str, Any], runner_cfgs: dict[str, dict[str, Any]], claims: dict[str, dict[str, Any]], ranges: dict[str, dict[str, Any]]) -> dict[str, Any]:
    coin = row["coin"]
    strategy = row["strategy"]
    current_cfg = runner_cfgs.get(coin, {})

    if strategy == "range_breakout_shadow":
        sweep = ranges[coin]
        return {
            "coin": coin,
            "strategy": "range_breakout",
            "range_lookback": int(sweep["best_range_lookback"]),
            "tp_pct": float(sweep["best_tp_pct"]) / 100.0,
            "sl_pct": float(sweep["best_sl_pct"]) / 100.0,
            "max_hold": int(sweep["best_max_hold"]),
            "config_status": "exact_from_range_sweep",
            "config_note": f"exact from saved range_breakout sweep best params for {coin}",
            "board_strategy": strategy,
        }

    if strategy == "momentum_registry_validation":
        claim = claims[coin]
        return {
            "coin": coin,
            "strategy": "momentum",
            "lookback": int(claim["optimized_best_lookback"]),
            "tp_pct": float(claim["optimized_best_tp_pct"]) / 100.0,
            "sl_pct": float(claim["optimized_best_sl_pct"]) / 100.0,
            "max_hold": int(current_cfg.get("max_hold", 48)),
            "config_status": "exact_from_claim_audit_plus_runner_hold",
            "config_note": f"momentum params from saved claim audit; max_hold carried from current runner default for {coin}",
            "board_strategy": strategy,
        }

    if strategy.startswith("mom_"):
        lookback = parse_lookback(strategy)
        if coin in {"A8-USD", "CFG-USD"} and current_cfg:
            return {
                "coin": coin,
                "strategy": "momentum",
                "lookback": int(lookback or current_cfg.get("lookback", 20)),
                "tp_pct": float(current_cfg.get("tp_pct", 0.10)),
                "sl_pct": float(current_cfg.get("sl_pct", 0.03)),
                "max_hold": int(current_cfg.get("max_hold", 48)),
                "config_status": "inferred_from_runner_family_with_board_lookback",
                "config_note": f"current runner momentum risk params retained for {coin}, but lookback corrected to the board-approved sleeve",
                "board_strategy": strategy,
            }

        return {
            "coin": coin,
            "strategy": "momentum",
            "lookback": int(lookback or 20),
            "tp_pct": 0.10,
            "sl_pct": 0.03,
            "max_hold": 48,
            "config_status": "inferred_family_baseline",
            "config_note": f"board-approved momentum lookback for {coin} with standard 10/3/48 family baseline because no exact saved param artifact is available here",
            "board_strategy": strategy,
        }

    raise ValueError(f"Unsupported sleeve strategy: {strategy}")


def build_payload() -> dict[str, Any]:
    allocator = load_json(SLEEVE_ALLOCATOR_PATH)
    runner_cfgs = current_runner_map()
    claims = claim_map()
    ranges = range_map()

    configs = [
        build_config_row(row, runner_cfgs, claims, ranges)
        for row in allocator.get("primary_sleeves", [])
    ]

    exact = [row["coin"] for row in configs if row["config_status"].startswith("exact_")]
    inferred = [row["coin"] for row in configs if not row["config_status"].startswith("exact_")]
    return {
        "generated_at": utc_now_iso(),
        "runner_path": str(SCRIPTS / "multi_coin_isolated_runner.py"),
        "allocator_path": str(SLEEVE_ALLOCATOR_PATH),
        "claim_audit_path": str(CLAIM_AUDIT_PATH),
        "range_sweep_path": str(RANGE_SWEEP_PATH),
        "leadership_read": [
            "This config lets the isolated runner smoke the board-approved sleeve book without mutating its baked-in defaults.",
            "NOM, SUP, BAL, and TRU are sourced from saved parameter artifacts; RAVE, A8, and CFG still carry inference because exact saved sleeve params are not centralized in one source yet.",
            "That keeps the run path honest: a config-override smoke can now target the sleeve allocator directly, but evidence class still matters coin by coin.",
        ],
        "summary": {
            "config_rows": len(configs),
            "exact_rows": len(exact),
            "inferred_rows": len(inferred),
            "exact_coins": exact,
            "inferred_coins": inferred,
        },
        "configs": configs,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Runner Sleeve Book Config",
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
            f"- Exact rows: `{', '.join(payload['summary']['exact_coins'])}`",
            f"- Inferred rows: `{', '.join(payload['summary']['inferred_coins'])}`",
            "",
            "## Config Rows",
            "",
            "| Coin | Runner Strategy | Board Strategy | Status | Note |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["configs"]:
        runner_strategy = row["strategy"]
        if row["strategy"] == "momentum":
            runner_strategy += f" lb={row['lookback']}"
        if row["strategy"] == "range_breakout":
            runner_strategy += f" lb={row['range_lookback']}"
        lines.append(
            f"| {row['coin']} | {runner_strategy} | {row['board_strategy']} | {row['config_status']} | {row['config_note']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
