#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

ALLOCATOR_PATH = REPORTS / "coinbase_isolated_sleeve_allocator.json"
RUNNER_BACKFILL_PATH = REPORTS / "multi_coin_runner_backfill.json"
PORTFOLIO_STATE_PATH = REPORTS / "multi_coin_portfolio_state.json"
A8_RSI_STATE_PATH = REPORTS / "coinbase_rsi_shadow_a8usd_state.json"
BAL_BURST_STATE_PATH = REPORTS / "burst_fade_balusd_live_shadow_state.json"

JSON_PATH = REPORTS / "coinbase_primary_runner_alignment_board.json"
MD_PATH = REPORTS / "coinbase_primary_runner_alignment_board.md"

STATUS_RANK = {
    "aligned_active_saved_state": 0,
    "aligned_config_needs_runtime_state": 1,
    "aligned_config_legacy_runtime_present": 2,
    "family_aligned_but_old_lane_still_present": 3,
    "runner_strategy_mismatch": 4,
    "missing_from_saved_runner": 5,
}


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def infer_family(strategy: str) -> str:
    text = str(strategy or "").lower()
    if "breakout" in text:
        return "range_breakout"
    if "mom" in text or "momentum" in text:
        return "momentum"
    if "rsi" in text:
        return "rsi_mean_reversion"
    if "burst" in text:
        return "burst"
    return "other"


def build_rows() -> list[dict[str, Any]]:
    allocator = load_json(ALLOCATOR_PATH)
    runner_backfill = load_json(RUNNER_BACKFILL_PATH)
    portfolio_state = load_json(PORTFOLIO_STATE_PATH)
    a8_rsi_state = load_json(A8_RSI_STATE_PATH)
    bal_burst_state = load_json(BAL_BURST_STATE_PATH)

    runner_configs = {
        str(row.get("coin") or ""): row
        for row in list(runner_backfill.get("coin_configs") or [])
        if row.get("coin")
    }
    portfolio_coins = {
        str(coin): row
        for coin, row in dict(portfolio_state.get("coins") or {}).items()
    }

    rows: list[dict[str, Any]] = []
    for sleeve in list(allocator.get("primary_sleeves") or []):
        coin = str(sleeve.get("coin") or "")
        planned_strategy = str(sleeve.get("strategy") or "")
        planned_family = infer_family(planned_strategy)
        runner_row = runner_configs.get(coin) or {}
        runner_strategy = str(runner_row.get("strategy") or "")
        runner_family = infer_family(runner_strategy)
        portfolio_row = portfolio_coins.get(coin) or {}

        status = "aligned_config_needs_runtime_state"
        blocker = "runner config matches the board family, but there is still no saved current runtime state for this primary lane"
        recommended = "launch_and_persist_primary_lane_state"
        runtime_saved = bool(portfolio_row)
        runtime_summary = ""

        if runtime_saved:
            runtime_summary = (
                f"{portfolio_row.get('strategy')} closes={int(portfolio_row.get('closes') or 0)} "
                f"position={portfolio_row.get('position')}"
            )

        if planned_family == "momentum" and runner_family == "momentum" and runtime_saved and coin == "RAVE-USD":
            status = "aligned_active_saved_state"
            blocker = "none"
            recommended = "maintain_live_and_refresh_saved_state_after_closes"
        elif coin == "A8-USD":
            status = "family_aligned_but_old_lane_still_present"
            blocker = (
                "saved runner config points to momentum, but the only dedicated saved product state is the losing RSI shadow "
                f"at {to_float(((a8_rsi_state.get('state') or {}).get('realized_net_usd'))):+.4f}"
            )
            recommended = "retire_or_ignore_old_rsi_state_then_persist_momentum_state"
        elif planned_family != runner_family and runner_row:
            status = "runner_strategy_mismatch"
            blocker = f"board primary family is {planned_family}, but saved runner family is {runner_family}"
            recommended = "align_saved_runner_family_to_board_or_demote_the_lane"
        elif not runner_row and planned_family == "range_breakout":
            status = "missing_from_saved_runner"
            blocker = "primary lane is on the launch board, but the saved runner backfill does not include this coin"
            recommended = "add_coin_to_saved_runner_config_before_claiming_runtime_alignment"
        elif coin == "BAL-USD" and runtime_saved and infer_family(str(portfolio_row.get("strategy") or "")) != planned_family:
            status = "aligned_config_legacy_runtime_present"
            blocker = (
                "saved runner config is now breakout-aligned, but the saved runtime trail still points to the old lane and the strongest "
                "dedicated live artifact "
                f"is legacy burst at {to_float(((bal_burst_state.get('engine') or {}).get('realized_net_usd'))):+.2f}"
            )
            recommended = "persist_breakout_runtime_and_retire_legacy_runtime_claims"
        elif runtime_saved:
            status = "aligned_config_needs_runtime_state"
            blocker = "runner config is aligned, but the current saved portfolio state does not yet prove this lane has fired recently"
            recommended = "wait_for_first_saved_closes_or_refresh_portfolio_state"

        rows.append(
            {
                "coin": coin,
                "sleeve_rank": int(sleeve.get("sleeve_rank") or 0),
                "planned_primary_lane": planned_strategy,
                "planned_family": planned_family,
                "launch_wave": str(sleeve.get("launch_wave") or ""),
                "saved_runner_strategy": runner_strategy,
                "saved_runner_family": runner_family,
                "saved_runner_params": dict(runner_row),
                "current_saved_portfolio_strategy": str(portfolio_row.get("strategy") or ""),
                "current_saved_runtime_summary": runtime_summary,
                "alignment_status": status,
                "blocker": blocker,
                "recommended_action": recommended,
            }
        )

    rows.sort(
        key=lambda row: (
            STATUS_RANK.get(str(row.get("alignment_status") or ""), 99),
            int(row.get("sleeve_rank") or 99),
            str(row.get("coin") or ""),
        )
    )
    return rows


def build_leadership_read(rows: list[dict[str, Any]]) -> list[str]:
    runtime_gap = [row["coin"] for row in rows if row["alignment_status"] == "aligned_config_needs_runtime_state"]
    legacy_runtime = [row["coin"] for row in rows if row["alignment_status"] == "aligned_config_legacy_runtime_present"]
    return [
        "The launch book and the saved runner artifacts are now source-aligned, but saved runtime proof still lags on most primaries.",
        f"RAVE is the only fully aligned primary today: board family, saved runner config, and saved live portfolio state all point to momentum.",
        f"{', '.join(runtime_gap)} now have board-family alignment in the saved runner config, but still need fresh saved runtime proof.",
        f"{', '.join(legacy_runtime)} is the remaining config-aligned but runtime-stale case: the breakout lane is configured, but the saved runtime trail still points to the old story.",
    ]


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(rows),
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Primary Runner Alignment Board",
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
            "| Rank | Coin | Planned Lane | Planned Family | Saved Runner | Saved Runtime | Status | Recommended Action |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {sleeve_rank} | {coin} | {planned_primary_lane} | {planned_family} | {saved_runner_strategy} | {current_saved_runtime_summary} | {alignment_status} | {recommended_action} |".format(
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
