#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_ab_comparison_board.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_ab_comparison_board.md"

LANES = [
    {
        "lane": "baseline",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_shadow_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_shadow_events.jsonl",
        "hypothesis": "current guarded tight-gate top-1 baseline",
    },
    {
        "lane": "cooldown_only",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_ab_events.jsonl",
        "hypothesis": "product-scoped shorter reentry cooldown on repeated winners",
    },
    {
        "lane": "parallel_only",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ab_events.jsonl",
        "hypothesis": "top-3 systemic parallel entries under the baseline cooldown",
    },
    {
        "lane": "parallel_cooldown",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_parallel_cooldown_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_cooldown_ab_events.jsonl",
        "hypothesis": "top-3 systemic parallel entries plus product-scoped cooldown overrides",
    },
    {
        "lane": "cooldown_size12",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_size12_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_size12_ab_events.jsonl",
        "hypothesis": "next-shadow-stage product-scoped cooldown with 12 USD quote cap",
    },
    {
        "lane": "cooldown_ratio50",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_ratio50_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_ratio50_ab_events.jsonl",
        "hypothesis": "product-scoped cooldown plus live/board spread ratio guard at 0.50",
    },
    {
        "lane": "parallel_ratio50",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_ab_events.jsonl",
        "hypothesis": "top-3 systemic parallel entries plus live/board spread ratio guard at 0.50 and 8 USD quote cap",
    },
    {
        "lane": "parallel_ratio50_taker_guard",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_ab_events.jsonl",
        "hypothesis": "parallel_ratio50 rerun after taker insurance exits were corrected to execute immediately at bid with taker fees",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_ab_events.jsonl",
        "hypothesis": "parallel_ratio50 taker-guard successor with 10 USD quote cap and radar-backed min-notional enforcement",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_fast_cooldown",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_fast_cooldown_ab_events.jsonl",
        "hypothesis": "live-executable min-notional proof with compressed product cooldowns to test throughput and top-3 exercise",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_dds25",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_ab_events.jsonl",
        "hypothesis": "isolated 25 USD max-quote DDS size proof with post-only reject simulation after fast-cooldown maturity",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_dds25_fixed",
        "state_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_ab_state.json",
        "events_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_ab_events.jsonl",
        "hypothesis": "fresh 25 USD max-quote DDS size proof after the multi-burst accounting fix",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1",
        "state_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1_ab_state.json",
        "events_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1_ab_events.jsonl",
        "hypothesis": "fresh Texas-safe 25 USD max-quote DDS size proof after excluding account-restricted FOLKS-USD",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_dds25_hold45",
        "state_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_hold45_ab_state.json",
        "events_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_hold45_ab_events.jsonl",
        "hypothesis": "fresh 25 USD DDS top-3 exercise challenger with harvest exits delayed 45 seconds while risk exits stay active",
    },
    {
        "lane": "parallel_ratio50_taker_guard_live_exec_dds50_fastbank",
        "state_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds50_fastbank_ab_state.json",
        "events_path": REPORTS
        / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds50_fastbank_ab_events.jsonl",
        "hypothesis": "fresh 50 USD DDS serial size challenger preserving the fast-bank harvest law after DDS25-fixed reached 20/0",
    },
    {
        "lane": "cooldown_ratio50_size12",
        "state_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_ratio50_size12_ab_state.json",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_ratio50_size12_ab_events.jsonl",
        "hypothesis": "single-position 12 USD quote cap plus live/board spread ratio guard at 0.50",
    },
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def max_concurrent_positions(events: list[dict[str, Any]]) -> int:
    active: set[str] = set()
    max_active = 0
    for event in events:
        action = str(event.get("action") or "")
        product_id = str(event.get("product_id") or "")
        if not product_id:
            continue
        if action == "open_maker_shadow":
            active.add(product_id)
            max_active = max(max_active, len(active))
        elif action == "close_maker_shadow":
            active.discard(product_id)
    return max_active


def lane_verdict(*, closes: int, losses: int, realized_net: float, risk_flags: list[str], max_concurrent: int) -> str:
    if risk_flags:
        return "quarantine_risk_flags"
    if closes <= 0:
        return "collect_no_closes"
    if realized_net <= 0:
        return "kill_or_rework_red"
    if losses > 0:
        return "collect_with_loss_watch"
    if closes < 20:
        return "promising_collect_more"
    if max_concurrent >= 3:
        return "parallel_candidate_maturing"
    return "baseline_or_cooldown_candidate_maturing"


def summarize_lane(config: dict[str, Any]) -> dict[str, Any]:
    state_payload = load_json(Path(config["state_path"]))
    state = state_payload.get("state") if isinstance(state_payload.get("state"), dict) else state_payload
    events = load_events(Path(config["events_path"]))
    opens = [event for event in events if str(event.get("action") or "") == "open_maker_shadow"]
    closes = [event for event in events if str(event.get("action") or "") == "close_maker_shadow"]
    losses = [event for event in closes if to_float(event.get("net")) <= 0.0]
    wins = [event for event in closes if to_float(event.get("net")) > 0.0]
    net_pcts = [to_float(event.get("net_pct")) for event in closes]
    products = Counter(str(event.get("product_id") or "") for event in closes)
    product_net = Counter()
    for event in closes:
        product_net[str(event.get("product_id") or "")] += to_float(event.get("net"))
    timestamps = [parse_time(event.get("ts_utc")) for event in events]
    timestamps = [ts for ts in timestamps if ts is not None]
    elapsed_hours = 0.0
    if len(timestamps) >= 2:
        elapsed_hours = max((max(timestamps) - min(timestamps)).total_seconds() / 3600.0, 0.0)
    realized_net = to_float(state.get("realized_net_usd"), sum(to_float(event.get("net")) for event in closes))
    risk_flags = list(state.get("risk_flags") or [])
    max_concurrent = max_concurrent_positions(events)
    return {
        "lane": config["lane"],
        "hypothesis": config["hypothesis"],
        "state_path": str(config["state_path"]),
        "events_path": str(config["events_path"]),
        "cash_usd": round(to_float(state.get("cash_usd")), 6),
        "realized_net_usd": round(realized_net, 6),
        "realized_closes": int(to_float(state.get("realized_closes"), len(closes))),
        "open_positions": len(state.get("active_positions") or {}),
        "open_events": len(opens),
        "close_events": len(closes),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closes), 6) if closes else 0.0,
        "avg_net_pct": round(sum(net_pcts) / len(net_pcts), 6) if net_pcts else 0.0,
        "max_concurrent_positions": max_concurrent,
        "elapsed_hours": round(elapsed_hours, 6),
        "realized_net_per_hour": round(realized_net / elapsed_hours, 6) if elapsed_hours > 0 else 0.0,
        "risk_flags": risk_flags,
        "verdict": lane_verdict(
            closes=len(closes),
            losses=len(losses),
            realized_net=realized_net,
            risk_flags=risk_flags,
            max_concurrent=max_concurrent,
        ),
        "products": [
            {
                "product_id": product_id,
                "closes": count,
                "net_usd": round(product_net[product_id], 6),
            }
            for product_id, count in products.most_common()
            if product_id
        ],
    }


def build_payload(lanes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [summarize_lane(config) for config in (LANES if lanes is None else lanes)]
    leader_by_net = max(rows, key=lambda row: to_float(row.get("realized_net_usd")), default={})
    leader_by_rate = max(rows, key=lambda row: to_float(row.get("realized_net_per_hour")), default={})
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_ab_comparison_board",
        "summary": {
            "lanes": len(rows),
            "green_lanes": sum(1 for row in rows if to_float(row.get("realized_net_usd")) > 0 and not row.get("risk_flags")),
            "leader_by_realized_net": leader_by_net.get("lane", ""),
            "leader_by_realized_net_per_hour": leader_by_rate.get("lane", ""),
            "read": (
                "Treat short A/B net/hour as directional only. The promotion question is repeatability, "
                "loss behavior, and ghost-horizon giveback under the same tight gate."
            ),
        },
        "lanes": rows,
        "kill_or_park": [
            {
                "hypothesis": "loose gate",
                "reason": "historical extra fills include old loser products; tight gate is required before parallelism helps",
            },
            {
                "hypothesis": "BMB/no-fill spread-only promotion",
                "reason": "wide-spread low-MER path has no public fill-supported proof yet",
            },
            {
                "hypothesis": "global cooldown mutation",
                "reason": "cooldown pressure is product-specific; mixed/losing products should not churn faster",
            },
        ],
        "next_tests": [
            "Let all A/B lanes mature to at least 20 closes or one red packet before promotion talk.",
            "Compare ghost-horizon giveback on profitable closes by lane; fast reentry is only useful if exits were not consistently perfect.",
            "Keep quote-size ladder tests isolated from cooldown and parallel proof tapes so sizing does not contaminate signal tests.",
        ],
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Maker A/B Comparison Board",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Green lanes: `{summary.get('green_lanes')}` / `{summary.get('lanes')}`",
        f"- Leader by realized net: `{summary.get('leader_by_realized_net')}`",
        f"- Leader by realized net/hour: `{summary.get('leader_by_realized_net_per_hour')}`",
        f"- Read: {summary.get('read')}",
        "",
        "## Lanes",
        "",
        "| Lane | Verdict | Closes | Wins | Losses | Net $ | Cash $ | Avg Net % | Max Concurrent | Net $/h | Open |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload.get("lanes") or []:
        lines.append(
            "| {lane} | {verdict} | {realized_closes} | {wins} | {losses} | {realized_net_usd:.6f} | {cash_usd:.6f} | {avg_net_pct:.4f} | {max_concurrent_positions} | {realized_net_per_hour:.6f} | {open_positions} |".format(
                **row
            )
        )
    lines.extend(["", "## Kill Or Park", ""])
    for item in payload.get("kill_or_park") or []:
        lines.append(f"- `{item['hypothesis']}`: {item['reason']}")
    lines.extend(["", "## Next Tests", ""])
    for item in payload.get("next_tests") or []:
        lines.append(f"- {item}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Kraken maker baseline and A/B shadow lanes.")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload()
    write_reports(payload, json_path=Path(args.json_path), md_path=Path(args.md_path))
    print(json.dumps({"summary": payload["summary"], "md_path": str(Path(args.md_path).resolve())}, indent=2))


if __name__ == "__main__":
    main()
