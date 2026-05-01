#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_GROUPS_PATH = ROOT / "configs" / "watchdog_groups.json"
JSON_PATH = REPORTS / "crypto_m15_warp_readiness.json"
MD_PATH = REPORTS / "crypto_m15_warp_readiness.md"

CANDIDATES: list[dict[str, Any]] = [
    {
        "lane_name": "shadow_ethusd_m15_warp",
        "symbol": "ETHUSD",
        "label": "ETH M15 Warp",
        "state_paths": [REPORTS / "penetration_lattice_shadow_ethusd_m15_warp_state.json"],
        "role": "live_candidate",
        "target_closes": 50,
    },
    {
        "lane_name": "shadow_solusd_m15_warp_v2",
        "lane_aliases": ["shadow_solusd_m15_warp"],
        "symbol": "SOLUSD",
        "label": "SOL M15 Warp",
        "state_paths": [
            REPORTS / "penetration_lattice_shadow_solusd_m15_warp_v2_state.json",
            REPORTS / "penetration_lattice_shadow_solusd_m15_warp_state.json",
        ],
        "role": "validation_probe",
        "target_closes": 10,
    },
    {
        "lane_name": "shadow_xrpusd_m15_warp_v2",
        "lane_aliases": ["shadow_xrpusd_m15_warp"],
        "symbol": "XRPUSD",
        "label": "XRP M15 Warp",
        "state_paths": [
            REPORTS / "penetration_lattice_shadow_xrpusd_m15_warp_v2_state.json",
            REPORTS / "penetration_lattice_shadow_xrpusd_m15_warp_state.json",
        ],
        "role": "validation_probe",
        "target_closes": 10,
    },
    {
        "lane_name": "live_ltcusd_m15_warp_941894",
        "lane_aliases": ["shadow_ltcusd_m15_warp"],
        "symbol": "LTCUSD",
        "label": "LTC M15 Warp",
        "state_paths": [
            REPORTS / "penetration_lattice_live_ltcusd_m15_warp_state.json",
            REPORTS / "penetration_lattice_shadow_ltcusd_m15_warp_state.json",
        ],
        "role": "blind_live_probe",
        "target_closes": 10,
    },
    {
        "lane_name": "live_adausd_m15_warp_941893",
        "lane_aliases": ["shadow_adausd_m15_warp"],
        "symbol": "ADAUSD",
        "label": "ADA M15 Warp",
        "state_paths": [
            REPORTS / "penetration_lattice_live_adausd_m15_warp_state.json",
            REPORTS / "penetration_lattice_shadow_adausd_m15_warp_state.json",
        ],
        "role": "blind_live_probe",
        "target_closes": 10,
    },
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            payload, _ = json.JSONDecoder().raw_decode(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def hours_since(value: Any) -> float | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    return round(max(0.0, (utc_now() - dt).total_seconds()) / 3600.0, 2)


def format_pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "-"
    return f"{(float(numerator) / float(denominator)) * 100.0:.1f}%"


def resolve_runner_tick_source(
    runner: dict[str, Any],
    *,
    symbol: str,
    source_key: str,
) -> str:
    symbol_key = str(symbol or "").upper().strip()
    bucket = runner.get(f"{source_key}_by_symbol")
    if isinstance(bucket, dict):
        bucket_entry = bucket.get(symbol_key) if symbol_key else None
        if isinstance(bucket_entry, dict):
            value = bucket_entry.get("last")
            if str(value or "").strip():
                return str(value)
    legacy_last = str(runner.get(f"{source_key}_last") or "").strip()
    if legacy_last:
        return legacy_last
    legacy = str(runner.get(source_key) or "").strip()
    return legacy


def load_registry_lanes() -> set[str]:
    payload = load_json(REGISTRY_PATH)
    lanes = payload.get("lanes") if isinstance(payload.get("lanes"), list) else []
    names = {str(row.get("name") or "") for row in lanes if isinstance(row, dict) and str(row.get("name") or "").strip()}
    if names:
        return names
    if not REGISTRY_PATH.exists():
        return set()
    try:
        text = REGISTRY_PATH.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()
    return {
        match.group(1).strip()
        for match in re.finditer(r'"name"\s*:\s*"([^"]+)"', text)
        if match.group(1).strip()
    }


def load_watchdog_groups() -> dict[str, set[str]]:
    payload = load_json(WATCHDOG_GROUPS_PATH)
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    out: dict[str, set[str]] = {}
    for group_name, row in groups.items():
        if not isinstance(row, dict):
            continue
        lanes = row.get("lanes") if isinstance(row.get("lanes"), list) else []
        out[str(group_name)] = {str(lane) for lane in lanes if str(lane).strip()}
    return out


def watchdog_group_for_lane(lane_name: str, groups: dict[str, set[str]]) -> str:
    for group_name, lanes in groups.items():
        if lane_name in lanes:
            return group_name
    return ""


def candidate_lane_names(base: dict[str, Any]) -> list[str]:
    names = [str(base.get("lane_name") or "").strip()]
    aliases = base.get("lane_aliases")
    if isinstance(aliases, list):
        names.extend(str(name).strip() for name in aliases if str(name).strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        deduped.append(name)
        seen.add(name)
    return deduped


def state_paths_for_candidate(base: dict[str, Any]) -> list[Path]:
    paths = base.get("state_paths")
    if isinstance(paths, list) and paths:
        return [Path(path) if not isinstance(path, Path) else path for path in paths]
    fallback = base.get("state_path")
    return [Path(fallback)] if fallback else []


def select_candidate_state(base: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
    for path in state_paths_for_candidate(base):
        payload = load_json(path)
        if payload:
            return path, payload
    paths = state_paths_for_candidate(base)
    return (paths[0] if paths else None), {}


def resolve_candidate_lane_identity(
    base: dict[str, Any],
    *,
    registry_lanes: set[str],
    watchdog_groups: dict[str, set[str]],
) -> tuple[str, bool, str]:
    candidate_names = candidate_lane_names(base)
    in_registry = any(name in registry_lanes for name in candidate_names)
    for name in candidate_names:
        group_name = watchdog_group_for_lane(name, watchdog_groups)
        if name in registry_lanes:
            return name, True, group_name
    for name in candidate_names:
        group_name = watchdog_group_for_lane(name, watchdog_groups)
        if group_name:
            return name, in_registry, group_name
    return candidate_names[0], in_registry, ""


def classify_candidate(base: dict[str, Any]) -> dict[str, Any]:
    state_path, payload = select_candidate_state(base)
    runner = payload.get("runner") if isinstance(payload.get("runner"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
    symbol = str(base["symbol"])
    symbol_row = symbols.get(symbol) if isinstance(symbols, dict) and isinstance(symbols.get(symbol), dict) else {}
    open_tickets = symbol_row.get("open_tickets") if isinstance(symbol_row.get("open_tickets"), list) else []
    realized_closes = int(symbol_row.get("realized_closes") or 0)
    realized_net_usd = float(symbol_row.get("realized_net_usd") or 0.0)
    anchor_resets = int(symbol_row.get("anchor_resets") or 0)
    tick_history_source = resolve_runner_tick_source(runner, symbol=symbol, source_key="tick_history_source")
    latest_tick_source = resolve_runner_tick_source(runner, symbol=symbol, source_key="latest_tick_source")
    latest_tick_append_source = resolve_runner_tick_source(runner, symbol=symbol, source_key="latest_tick_append_source")
    step = float(metadata.get("step") or symbol_row.get("base_step_px") or 0.0)
    anchor = float(symbol_row.get("anchor") or 0.0)
    step_pct = round((step / anchor) * 100.0, 3) if step > 0.0 and anchor > 0.0 else 0.0
    dollars_per_close = round(realized_net_usd / realized_closes, 2) if realized_closes > 0 else 0.0
    open_count = len(open_tickets)
    runtime_age_hours = hours_since(runner.get("started_at"))
    heartbeat_at = str(runner.get("heartbeat_at") or payload.get("updated_at") or "")
    lane_status = "running" if heartbeat_at else ("state_only" if payload else "missing_state")

    registry_lanes = load_registry_lanes()
    watchdog_groups = load_watchdog_groups()
    lane_name, in_registry, watchdog_group = resolve_candidate_lane_identity(
        base,
        registry_lanes=registry_lanes,
        watchdog_groups=watchdog_groups,
    )

    readiness = "bootstrapping"
    gate_status = "await_first_close"
    next_gate = "first_realized_close"

    if not payload:
        readiness = "missing_state"
        gate_status = "state_missing"
        next_gate = "restore_or_launch_probe"
    elif base["role"] == "live_candidate":
        if realized_closes >= int(base["target_closes"]) and realized_net_usd >= 500.0 and dollars_per_close >= 15.0 and anchor_resets == 0:
            readiness = "live_review_ready"
            gate_status = "shadow_gate_cleared"
            next_gate = "manual_live_review"
        elif realized_closes > 0:
            readiness = "shadow_collecting"
            gate_status = "collecting_to_live_gate"
            next_gate = "reach_50_closes_positive_reset_free"
        else:
            readiness = "seeded_flat"
            gate_status = "await_first_close"
            next_gate = "first_realized_close"
    else:
        if anchor_resets >= 10:
            readiness = "unstable_resets"
            gate_status = "probe_unstable"
            next_gate = "retune_step_before_scale_claims"
        elif realized_closes >= int(base["target_closes"]):
            readiness = "validation_ready_review"
            gate_status = "probe_gate_cleared"
            next_gate = "compare_against_eth_baseline"
        elif realized_closes > 0:
            readiness = "collecting_probe"
            gate_status = "accumulating_probe_closes"
            next_gate = "reach_10_closes_cleanly"
        elif open_count > 0:
            readiness = "seeded_open"
            gate_status = "await_first_close"
            next_gate = "first_realized_close"
        else:
            readiness = "bootstrapping"
            gate_status = "bootstrapping"
            next_gate = "collect_first_entries"

    visibility = "registry_watchdog" if in_registry and watchdog_group else ("registry_only" if in_registry else "manual_only")
    progress_label = f"{realized_closes}/{int(base['target_closes'])} closes"
    evidence_parts = [
        f"${realized_net_usd:+.2f} realized",
        f"{realized_closes} closes",
        f"{anchor_resets} resets",
        f"{open_count} open",
        f"step={step:g}",
    ]
    if step_pct > 0.0:
        evidence_parts.append(f"step_pct={step_pct:.3f}%")
    if runtime_age_hours is not None:
        evidence_parts.append(f"{runtime_age_hours:.2f}h runtime")

    visibility_parts = [visibility]
    if watchdog_group:
        visibility_parts.append(f"group={watchdog_group}")
    if int(metadata.get("shared_price_max_age_ms") or 0) > 0:
        visibility_parts.append("shared_history=yes")
    if state_path is not None:
        visibility_parts.append(f"source={state_path.name}")
    if tick_history_source:
        visibility_parts.append(f"history_source={tick_history_source}")
    if latest_tick_source:
        visibility_parts.append(f"latest_source={latest_tick_source}")

    return {
        "lane_name": lane_name,
        "lane_aliases": candidate_lane_names(base)[1:],
        "label": str(base["label"]),
        "symbol": str(base["symbol"]),
        "role": str(base["role"]),
        "target_closes": int(base["target_closes"]),
        "lane_status": lane_status,
        "readiness": readiness,
        "gate_status": gate_status,
        "next_gate": next_gate,
        "progress_label": progress_label,
        "progress_pct": format_pct(realized_closes, int(base["target_closes"])),
        "heartbeat_at": heartbeat_at,
        "runtime_age_hours": runtime_age_hours,
        "realized_closes": realized_closes,
        "realized_net_usd": round(realized_net_usd, 2),
        "dollars_per_close": dollars_per_close,
        "anchor_resets": anchor_resets,
        "open_count": open_count,
        "max_open_seen": int(symbol_row.get("max_open_total") or 0),
        "tick_history_source": tick_history_source,
        "latest_tick_source": latest_tick_source,
        "latest_tick_append_source": latest_tick_append_source,
        "step": step,
        "step_pct": step_pct,
        "shared_price_max_age_ms": int(metadata.get("shared_price_max_age_ms") or 0),
        "state_source": str(state_path.name) if state_path is not None else "",
        "in_registry": in_registry,
        "watchdog_group": watchdog_group,
        "visibility": ", ".join(visibility_parts),
        "evidence": " / ".join(evidence_parts),
    }


def build_payload() -> dict[str, Any]:
    rows = [classify_candidate(base) for base in CANDIDATES]
    order = {
        "live_review_ready": 0,
        "shadow_collecting": 1,
        "validation_ready_review": 2,
        "collecting_probe": 3,
        "seeded_open": 4,
        "unstable_resets": 5,
        "bootstrapping": 6,
        "seeded_flat": 7,
        "missing_state": 8,
    }
    rows.sort(key=lambda row: (order.get(str(row["readiness"]), 99), str(row["lane_name"])))

    eth_row = next((row for row in rows if row["lane_name"] == "shadow_ethusd_m15_warp"), None)
    unstable = [row for row in rows if row["readiness"] == "unstable_resets"]
    manual_only = [row for row in rows if not row["in_registry"]]
    current_read: list[str] = []
    if eth_row is not None:
        current_read.append(
            f"ETH remains the lead live candidate at {eth_row['progress_label']} with ${float(eth_row['realized_net_usd']):+.2f} realized, ${float(eth_row['dollars_per_close']):.2f}/close, and {int(eth_row['anchor_resets'])} resets."
        )
    if unstable:
        names = ", ".join(str(row["symbol"]) for row in unstable)
        current_read.append(
            f"Reset instability is currently concentrated in {names}; treat those probes as tuning inputs, not promotion evidence."
        )
    blind_live = [row for row in rows if row["role"] == "blind_live_probe" and row["in_registry"]]
    if blind_live:
        names = ", ".join(str(row["symbol"]) for row in blind_live)
        current_read.append(
            f"{names} are now registry-backed blind live probes under crypto supervision; treat their zero-close windows as forward sample debt, not manual-only rollout drift."
        )
    if manual_only:
        names = ", ".join(str(row["symbol"]) for row in manual_only)
        current_read.append(
            f"{names} already have active state files but are still manual-only probes outside the registry/watchdog reporting path."
        )

    return {
        "generated_at": utc_now_iso(),
        "promotion_bar": "ETH uses the 50-close live-review gate; the other crypto probes use 10 closes as the first meaningful validation bar.",
        "current_read": current_read,
        "summary": {
            "rows": len(rows),
            "registry_visible_rows": sum(1 for row in rows if row["in_registry"]),
            "manual_only_rows": sum(1 for row in rows if not row["in_registry"]),
            "unstable_rows": sum(1 for row in rows if row["readiness"] == "unstable_resets"),
        },
        "rows": rows,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path = JSON_PATH, md_path: Path = MD_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Crypto M15 Warp Readiness",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Promotion bar: {payload['promotion_bar']}",
        "",
        "## Current Read",
        "",
    ]
    for line in payload.get("current_read") or []:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Lane | Symbol | Role | Visibility | Lane Status | Readiness | Gate Status | Progress | Next Gate | Realized $ | $/Close | Resets | Open | Step | Step % | Runtime Age Hrs | Evidence |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload.get("rows") or []:
        runtime_age = "-" if row["runtime_age_hours"] is None else f"{float(row['runtime_age_hours']):.2f}"
        lines.append(
            f"| {row['lane_name']} | {row['symbol']} | {row['role']} | {row['visibility']} | {row['lane_status']} | {row['readiness']} | "
            f"{row['gate_status']} | {row['progress_label']} ({row['progress_pct']}) | {row['next_gate']} | {float(row['realized_net_usd']):+.2f} | "
            f"{float(row['dollars_per_close']):.2f} | {int(row['anchor_resets'])} | {int(row['open_count'])} | {float(row['step']):g} | "
            f"{float(row['step_pct']):.3f}% | {runtime_age} | {row['evidence']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
