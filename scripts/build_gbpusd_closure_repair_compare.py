#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

BASELINE_STATE_PATH = REPORTS / "shadow_gbpusd_tick_forward_state.json"
NO_ESCAPE_STATE_PATH = REPORTS / "shadow_gbpusd_tick_forward_no_escape_state.json"

OUTPUT_JSON_PATH = REPORTS / "gbpusd_closure_repair_compare.json"
OUTPUT_MD_PATH = REPORTS / "gbpusd_closure_repair_compare.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def lane_snapshot(payload: dict[str, Any] | None, *, lane_name: str, state_path: Path) -> dict[str, Any]:
    if not payload:
        return {
            "lane": lane_name,
            "state_path": str(state_path.relative_to(ROOT)),
            "present": False,
            "status": "missing",
        }

    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    symbol = symbols.get("GBPUSD") if isinstance(symbols.get("GBPUSD"), dict) else {}

    realized = float(symbol.get("realized_net_usd", 0.0) or 0.0)
    closes = int(symbol.get("realized_closes", 0) or 0)
    open_count = len(symbol.get("open_tickets") or [])
    return {
        "lane": lane_name,
        "state_path": str(state_path.relative_to(ROOT)),
        "present": True,
        "status": "present",
        "updated_at": str(payload.get("updated_at") or ""),
        "heartbeat_at": str(runner.get("heartbeat_at") or ""),
        "realized_net_usd": realized,
        "realized_closes": closes,
        "avg_per_close": (realized / closes) if closes > 0 else None,
        "open_count": open_count,
        "no_offensive_escape": bool(metadata.get("no_offensive_escape", False)),
        "offensive_closure_enabled": bool(metadata.get("offensive_closure_enabled", True)),
    }


def build_payload(
    baseline_payload: dict[str, Any] | None,
    no_escape_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    baseline = lane_snapshot(
        baseline_payload,
        lane_name="shadow_gbpusd_tick_forward",
        state_path=BASELINE_STATE_PATH,
    )
    no_escape = lane_snapshot(
        no_escape_payload,
        lane_name="shadow_gbpusd_tick_forward_no_escape",
        state_path=NO_ESCAPE_STATE_PATH,
    )

    leadership_read: list[str] = []
    if not no_escape["present"]:
        leadership_read.append(
            "The baseline GBP closure-dominated lane is live, but the registered no-escape companion has not produced state yet, so the closure-repair experiment is not actually running as a pair."
        )
    else:
        base_avg = baseline.get("avg_per_close")
        no_escape_avg = no_escape.get("avg_per_close")
        leadership_read.append(
            "Both GBP closure-repair lanes now exist; judge the experiment on realized closes and avg-per-close deltas rather than on abstract closure-policy arguments."
        )
        if isinstance(base_avg, float) and isinstance(no_escape_avg, float):
            leadership_read.append(
                f"Current avg-per-close delta (no-escape minus baseline): {no_escape_avg - base_avg:+.4f}."
            )
    leadership_read.append(
        "The no-escape lane should report `offensive_closure_enabled=false`; if it does not, the closure-diagnosis control is dishonest."
    )

    if not no_escape["present"]:
        next_action = "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair"
    else:
        next_action = "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape"

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(BASELINE_STATE_PATH.relative_to(ROOT)),
            str(NO_ESCAPE_STATE_PATH.relative_to(ROOT)),
        ],
        "leadership_read": leadership_read,
        "summary": {
            "next_action": next_action,
            "paired_experiment_live": bool(baseline["present"] and no_escape["present"]),
        },
        "lanes": [baseline, no_escape],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# GBPUSD Closure Repair Compare",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: keep the GBP baseline lane and the no-escape closure-diagnosis control readable as one paired experiment.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    summary = dict(payload.get("summary") or {})
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Next action: `{summary.get('next_action', '')}`")
    lines.append(f"- Paired experiment live: `{str(bool(summary.get('paired_experiment_live'))).lower()}`")

    lines.extend(["", "## Lanes", ""])
    for lane in list(payload.get("lanes") or []):
        lines.append(f"### {lane.get('lane', '')}")
        lines.append("")
        lines.append(f"- Present: `{str(bool(lane.get('present'))).lower()}`")
        lines.append(f"- State path: `{lane.get('state_path', '')}`")
        lines.append(f"- Status: `{lane.get('status', '')}`")
        if lane.get("present"):
            lines.append(f"- Updated at: `{lane.get('updated_at', '')}`")
            lines.append(f"- Heartbeat at: `{lane.get('heartbeat_at', '')}`")
            lines.append(f"- Realized net USD: `{float(lane.get('realized_net_usd', 0.0)):+.2f}`")
            lines.append(f"- Realized closes: `{int(lane.get('realized_closes', 0))}`")
            avg = lane.get("avg_per_close")
            lines.append(f"- Avg per close: `{float(avg):+.4f}`" if isinstance(avg, float) else "- Avg per close: `n/a`")
            lines.append(f"- Open count: `{int(lane.get('open_count', 0))}`")
            lines.append(f"- No offensive escape: `{str(bool(lane.get('no_offensive_escape'))).lower()}`")
            lines.append(f"- Offensive closure enabled: `{str(bool(lane.get('offensive_closure_enabled'))).lower()}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(BASELINE_STATE_PATH),
        load_json(NO_ESCAPE_STATE_PATH),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
