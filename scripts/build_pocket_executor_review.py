#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
EVENT_PATH = REPORTS / "pocket_executor_events.jsonl"
STATE_PATH = REPORTS / "pocket_executor_state.json"
JSON_PATH = REPORTS / "pocket_executor_review.json"
MD_PATH = REPORTS / "pocket_executor_review.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def longest_runs(closes: list[dict[str, Any]]) -> dict[str, Any]:
    current_win = 0
    current_loss = 0
    best_win = 0
    best_loss = 0
    best_win_start = best_win_end = None
    best_loss_start = best_loss_end = None
    active_win_start = active_loss_start = None

    for row in closes:
        won = to_float(row.get("net")) > 0.0
        ts = row.get("ts_utc")
        if won:
            if current_win == 0:
                active_win_start = ts
            current_win += 1
            current_loss = 0
            active_loss_start = None
            if current_win > best_win:
                best_win = current_win
                best_win_start = active_win_start
                best_win_end = ts
        else:
            if current_loss == 0:
                active_loss_start = ts
            current_loss += 1
            current_win = 0
            active_win_start = None
            if current_loss > best_loss:
                best_loss = current_loss
                best_loss_start = active_loss_start
                best_loss_end = ts

    return {
        "current_profitable_run": current_win,
        "current_loss_run": current_loss,
        "max_profitable_run": best_win,
        "max_profitable_run_start": best_win_start,
        "max_profitable_run_end": best_win_end,
        "max_loss_run": best_loss,
        "max_loss_run_start": best_loss_start,
        "max_loss_run_end": best_loss_end,
    }


def summarize(events: list[dict[str, Any]], state: dict[str, Any] | None = None) -> dict[str, Any]:
    closes = [row for row in events if row.get("action") == "shadow_close"]
    opens = [row for row in events if row.get("action") == "shadow_open"]
    rejects = [row for row in events if row.get("action") == "shadow_reject"]
    net_values = [to_float(row.get("net")) for row in closes]
    net_pct_values = [to_float(row.get("net_pct")) for row in closes]
    fee_values = [to_float(row.get("entry_fee")) + to_float(row.get("exit_fee")) for row in closes]
    winners = [row for row in closes if to_float(row.get("net")) > 0.0]
    mfe_rows = [row for row in closes if row.get("net_mfe_capture_pct") is not None]

    summary = {
        "generated_at": utc_now_iso(),
        "event_path": str(EVENT_PATH),
        "state_path": str(STATE_PATH),
        "opens": len(opens),
        "closes": len(closes),
        "rejects": len(rejects),
        "wins": len(winners),
        "losses": len(closes) - len(winners),
        "win_rate_pct": round((len(winners) / len(closes)) * 100, 4) if closes else 0.0,
        "total_net": round(sum(net_values), 8),
        "total_net_pct_points": round(sum(net_pct_values), 8),
        "avg_net_pct": round(mean(net_pct_values), 8) if net_pct_values else None,
        "median_net_pct": round(median(net_pct_values), 8) if net_pct_values else None,
        "best_net_pct": round(max(net_pct_values), 8) if net_pct_values else None,
        "worst_net_pct": round(min(net_pct_values), 8) if net_pct_values else None,
        "total_fees": round(sum(fee_values), 8),
        "avg_fee_per_close": round(mean(fee_values), 8) if fee_values else None,
        "mfe_capture_closes": len(mfe_rows),
        "avg_net_mfe_capture_pct": round(mean(to_float(row.get("net_mfe_capture_pct")) for row in mfe_rows), 8) if mfe_rows else None,
        "last_close": closes[-1] if closes else None,
        "current_state": state or {},
    }
    summary.update(longest_runs(closes))
    summary["verdict"] = (
        "consecutive_profit_proof"
        if summary["max_profitable_run"] >= 3 and summary["total_net"] > 0.0
        else "no_consecutive_profit_proof"
    )
    return summary


def write_markdown(payload: dict[str, Any]) -> None:
    last = payload.get("last_close") or {}
    lines = [
        "# Pocket Executor Review",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Opens: `{payload['opens']}`",
        f"- Closes: `{payload['closes']}`",
        f"- Rejects: `{payload['rejects']}`",
        f"- Wins: `{payload['wins']}`",
        f"- Losses: `{payload['losses']}`",
        f"- Win rate: `{payload['win_rate_pct']:.2f}%`",
        f"- Total net: `{payload['total_net']:.6f}`",
        f"- Total close-fee drag: `{payload['total_fees']:.6f}`",
        f"- Current profitable run: `{payload['current_profitable_run']}`",
        f"- Max profitable run: `{payload['max_profitable_run']}`",
        f"- Current loss run: `{payload['current_loss_run']}`",
        f"- Max loss run: `{payload['max_loss_run']}`",
        f"- Verdict: `{payload['verdict']}`",
        "",
        "## Last Close",
        "",
        f"- Product: `{last.get('product_id', '')}`",
        f"- Reason: `{last.get('exit_reason', '')}`",
        f"- Net %: `{to_float(last.get('net_pct')):.4f}`",
        f"- Max net %: `{to_float(last.get('max_net_pct')):.4f}`",
        f"- Fee bps/side: `{to_float(last.get('fee_bps_per_side')):.2f}`",
        f"- Live spread bps: `{last.get('live_spread_bps', '')}`",
    ]
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    payload = summarize(read_jsonl(EVENT_PATH), state)
    JSON_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(payload)
    print(json.dumps({"json_path": str(JSON_PATH), "md_path": str(MD_PATH), "verdict": payload["verdict"]}, indent=2))


if __name__ == "__main__":
    main()
