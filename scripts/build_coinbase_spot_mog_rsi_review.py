#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_STATE_PATH = REPORTS / "coinbase_rsi_shadow_mogusd_state.json"
DEFAULT_EVENT_PATH = REPORTS / "coinbase_rsi_shadow_mogusd_events.jsonl"
DEFAULT_JSON_PATH = REPORTS / "coinbase_spot_mog_rsi_review.json"
DEFAULT_MD_PATH = REPORTS / "coinbase_spot_mog_rsi_review.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
    return events


def latest_fee_reset(events: list[dict[str, Any]]) -> datetime | None:
    resets = [
        parse_iso(event.get("ts_utc"))
        for event in events
        if str(event.get("action") or "") == "fresh_start_fee_model_reset"
    ]
    resets = [item for item in resets if item is not None]
    return max(resets) if resets else None


def filter_after(events: list[dict[str, Any]], ts: datetime | None) -> list[dict[str, Any]]:
    if ts is None:
        return list(events)
    filtered: list[dict[str, Any]] = []
    for event in events:
        event_ts = parse_iso(event.get("ts_utc"))
        if event_ts is None or event_ts >= ts:
            filtered.append(event)
    return filtered


def close_metrics(event: dict[str, Any]) -> dict[str, Any]:
    entry = to_float(event.get("entry_price"))
    exit_price = to_float(event.get("exit_price"))
    entry_fee = to_float(event.get("entry_fee"))
    exit_fee = to_float(event.get("exit_fee"))
    total_fee = to_float(event.get("fee")) or entry_fee + exit_fee
    fee_bps = to_float(event.get("fee_bps_per_side"))
    fee_rate = fee_bps / 10000.0 if fee_bps > 0 else 0.0
    entry_notional = entry_fee / fee_rate if fee_rate > 0 else 0.0
    exit_notional = exit_fee / fee_rate if fee_rate > 0 else 0.0
    gross_move_pct = ((exit_price - entry) / entry * 100.0) if entry > 0 else 0.0
    fee_drag_pct = (total_fee / entry_notional * 100.0) if entry_notional > 0 else 0.0
    net_pct = (to_float(event.get("net_pnl")) / entry_notional * 100.0) if entry_notional > 0 else 0.0
    return {
        "ts_utc": str(event.get("ts_utc") or ""),
        "entry_price": entry,
        "exit_price": exit_price,
        "gross_move_pct": round(gross_move_pct, 4),
        "entry_notional_usd": round(entry_notional, 4),
        "exit_notional_usd": round(exit_notional, 4),
        "gross_pnl": round(to_float(event.get("gross_pnl")), 4),
        "fee_drag_usd": round(total_fee, 4),
        "fee_drag_pct": round(fee_drag_pct, 4),
        "net_pnl": round(to_float(event.get("net_pnl")), 4),
        "net_pct": round(net_pct, 4),
        "hold_bars": int(to_float(event.get("hold_bars"))),
        "exit_reason": str(event.get("exit_reason") or ""),
        "fee_bps_per_side": round(fee_bps, 4),
        "fee_model": str(event.get("fee_model") or ""),
        "fee_tier": str(event.get("fee_tier") or ""),
        "fill_model": str(event.get("fill_model") or ""),
    }


def max_run(values: list[bool], wanted: bool) -> int:
    best = 0
    current = 0
    for value in values:
        if value is wanted:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def build_review(state_path: Path, event_path: Path) -> dict[str, Any]:
    state_payload = load_json(state_path)
    events = load_events(event_path)
    reset_ts = latest_fee_reset(events)
    current_events = filter_after(events, reset_ts)
    closes = [close_metrics(event) for event in current_events if str(event.get("action") or "") == "close_trade"]
    opens = [event for event in current_events if str(event.get("action") or "") == "open_trade"]
    exceptions = [event for event in current_events if str(event.get("action") or "") == "runner_exception"]
    win_flags = [to_float(row.get("net_pnl")) > 0 for row in closes]
    state = state_payload.get("state") or {}
    runner = state_payload.get("runner") or {}
    current_trade = state.get("current_trade") or {}
    heartbeat = parse_iso(runner.get("heartbeat_at") or runner.get("last_successful_run_at") or state_payload.get("updated_at"))
    heartbeat_age = (datetime.now(timezone.utc) - heartbeat).total_seconds() if heartbeat else None
    fee_bps = to_float((state.get("config") or {}).get("fee_bps_per_side"))
    fee_roundtrip_pct = (fee_bps * 2.0) / 100.0
    current_entry = to_float(current_trade.get("entry_price"))
    current_entry_fee = to_float(current_trade.get("entry_fee"))
    current_qty = to_float(current_trade.get("quantity"))
    current_entry_notional = current_entry * current_qty if current_entry > 0 and current_qty > 0 else 0.0
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_mog_rsi_review",
        "state_path": str(state_path),
        "event_path": str(event_path),
        "fee_reset_at": reset_ts.isoformat() if reset_ts else "",
        "summary": {
            "product_id": str(state.get("product_id") or runner.get("product_id") or "MOG-USD"),
            "lane_name": str(runner.get("lane_name") or ""),
            "pid": int(to_float(runner.get("pid"))),
            "heartbeat_age_seconds": round(heartbeat_age, 1) if heartbeat_age is not None else None,
            "fee_bps_per_side": round(fee_bps, 4),
            "fee_roundtrip_pct": round(fee_roundtrip_pct, 4),
            "fee_source": str((state.get("config") or {}).get("fee_source") or runner.get("fee_source") or ""),
            "fee_tier": str((state.get("config") or {}).get("fee_tier") or runner.get("fee_tier") or ""),
            "fill_model": str((state.get("config") or {}).get("fill_model") or ""),
            "cash_usd": round(to_float(state.get("cash_usd")), 4),
            "realized_net_usd": round(to_float(state.get("realized_net_usd")), 4),
            "realized_closes": int(to_float(state.get("realized_closes"))),
            "in_position": bool(state.get("in_position")),
            "signals_generated": int(to_float(state.get("signals_generated"))),
            "total_fees": round(to_float(state.get("total_fees")), 4),
            "post_reset_opens": len(opens),
            "post_reset_closes": len(closes),
            "post_reset_wins": sum(1 for flag in win_flags if flag),
            "post_reset_losses": sum(1 for flag in win_flags if not flag),
            "best_win_run": max_run(win_flags, True),
            "worst_loss_run": max_run(win_flags, False),
            "runner_exceptions": len(exceptions),
        },
        "latest_close": closes[-1] if closes else {},
        "current_open": {
            "entry_price": current_entry,
            "entry_fee": round(current_entry_fee, 4),
            "quantity": round(current_qty, 6),
            "entry_notional_usd": round(current_entry_notional, 4),
            "entry_bar": int(to_float(current_trade.get("entry_bar"))),
            "current_bar": int(to_float(state.get("current_bar"))),
            "bars_held": max(0, int(to_float(state.get("current_bar"))) - int(to_float(current_trade.get("entry_bar")))) if current_trade else 0,
            "max_hold_bars": int(to_float((state.get("config") or {}).get("max_hold_bars"))),
        },
        "closes": closes,
        "risk_read": [
            "Post-reset proof is still a tiny sample; do not generalize from one green close.",
            "The runner uses candle-close proxy fills, so bid/ask executable fill proof is still missing.",
            "MOG survives fees only when the move is large enough to clear the round-trip fee wall plus spread.",
        ],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_md(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    latest = payload.get("latest_close") or {}
    current = payload.get("current_open") or {}
    lines = [
        "# Coinbase Spot MOG RSI Review",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Fee reset: `{payload.get('fee_reset_at') or 'none'}`",
        f"- Lane: `{summary['lane_name']}` / `{summary['product_id']}` / PID `{summary['pid']}`",
        f"- Fee model: `{summary['fee_bps_per_side']}` bps per side, round trip `{summary['fee_roundtrip_pct']:.4f}%`, tier `{summary['fee_tier']}`",
        f"- Fill model: `{summary['fill_model']}`",
        "",
        "## Post-Reset Score",
        "",
        f"- Realized: `${summary['realized_net_usd']:.4f}` across `{summary['post_reset_closes']}` closes",
        f"- Wins/losses: `{summary['post_reset_wins']}` / `{summary['post_reset_losses']}`",
        f"- Best green run: `{summary['best_win_run']}`",
        f"- Runner exceptions since reset: `{summary['runner_exceptions']}`",
        f"- In position: `{summary['in_position']}`; cash `${summary['cash_usd']:.4f}`; total fees `${summary['total_fees']:.4f}`",
        "",
        "## Latest Close",
        "",
    ]
    if latest:
        lines.extend(
            [
                f"- Time: `{latest['ts_utc']}`",
                f"- Entry -> exit: `{latest['entry_price']}` -> `{latest['exit_price']}`",
                f"- Gross move: `{latest['gross_move_pct']:.4f}%`; fee drag: `{latest['fee_drag_pct']:.4f}%`; net: `{latest['net_pct']:.4f}%` / `${latest['net_pnl']:.4f}`",
                f"- Exit reason: `{latest['exit_reason']}`, held `{latest['hold_bars']}` bars",
            ]
        )
    else:
        lines.append("- No post-reset closes found.")
    lines.extend(
        [
            "",
            "## Current Open",
            "",
            f"- Entry price: `{current.get('entry_price', 0.0)}`",
            f"- Entry notional: `${float(current.get('entry_notional_usd') or 0.0):.4f}`",
            f"- Bars held: `{current.get('bars_held', 0)}` / `{current.get('max_hold_bars', 0)}`",
            "",
            "## Risk Read",
            "",
        ]
    )
    for item in payload.get("risk_read") or []:
        lines.append(f"- {item}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review the post-fee-reset MOG RSI shadow lane.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_review(Path(args.state_path), Path(args.event_path))
    write_json(Path(args.json_path), payload)
    write_md(Path(args.md_path), payload)
    print(json.dumps({"json_path": args.json_path, "md_path": args.md_path, "summary": payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
