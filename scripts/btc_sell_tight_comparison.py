#!/usr/bin/env python3
"""
BTC M15 Sell-Tight v1 vs v2 Comparison Board

Compares the failed v1 (0.5x ATR) against the retuned v2 (1.0x ATR)
and adds close-mix truth so the room can distinguish "less chaotic"
from "actually harvesting".
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

V1_STATE = REPORTS / "penetration_lattice_shadow_btcusd_m15_sell_tight_v1_state.json"
V2_STATE = REPORTS / "penetration_lattice_shadow_btcusd_m15_sell_tight_v2_state.json"
V2_EVENTS = REPORTS / "penetration_lattice_shadow_btcusd_m15_sell_tight_v2_events.jsonl"
DEFAULT_MD_OUTPUT = REPORTS / "btc_sell_tight_comparison_latest.md"
DEFAULT_JSON_OUTPUT = REPORTS / "btc_sell_tight_comparison_latest.json"

CLOSE_ACTIONS = (
    "close_ticket",
    "escape_tier2_surgical",
    "forced_unwind",
    "breakout_kill",
    "timed_kill",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the BTC sell-tight comparison board.")
    parser.add_argument("--output", type=Path, default=DEFAULT_MD_OUTPUT, help="Markdown output path.")
    parser.add_argument(
        "--json-output",
        type=Path,
        default=DEFAULT_JSON_OUTPUT,
        help="Machine-readable JSON output path.",
    )
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def extract_metrics(state: dict[str, Any], label: str) -> dict[str, Any]:
    sym = dict((state.get("symbols") or {}).get("BTCUSD") or {})
    meta = dict(state.get("metadata") or {})
    runner = dict(state.get("runner") or {})

    closes = int(sym.get("realized_closes") or 0)
    net = round(float(sym.get("realized_net_usd") or 0.0), 2)
    resets = int(sym.get("anchor_resets") or 0)
    resets_flat = int(sym.get("anchor_resets_flat") or 0)
    resets_risk = int(sym.get("anchor_resets_risk") or 0)
    open_n = len(sym.get("open_tickets") or [])

    step = float(meta.get("step") or 0.0)
    step_sell = float(meta.get("step_sell") or 0.0)
    step_buy = float(meta.get("step_buy") or 0.0)
    sell_coeff = meta.get("sell_step_coeff", "?")

    hb_str = runner.get("heartbeat_at") or runner.get("last_successful_run_at")
    started_str = runner.get("started_at", "?")

    avg = round(net / closes, 2) if closes > 0 else 0.0
    resets_per_close = round(resets / closes, 4) if closes > 0 else None

    return {
        "label": label,
        "step": step,
        "step_sell": step_sell,
        "step_buy": step_buy,
        "sell_coeff": sell_coeff,
        "closes": closes,
        "net": net,
        "avg": avg,
        "resets": resets,
        "resets_flat": resets_flat,
        "resets_risk": resets_risk,
        "resets_per_close": resets_per_close,
        "open": open_n,
        "heartbeat": hb_str or "N/A",
        "started": started_str,
    }


def summarize_close_mix(events: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {action: 0 for action in CLOSE_ACTIONS}
    realized_pnl = {action: 0.0 for action in CLOSE_ACTIONS}

    for event in events:
        action = str(event.get("action") or "")
        if action not in counts:
            continue
        counts[action] += 1
        realized_pnl[action] = round(
            realized_pnl[action] + float(event.get("realized_pnl") or 0.0),
            2,
        )

    total_close_events = sum(counts.values())
    harvest_closes = counts["close_ticket"]
    escape_tier2_surgical_closes = counts["escape_tier2_surgical"]
    harvest_share = round(harvest_closes / total_close_events, 4) if total_close_events else None
    all_closes_escape_dominated = total_close_events > 0 and escape_tier2_surgical_closes == total_close_events

    if total_close_events == 0:
        close_mix_status = "no_close_events"
    elif harvest_closes == 0 and all_closes_escape_dominated:
        close_mix_status = "zero_harvest_all_escape_so_far"
    elif harvest_closes == 0:
        close_mix_status = "zero_harvest_non_harvest_mix"
    elif harvest_closes < total_close_events:
        close_mix_status = "harvest_present_but_mixed"
    else:
        close_mix_status = "all_harvest_so_far"

    return {
        "total_close_events": total_close_events,
        "harvest_closes": harvest_closes,
        "escape_tier2_surgical_closes": escape_tier2_surgical_closes,
        "forced_unwind_closes": counts["forced_unwind"],
        "breakout_kill_closes": counts["breakout_kill"],
        "timed_kill_closes": counts["timed_kill"],
        "harvest_share": harvest_share,
        "close_mix_status": close_mix_status,
        "all_closes_escape_dominated": all_closes_escape_dominated,
        "realized_pnl_by_action": realized_pnl,
    }


def build_payload(v1: dict[str, Any] | None, v2: dict[str, Any], v2_close_mix: dict[str, Any]) -> dict[str, Any]:
    reset_improvement_multiple = None
    if v1 and v1["resets_per_close"] and v2["resets_per_close"] not in (None, 0):
        reset_improvement_multiple = round(v1["resets_per_close"] / v2["resets_per_close"], 2)

    if v2_close_mix["close_mix_status"] == "zero_harvest_all_escape_so_far":
        decision_status = "proof_started_but_all_closes_are_escape_tier2_surgical"
        decision_summary = (
            "Proof has started, but every realized close so far is an escape_tier2_surgical exit with zero close_ticket harvests."
        )
    elif v2_close_mix["close_mix_status"] == "no_close_events":
        decision_status = "proof_started_without_close_mix"
        decision_summary = "State is live, but there are still no classified close events to judge."
    elif v2["closes"] < 10:
        decision_status = "too_early_to_judge"
        decision_summary = "The sample is still too small for a hard verdict even though fresh proof exists."
    elif v2["net"] > 0 and (v2_close_mix["harvest_share"] or 0.0) > 0:
        decision_status = "promising_but_not_validated"
        decision_summary = "Fresh proof is positive and harvest has appeared, but the sample is still not large enough to validate."
    else:
        decision_status = "keep_monitoring"
        decision_summary = "Fresh proof exists, but the room still needs more closes and a clearer harvest-vs-escape split."

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(V1_STATE.relative_to(ROOT)),
            str(V2_STATE.relative_to(ROOT)),
            str(V2_EVENTS.relative_to(ROOT)),
        ],
        "v1": v1,
        "v2": v2,
        "v2_close_mix": v2_close_mix,
        "comparison": {
            "reset_improvement_multiple": reset_improvement_multiple,
            "decision_status": decision_status,
            "decision_summary": decision_summary,
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    v1 = payload.get("v1")
    v2 = dict(payload.get("v2") or {})
    v2_close_mix = dict(payload.get("v2_close_mix") or {})
    comparison = dict(payload.get("comparison") or {})

    lines: list[str] = []
    lines.append("# BTC M15 Sell-Tight v1 vs v2 Comparison")
    lines.append(f"- Generated at: `{payload.get('generated_at', '')}`")
    lines.append("")

    lines.append("## Configuration")
    lines.append("")
    lines.append("| Metric | v1 (0.5x ATR) | v2 (1.0x ATR) | Change |")
    lines.append("|--------|--------------|--------------|--------|")
    lines.append(f"| Sell step | $129.71 | ${v2['step_sell']:.2f} | Widened {v2['step_sell'] / 129.71:.1f}x |")
    lines.append(f"| Buy step | $389.14 | ${v2['step_buy']:.2f} | Same |")
    lines.append("| Sell coeff | 0.5x ATR | 1.0x ATR | 2x wider |")
    lines.append(f"| Max open | 12 | {v2['open'] or 8} | Reduced |")

    if v1:
        lines.append("")
        lines.append("## Performance Comparison")
        lines.append("")
        lines.append("| Metric | v1 (0.5x ATR) | v2 (1.0x ATR) | Improvement |")
        lines.append("|--------|--------------|--------------|-------------|")
        lines.append(f"| Closes | {v1['closes']} | {v2['closes']} | - |")
        lines.append(f"| Net USD | ${v1['net']:+.2f} | ${v2['net']:+.2f} | - |")
        lines.append(f"| $/Close | ${v1['avg']:+.2f} | ${v2['avg']:+.2f} | - |")
        lines.append(f"| Resets | {v1['resets']} | {v2['resets']} | {v1['resets'] - v2['resets']:+d} |")
        if v1["resets_per_close"] is not None and v2["resets_per_close"] not in (None, 0):
            lines.append(
                f"| Resets/Close | {v1['resets_per_close']:.1f} | {v2['resets_per_close']:.1f} | {v1['resets_per_close'] / v2['resets_per_close']:.1f}x better |"
            )
        else:
            lines.append("| Resets/Close | N/A | N/A | N/A |")
        lines.append(f"| Open positions | {v1['open']} | {v2['open']} | - |")

    else:
        lines.append("")
        lines.append("## v1 Baseline")
        lines.append("")
        lines.append("- v1 state file is missing, so the comparison is using the historical v1 headline numbers only.")
        lines.append("- Historical reference: `53 closes`, `-$1,034.80`, `1,362 resets`, `25.7 resets/close`.")

    lines.append("")
    lines.append("## v2 Close Mix")
    lines.append("")
    lines.append(f"- Total close events: `{v2_close_mix['total_close_events']}`")
    lines.append(f"- Harvest closes (`close_ticket`): `{v2_close_mix['harvest_closes']}`")
    lines.append(f"- Surgical escapes (`escape_tier2_surgical`): `{v2_close_mix['escape_tier2_surgical_closes']}`")
    lines.append(f"- Forced unwinds: `{v2_close_mix['forced_unwind_closes']}`")
    lines.append(f"- Breakout kills: `{v2_close_mix['breakout_kill_closes']}`")
    lines.append(f"- Timed kills: `{v2_close_mix['timed_kill_closes']}`")
    lines.append(f"- Harvest share: `{v2_close_mix['harvest_share']}`")
    lines.append(f"- Close mix status: `{v2_close_mix['close_mix_status']}`")
    lines.append(f"- All closes escape-dominated: `{v2_close_mix['all_closes_escape_dominated']}`")

    lines.append("")
    lines.append("## Verdict Matrix")
    lines.append("")
    if v1 and v1["resets_per_close"] is not None and v2["resets_per_close"] is not None:
        if v2["resets_per_close"] < v1["resets_per_close"] / 10:
            lines.append(
                f"- Reset ratio: {v1['resets_per_close']:.1f} -> {v2['resets_per_close']:.1f} = >10x improvement"
            )
        elif v2["resets_per_close"] < v1["resets_per_close"] / 2:
            lines.append(
                f"- Reset ratio: {v1['resets_per_close']:.1f} -> {v2['resets_per_close']:.1f} = >2x improvement"
            )
        elif v2["resets_per_close"] < v1["resets_per_close"]:
            lines.append(
                f"- Reset ratio: {v1['resets_per_close']:.1f} -> {v2['resets_per_close']:.1f} = improved but still not clean"
            )
        else:
            lines.append(
                f"- Reset ratio: {v1['resets_per_close']:.1f} -> {v2['resets_per_close']:.1f} = no improvement or worse"
            )

    if v2["net"] > 0:
        lines.append(f"- Net: ${v2['net']:+.2f} over {v2['closes']} closes = positive")
    else:
        lines.append(f"- Net: ${v2['net']:+.2f} over {v2['closes']} closes = still negative")

    if v2_close_mix["close_mix_status"] == "zero_harvest_all_escape_so_far":
        lines.append("- Close mix: zero harvest so far and every close is escape_tier2_surgical")
    elif v2_close_mix["harvest_closes"] > 0:
        lines.append(
            f"- Close mix: harvest has appeared, but harvest share is only {v2_close_mix['harvest_share']}"
        )
    else:
        lines.append("- Close mix: still not enough harvest evidence to call the retune a win")

    lines.append("")
    lines.append("## Decision Tree")
    lines.append("")
    if v2_close_mix["close_mix_status"] == "zero_harvest_all_escape_so_far":
        lines.append(
            f"- Active proof exists with {v2['closes']} closes, but all {v2_close_mix['total_close_events']} close events are escape_tier2_surgical."
        )
        lines.append("- Keep the lane running long enough to see whether any close_ticket harvest appears.")
        lines.append("- Do not treat 'less chaotic than v1' as success while harvest count is still zero.")
    elif v2["closes"] < 10:
        lines.append(f"- Continue monitoring. Need 10+ closes for a meaningful read; currently at {v2['closes']}.")
        lines.append("- Watch whether resets stay inside guardrails and whether any harvest closes appear.")
    elif v2["closes"] < 25:
        lines.append(f"- Approaching judgment at {v2['closes']}/25 closes.")
        lines.append("- Keep tracking whether harvest share becomes readable and whether net recovers.")
    elif v2["net"] > 0 and (v2_close_mix["harvest_share"] or 0.0) > 0:
        lines.append("- Passed the first serious smell test: positive net with at least some harvest present.")
        lines.append("- Still require broader proof before graduation, but the lane is no longer all-escape.")
    else:
        lines.append("- Failed the current smell test: the lane still does not show enough harvest-quality proof.")
        lines.append("- Recommendation: keep it in shadow only if more harvest is plausible; otherwise kill the shape cleanly.")

    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Decision status: `{comparison.get('decision_status', '')}`")
    lines.append(f"- Decision summary: {comparison.get('decision_summary', '')}")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any], markdown_path: Path, json_path: Path) -> None:
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    v1_state = load_state(V1_STATE)
    v2_state = load_state(V2_STATE)

    if not v2_state:
        print("ERROR: v2 state file not found. BTC sell-tight v2 has not been launched.")
        return 1

    v1 = extract_metrics(v1_state, "v1 (0.5x ATR)") if v1_state else None
    v2 = extract_metrics(v2_state, "v2 (1.0x ATR)")
    v2_close_mix = summarize_close_mix(load_jsonl(V2_EVENTS))
    payload = build_payload(v1, v2, v2_close_mix)

    markdown = render_markdown(payload)
    print(markdown)
    write_outputs(payload, args.output, args.json_output)
    print(f"Wrote {args.output}", file=sys.stderr)
    print(f"Wrote {args.json_output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
