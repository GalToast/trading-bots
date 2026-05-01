#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_COMPARISON_PATH = REPORTS / "kraken_maker_ab_comparison_board.json"
DEFAULT_GHOST_PATH = REPORTS / "kraken_maker_ab_ghost_giveback_board.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_ab_promotion_gate.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_ab_promotion_gate.md"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def lane_by_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("lanes") if isinstance(payload.get("lanes"), list) else []
    return {str(row.get("lane") or ""): row for row in rows if str(row.get("lane") or "")}


def gate_lane(
    lane: dict[str, Any],
    ghost_lane: dict[str, Any],
    *,
    min_closes: int,
    max_losses: int,
    min_ghost_marks: int,
    require_parallel_exercised: bool,
) -> dict[str, Any]:
    reasons: list[str] = []
    closes = int(to_float(lane.get("realized_closes")))
    losses = int(to_float(lane.get("losses")))
    open_positions = int(to_float(lane.get("open_positions")))
    risk_flags = list(lane.get("risk_flags") or [])
    max_concurrent = int(to_float(lane.get("max_concurrent_positions")))
    ghost_marks = int(to_float(ghost_lane.get("ghost_marks")))
    ghost_verdict = str(ghost_lane.get("verdict") or "collect_no_ghost_marks")
    realized_net = to_float(lane.get("realized_net_usd"))
    if closes < min_closes:
        reasons.append(f"needs_{min_closes}_closes")
    if losses > max_losses:
        reasons.append("loss_limit_exceeded")
    if closes > 0 and realized_net <= 0.0:
        reasons.append("not_net_positive")
    if open_positions > 0:
        reasons.append("open_residue")
    if risk_flags:
        reasons.append("risk_flags_present")
    if ghost_marks < min_ghost_marks:
        reasons.append(f"needs_{min_ghost_marks}_ghost_marks")
    if ghost_verdict != "banking_supported":
        reasons.append("ghost_banking_not_supported")
    if require_parallel_exercised and max_concurrent < 3:
        reasons.append("parallel_not_exercised")
    if not reasons:
        gate = "eligible_for_next_shadow_stage"
    elif losses <= max_losses and not risk_flags and (closes == 0 or realized_net > 0.0):
        gate = "collect_more"
    else:
        gate = "do_not_promote"
    return {
        "lane": str(lane.get("lane") or ""),
        "gate": gate,
        "reasons": reasons,
        "realized_closes": closes,
        "wins": int(to_float(lane.get("wins"))),
        "losses": losses,
        "realized_net_usd": round(realized_net, 6),
        "cash_usd": round(to_float(lane.get("cash_usd")), 6),
        "avg_net_pct": round(to_float(lane.get("avg_net_pct")), 6),
        "max_concurrent_positions": max_concurrent,
        "open_positions": open_positions,
        "ghost_marks": ghost_marks,
        "ghost_verdict": ghost_verdict,
        "ghost_avg_delta_net": round(to_float(ghost_lane.get("avg_delta_net")), 6),
        "realized_net_per_hour": round(to_float(lane.get("realized_net_per_hour")), 6),
    }


def build_payload(
    *,
    comparison_path: Path = DEFAULT_COMPARISON_PATH,
    ghost_path: Path = DEFAULT_GHOST_PATH,
    min_closes: int = 20,
    max_losses: int = 0,
    min_ghost_marks: int = 20,
) -> dict[str, Any]:
    comparison = load_json(comparison_path)
    ghost = load_json(ghost_path)
    ghost_lanes = lane_by_name(ghost)
    rows = []
    for lane in comparison.get("lanes") or []:
        lane_name = str(lane.get("lane") or "")
        rows.append(
            gate_lane(
                lane,
                ghost_lanes.get(lane_name, {}),
                min_closes=min_closes,
                max_losses=max_losses,
                min_ghost_marks=min_ghost_marks,
                require_parallel_exercised=lane_name
                in {
                    "parallel_only",
                    "parallel_cooldown",
                    "parallel_ratio50",
                    "parallel_ratio50_taker_guard",
                    "parallel_ratio50_taker_guard_live_exec",
                    "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
                    "parallel_ratio50_taker_guard_live_exec_dds25",
                    "parallel_ratio50_taker_guard_live_exec_dds25_fixed",
                    "parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1",
                },
            )
        )
    eligible = [row["lane"] for row in rows if row["gate"] == "eligible_for_next_shadow_stage"]
    return {
        "generated_at": comparison.get("generated_at") or ghost.get("generated_at") or "",
        "mode": "kraken_maker_ab_promotion_gate",
        "parameters": {
            "comparison_path": str(comparison_path),
            "ghost_path": str(ghost_path),
            "min_closes": min_closes,
            "max_losses": max_losses,
            "min_ghost_marks": min_ghost_marks,
            "promotion_scope": "next_shadow_stage_only_not_live_orders",
        },
        "summary": {
            "lanes": len(rows),
            "eligible_lanes": eligible,
            "collect_more_lanes": [row["lane"] for row in rows if row["gate"] == "collect_more"],
            "do_not_promote_lanes": [row["lane"] for row in rows if row["gate"] == "do_not_promote"],
            "read": (
                "This gate is for the next shadow stage only. It is not permission to place live orders. "
                "Live promotion would require a separate broker/execution-readiness gate."
            ),
        },
        "lanes": rows,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload.get("summary") or {}
    params = payload.get("parameters") or {}
    lines = [
        "# Kraken Maker A/B Promotion Gate",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Scope: `{params.get('promotion_scope')}`",
        f"- Min closes: `{params.get('min_closes')}`",
        f"- Max losses: `{params.get('max_losses')}`",
        f"- Min ghost marks: `{params.get('min_ghost_marks')}`",
        f"- Eligible lanes: `{summary.get('eligible_lanes')}`",
        f"- Collect-more lanes: `{summary.get('collect_more_lanes')}`",
        f"- Do-not-promote lanes: `{summary.get('do_not_promote_lanes')}`",
        f"- Read: {summary.get('read')}",
        "",
        "## Lanes",
        "",
        "| Lane | Gate | Reasons | Closes | Wins | Losses | Net $ | Avg Net % | Max Concurrent | Ghost Marks | Ghost Verdict | Open |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for row in payload.get("lanes") or []:
        reasons = ", ".join(row.get("reasons") or []) or "none"
        render_row = dict(row)
        render_row["reasons"] = reasons
        lines.append(
            "| {lane} | {gate} | {reasons} | {realized_closes} | {wins} | {losses} | {realized_net_usd:.6f} | {avg_net_pct:.4f} | {max_concurrent_positions} | {ghost_marks} | {ghost_verdict} | {open_positions} |".format(
                **render_row,
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gate Kraken maker A/B lanes for next shadow-stage promotion.")
    parser.add_argument("--comparison-path", default=str(DEFAULT_COMPARISON_PATH))
    parser.add_argument("--ghost-path", default=str(DEFAULT_GHOST_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--min-closes", type=int, default=20)
    parser.add_argument("--max-losses", type=int, default=0)
    parser.add_argument("--min-ghost-marks", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(
        comparison_path=Path(args.comparison_path),
        ghost_path=Path(args.ghost_path),
        min_closes=int(args.min_closes),
        max_losses=int(args.max_losses),
        min_ghost_marks=int(args.min_ghost_marks),
    )
    write_reports(payload, json_path=Path(args.json_path), md_path=Path(args.md_path))
    print(json.dumps({"summary": payload["summary"], "md_path": str(Path(args.md_path).resolve())}, indent=2))


if __name__ == "__main__":
    main()
