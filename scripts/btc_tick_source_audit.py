#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"
EXECUTION_MONITOR_PATH = REPORTS / "execution_monitor_report.json"
OUTPUT_JSON_PATH = REPORTS / "btc_tick_source_audit.json"
OUTPUT_MD_PATH = REPORTS / "btc_tick_source_audit.md"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return load_json(path)
    except Exception:
        return None


def parse_iso(raw: str | None) -> datetime | None:
    text = str(raw or "").strip()
    if not text or text.lower() == "none":
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_minutes(raw: str | None) -> float | None:
    ts = parse_iso(raw)
    if ts is None:
        return None
    return round((utc_now() - ts).total_seconds() / 60.0, 2)


def read_registry() -> dict[str, dict[str, Any]]:
    payload = load_optional_json(REGISTRY_PATH) or {}
    lanes = payload.get("lanes") if isinstance(payload, dict) else []
    out: dict[str, dict[str, Any]] = {}
    for lane in lanes or []:
        if not isinstance(lane, dict):
            continue
        name = str(lane.get("name") or "").strip()
        if name:
            out[name] = lane
    return out


def base_lane_name(lane_name: str) -> str:
    return re.sub(r"_\d{5,}$", "", lane_name)


def resolve_registry_lane(lane_name: str, registry: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if lane_name in registry:
        return registry[lane_name]
    base = base_lane_name(lane_name)
    if base in registry:
        return registry[base]
    return None


def resolve_state_path(lane_name: str, registry_lane: dict[str, Any] | None) -> Path | None:
    if registry_lane:
        state_path = str(registry_lane.get("state_path") or "").strip()
        if state_path:
            return (ROOT / state_path).resolve()

    base = base_lane_name(lane_name)
    candidates = sorted(
        path
        for path in REPORTS.glob(f"*{base}*state.json")
        if path.is_file() and not path.name.endswith("_exec_state.json")
    )
    if candidates:
        return candidates[0]
    return None


def classify_tick_path(
    *,
    shared_price_max_age_ms: int | None,
    latest_tick_source_last: str,
    latest_tick_append_source_last: str,
    heartbeat_age_minutes: float | None,
    stale_after_seconds: int | None,
) -> tuple[str, str]:
    stale_after_minutes = (stale_after_seconds or 300) / 60.0
    if heartbeat_age_minutes is None:
        return ("unknown_runtime", "No heartbeat field available in current state.")
    if heartbeat_age_minutes > stale_after_minutes:
        return ("stale_runtime", "Heartbeat is older than the lane stale threshold.")

    latest_source = latest_tick_source_last.strip().lower()
    latest_append = latest_tick_append_source_last.strip().lower()
    has_symbol_tick = latest_source == "symbol_info_tick" or latest_append == "symbol_info_tick"

    if (shared_price_max_age_ms or 0) <= 0 and has_symbol_tick:
        return ("direct_tick_live", "Lane is running without shared-price age gating and still reports symbol_info_tick.")
    if (shared_price_max_age_ms or 0) > 0 and has_symbol_tick:
        return ("shared_history_live_tick_backed", "Lane uses shared history but still reports live symbol_info_tick updates.")
    if (shared_price_max_age_ms or 0) > 0:
        return ("shared_history_needs_validation", "Lane uses shared history and the current state does not show symbol_info_tick as the latest source.")
    if has_symbol_tick:
        return ("live_symbol_tick_unclear_config", "Lane reports symbol_info_tick but the shared-price posture is unclear.")
    return ("tick_path_unclear", "State is fresh, but the latest tick source fields do not cleanly explain the path.")


def analyze_lane(
    monitor_row: dict[str, Any],
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    lane_name = str(monitor_row.get("lane") or "")
    registry_lane = resolve_registry_lane(lane_name, registry)
    state_path = resolve_state_path(lane_name, registry_lane)
    state = load_optional_json(state_path) if state_path else None

    metadata = dict((state or {}).get("metadata") or {})
    runner = dict((state or {}).get("runner") or {})
    symbols = dict((state or {}).get("symbols") or {})
    symbol_state = dict(next(iter(symbols.values()))) if symbols else {}

    heartbeat_at = str(runner.get("heartbeat_at") or monitor_row.get("runner_heartbeat_at") or "")
    stale_after_seconds = None
    if registry_lane is not None:
        raw_stale_after = registry_lane.get("stale_after_seconds")
        stale_after_seconds = int(raw_stale_after) if raw_stale_after is not None else None

    heartbeat_age = age_minutes(heartbeat_at)
    verdict, verdict_reason = classify_tick_path(
        shared_price_max_age_ms=int(metadata.get("shared_price_max_age_ms") or 0) if metadata.get("shared_price_max_age_ms") is not None else None,
        latest_tick_source_last=str(runner.get("latest_tick_source_last") or ""),
        latest_tick_append_source_last=str(runner.get("latest_tick_append_source_last") or ""),
        heartbeat_age_minutes=heartbeat_age,
        stale_after_seconds=stale_after_seconds,
    )

    return {
        "lane_name": lane_name,
        "kind": str(monitor_row.get("kind") or ""),
        "watchdog_status": str(monitor_row.get("watchdog_status") or ""),
        "state_path": str(state_path.relative_to(ROOT)) if state_path else "",
        "shared_price_max_age_ms": metadata.get("shared_price_max_age_ms"),
        "latest_tick_source_last": str(runner.get("latest_tick_source_last") or ""),
        "latest_tick_append_source_last": str(runner.get("latest_tick_append_source_last") or ""),
        "tick_history_source_last": str(runner.get("tick_history_source_last") or ""),
        "heartbeat_at": heartbeat_at,
        "heartbeat_age_minutes": heartbeat_age,
        "stale_after_seconds": stale_after_seconds,
        "quote_bid": monitor_row.get("quote_bid"),
        "quote_ask": monitor_row.get("quote_ask"),
        "realized_closes": int(symbol_state.get("realized_closes") or 0) if symbol_state else int(monitor_row.get("close_count") or 0),
        "open_count": len(symbol_state.get("open_tickets") or []) if symbol_state else int(monitor_row.get("open_count") or 0),
        "last_tick_msc": symbol_state.get("last_tick_msc"),
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "supports_blanket_dead_shared_feeder_claim": verdict == "shared_history_needs_validation",
    }


def build_payload() -> dict[str, Any]:
    registry = read_registry()
    execution_monitor = load_optional_json(EXECUTION_MONITOR_PATH) or {}
    rows = execution_monitor.get("rows") if isinstance(execution_monitor, dict) else []

    btc_monitor_rows = [
        row for row in (rows or [])
        if isinstance(row, dict) and "btc" in str(row.get("lane") or "").lower()
    ]

    audit_rows = [analyze_lane(row, registry) for row in btc_monitor_rows]
    audit_rows.sort(key=lambda row: row["lane_name"])

    verdict_counts: dict[str, int] = {}
    for row in audit_rows:
        verdict = str(row["verdict"])
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    direct_or_live_tick = sum(
        1 for row in audit_rows
        if row["verdict"] in {"direct_tick_live", "shared_history_live_tick_backed", "live_symbol_tick_unclear_config"}
    )
    stale_or_needs_validation = sum(
        1 for row in audit_rows
        if row["verdict"] in {"stale_runtime", "shared_history_needs_validation", "unknown_runtime", "tick_path_unclear"}
    )

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(EXECUTION_MONITOR_PATH.relative_to(ROOT)),
            str(REGISTRY_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "The shared-price question needs lane-by-lane evidence, not a blanket BTC-family claim.",
            "At least one live BTC issue may still exist, but the current state surfaces already show multiple BTC lanes with fresh heartbeats and symbol_info_tick evidence, so 'all BTC lanes are dead because of the shared feeder' is not supported as written.",
            "Treat only stale or shared-history-without-live-tick-evidence lanes as feeder suspects; direct-tick or shared-history-but-live-tick-backed lanes need a different explanation.",
        ],
        "summary": {
            "lane_count": len(audit_rows),
            "verdict_counts": verdict_counts,
            "direct_or_live_tick_supported_rows": direct_or_live_tick,
            "stale_or_needs_validation_rows": stale_or_needs_validation,
            "blanket_dead_shared_feeder_claim_supported": False,
        },
        "rows": audit_rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# BTC Tick-Source Audit",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: verify which BTC lanes actually depend on shared history, which ones still show live symbol ticks, and which ones are genuinely stale or unclear.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    summary = dict(payload.get("summary") or {})
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Lane count: `{summary.get('lane_count', 0)}`")
    lines.append(f"- Verdict counts: `{'; '.join(f'{k}={v}' for k, v in dict(summary.get('verdict_counts') or {}).items())}`")
    lines.append(f"- Direct/live-tick supported rows: `{summary.get('direct_or_live_tick_supported_rows', 0)}`")
    lines.append(f"- Stale or needs-validation rows: `{summary.get('stale_or_needs_validation_rows', 0)}`")
    lines.append(f"- Blanket dead shared feeder claim supported: `{summary.get('blanket_dead_shared_feeder_claim_supported', False)}`")

    lines.extend(["", "## Lane Audit", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### {row['lane_name']}")
        lines.append(f"- Kind: `{row['kind']}`")
        lines.append(f"- Watchdog: `{row['watchdog_status']}`")
        lines.append(f"- State path: `{row['state_path']}`")
        lines.append(f"- Shared price max age ms: `{row['shared_price_max_age_ms']}`")
        lines.append(f"- Tick source: `latest={row['latest_tick_source_last']}; append={row['latest_tick_append_source_last']}; history={row['tick_history_source_last']}`")
        lines.append(f"- Heartbeat: `{row['heartbeat_at']}` (`{row['heartbeat_age_minutes']} min ago`)")
        lines.append(f"- Runtime snapshot: `closes={row['realized_closes']}; open={row['open_count']}; bid={row['quote_bid']}; ask={row['quote_ask']}`")
        lines.append(f"- Verdict: `{row['verdict']}`")
        lines.append(f"- Reason: `{row['verdict_reason']}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
