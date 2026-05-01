#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_spot_maker_machinegun_shadow_events.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_mfe_exit_replay.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_mfe_exit_replay.md"


@dataclass(frozen=True)
class ExitPolicy:
    name: str
    activation_pct: float
    giveback_pct: float | None = None
    giveback_fraction_under_2pct: float | None = None
    giveback_fraction_over_2pct: float | None = None


DEFAULT_POLICIES = [
    ExitPolicy("insurance_activate0p00_giveback0p05", 0.0, giveback_pct=0.05),
    ExitPolicy("insurance_activate0p05_giveback0p05", 0.05, giveback_pct=0.05),
    ExitPolicy("insurance_activate0p10_giveback0p10", 0.10, giveback_pct=0.10),
    ExitPolicy(
        "accelerating_activate0p50_giveback25pct_to_10pct",
        0.50,
        giveback_fraction_under_2pct=0.25,
        giveback_fraction_over_2pct=0.10,
    ),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def close_trades(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("action") or "") != "close_maker_shadow":
            continue
        cost = to_float(event.get("cost_usd"))
        net_pct = to_float(event.get("net_pct"))
        max_net_pct = to_float(event.get("max_net_pct_on_cost"), net_pct)
        trades.append(
            {
                "product_id": str(event.get("product_id") or ""),
                "opened_at": str(event.get("opened_at") or ""),
                "closed_at": str(event.get("ts_utc") or ""),
                "reason": str(event.get("reason") or ""),
                "cost_usd": cost,
                "net": to_float(event.get("net")),
                "net_pct": net_pct,
                "max_net_pct_on_cost": max_net_pct,
                "max_net_pnl": to_float(event.get("max_net_pnl"), cost * max_net_pct / 100.0),
                "age_seconds": to_float(event.get("age_seconds")),
                "entry_mer": to_float(event.get("entry_mer")),
                "spread_bps": to_float(event.get("spread_bps")),
            }
        )
    return trades


def policy_giveback(policy: ExitPolicy, peak_pct: float) -> float:
    if policy.giveback_pct is not None:
        return max(0.0, policy.giveback_pct)
    under = policy.giveback_fraction_under_2pct
    over = policy.giveback_fraction_over_2pct
    fraction = over if peak_pct >= 2.0 and over is not None else under
    return max(0.0, peak_pct * float(fraction or 0.0))


def replay_trade(trade: dict[str, Any], policy: ExitPolicy) -> dict[str, Any]:
    actual_net_pct = to_float(trade.get("net_pct"))
    peak_pct = to_float(trade.get("max_net_pct_on_cost"), actual_net_pct)
    cost = to_float(trade.get("cost_usd"))
    if peak_pct < policy.activation_pct:
        simulated_pct = actual_net_pct
        actual_net = to_float(trade.get("net"))
        simulated_net = actual_net
        changed = False
        reason = "not_activated"
    else:
        stop_pct = peak_pct - policy_giveback(policy, peak_pct)
        if actual_net_pct < stop_pct:
            simulated_pct = stop_pct
            simulated_net = cost * simulated_pct / 100.0
            changed = True
            reason = "trail_stop_improves_close"
        else:
            simulated_pct = actual_net_pct
            actual_net = to_float(trade.get("net"))
            simulated_net = actual_net
            changed = False
            reason = "actual_not_below_trail"
    actual_net = to_float(trade.get("net"))
    return {
        "product_id": trade.get("product_id"),
        "actual_net": round(actual_net, 6),
        "actual_net_pct": round(actual_net_pct, 6),
        "peak_net_pct": round(peak_pct, 6),
        "simulated_net": round(simulated_net, 6),
        "simulated_net_pct": round(simulated_pct, 6),
        "delta_net": round(simulated_net - actual_net, 6),
        "changed": changed,
        "reason": reason,
        "exit_reason": trade.get("reason"),
    }


def summarize_policy(trades: list[dict[str, Any]], policy: ExitPolicy) -> dict[str, Any]:
    rows = [replay_trade(trade, policy) for trade in trades]
    actual_net = sum(to_float(row.get("actual_net")) for row in rows)
    simulated_net = sum(to_float(row.get("simulated_net")) for row in rows)
    improved = [row for row in rows if bool(row.get("changed")) and to_float(row.get("delta_net")) > 0.000001]
    worsened = [row for row in rows if bool(row.get("changed")) and to_float(row.get("delta_net")) < -0.000001]
    return {
        "policy": policy.name,
        "activation_pct": policy.activation_pct,
        "giveback_pct": policy.giveback_pct,
        "giveback_fraction_under_2pct": policy.giveback_fraction_under_2pct,
        "giveback_fraction_over_2pct": policy.giveback_fraction_over_2pct,
        "trades": len(rows),
        "actual_net_usd": round(actual_net, 6),
        "simulated_net_usd": round(simulated_net, 6),
        "delta_net_usd": round(simulated_net - actual_net, 6),
        "improved_trades": len(improved),
        "worsened_trades": len(worsened),
        "improved_products": sorted({str(row.get("product_id")) for row in improved}),
        "largest_improvements": sorted(improved, key=lambda row: to_float(row.get("delta_net")), reverse=True)[:10],
        "rows": rows,
    }


def build_payload(*, events_path: Path, policies: list[ExitPolicy]) -> dict[str, Any]:
    events = load_events(events_path)
    trades = close_trades(events)
    winners = [trade for trade in trades if to_float(trade.get("net")) > 0]
    losers = [trade for trade in trades if to_float(trade.get("net")) <= 0]
    censored_winners = [
        trade
        for trade in winners
        if abs(to_float(trade.get("max_net_pct_on_cost")) - to_float(trade.get("net_pct"))) < 0.0001
    ]
    green_then_red = [
        trade
        for trade in losers
        if to_float(trade.get("max_net_pct_on_cost")) > 0.0
    ]
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_mfe_exit_replay",
        "parameters": {
            "events_path": str(events_path),
            "censoring_note": "Recorded MFE is path-censored by current exits. Winner upside beyond the actual close cannot be proven from this tape; only loser insurance where max_net_pct_on_cost was positive can be replayed conservatively.",
        },
        "summary": {
            "closed_trades": len(trades),
            "winners": len(winners),
            "losers": len(losers),
            "censored_winners": len(censored_winners),
            "green_then_red_losers": len(green_then_red),
            "actual_net_usd": round(sum(to_float(trade.get("net")) for trade in trades), 6),
            "green_then_red_loser_products": sorted({str(trade.get("product_id")) for trade in green_then_red}),
        },
        "policy_summaries": [summarize_policy(trades, policy) for policy in policies],
        "green_then_red_losers": green_then_red,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# Kraken Maker MFE Exit Replay",
        "",
        "## Summary",
        "",
        f"- Closed trades: `{payload['summary']['closed_trades']}`",
        f"- Winners: `{payload['summary']['winners']}`",
        f"- Losers: `{payload['summary']['losers']}`",
        f"- Censored winners: `{payload['summary']['censored_winners']}`",
        f"- Green-then-red losers: `{payload['summary']['green_then_red_losers']}`",
        f"- Actual net: `${payload['summary']['actual_net_usd']:.6f}`",
        "",
        "Winner upside is censored by the current runner because recorded MFE often equals the actual harvest close. Treat this as an insurance replay, not proof that trailing would have captured unobserved upside.",
        "",
        "## Policy Replay",
        "",
        "| Policy | Trades | Actual $ | Simulated $ | Delta $ | Improved | Worsened | Improved Products |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for summary in payload["policy_summaries"]:
        products = ", ".join(summary["improved_products"])
        lines.append(
            "| {policy} | {trades} | {actual_net_usd:.6f} | {simulated_net_usd:.6f} | {delta_net_usd:.6f} | {improved_trades} | {worsened_trades} | {products} |".format(
                products=products,
                **summary,
            )
        )
    lines.extend(["", "## Green-Then-Red Losers", ""])
    if payload["green_then_red_losers"]:
        lines.extend(
            [
                "| Product | Net % | Peak Net % | Net $ | Age Sec | Reason |",
                "| --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for trade in payload["green_then_red_losers"]:
            lines.append(
                "| {product_id} | {net_pct:.4f} | {max_net_pct_on_cost:.4f} | {net:.6f} | {age_seconds:.1f} | {reason} |".format(
                    **trade
                )
            )
    else:
        lines.append("No loser had positive recorded MFE.")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay Kraken maker MFE exit policies against recorded close events.")
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(events_path=Path(args.events_path), policies=DEFAULT_POLICIES)
    write_reports(payload, json_path=Path(args.json_path), md_path=Path(args.md_path))
    print(
        json.dumps(
            {
                "summary": payload["summary"],
                "policy_summaries": [
                    {key: value for key, value in summary.items() if key != "rows"}
                    for summary in payload["policy_summaries"]
                ],
                "md_path": args.md_path,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
