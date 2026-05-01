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

import multi_coin_portfolio as runner_source


ALLOCATOR_PATH = REPORTS / "coinbase_isolated_sleeve_allocator.json"
RUNNER_BACKFILL_PATH = REPORTS / "multi_coin_runner_backfill.json"
PORTFOLIO_STATE_PATH = REPORTS / "multi_coin_portfolio_state.json"
A8_RSI_STATE_PATH = REPORTS / "coinbase_rsi_shadow_a8usd_state.json"
BAL_BURST_STATE_PATH = REPORTS / "burst_fade_balusd_live_shadow_state.json"

JSON_PATH = REPORTS / "coinbase_runner_truth_split_board.json"
MD_PATH = REPORTS / "coinbase_runner_truth_split_board.md"

STATUS_RANK = {
    "source_and_saved_live_aligned": 0,
    "source_aligned_saved_runtime_missing": 1,
    "source_aligned_saved_runtime_stale": 2,
    "source_aligned_saved_old_lane_artifact": 3,
    "source_fixed_saved_backfill_stale": 4,
    "source_fixed_saved_backfill_missing": 5,
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
    backfill = load_json(RUNNER_BACKFILL_PATH)
    portfolio = load_json(PORTFOLIO_STATE_PATH)
    a8_rsi = load_json(A8_RSI_STATE_PATH)
    bal_burst = load_json(BAL_BURST_STATE_PATH)

    source_configs = runner_source.STRATEGY_CONFIGS
    saved_configs = {
        str(row.get("coin") or ""): row
        for row in list(backfill.get("coin_configs") or [])
        if row.get("coin")
    }
    saved_runtime = {
        str(coin): row
        for coin, row in dict(portfolio.get("coins") or {}).items()
    }

    rows: list[dict[str, Any]] = []
    for sleeve in list(allocator.get("primary_sleeves") or []):
        coin = str(sleeve.get("coin") or "")
        source_cfg = dict(source_configs.get(coin) or {})
        saved_cfg = dict(saved_configs.get(coin) or {})
        runtime_row = dict(saved_runtime.get(coin) or {})

        source_strategy = str(source_cfg.get("type") or "")
        saved_strategy = str(saved_cfg.get("strategy") or "")
        source_family = infer_family(source_strategy)
        saved_family = infer_family(saved_strategy)
        status = "source_aligned_saved_runtime_missing"
        truth_read = "source and saved config agree, but there is no fresh saved runtime state for this lane yet"

        if coin == "RAVE-USD":
            status = "source_and_saved_live_aligned"
            truth_read = "source config, saved backfill, and saved runtime all agree on momentum"
        elif coin == "A8-USD":
            status = "source_aligned_saved_old_lane_artifact"
            truth_read = "source and saved config agree on momentum, but the only dedicated saved product artifact is the stale losing RSI shadow"
        elif source_family and source_family == saved_family and not runtime_row:
            status = "source_aligned_saved_runtime_missing"
            truth_read = f"source config and saved backfill agree on {source_family}, but saved runtime proof has not been refreshed"
        elif source_family and source_family == saved_family:
            status = "source_aligned_saved_runtime_stale"
            truth_read = "source config and saved backfill agree, but the saved runtime trail still belongs to the old lane story"
        elif source_cfg and saved_cfg:
            status = "source_fixed_saved_backfill_stale"
            truth_read = "runner source has been fixed, but the saved backfill still reflects the pre-fix family"
        elif source_cfg and not saved_cfg:
            status = "source_fixed_saved_backfill_missing"
            truth_read = "runner source has been fixed, but there is still no saved backfill row for this coin"

        row = {
            "coin": coin,
            "sleeve_rank": int(sleeve.get("sleeve_rank") or 0),
            "board_primary_lane": str(sleeve.get("strategy") or ""),
            "board_primary_family": infer_family(str(sleeve.get("strategy") or "")),
            "source_strategy": source_strategy,
            "source_family": source_family,
            "saved_backfill_strategy": saved_strategy,
            "saved_backfill_family": saved_family,
            "saved_runtime_strategy": str(runtime_row.get("strategy") or ""),
            "saved_runtime_position": str(runtime_row.get("position") or ""),
            "truth_status": status,
            "truth_read": truth_read,
        }
        if coin == "A8-USD":
            row["stale_artifact_net_usd"] = float(((a8_rsi.get("state") or {}).get("realized_net_usd")) or 0.0)
        if coin == "BAL-USD":
            row["legacy_live_artifact_net_usd"] = float(((bal_burst.get("engine") or {}).get("realized_net_usd")) or 0.0)
        rows.append(row)

    rows.sort(
        key=lambda row: (
            STATUS_RANK.get(str(row.get("truth_status") or ""), 99),
            int(row.get("sleeve_rank") or 99),
            str(row.get("coin") or ""),
        )
    )
    return rows


def build_leadership_read(rows: list[dict[str, Any]]) -> list[str]:
    stale = [row["coin"] for row in rows if row["truth_status"] in {"source_fixed_saved_backfill_stale", "source_fixed_saved_backfill_missing"}]
    missing_runtime = [row["coin"] for row in rows if row["truth_status"] == "source_aligned_saved_runtime_missing"]
    stale_runtime = [row["coin"] for row in rows if row["truth_status"] == "source_aligned_saved_runtime_stale"]
    return [
        "Source truth and saved-artifact truth are now closer: the saved backfill has caught up to the live runner source, but saved runtime proof still lags.",
        "RAVE is still the only lane where source config, saved backfill, and saved runtime all line up cleanly.",
        f"{', '.join(missing_runtime)} are now config-aligned in both source and saved backfill, but still need first saved runtime proof.",
        f"{', '.join(stale_runtime)} is config-aligned at the source and backfill layers, but still carries a stale saved runtime trail.",
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
        "# Coinbase Runner Truth Split Board",
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
            "| Rank | Coin | Board Family | Source Family | Saved Backfill Family | Saved Runtime | Status |",
            "| ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        runtime = row["saved_runtime_strategy"] or row["saved_runtime_position"]
        lines.append(
            "| {sleeve_rank} | {coin} | {board_primary_family} | {source_family} | {saved_backfill_family} | {runtime} | {truth_status} |".format(
                sleeve_rank=row["sleeve_rank"],
                coin=row["coin"],
                board_primary_family=row["board_primary_family"],
                source_family=row["source_family"],
                saved_backfill_family=row["saved_backfill_family"] or "",
                runtime=runtime,
                truth_status=row["truth_status"],
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
