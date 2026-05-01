#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_JSON_PATH = REPORTS / "kraken_maker_red_packet_autopsy.json"
DEFAULT_MD_PATH = REPORTS / "kraken_maker_red_packet_autopsy.md"

LANES = [
    {
        "lane": "cooldown_only",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_ab_events.jsonl",
    },
    {
        "lane": "parallel_cooldown",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_cooldown_ab_events.jsonl",
    },
    {
        "lane": "cooldown_size12",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_cooldown_size12_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_ab_events.jsonl",
    },
    {
        "lane": "parallel_ratio50_taker_guard",
        "events_path": REPORTS / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_ab_events.jsonl",
    },
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
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def pair_open_close(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    opens_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    fallback_opens: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades: list[dict[str, Any]] = []
    for event in events:
        action = str(event.get("action") or "")
        product_id = str(event.get("product_id") or "")
        if not product_id:
            continue
        if action == "open_maker_shadow":
            opened_at = str(event.get("ts_utc") or "")
            bundle = {"open": event, "events": []}
            opens_by_key[(product_id, opened_at)] = bundle
            fallback_opens[product_id].append(bundle)
        elif action == "close_maker_shadow":
            opened_at = str(event.get("opened_at") or "")
            bundle = opens_by_key.pop((product_id, opened_at), None)
            if bundle is None and fallback_opens.get(product_id):
                bundle = fallback_opens[product_id][-1]
            if bundle is not None and bundle in fallback_opens.get(product_id, []):
                fallback_opens[product_id].remove(bundle)
            trades.append(
                {
                    "open": (bundle or {}).get("open") or {},
                    "close": event,
                    "events": list((bundle or {}).get("events") or []),
                }
            )
        else:
            for bundle in fallback_opens.get(product_id, []):
                bundle.setdefault("events", []).append(event)
    return trades


def live_spread_ratio(open_event: dict[str, Any]) -> float:
    board_spread = to_float(open_event.get("board_spread_bps"))
    live_spread = to_float(open_event.get("live_spread_bps"))
    return live_spread / board_spread if board_spread > 0.0 else 0.0


def has_trade_event(trade: dict[str, Any], action: str) -> bool:
    return any(str(event.get("action") or "") == action for event in trade.get("events") or [])


def trade_row(lane: str, trade: dict[str, Any]) -> dict[str, Any]:
    open_event = trade.get("open") or {}
    close_event = trade.get("close") or {}
    net = to_float(close_event.get("net"))
    live_spread = to_float(open_event.get("live_spread_bps"))
    board_spread = to_float(open_event.get("board_spread_bps"))
    ratio = live_spread_ratio(open_event)
    max_net_pct = to_float(close_event.get("max_net_pct_on_cost"))
    reasons: list[str] = []
    if str(close_event.get("reason") or "") == "maker_no_mfe_adverse_stop":
        reasons.append("no_mfe_adverse_stop")
    if live_spread < 50.0:
        reasons.append("entry_live_spread_below_50bps")
    if ratio < 0.50:
        reasons.append("entry_live_to_board_spread_ratio_below_0_50")
    if ratio < 0.75:
        reasons.append("entry_live_to_board_spread_ratio_below_0_75")
    if max_net_pct <= 0.0:
        reasons.append("never_reached_positive_net_mfe")
    if has_trade_event(trade, "maker_exit_miss"):
        reasons.append("maker_exit_miss_before_close")
    if net <= 0.0 and max_net_pct > 0.0:
        reasons.append("positive_mfe_closed_red")
    if net <= 0.0 and to_float(close_event.get("exit_fee_bps")) >= 40.0:
        reasons.append("taker_fee_loss")
    if net <= 0.0 and str(close_event.get("reason") or "") == "maker_green_then_red_insurance":
        reasons.append("green_then_red_insurance_loss")
    if net <= 0.0 and to_float(close_event.get("spread_bps")) < 75.0:
        reasons.append("close_spread_below_75bps")
    return {
        "lane": lane,
        "product_id": str(close_event.get("product_id") or ""),
        "opened_at": str(close_event.get("opened_at") or open_event.get("ts_utc") or ""),
        "closed_at": str(close_event.get("ts_utc") or ""),
        "reason": str(close_event.get("reason") or ""),
        "net_usd": round(net, 6),
        "net_pct": round(to_float(close_event.get("net_pct")), 6),
        "age_seconds": round(to_float(close_event.get("age_seconds")), 6),
        "entry_mer": round(to_float(close_event.get("entry_mer") or open_event.get("mer")), 6),
        "board_spread_bps": round(board_spread, 6),
        "entry_live_spread_bps": round(live_spread, 6),
        "live_to_board_spread_ratio": round(ratio, 6),
        "close_spread_bps": round(to_float(close_event.get("spread_bps")), 6),
        "max_net_pct_on_cost": round(max_net_pct, 6),
        "min_net_pct_on_cost": round(to_float(close_event.get("min_net_pct_on_cost")), 6),
        "potential_blockers": reasons,
    }


def evaluate_rule(
    name: str,
    trades: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    blocked = [trade for trade in trades if predicate(trade)]
    blocked_losses = [trade for trade in blocked if to_float((trade.get("close") or {}).get("net")) <= 0.0]
    blocked_wins = [trade for trade in blocked if to_float((trade.get("close") or {}).get("net")) > 0.0]
    return {
        "rule": name,
        "blocked_trades": len(blocked),
        "blocked_losses": len(blocked_losses),
        "blocked_wins": len(blocked_wins),
        "loss_usd_avoided": round(sum(abs(to_float((trade.get("close") or {}).get("net"))) for trade in blocked_losses), 6),
        "win_usd_forfeited": round(sum(to_float((trade.get("close") or {}).get("net")) for trade in blocked_wins), 6),
    }


def build_payload(lanes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    lane_rows = []
    all_trades: list[dict[str, Any]] = []
    loss_rows: list[dict[str, Any]] = []
    for config in (LANES if lanes is None else lanes):
        lane = str(config["lane"])
        events_path = Path(config["events_path"])
        events = load_events(events_path)
        trades = pair_open_close(events)
        all_trades.extend(trades)
        losses = [trade for trade in trades if to_float((trade.get("close") or {}).get("net")) <= 0.0]
        wins = [trade for trade in trades if to_float((trade.get("close") or {}).get("net")) > 0.0]
        lane_loss_rows = [trade_row(lane, trade) for trade in losses]
        loss_rows.extend(lane_loss_rows)
        lane_rows.append(
            {
                "lane": lane,
                "events_path": str(events_path),
                "trades": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "net_usd": round(sum(to_float((trade.get("close") or {}).get("net")) for trade in trades), 6),
                "loss_products": sorted({row["product_id"] for row in lane_loss_rows}),
            }
        )

    rules = [
        evaluate_rule(
            "entry_live_spread_bps_lt_50",
            all_trades,
            lambda trade: to_float((trade.get("open") or {}).get("live_spread_bps")) < 50.0,
        ),
        evaluate_rule(
            "entry_live_to_board_spread_ratio_lt_0_50",
            all_trades,
            lambda trade: live_spread_ratio(trade.get("open") or {}) < 0.50,
        ),
        evaluate_rule(
            "entry_live_spread_bps_lt_75",
            all_trades,
            lambda trade: to_float((trade.get("open") or {}).get("live_spread_bps")) < 75.0,
        ),
        evaluate_rule(
            "entry_live_to_board_spread_ratio_lt_0_75",
            all_trades,
            lambda trade: live_spread_ratio(trade.get("open") or {}) < 0.75,
        ),
        evaluate_rule(
            "maker_exit_miss_before_close",
            all_trades,
            lambda trade: has_trade_event(trade, "maker_exit_miss"),
        ),
        evaluate_rule(
            "positive_mfe_taker_insurance_loss",
            all_trades,
            lambda trade: to_float((trade.get("close") or {}).get("net")) <= 0.0
            and to_float((trade.get("close") or {}).get("max_net_pct_on_cost")) > 0.0
            and to_float((trade.get("close") or {}).get("exit_fee_bps")) >= 40.0,
        ),
        evaluate_rule(
            "green_then_red_insurance_loss",
            all_trades,
            lambda trade: to_float((trade.get("close") or {}).get("net")) <= 0.0
            and str((trade.get("close") or {}).get("reason") or "") == "maker_green_then_red_insurance",
        ),
        evaluate_rule(
            "close_spread_bps_lt_75",
            all_trades,
            lambda trade: to_float((trade.get("close") or {}).get("spread_bps")) < 75.0,
        ),
    ]
    products = sorted({row["product_id"] for row in loss_rows})
    return {
        "generated_at": utc_now_iso(),
        "mode": "kraken_maker_red_packet_autopsy",
        "summary": {
            "lanes": len(lane_rows),
            "trades": sum(row["trades"] for row in lane_rows),
            "losses": len(loss_rows),
            "loss_products": products,
            "verdict": "red_packet_present" if loss_rows else "no_red_packets",
            "read": (
                "Loss rows should falsify promotion until a blocker is proven not to discard too much winning edge."
            ),
        },
        "lanes": lane_rows,
        "losses": loss_rows,
        "candidate_blockers": rules,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Maker Red Packet Autopsy",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Verdict: `{summary.get('verdict')}`",
        f"- Trades: `{summary.get('trades')}`",
        f"- Losses: `{summary.get('losses')}`",
        f"- Loss products: `{summary.get('loss_products')}`",
        f"- Read: {summary.get('read')}",
        "",
        "## Lanes",
        "",
        "| Lane | Trades | Wins | Losses | Net $ | Loss Products |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload.get("lanes") or []:
        lines.append(
            "| {lane} | {trades} | {wins} | {losses} | {net_usd:.6f} | {loss_products} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Losses",
            "",
            "| Lane | Product | Reason | Net $ | Net % | Age s | Entry Live Spread | Board Spread | Live/Board | Max Net % | Blockers |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload.get("losses") or []:
        lines.append(
            "| {lane} | {product_id} | {reason} | {net_usd:.6f} | {net_pct:.4f} | {age_seconds:.1f} | {entry_live_spread_bps:.2f} | {board_spread_bps:.2f} | {live_to_board_spread_ratio:.3f} | {max_net_pct_on_cost:.4f} | {potential_blockers} |".format(
                **{**row, "potential_blockers": ", ".join(row.get("potential_blockers") or [])}
            )
        )
    lines.extend(
        [
            "",
            "## Candidate Blockers",
            "",
            "| Rule | Blocked Trades | Blocked Losses | Blocked Wins | Loss $ Avoided | Win $ Forfeited |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload.get("candidate_blockers") or []:
        lines.append(
            "| {rule} | {blocked_trades} | {blocked_losses} | {blocked_wins} | {loss_usd_avoided:.6f} | {win_usd_forfeited:.6f} |".format(
                **row
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build red-packet autopsy for Kraken maker A/B lanes.")
    parser.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--md-path", type=Path, default=DEFAULT_MD_PATH)
    args = parser.parse_args()
    payload = build_payload()
    write_reports(payload, json_path=args.json_path, md_path=args.md_path)
    print(json.dumps({"summary": payload["summary"], "md_path": str(args.md_path)}, indent=2))


if __name__ == "__main__":
    main()
