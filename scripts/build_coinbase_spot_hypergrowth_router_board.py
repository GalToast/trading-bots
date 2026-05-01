#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

NEXT_LAUNCH_WAVE_PATH = REPORTS / "coinbase_spot_next_launch_wave.json"
STACK_ADMISSION_PATH = REPORTS / "coinbase_same_coin_stack_admission_board.json"
RUNTIME_BOARD_PATH = REPORTS / "coinbase_spot_runtime_board.json"

JSON_PATH = REPORTS / "coinbase_spot_hypergrowth_router_board.json"
MD_PATH = REPORTS / "coinbase_spot_hypergrowth_router_board.md"


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


def infer_family(lane: str) -> str:
    if "breakout" in lane:
        return "range_breakout"
    if "mom" in lane or "momentum" in lane:
        return "momentum"
    if "rsi" in lane:
        return "rsi_mean_reversion"
    if "piranha" in lane:
        return "spot_piranha"
    return "other"


def build_runtime_hint_map(payload: dict[str, Any]) -> dict[str, str]:
    hints: dict[str, str] = {}
    for row in list(payload.get("key_lanes") or []):
        product_id = str(row.get("product_id") or "")
        if product_id and product_id != "multi-asset":
            hints[product_id] = f"{row.get('lane')}={row.get('status')}"
    for row in list(payload.get("rsi_shadow_queue") or []):
        product_id = str(row.get("product_id") or "")
        if product_id and product_id not in hints:
            hints[product_id] = f"{row.get('lane')}={row.get('status')}"
    return hints


def select_primary_row(rows: list[dict[str, Any]], preferred_lane: str) -> dict[str, Any] | None:
    preferred = next((row for row in rows if str(row.get("strategy") or "") == preferred_lane), None)
    if preferred is not None:
        return preferred
    if not rows:
        return None
    return sorted(rows, key=lambda row: (-to_float(row.get("priority_score")), str(row.get("strategy") or "")))[0]


def router_tier(primary_wave: str, primary_score: float, max_live_lanes: int) -> str:
    if primary_wave == "maintain_live":
        return "active_core"
    if primary_score >= 500:
        return "hypergrowth_core"
    if primary_wave in {"launch_now", "launch_after_wave_1"} and max_live_lanes >= 2:
        return "stack_candidate"
    if primary_wave in {"launch_now", "launch_after_wave_1"}:
        return "expansion_core"
    if primary_wave == "watch_only":
        return "watchlist"
    return "hold_or_debug"


def build_rows() -> list[dict[str, Any]]:
    next_wave = load_json(NEXT_LAUNCH_WAVE_PATH)
    stack_board = load_json(STACK_ADMISSION_PATH)
    runtime_board = load_json(RUNTIME_BOARD_PATH)

    wave_rows_by_coin: dict[str, list[dict[str, Any]]] = {}
    for row in list(next_wave.get("rows") or []):
        coin = str(row.get("coin") or "")
        if coin:
            wave_rows_by_coin.setdefault(coin, []).append(row)

    stack_rows_by_coin = {
        str(row.get("coin") or ""): row for row in list(stack_board.get("rows") or []) if row.get("coin")
    }
    runtime_hint_map = build_runtime_hint_map(runtime_board)

    coins = sorted(set(wave_rows_by_coin) | set(stack_rows_by_coin))
    rows: list[dict[str, Any]] = []
    for coin in coins:
        stack_row = stack_rows_by_coin.get(coin) or {}
        wave_rows = wave_rows_by_coin.get(coin) or []
        preferred_primary_lane = str(stack_row.get("preferred_primary_lane") or "")
        primary_row = select_primary_row(wave_rows, preferred_primary_lane)
        if primary_row is None and not stack_row:
            continue

        primary_lane = preferred_primary_lane or str((primary_row or {}).get("strategy") or "")
        primary_wave = str((primary_row or {}).get("launch_wave") or "")
        primary_score = round(to_float((primary_row or {}).get("priority_score")), 4)
        primary_reason = str((primary_row or {}).get("reason") or stack_row.get("reason") or "")
        max_live_lanes = int(stack_row.get("recommended_max_live_lanes") or 1)
        secondary_candidates = list(stack_row.get("secondary_candidates") or [])
        secondary_lane = secondary_candidates[0] if max_live_lanes > 1 and secondary_candidates else ""

        rows.append(
            {
                "coin": coin,
                "router_tier": router_tier(primary_wave, primary_score, max_live_lanes),
                "primary_lane": primary_lane,
                "primary_family": infer_family(primary_lane),
                "primary_wave": primary_wave or "stack_only",
                "primary_score": primary_score,
                "primary_reason": primary_reason,
                "same_coin_stack_policy": str(stack_row.get("current_stack_policy") or ""),
                "admission_decision": str(stack_row.get("admission_decision") or ""),
                "max_live_lanes": max_live_lanes,
                "secondary_lane": secondary_lane,
                "secondary_family": infer_family(secondary_lane),
                "runtime_hint": runtime_hint_map.get(coin, ""),
            }
        )

    rows.sort(
        key=lambda row: (
            row["router_tier"] not in {"active_core", "hypergrowth_core", "stack_candidate", "expansion_core"},
            {"active_core": 0, "hypergrowth_core": 1, "stack_candidate": 2, "expansion_core": 3, "watchlist": 4, "hold_or_debug": 5}.get(row["router_tier"], 9),
            -to_float(row["primary_score"]),
            row["coin"],
        )
    )
    return rows


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active = [row for row in rows if row["router_tier"] in {"active_core", "hypergrowth_core"}]
    stack = [row for row in rows if row["max_live_lanes"] > 1]
    family_counts: dict[str, int] = {}
    for row in rows:
        family_counts[row["primary_family"]] = family_counts.get(row["primary_family"], 0) + 1
    return {
        "core_coins": [row["coin"] for row in active],
        "stack_enabled_coins": [row["coin"] for row in stack],
        "family_counts": family_counts,
        "total_primary_score": round(sum(to_float(row["primary_score"]) for row in active), 4),
    }


def build_leadership_read(rows: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
    core_labels = ", ".join(str(coin).replace("-USD", "") for coin in summary["core_coins"][:6])
    stack_labels = ", ".join(str(coin).replace("-USD", "") for coin in summary["stack_enabled_coins"])
    lines: list[str] = []
    lines.append(
        "The repo’s hypergrowth path is a router, not a single indicator: momentum where momentum wins, breakout where breakout wins, and RSI only where it is already runtime-real."
    )
    if core_labels:
        lines.append(f"Current core router book is {core_labels}.")
    if stack_labels:
        lines.append(f"Same-coin secondary lanes are only admissible on {stack_labels}; everything else stays single-lane until overlap or runtime proof catches up.")
    lines.append(
        "That means the insane spot strategy is a winner-take-most capital stack with strict per-coin lane caps, not another attempt to force one family across the entire market."
    )
    return lines


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    summary = build_summary(rows)
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(rows, summary),
        "summary": summary,
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Spot Hypergrowth Router Board",
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
            f"- Core coins: `{', '.join(payload['summary']['core_coins'])}`",
            f"- Stack-enabled coins: `{', '.join(payload['summary']['stack_enabled_coins'])}`",
            f"- Total primary score: `{payload['summary']['total_primary_score']}`",
            "",
            "## Rows",
            "",
            "| Coin | Tier | Primary Lane | Primary Family | Wave | Score | Max Live Lanes | Secondary Lane | Runtime Hint |",
            "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {coin} | {router_tier} | {primary_lane} | {primary_family} | {primary_wave} | {primary_score:.4f} | {max_live_lanes} | {secondary_lane} | {runtime_hint} |".format(
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
