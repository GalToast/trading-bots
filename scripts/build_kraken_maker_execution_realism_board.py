#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_execution_realism_board.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_execution_realism_board.md"
DEFAULT_LANES = {
    "dds25_fixed": REPORTS
    / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_ab_events.jsonl",
    "dds50_fastbank": REPORTS
    / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds50_fastbank_ab_events.jsonl",
    "fast_cooldown": REPORTS
    / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_events.jsonl",
}


@dataclass(frozen=True)
class Scenario:
    name: str
    maker_exit_success_rate: float
    latency_slippage_bps: float
    entry_adverse_bps: float
    taker_fee_bps: float = 40.0


DEFAULT_SCENARIOS = [
    Scenario("near_shadow", maker_exit_success_rate=0.98, latency_slippage_bps=1.0, entry_adverse_bps=0.0),
    Scenario("conservative", maker_exit_success_rate=0.75, latency_slippage_bps=5.0, entry_adverse_bps=2.0),
    Scenario("brutal", maker_exit_success_rate=0.50, latency_slippage_bps=10.0, entry_adverse_bps=5.0),
    Scenario("all_profit_exits_taker_fallback", maker_exit_success_rate=0.0, latency_slippage_bps=10.0, entry_adverse_bps=5.0),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    rows: list[dict[str, Any]] = []
    bad = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows, bad


def close_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in events if str(row.get("action") or "") == "close_maker_shadow"]


def open_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in events if str(row.get("action") or "") == "open_maker_shadow"]


def gross_quantity(row: dict[str, Any]) -> float:
    exit_price = to_float(row.get("exit_price"))
    gross_proceeds = to_float(row.get("gross_proceeds"))
    if exit_price > 0.0 and gross_proceeds > 0.0:
        return gross_proceeds / exit_price
    entry_price = to_float(row.get("entry_price"))
    cost = to_float(row.get("cost_usd"))
    entry_fee = to_float(row.get("entry_fee"))
    if entry_price > 0.0 and cost > entry_fee:
        return (cost - entry_fee) / entry_price
    return 0.0


def estimated_bid_from_maker_exit(row: dict[str, Any]) -> float:
    exit_price = to_float(row.get("exit_price"))
    spread_bps = max(0.0, to_float(row.get("spread_bps")))
    if exit_price <= 0.0:
        return 0.0
    return max(0.0, exit_price * (1.0 - spread_bps / 10000.0))


def actual_net_after_adverse_costs(row: dict[str, Any], scenario: Scenario) -> float:
    cost = to_float(row.get("cost_usd"))
    net = to_float(row.get("net"))
    adverse_bps = scenario.entry_adverse_bps + scenario.latency_slippage_bps
    return net - cost * adverse_bps / 10000.0


def taker_fallback_net(row: dict[str, Any], scenario: Scenario) -> float:
    cost = to_float(row.get("cost_usd"))
    qty = gross_quantity(row)
    if qty <= 0.0 or cost <= 0.0:
        return actual_net_after_adverse_costs(row, scenario)
    is_maker_exit = str(row.get("exit_type") or "") == "maker_fill"
    exit_price = estimated_bid_from_maker_exit(row) if is_maker_exit else to_float(row.get("exit_price"))
    exit_price *= max(0.0, 1.0 - scenario.latency_slippage_bps / 10000.0)
    gross = qty * exit_price
    exit_fee = gross * scenario.taker_fee_bps / 10000.0
    entry_adverse = cost * scenario.entry_adverse_bps / 10000.0
    return gross - exit_fee - cost - entry_adverse


def scenario_trade_net(row: dict[str, Any], scenario: Scenario) -> dict[str, Any]:
    actual_net = to_float(row.get("net"))
    maker_net = actual_net_after_adverse_costs(row, scenario)
    is_maker_exit = str(row.get("exit_type") or "") == "maker_fill"
    fallback_net = taker_fallback_net(row, scenario)
    if is_maker_exit:
        p = min(1.0, max(0.0, scenario.maker_exit_success_rate))
        expected_net = p * maker_net + (1.0 - p) * fallback_net
    else:
        p = 0.0
        expected_net = fallback_net
    cost = to_float(row.get("cost_usd"))
    return {
        "product_id": str(row.get("product_id") or ""),
        "actual_net": actual_net,
        "maker_net": maker_net,
        "fallback_net": fallback_net,
        "expected_net": expected_net,
        "expected_net_pct": (expected_net / cost * 100.0) if cost else 0.0,
        "maker_exit_success_rate": p,
        "would_be_red_expected": expected_net <= 0.0,
        "would_be_red_if_fallback": fallback_net <= 0.0,
    }


def scenario_summary(closes: list[dict[str, Any]], scenario: Scenario) -> dict[str, Any]:
    rows = [scenario_trade_net(row, scenario) for row in closes]
    actual_net = sum(to_float(row.get("net")) for row in closes)
    expected_net = sum(row["expected_net"] for row in rows)
    fallback_net = sum(row["fallback_net"] for row in rows)
    expected_wins = sum(1 for row in rows if row["expected_net"] > 0.0)
    fallback_wins = sum(1 for row in rows if row["fallback_net"] > 0.0)
    return {
        "name": scenario.name,
        "maker_exit_success_rate": scenario.maker_exit_success_rate,
        "latency_slippage_bps": scenario.latency_slippage_bps,
        "entry_adverse_bps": scenario.entry_adverse_bps,
        "taker_fee_bps": scenario.taker_fee_bps,
        "actual_net_usd": round(actual_net, 6),
        "expected_net_usd": round(expected_net, 6),
        "all_fallback_net_usd": round(fallback_net, 6),
        "net_retention_pct": round((expected_net / actual_net * 100.0), 4) if actual_net else 0.0,
        "expected_wins": expected_wins,
        "expected_losses": len(rows) - expected_wins,
        "expected_win_rate_pct": round((expected_wins / len(rows) * 100.0), 4) if rows else 0.0,
        "fallback_wins": fallback_wins,
        "fallback_losses": len(rows) - fallback_wins,
        "fallback_win_rate_pct": round((fallback_wins / len(rows) * 100.0), 4) if rows else 0.0,
        "worst_expected_trades": sorted(rows, key=lambda row: row["expected_net"])[:5],
    }


def product_breakdown(closes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in closes:
        grouped[str(row.get("product_id") or "")].append(row)
    total_net = sum(to_float(row.get("net")) for row in closes)
    out: list[dict[str, Any]] = []
    for product, rows in sorted(grouped.items()):
        net = sum(to_float(row.get("net")) for row in rows)
        out.append(
            {
                "product_id": product,
                "closes": len(rows),
                "wins": sum(1 for row in rows if to_float(row.get("net")) > 0.0),
                "losses": sum(1 for row in rows if to_float(row.get("net")) <= 0.0),
                "net_usd": round(net, 6),
                "net_share_pct": round((net / total_net * 100.0), 4) if total_net else 0.0,
                "avg_spread_bps": round(
                    sum(to_float(row.get("spread_bps")) for row in rows) / len(rows), 6
                )
                if rows
                else 0.0,
            }
        )
    return sorted(out, key=lambda row: abs(to_float(row.get("net_usd"))), reverse=True)


def lane_payload(name: str, path: Path, scenarios: list[Scenario]) -> dict[str, Any]:
    events, parse_errors = load_jsonl(path)
    opens = open_events(events)
    closes = close_events(events)
    actual_net = sum(to_float(row.get("net")) for row in closes)
    wins = sum(1 for row in closes if to_float(row.get("net")) > 0.0)
    losses = len(closes) - wins
    exits = Counter(str(row.get("exit_type") or "") for row in closes)
    reasons = Counter(str(row.get("reason") or "") for row in closes)
    products = product_breakdown(closes)
    max_product_share = max([abs(to_float(row.get("net_share_pct"))) for row in products] or [0.0])
    scenario_rows = [scenario_summary(closes, scenario) for scenario in scenarios]
    conservative = next((row for row in scenario_rows if row["name"] == "conservative"), scenario_rows[0] if scenario_rows else {})

    blockers: list[str] = []
    if parse_errors:
        blockers.append("jsonl_parse_errors")
    if not closes:
        blockers.append("no_close_events")
    if losses:
        blockers.append("raw_shadow_losses_present")
    if max_product_share >= 70.0:
        blockers.append("single_product_net_concentration_ge_70pct")
    if to_float(conservative.get("expected_net_usd")) <= 0.0:
        blockers.append("conservative_execution_expected_net_not_positive")
    if to_float(conservative.get("expected_win_rate_pct")) < 90.0:
        blockers.append("conservative_execution_win_rate_below_90pct")
    if to_float(conservative.get("all_fallback_net_usd")) <= 0.0:
        blockers.append("profit_depends_on_maker_exit_fill")
    if exits.get("maker_fill", 0) and not exits.get("taker_insurance", 0):
        blockers.append("no_live_taker_exit_stress_in_raw_tape")

    if not closes:
        verdict = "no_tape"
    elif blockers:
        verdict = "not_live_equivalent_yet"
    else:
        verdict = "stress_pass_shadow_only"

    model_score = 0.0
    if closes and actual_net > 0.0:
        retention = max(0.0, min(1.0, to_float(conservative.get("net_retention_pct")) / 100.0))
        win_rate = max(0.0, min(1.0, to_float(conservative.get("expected_win_rate_pct")) / 100.0))
        concentration = max(0.0, min(1.0, 1.0 - max_product_share / 100.0))
        model_score = 100.0 * (0.50 * retention + 0.30 * win_rate + 0.20 * concentration)

    return {
        "lane": name,
        "events_path": str(path),
        "parse_errors": parse_errors,
        "opens": len(opens),
        "closes": len(closes),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round((wins / len(closes) * 100.0), 4) if closes else 0.0,
        "actual_net_usd": round(actual_net, 6),
        "exit_type_counts": dict(exits),
        "reason_counts": dict(reasons),
        "max_product_net_share_pct": round(max_product_share, 4),
        "model_realism_score_pct": round(model_score, 2),
        "evidence_closeness_cap_pct": 55.0,
        "verdict": verdict,
        "blockers": blockers,
        "products": products,
        "scenarios": scenario_rows,
    }


def parse_lane_args(values: list[str]) -> dict[str, Path]:
    if not values:
        return dict(DEFAULT_LANES)
    out: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--lane must be NAME=PATH, got {value!r}")
        name, raw_path = value.split("=", 1)
        out[name] = Path(raw_path)
    return out


def build_payload(*, lanes: dict[str, Path], scenarios: list[Scenario]) -> dict[str, Any]:
    lane_rows = [lane_payload(name, path, scenarios) for name, path in lanes.items() if path.exists()]
    viable = [row for row in lane_rows if row["verdict"] == "stress_pass_shadow_only"]
    best = max(lane_rows, key=lambda row: to_float(row.get("model_realism_score_pct")), default={})
    blockers = sorted({blocker for row in lane_rows for blocker in row.get("blockers", [])})
    if not lane_rows:
        verdict = "no_tapes_found"
        next_action = "point_board_at_current_kraken_maker_event_tapes"
    elif viable:
        verdict = "shadow_stress_pass_but_live_microfill_needed"
        next_action = "calibrate_with_public_book_queue_tape_then_explicit_user_approved_min_size_live_probe"
    else:
        verdict = "not_close_enough_to_live"
        next_action = "fix_execution_model_blockers_before_capital"
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_execution_realism_board",
        "summary": {
            "verdict": verdict,
            "next_action": next_action,
            "best_model_lane": best.get("lane", ""),
            "best_model_realism_score_pct": best.get("model_realism_score_pct", 0.0),
            "evidence_closeness_cap_pct": 55.0,
            "blockers": blockers,
            "read": (
                "This is a shadow-to-live realism haircut, not live proof. Without real order/fill "
                "telemetry, confidence is capped at 55%; 99% live equivalence requires calibrated "
                "microfill evidence."
            ),
        },
        "scenarios": [scenario.__dict__ for scenario in scenarios],
        "lanes": lane_rows,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload["summary"]
    lines = [
        "# Kraken Maker Execution Realism Board",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Verdict: `{summary['verdict']}`",
        f"- Next action: `{summary['next_action']}`",
        f"- Best model lane: `{summary['best_model_lane']}`",
        f"- Best model realism score: `{summary['best_model_realism_score_pct']}`",
        f"- Evidence closeness cap: `{summary['evidence_closeness_cap_pct']}`",
        f"- Blockers: `{summary['blockers']}`",
        f"- Read: {summary['read']}",
        "",
        "## Lane Stress",
        "",
        "| Lane | Verdict | Closes | Wins | Losses | Actual $ | Model Score | Max Product Share | Conservative $ | Conservative WR | All-Fallback $ | Blockers |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for lane in payload["lanes"]:
        conservative = next((row for row in lane["scenarios"] if row["name"] == "conservative"), {})
        lines.append(
            "| {lane} | {verdict} | {closes} | {wins} | {losses} | {actual_net_usd:.6f} | {model_realism_score_pct:.2f} | {max_product_net_share_pct:.2f}% | {expected_net:.6f} | {expected_wr:.2f}% | {fallback_net:.6f} | {blockers} |".format(
                lane=lane["lane"],
                verdict=lane["verdict"],
                closes=lane["closes"],
                wins=lane["wins"],
                losses=lane["losses"],
                actual_net_usd=lane["actual_net_usd"],
                model_realism_score_pct=lane["model_realism_score_pct"],
                max_product_net_share_pct=lane["max_product_net_share_pct"],
                expected_net=to_float(conservative.get("expected_net_usd")),
                expected_wr=to_float(conservative.get("expected_win_rate_pct")),
                fallback_net=to_float(conservative.get("all_fallback_net_usd")),
                blockers=", ".join(lane["blockers"]),
            )
        )
    lines.extend(["", "## Product Concentration", ""])
    for lane in payload["lanes"]:
        lines.extend(
            [
                f"### {lane['lane']}",
                "",
                "| Product | Closes | Wins | Losses | Net $ | Net Share | Avg Spread bps |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for product in lane["products"][:8]:
            lines.append(
                "| {product_id} | {closes} | {wins} | {losses} | {net_usd:.6f} | {net_share_pct:.2f}% | {avg_spread_bps:.2f} |".format(
                    **product
                )
            )
        lines.append("")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a pessimistic Kraken maker shadow-to-live execution realism board.")
    parser.add_argument("--lane", action="append", default=[], help="Lane event source as NAME=PATH. Repeatable.")
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_MD_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(lanes=parse_lane_args(args.lane), scenarios=DEFAULT_SCENARIOS)
    write_reports(payload, json_path=args.json_path, md_path=args.md_path)
    print(json.dumps({"summary": payload["summary"], "md_path": str(args.md_path)}, indent=2))


if __name__ == "__main__":
    main()
