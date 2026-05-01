#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from unified_objective import UnifiedObjective, ObjectiveInput
except ImportError:
    from scripts.unified_objective import UnifiedObjective, ObjectiveInput


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

LIVE_DASHBOARD_PATH = REPORTS / "live_lane_dashboard.json"
REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"
FX_GRADUATION_PATH = REPORTS / "fx_graduation_readiness.json"
BTC_CONCENTRATION_PATH = REPORTS / "live_btcusd_concentration_board.json"
ADAPTIVE_ACCEPTANCE_PATH = REPORTS / "adaptive_harness_acceptance_verdict_board.json"
ADAPTIVE_OVERNIGHT_PATH = REPORTS / "adaptive_overnight_launch_packet_board.json"
HUNGRY_HIPPO_FORWARD_PATH = REPORTS / "hungry_hippo_forward_shadow_watch_board.json"
CRYPTO_WARP_READINESS_PATH = REPORTS / "crypto_m15_warp_readiness.json"
ETH_WARP_READINESS_PATH = REPORTS / "eth_m15_warp_readiness.json"
BOOKED_PNL_PATH = REPORTS / "booked_pnl_breakdown_board.json"
TELEMETRY_ENFORCEMENT_PATH = REPORTS / "telemetry_enforcement_priority_board.json"
ADAPTIVE_PROOF_PATH = REPORTS / "adaptive_lattice_proof_board.json"
ADAPTIVE_LAB_QUEUE_PATH = REPORTS / "adaptive_lab_queue.json"
ADAPTIVE_BTC_SHADOW_RUNNER_PLAN_PATH = REPORTS / "adaptive_btc_shadow_runner_plan.json"

OUTPUT_JSON_PATH = REPORTS / "per_symbol_live_seat_board.json"
OUTPUT_MD_PATH = REPORTS / "per_symbol_live_seat_board.md"

EVIDENCE_RANK = {
    "graduated_live_reference": 4,
    "fresh_forward_live": 3,
    "carry_weighted_live": 2,
    "inherited_history_live": 1,
    "decommissioned_or_parked": 0,
}

CANDIDATE_CLASS_RANK = {
    "promotion_ready": 4,
    "shadow_ready": 3,
    "ready_for_shadow_discussion": 2,
    "shadow_collecting": 1,
    "research_only": 0,
    "rejected": -1,
}

OBJECTIVE_EVIDENCE_BONUS = {
    "graduated_live_reference": 6.0,
    "fresh_forward_live": 4.0,
    "carry_weighted_live": -3.0,
    "inherited_history_live": -4.0,
    "decommissioned_or_parked": -6.0,
}

OBJECTIVE_POSTURE_BONUS = {
    "keep_live_reference": 2.0,
    "require_fresh_forward_sample": -2.0,
}

CHALLENGER_CLASS_BONUS = {
    "promotion_ready": 6.0,
    "shadow_ready": 4.0,
    "shadow_collecting": 2.0,
    "ready_for_shadow_discussion": 2.0,
    "research_only": -1.0,
    "rejected": -4.0,
}

CHALLENGER_RUNTIME_BONUS = {
    "already_running_monitor_only": 2.0,
    "forward_proof_started": 2.0,
    "counting_clean_closes": 2.0,
    "launched_waiting_first_close": 1.0,
    "waiting_good_session_window": 0.5,
    "hold_launch_packet_defined_not_started": -0.5,
    "hold_runtime_repair_candidate": -1.0,
    "hold_disabled_proof_candidate": -2.0,
    "hold_parked_artifact": -3.0,
    "not_launched_yet": -3.0,
}

SYMBOL_ASSET_CLASS = {
    "AUDUSD": "fx",
    "BTCUSD": "crypto",
    "EURUSD": "fx",
    "GBPUSD": "fx",
    "NZDUSD": "fx",
    "USDCAD": "fx",
    "USDJPY": "fx",
    "XRPUSD": "crypto",
}

KNOWN_SYMBOLS = tuple(SYMBOL_ASSET_CLASS.keys())

PENDING_QUEUE_STATUS_RANK = {
    "ready": 0,
    "blocked": 1,
}

SEAT_PRIORITY_STATUS_ORDER = {
    "queue_ready": 0,
    "unqueued_action": 1,
    "queue_blocked": 2,
}

UNQUEUED_ACTION_URGENCY = {
    "controlled_displacement_review": 1,
    "clear_launchability_blocker": 2,
    "prepare_first_live_seat_case": 3,
    "launch_challenger_proof": 4,
    "enrich_challenger_telemetry_first": 5,
    "complete_challenger_comparison": 6,
    "collect_first_comparable_proof": 7,
    "keep_incumbent_collect_challenger_proof": 8,
    "hold_and_monitor": 99,
}

SEAT_ACTION_STAGE = {
    "enrich_challenger_telemetry_first": 0,
    "define_first_challenger": 1,
    "launch_challenger_proof": 1,
    "clear_launchability_blocker": 1,
    "collect_first_comparable_proof": 2,
    "complete_challenger_comparison": 2,
    "keep_incumbent_collect_challenger_proof": 2,
    "hold_and_monitor": 2,
    "prepare_first_live_seat_case": 3,
    "controlled_displacement_review": 4,
}

QUEUE_ACTION_CLASS_STAGE = {
    "control_shadow_and_collect_path_safety_evidence": 1,
    "build_executable_comparison_packet": 2,
    "shadow_compare_and_score": 2,
    "prove_executability_and_survival_before_promotion": 2,
    "keep_in_research_until_forward_proof": 2,
}

LOCAL_ACTIONABLE_UNQUEUED_ACTIONS = {
    "define_first_challenger",
    "launch_challenger_proof",
    "clear_launchability_blocker",
    "collect_first_comparable_proof",
    "complete_challenger_comparison",
    "prepare_first_live_seat_case",
    "enrich_challenger_telemetry_first",
}

REALIZED_USD_RE = re.compile(r"\$([+-]?\d+(?:\.\d+)?)")
OPEN_COUNT_RE = re.compile(r"(\d+)\s+open\b", re.IGNORECASE)
REALIZED_CLOSES_RE = re.compile(r"(\d+)\s+(?:realized\s+)?closes?\b", re.IGNORECASE)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def extract_realized_usd(*texts: str) -> float | None:
    for text in texts:
        match = REALIZED_USD_RE.search(str(text or ""))
        if match:
            return parse_float(match.group(1), 0.0)
    return None


def extract_open_count(*texts: str) -> int | None:
    for text in texts:
        match = OPEN_COUNT_RE.search(str(text or ""))
        if match:
            return parse_int(match.group(1), 0)
    return None


def extract_close_count(*texts: str) -> int | None:
    for text in texts:
        match = REALIZED_CLOSES_RE.search(str(text or ""))
        if match:
            return parse_int(match.group(1), 0)
    return None


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return ordered


def iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(iter_strings(item))
        return values
    if isinstance(value, (list, tuple, set)):
        values = []
        for item in value:
            values.extend(iter_strings(item))
        return values
    return []


def infer_asset_class(symbol: str) -> str:
    return SYMBOL_ASSET_CLASS.get(str(symbol or "").upper(), "unknown")


def registry_lane_symbols(registry_payload: dict[str, Any]) -> dict[str, list[str]]:
    mapped: dict[str, list[str]] = {}
    for lane in list(registry_payload.get("lanes") or []):
        if not isinstance(lane, dict):
            continue
        lane_name = str(lane.get("name") or "").strip()
        args = list(lane.get("restart_args") or [])
        symbols: list[str] = []
        index = 0
        while index < len(args):
            token = str(args[index] or "")
            if token == "--symbol" and index + 1 < len(args):
                symbols.append(str(args[index + 1]).upper())
                index += 2
                continue
            if token == "--symbols":
                index += 1
                while index < len(args) and not str(args[index]).startswith("--"):
                    symbols.append(str(args[index]).upper())
                    index += 1
                continue
            index += 1
        if lane_name:
            mapped[lane_name] = unique_strings(symbols)
    return mapped


def live_booked_map(booked_payload: dict[str, Any]) -> dict[str, float]:
    mapped: dict[str, float] = {}
    live_rows = dict(booked_payload.get("live") or {}).get("rows") or []
    for row in live_rows:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if lane:
            mapped[lane] = parse_float(row.get("booked_usd"))
    return mapped


def prefer_live_holder_booked(booked_map: dict[str, float], lane: str, broker_net_usd: float) -> float:
    booked_usd = booked_map.get(lane)
    if booked_usd is None:
        return broker_net_usd
    if abs(booked_usd) <= 1e-9 and abs(broker_net_usd) > 1e-9:
        return broker_net_usd
    return booked_usd


def pick_primary_live_holder(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    ordered = sorted(
        [dict(row) for row in rows],
        key=lambda row: (
            -EVIDENCE_RANK.get(str(row.get("evidence_basis") or ""), -1),
            -parse_float(row.get("booked_usd")),
            -parse_int(row.get("close_count")),
            str(row.get("lane") or ""),
        ),
    )
    return ordered[0]


def fx_watch_lead_map(fx_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    target = str(dict(fx_payload.get("watch_lead") or {}).get("candidate") or "").strip()
    for row in list(fx_payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("candidate") or "").strip() != target:
            continue
        scope_symbols = [
            piece.strip().upper()
            for piece in str(row.get("scope") or "").replace("+", " ").split()
            if piece.strip().upper().endswith("USD") or piece.strip().upper().endswith("JPY")
        ]
        return {
            symbol: {
                "symbol": symbol,
                "lane_name": str(row.get("lane_name") or ""),
                "label": str(row.get("candidate") or ""),
                "family": "fx_shadow",
                "candidate_class": str(row.get("readiness") or ""),
                "runtime_status": str(row.get("gate_status") or ""),
                "challenger_read": str(row.get("recommendation") or ""),
                "scope": str(row.get("scope") or ""),
                "notes": str(row.get("evidence") or ""),
                "objective_realized_closes": parse_int(row.get("progress_value")) if "closes" in str(row.get("progress_label") or "").lower() else extract_close_count(str(row.get("evidence") or "")),
                "objective_realized_net_usd": extract_realized_usd(str(row.get("evidence") or "")),
                "objective_open_count": (
                    extract_open_count(str(row.get("operator_posture") or ""), str(row.get("evidence") or ""))
                ),
                "objective_source_read": "fx_graduation_readiness",
            }
            for symbol in scope_symbols
        }
    return {}


def adaptive_acceptance_maps(acceptance_payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    by_symbol: dict[str, str] = {}
    by_candidate_id: dict[str, str] = {}
    for row in list(acceptance_payload.get("candidates") or []):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        verdict = str(row.get("verdict") or "")
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id and verdict:
            by_candidate_id[candidate_id] = verdict
        if symbol and verdict:
            current = by_symbol.get(symbol, "")
            if CANDIDATE_CLASS_RANK.get(verdict, -99) >= CANDIDATE_CLASS_RANK.get(current, -99):
                by_symbol[symbol] = verdict
    return by_symbol, by_candidate_id


def adaptive_challenger_rows(
    acceptance_payload: dict[str, Any],
    overnight_payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    acceptance_by_symbol, acceptance_by_candidate_id = adaptive_acceptance_maps(acceptance_payload)
    primary: dict[str, dict[str, Any]] = {}
    secondary: dict[str, list[dict[str, Any]]] = {}

    symbol_by_packet_id = {
        "btc_restore_comparison_shadow": "BTCUSD",
        "gbpusd_adaptive_comparison_packet": "GBPUSD",
        "nzdusd_transfer_probe": "NZDUSD",
        "shadow_usdjpy_gap2": "USDJPY",
        "shadow_usdjpy_shallow03": "USDJPY",
    }
    candidate_id_by_packet_id = {
        "btc_restore_comparison_shadow": "btc_restore_comparison_shadow",
        "gbpusd_adaptive_comparison_packet": "gbpusd_adaptive_comparison_packet",
        "shadow_usdjpy_gap2": "usdjpy_bounded_proof_refresh",
        "shadow_usdjpy_shallow03": "usdjpy_bounded_proof_refresh",
    }
    label_by_packet_id = {
        "btc_restore_comparison_shadow": "BTC restore comparison shadow",
        "gbpusd_adaptive_comparison_packet": "GBPUSD adaptive trend-harvest shadow",
        "nzdusd_transfer_probe": "NZDUSD adapt-first transfer probe",
        "shadow_usdjpy_gap2": "USDJPY bounded proof refresh",
        "shadow_usdjpy_shallow03": "USDJPY bounded proof refresh",
    }

    for row in list(overnight_payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        packet_id = str(row.get("packet_id") or "")
        symbol = symbol_by_packet_id.get(packet_id, "")
        if not symbol:
            continue
        candidate_id = candidate_id_by_packet_id.get(packet_id, "")
        item = {
            "symbol": symbol,
            "lane_name": str(row.get("lane_name") or ""),
            "label": label_by_packet_id.get(packet_id, packet_id),
            "family": "adaptive_shadow",
            "candidate_class": (
                acceptance_by_candidate_id.get(candidate_id)
                or acceptance_by_symbol.get(symbol)
                or "research_only"
            ),
            "runtime_status": str(row.get("action_status") or ""),
            "challenger_read": str(row.get("action_read") or ""),
            "notes": str(row.get("why") or ""),
            "packet_id": packet_id,
            "objective_realized_closes": (
                parse_int(row.get("artifact_trade_closes"))
                if parse_int(row.get("artifact_trade_closes")) > 0
                else parse_int(row.get("execution_trade_closes"))
            ),
            "objective_realized_net_usd": (
                parse_float(row.get("first_path_close_realized_pnl"))
                if row.get("first_path_close_realized_pnl") is not None
                else None
            ),
            "objective_open_count": (
                parse_int(row.get("artifact_open_count"))
                if parse_int(row.get("artifact_open_count")) > 0
                else parse_int(row.get("execution_open_count"))
            ),
            "objective_source_read": "adaptive_overnight_launch_packet",
        }
        secondary.setdefault(symbol, []).append(item)
        if symbol not in primary:
            primary[symbol] = item
    return primary, secondary


def hungry_hippo_challenger_map(hh_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in list(hh_payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        runtime_state = str(row.get("runtime_state") or "")
        proof_started = bool(row.get("proof_started")) or runtime_state == "forward_proof_started" or parse_int(row.get("realized_closes")) > 0
        mapped[symbol] = {
            "symbol": symbol,
            "lane_name": Path(str(row.get("state_path") or "")).stem.replace("_state", ""),
            "label": f"Hungry Hippo {symbol} breakout shadow",
            "family": "hungry_hippo_shadow",
            "candidate_class": str(row.get("generalization_status") or ""),
            "runtime_status": str(row.get("runtime_state") or ""),
            "challenger_read": str(row.get("next_action") or ""),
            "notes": (
                f"deployment={row.get('deployment_verdict')}; "
                f"guardrail={row.get('guardrail_status')}; "
                f"realized_closes={parse_int(row.get('realized_closes'))}; "
                f"current_open_count={parse_int(row.get('current_open_count'))}"
            ),
            "objective_realized_closes": parse_int(row.get("realized_closes")) if proof_started else None,
            "objective_realized_net_usd": parse_float(row.get("realized_net_usd"), 0.0) if proof_started else None,
            "objective_open_count": parse_int(row.get("current_open_count")) if proof_started or runtime_state != "not_launched_yet" else None,
            "objective_source_read": "hungry_hippo_forward_shadow_watch",
        }
    return mapped


def crypto_shadow_candidate_class(readiness: str) -> str:
    normalized = str(readiness or "").strip()
    if normalized in {"shadow_collecting", "collecting_probe"}:
        return "shadow_collecting"
    if normalized in {"shadow_gate_failed", "unstable_resets", "probe_unstable"}:
        return "rejected"
    if normalized in {"promotion_ready", "shadow_ready", "ready_for_shadow_discussion", "research_only", "rejected"}:
        return normalized
    return "research_only"


def crypto_warp_challenger_map(
    crypto_payload: dict[str, Any],
    eth_payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in list(crypto_payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        lane_name = str(row.get("lane_name") or "").strip()
        symbol = str(row.get("symbol") or "").upper()
        if not lane_name.startswith("shadow_") or not symbol:
            continue
        mapped[symbol] = {
            "symbol": symbol,
            "lane_name": lane_name,
            "label": str(row.get("label") or lane_name),
            "family": "crypto_shadow",
            "candidate_class": crypto_shadow_candidate_class(str(row.get("readiness") or "")),
            "runtime_status": str(row.get("gate_status") or row.get("lane_status") or ""),
            "challenger_read": str(row.get("next_gate") or row.get("evidence") or ""),
            "notes": str(row.get("evidence") or ""),
            "objective_realized_closes": parse_int(row.get("realized_closes")) if parse_int(row.get("realized_closes")) > 0 else None,
            "objective_realized_net_usd": parse_float(row.get("realized_net_usd")) if row.get("realized_net_usd") is not None else None,
            "objective_open_count": parse_int(row.get("open_count")) if parse_int(row.get("open_count")) > 0 else None,
            "objective_source_read": "crypto_m15_warp_readiness",
        }
    for row in list(eth_payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        lane_name = str(row.get("lane_name") or "").strip()
        symbol = str(row.get("symbol") or "").upper()
        if not lane_name.startswith("shadow_") or not symbol:
            continue
        mapped[symbol] = {
            "symbol": symbol,
            "lane_name": lane_name,
            "label": str(row.get("candidate") or lane_name),
            "family": "crypto_shadow",
            "candidate_class": crypto_shadow_candidate_class(str(row.get("readiness") or "")),
            "runtime_status": str(row.get("gate_status") or row.get("lane_status") or ""),
            "challenger_read": str(row.get("next_gate") or row.get("evidence") or ""),
            "notes": str(row.get("evidence") or ""),
            "objective_realized_closes": parse_int(row.get("realized_closes")) if parse_int(row.get("realized_closes")) > 0 else None,
            "objective_realized_net_usd": parse_float(row.get("realized_net_usd")) if row.get("realized_net_usd") is not None else None,
            "objective_open_count": parse_int(row.get("open_count")) if parse_int(row.get("open_count")) > 0 else None,
            "objective_source_read": "eth_m15_warp_readiness",
        }
    return mapped


def build_live_holder_rows(
    live_payload: dict[str, Any],
    registry_payload: dict[str, Any],
    booked_payload: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    lane_symbols = registry_lane_symbols(registry_payload)
    booked_map = live_booked_map(booked_payload)
    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in list(live_payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        if not bool(row.get("enabled")):
            continue
        lane = str(row.get("lane") or "")
        symbols = lane_symbols.get(lane, [])
        if not symbols:
            continue
        row_payload = {
            "lane": lane,
            "kind": str(row.get("kind") or ""),
            "status": str(row.get("status") or ""),
            "evidence_basis": str(row.get("evidence_basis") or ""),
            "operator_posture": str(row.get("operator_posture") or ""),
            "booked_usd": prefer_live_holder_booked(booked_map, lane, parse_float(row.get("broker_net_usd"))),
            "broker_net_usd": parse_float(row.get("broker_net_usd")),
            "fresh_session_booked_usd": parse_float(row.get("fresh_session_booked_usd")),
            "fresh_session_usd_per_hour": parse_float(row.get("fresh_session_usd_per_hour")),
            "close_count": parse_int(row.get("close_count")),
            "open_count": parse_int(row.get("broker_open_count")),
            "notes": str(row.get("notes") or ""),
        }
        for symbol in symbols:
            rows_by_symbol.setdefault(symbol, []).append(dict(row_payload))
    return rows_by_symbol


def seat_verdict(
    primary_holder: dict[str, Any] | None,
    holder_count: int,
) -> str:
    if primary_holder is None:
        return "no_live_seat"
    evidence_basis = str(primary_holder.get("evidence_basis") or "")
    if evidence_basis == "graduated_live_reference" and holder_count == 1:
        return "defended_live_seat"
    if evidence_basis == "graduated_live_reference" and holder_count > 1:
        return "defended_but_contested_live_seat"
    if holder_count > 1:
        return "contested_provisional_live_seat"
    return "provisional_live_seat"


def next_action_for_symbol(
    symbol: str,
    primary_holder: dict[str, Any] | None,
    holder_count: int,
    challenger: dict[str, Any] | None,
) -> str:
    if primary_holder is None:
        return "shadow_challenger_needed"
    evidence_basis = str(primary_holder.get("evidence_basis") or "")
    if symbol == "BTCUSD":
        return "live_demotion_candidate" if holder_count > 1 else "keep_live_but_under_audit"
    if evidence_basis == "graduated_live_reference" and holder_count == 1:
        return "keep_live"
    if holder_count > 1:
        return "keep_live_but_under_audit"
    if evidence_basis != "graduated_live_reference" and challenger is not None:
        return "keep_live_but_under_audit"
    return "keep_live_but_under_audit"


def build_row_reason(
    symbol: str,
    primary_holder: dict[str, Any] | None,
    holder_count: int,
    challenger: dict[str, Any] | None,
    btc_concentration_payload: dict[str, Any],
) -> str:
    if primary_holder is None and challenger is not None:
        return (
            f"{symbol} has no live seat yet. Current challenger class is "
            f"`{challenger.get('candidate_class', '')}` with runtime state "
            f"`{challenger.get('runtime_status', '')}`."
        )
    if primary_holder is None:
        return f"{symbol} has no live seat and no active challenger surface yet."
    if symbol == "BTCUSD":
        operator_posture = str(dict(btc_concentration_payload.get("summary") or {}).get("operator_posture") or "")
        return (
            f"BTCUSD currently has `{holder_count}` live occupants and no graduated reference. "
            f"The top booked live row is `{primary_holder.get('lane')}` at `${parse_float(primary_holder.get('booked_usd')):+.2f}`, "
            f"but the strongest challenger is `{(challenger or {}).get('candidate_class', 'missing')}` / "
            f"`{(challenger or {}).get('runtime_status', 'missing')}` and BTC concentration posture is `{operator_posture}`."
        )
    if str(primary_holder.get("evidence_basis") or "") == "graduated_live_reference" and holder_count == 1:
        return (
            f"{symbol} is currently defended by `{primary_holder.get('lane')}` as a graduated live reference "
            f"with `${parse_float(primary_holder.get('booked_usd')):+.2f}` booked."
        )
    if str(primary_holder.get("evidence_basis") or "") == "graduated_live_reference":
        return (
            f"{symbol} has a real graduated winner in `{primary_holder.get('lane')}`, but `{holder_count - 1}` "
            f"other live occupant(s) still share the seat so the symbol needs audit rather than fresh promotion language."
        )
    return (
        f"{symbol} is still carried by provisional live truth from `{primary_holder.get('lane')}` "
        f"(`{primary_holder.get('evidence_basis')}`), so the seat needs audit before it can count as a durable live winner."
    )


def live_holder_objective_proxy(
    symbol: str,
    primary_holder: dict[str, Any] | None,
    *,
    seat_conflict: bool,
    btc_concentration_payload: dict[str, Any],
) -> dict[str, Any]:
    if primary_holder is None:
        return {
            "score": 0.0,
            "status": "missing_live_seat",
            "read": "No live seat exists yet, so there is no current holder to score on a max-profit basis.",
            "components": {
                "realized_cash_component": 0.0,
                "close_density_component": 0.0,
                "evidence_component": 0.0,
                "operator_posture_component": 0.0,
                "open_inventory_penalty": 0.0,
                "seat_conflict_penalty": 0.0,
                "concentration_penalty": 0.0,
            },
        }

    booked_usd = parse_float(primary_holder.get("booked_usd"))
    fresh_session_booked_usd = parse_float(primary_holder.get("fresh_session_booked_usd"))
    fresh_session_usd_per_hour = parse_float(primary_holder.get("fresh_session_usd_per_hour"))
    close_count = parse_int(primary_holder.get("close_count"))
    open_count = parse_int(primary_holder.get("open_count"))
    evidence_basis = str(primary_holder.get("evidence_basis") or "")
    operator_posture = str(primary_holder.get("operator_posture") or "")
    runtime_status = str(primary_holder.get("status") or "")
    btc_operator_posture = str(dict(btc_concentration_payload.get("summary") or {}).get("operator_posture") or "")

    realized_cash_component = clamp(booked_usd / 250.0, -12.0, 12.0)
    fresh_cash_component = clamp(fresh_session_booked_usd / 4.0, -6.0, 8.0)
    fresh_velocity_component = clamp(fresh_session_usd_per_hour, -6.0, 12.0)
    close_density_component = clamp(close_count / 100.0, 0.0, 4.0)
    evidence_component = OBJECTIVE_EVIDENCE_BONUS.get(evidence_basis, 0.0)
    operator_posture_component = OBJECTIVE_POSTURE_BONUS.get(operator_posture, 0.0)
    open_inventory_penalty = min(open_count * 0.75, 6.0)
    seat_conflict_penalty = 3.0 if seat_conflict else 0.0
    concentration_penalty = 2.0 if symbol == "BTCUSD" and btc_operator_posture == "carry_until_threshold_break" else 0.0
    runtime_penalty = 4.0 if runtime_status and runtime_status != "ok" else 0.0
    flat_nonmonetizing_penalty = (
        3.0
        if runtime_status and runtime_status != "ok" and open_count == 0 and abs(fresh_session_booked_usd) <= 1e-9
        else 0.0
    )

    score = round(
        realized_cash_component
        + fresh_cash_component
        + fresh_velocity_component
        + close_density_component
        + evidence_component
        + operator_posture_component
        - open_inventory_penalty
        - seat_conflict_penalty
        - concentration_penalty,
        2,
    )
    score = round(
        score
        - runtime_penalty
        - flat_nonmonetizing_penalty,
        2,
    )

    # Unified objective function (Gap 2): evaluate alongside legacy proxy
    unified_result = None
    if booked_usd is not None and close_count and close_count > 0:
        unified_result = UnifiedObjective.evaluate(ObjectiveInput(
            realized_net_usd=booked_usd,
            close_count=close_count,
            floating_usd=0.0,  # Not available in seat board
            open_count=open_count,
            anchor_reset_count=0,  # Not available in seat board
            max_adverse_excursion_usd=0.0,
            first_path_verdict="",
            realized_win_rate=0.0,  # Not available in seat board
        ))

    if evidence_basis == "graduated_live_reference" and not seat_conflict and score >= 10.0:
        status = "strong_live_reference"
    elif evidence_basis == "graduated_live_reference" and score >= 5.0:
        status = "profitable_but_contested_reference"
    elif score >= 2.0:
        status = "cashflow_positive_but_provisional"
    elif score >= 0.0:
        status = "thin_edge_under_audit"
    else:
        status = "carry_dominated_or_unproven"

    read = (
        f"Proxy score `{score:+.2f}` combines realized cash `{realized_cash_component:+.2f}`, "
        f"fresh booked `{fresh_cash_component:+.2f}`, fresh velocity `{fresh_velocity_component:+.2f}`, "
        f"close density `{close_density_component:+.2f}`, evidence `{evidence_component:+.2f}`, "
        f"and operator posture `{operator_posture_component:+.2f}` against open inventory "
        f"`-{open_inventory_penalty:.2f}`, seat conflict `-{seat_conflict_penalty:.2f}`, "
        f"concentration `-{concentration_penalty:.2f}`, runtime `-{runtime_penalty:.2f}`, "
        f"and flat non-monetizing penalty `-{flat_nonmonetizing_penalty:.2f}`."
    )

    return {
        "score": score,
        "status": status,
        "read": read,
        "unified_objective_score": round(unified_result.total, 3) if unified_result else None,
        "unified_objective_verdict": unified_result.verdict if unified_result else None,
        "components": {
            "realized_cash_component": round(realized_cash_component, 2),
            "fresh_cash_component": round(fresh_cash_component, 2),
            "fresh_velocity_component": round(fresh_velocity_component, 2),
            "close_density_component": round(close_density_component, 2),
            "evidence_component": round(evidence_component, 2),
            "operator_posture_component": round(operator_posture_component, 2),
            "open_inventory_penalty": round(open_inventory_penalty, 2),
            "seat_conflict_penalty": round(seat_conflict_penalty, 2),
            "concentration_penalty": round(concentration_penalty, 2),
            "runtime_penalty": round(runtime_penalty, 2),
            "flat_nonmonetizing_penalty": round(flat_nonmonetizing_penalty, 2),
        },
    }


def challenger_objective_proxy(challenger: dict[str, Any] | None) -> dict[str, Any]:
    if challenger is None:
        return {
            "score": 0.0,
            "status": "missing_challenger",
            "read": "No challenger is attached to this symbol yet, so there is no challenger-side objective read.",
            "components": {},
        }

    close_count_raw = challenger.get("objective_realized_closes")
    open_count_raw = challenger.get("objective_open_count")
    realized_net_raw = challenger.get("objective_realized_net_usd")
    candidate_class = str(challenger.get("candidate_class") or "")
    runtime_status = str(challenger.get("runtime_status") or "")

    close_count = parse_int(close_count_raw, -1)
    open_count = parse_int(open_count_raw, -1)
    has_close_count = close_count >= 0
    has_open_count = open_count >= 0
    has_realized_net = realized_net_raw is not None

    if runtime_status == "not_launched_yet" and close_count <= 0 and open_count <= 0 and not has_realized_net:
        return {
            "score": 0.0,
            "status": "challenger_incomparable",
            "read": (
                f"Challenger `{challenger.get('lane_name') or challenger.get('label') or ''}` is still parked without "
                "runtime proof, so zero-valued placeholders do not count as objective-comparable evidence."
            ),
            "components": {},
        }

    if not has_close_count and not has_open_count:
        return {
            "score": 0.0,
            "status": "challenger_incomparable",
            "read": (
                f"Challenger `{challenger.get('lane_name') or challenger.get('label') or ''}` does not yet expose "
                "close/open proof fields on an objective-comparable surface."
            ),
            "components": {},
        }

    realized_cash_component = clamp(parse_float(realized_net_raw) / 250.0, -12.0, 12.0) if has_realized_net else 0.0
    close_density_component = clamp(parse_int(close_count_raw) / 100.0, 0.0, 4.0) if has_close_count else 0.0
    class_component = CHALLENGER_CLASS_BONUS.get(candidate_class, 0.0)
    runtime_component = CHALLENGER_RUNTIME_BONUS.get(runtime_status, 0.0)
    open_inventory_penalty = min(parse_int(open_count_raw, 0) * 0.75, 6.0) if has_open_count else 0.0

    score = round(
        realized_cash_component
        + close_density_component
        + class_component
        + runtime_component
        - open_inventory_penalty,
        2,
    )

    if has_realized_net and has_close_count and has_open_count:
        status = "challenger_comparable"
    else:
        status = "challenger_partially_comparable"

    read = (
        f"Challenger proxy `{score:+.2f}` combines realized cash `{realized_cash_component:+.2f}`, "
        f"close density `{close_density_component:+.2f}`, candidate class `{class_component:+.2f}`, "
        f"runtime `{runtime_component:+.2f}`, and open inventory `-{open_inventory_penalty:.2f}` "
        f"from `{challenger.get('objective_source_read') or 'unknown source'}`."
    )
    if not has_realized_net:
        read += " Realized-net proof is still missing, so this remains partial rather than fully comparable."

    return {
        "score": score,
        "status": status,
        "read": read,
        "components": {
            "realized_cash_component": round(realized_cash_component, 2),
            "close_density_component": round(close_density_component, 2),
            "candidate_class_component": round(class_component, 2),
            "runtime_component": round(runtime_component, 2),
            "open_inventory_penalty": round(open_inventory_penalty, 2),
        },
    }


def telemetry_rows_by_lane(telemetry_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in list(telemetry_payload.get("lanes") or []):
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane_name") or "").strip()
        if lane:
            indexed[lane] = dict(row)
    return indexed


def adaptive_proof_rows_by_symbol(proof_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in list(proof_payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            indexed[symbol] = dict(row)
    return indexed


def adaptive_lab_tasks_by_symbol(queue_payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for task in list(queue_payload.get("tasks") or []):
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "")
        if status not in PENDING_QUEUE_STATUS_RANK:
            continue
        haystack = " ".join(iter_strings(task)).upper()
        matched_symbols = [
            symbol
            for symbol in KNOWN_SYMBOLS
            if symbol in haystack or symbol.lower() in haystack.lower()
        ]
        for symbol in unique_strings(matched_symbols):
            indexed.setdefault(symbol, []).append(dict(task))

    for symbol, tasks in indexed.items():
        tasks.sort(
            key=lambda task: (
                PENDING_QUEUE_STATUS_RANK.get(str(task.get("status") or ""), 99),
                parse_int(task.get("priority"), 9999),
                str(task.get("task_id") or ""),
            )
        )
    return indexed


def adaptive_runner_plan_by_symbol(plan_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    symbol = str(plan_payload.get("symbol") or "").upper()
    if not symbol:
        return {}
    return {symbol: dict(plan_payload)}


def challenger_proof_integrity(
    challenger: dict[str, Any] | None,
    telemetry_by_lane: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    if challenger is None:
        return "no_challenger", "No challenger is attached, so there is no challenger telemetry integrity to judge."

    lane_name = str(challenger.get("lane_name") or "").strip()
    if not lane_name:
        return "telemetry_unknown", "Challenger has no lane identity, so telemetry integrity cannot be checked."

    telemetry_row = telemetry_by_lane.get(lane_name)
    if telemetry_row is None:
        return (
            "telemetry_not_flagged",
            f"No telemetry-enforcement debt row is currently recorded for `{lane_name}`; treat proof integrity as not presently blocked by the enforcement board.",
        )

    verdict = str(telemetry_row.get("enrichment_verdict") or "")
    active_closes = parse_int(telemetry_row.get("active_closes"))
    total_closes = parse_int(telemetry_row.get("total_closes"))
    score = parse_int(telemetry_row.get("enrichment_score"))
    if verdict != "needs_enrichment":
        return (
            "telemetry_ok",
            f"Telemetry board does not currently flag `{lane_name}` for enrichment debt.",
        )
    if active_closes > 0:
        return (
            "telemetry_debt_active",
            f"`{lane_name}` still has telemetry-enforcement debt on `{active_closes}` active closes (score `{score}`), so objective reads remain proof-blind until those events are enriched.",
        )
    if total_closes > 0:
        return (
            "telemetry_debt_inherited_only",
            f"`{lane_name}` has `{total_closes}` close-like events but they are inherited/pre-enrichment only (score `{score}`), so current objective proof is still integrity-limited.",
        )
    return (
        "telemetry_debt_preclose",
        f"`{lane_name}` is flagged for telemetry enrichment before meaningful close-like proof exists (score `{score}`).",
    )


def objective_comparison_read(
    live_proxy: dict[str, Any],
    challenger_proxy: dict[str, Any],
    primary_holder: dict[str, Any] | None,
    challenger: dict[str, Any] | None,
) -> tuple[str, str]:
    if primary_holder is None:
        return (
            "no_live_incumbent",
            "No live incumbent exists yet, so the seat is still in challenger-building mode rather than true objective comparison.",
        )
    if challenger is None:
        return (
            "no_challenger",
            "No challenger is attached to this symbol yet, so there is no live-vs-challenger objective comparison to read.",
        )

    challenger_status = str(challenger_proxy.get("status") or "")
    if challenger_status == "challenger_incomparable":
        return (
            "challenger_incomparable",
            "The incumbent now has an objective proxy, but the challenger still lacks enough passive proof fields for even a partial comparison.",
        )

    live_score = parse_float(live_proxy.get("score"))
    challenger_score = parse_float(challenger_proxy.get("score"))
    delta = round(challenger_score - live_score, 2)
    if challenger_status == "challenger_partially_comparable":
        return (
            "partial_objective_comparison",
            f"Partial comparison only: challenger proxy is `{delta:+.2f}` vs the live holder, but realized-net challenger proof is still missing.",
        )
    if delta >= 2.0:
        return (
            "challenger_objective_edge",
            f"Comparable objective read favors the challenger by `{delta:+.2f}` on current passive proof.",
        )
    if delta <= -2.0:
        return (
            "incumbent_objective_edge",
            f"Comparable objective read still favors the incumbent by `{abs(delta):.2f}` on current passive proof.",
        )
    return (
        "objective_neck_and_neck",
        f"Comparable objective read is roughly tied (`delta={delta:+.2f}`), so seat judgment should stay with broader proof and runtime truth.",
    )


def objective_displacement_status(
    primary_holder: dict[str, Any] | None,
    challenger: dict[str, Any] | None,
    comparison_status: str,
    challenger_proxy_status: str,
    proof_integrity_status: str,
) -> tuple[str, str]:
    if primary_holder is None:
        return (
            "no_live_incumbent",
            "No live incumbent exists yet, so the symbol is still in first-seat construction rather than seat displacement.",
        )
    if challenger is None:
        return (
            "no_active_challenger",
            "No active challenger is attached to this symbol, so there is no displacement case to evaluate.",
        )

    challenger_class = str(challenger.get("candidate_class") or "")
    runtime_status = str(challenger.get("runtime_status") or "")
    hold_statuses = {"hold_runtime_repair_candidate", "hold_disabled_proof_candidate", "hold_parked_artifact", "not_launched_yet"}
    telemetry_blocked_statuses = {"telemetry_debt_active", "telemetry_debt_inherited_only"}

    if comparison_status == "challenger_objective_edge" and proof_integrity_status in telemetry_blocked_statuses:
        return (
            "objective_edge_but_telemetry_blind",
            "The challenger has a passive objective edge, but telemetry enforcement still flags its proof as blind or inherited-only, so displacement language would be premature.",
        )
    if comparison_status == "challenger_objective_edge" and challenger_class in {"shadow_ready", "promotion_ready"} and runtime_status not in hold_statuses:
        return (
            "objective_displacement_candidate",
            "The challenger has a real objective edge on current passive proof and is already in a launchable candidate class, so this is now an honest seat-displacement candidate.",
        )
    if comparison_status == "challenger_objective_edge":
        return (
            "objective_edge_but_not_launchable",
            "The challenger proxy is stronger, but launch/readiness posture is still not strong enough to call this an executable displacement case.",
        )
    if comparison_status == "incumbent_objective_edge":
        return (
            "incumbent_still_leads",
            "Objective comparison still favors the incumbent, so the challenger remains proof-collection pressure rather than a displacement case.",
        )
    if comparison_status == "objective_neck_and_neck":
        return (
            "displacement_tie_needs_more_proof",
            "Live and challenger objective reads are too close to separate honestly, so keep collecting proof instead of forcing a seat call.",
        )
    if challenger_proxy_status == "challenger_partially_comparable":
        return (
            "comparison_incomplete",
            "The challenger has some objective fields but not enough comparable proof to make a displacement judgment yet.",
        )
    return (
        "comparison_not_ready",
        "This symbol still lacks enough challenger-side proof to make a real objective displacement judgment.",
    )


def seat_unblocker_action(
    *,
    primary_holder: dict[str, Any] | None,
    challenger: dict[str, Any] | None,
    challenger_proxy_status: str,
    proof_integrity_status: str,
    displacement_status: str,
    adaptive_proof_row: dict[str, Any] | None,
) -> tuple[str, str]:
    proof_stage = str((adaptive_proof_row or {}).get("stage") or "")
    profit_mode = str((adaptive_proof_row or {}).get("profit_mode") or "")

    if primary_holder is None:
        if challenger is None:
            return (
                "define_first_challenger",
                "No live incumbent or active challenger exists yet, so the next move is to define and launch the first honest challenger for this symbol.",
            )
        if str((challenger or {}).get("runtime_status") or "") == "not_launched_yet":
            return (
                "launch_challenger_proof",
                "The symbol has no live incumbent and the challenger is still parked, so the next move is to launch the challenger proof lane rather than debate seat displacement.",
            )
        if challenger_proxy_status == "challenger_comparable":
            return (
                "prepare_first_live_seat_case",
                "The symbol has no live incumbent and the challenger now has comparable proof, so the next move is to turn that proof into a first live-seat case.",
            )
        return (
            "collect_first_comparable_proof",
            "The symbol has no live incumbent yet, and the challenger still needs more comparable proof before a first seat case is honest.",
        )

    if displacement_status == "objective_displacement_candidate":
        extra = f" Adaptive proof stage is `{proof_stage}` with profit mode `{profit_mode}`." if proof_stage or profit_mode else ""
        return (
            "controlled_displacement_review",
            "The challenger has cleared the current passive seat gates, so the next move is a controlled displacement review against the incumbent." + extra,
        )
    if displacement_status == "objective_edge_but_telemetry_blind":
        return (
            "enrich_challenger_telemetry_first",
            "The challenger may have an objective edge, but telemetry blindness still blocks a trustworthy seat call. Fix the enrichment debt first.",
        )
    if displacement_status == "objective_edge_but_not_launchable":
        return (
            "clear_launchability_blocker",
            "The challenger looks stronger on the passive objective read, but a launchability blocker still prevents a real seat challenge.",
        )
    if displacement_status == "incumbent_still_leads":
        extra = f" Current adaptive proof stage is `{proof_stage}`." if proof_stage else ""
        return (
            "keep_incumbent_collect_challenger_proof",
            "The incumbent still leads, so the next move is to keep the live seat and keep collecting cleaner challenger proof rather than forcing a displacement story." + extra,
        )
    if proof_integrity_status in {"telemetry_debt_active", "telemetry_debt_inherited_only"}:
        return (
            "enrich_challenger_telemetry_first",
            "Telemetry debt is the clearest blocker on this seat. Enrich the challenger path before trusting its score.",
        )
    if displacement_status == "comparison_incomplete":
        return (
            "complete_challenger_comparison",
            "The challenger still lacks enough comparable proof to support a seat judgment, so finish the comparison data first.",
        )
    return (
        "hold_and_monitor",
        "No stronger unblocker surfaced from the current passive data; keep monitoring while the current proof stack matures.",
    )


def seat_unblocker_priority_context(
    *,
    symbol: str,
    action: str,
    queue_tasks_by_symbol: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    symbol_tasks = [dict(task) for task in queue_tasks_by_symbol.get(symbol, [])]
    if symbol_tasks:
        chosen = dict(symbol_tasks[0])
        task_status = str(chosen.get("status") or "")
        priority_rank = parse_int(chosen.get("priority"), 9999)
        priority_status = "queue_ready" if task_status == "ready" else "queue_blocked"
        read = (
            f"Adaptive lab queue already carries a `{task_status}` symbol task at priority `{priority_rank}`: "
            f"`{chosen.get('task_id') or ''}` in `{chosen.get('lane') or ''}`. "
            "Use that queue row as the ordering anchor for this seat action instead of inventing a duplicate plan."
        )
        return {
            "priority_rank": priority_rank,
            "priority_status": priority_status,
            "priority_read": read,
            "queue_task_id": str(chosen.get("task_id") or ""),
            "queue_task_title": str(chosen.get("title") or ""),
            "queue_task_status": task_status,
            "queue_task_lane": str(chosen.get("lane") or ""),
            "queue_task_next_action_class": str(chosen.get("next_action_class") or ""),
        }

    urgency = UNQUEUED_ACTION_URGENCY.get(action, 99)
    return {
        "priority_rank": None,
        "priority_status": "unqueued_action",
        "priority_read": (
            "No pending adaptive lab queue task currently names this symbol, so the seat board keeps the symbol "
            f"ordered by local unblocker urgency (`{action}` => `{urgency}`) until the room formalizes a queue row."
        ),
        "queue_task_id": "",
        "queue_task_title": "",
        "queue_task_status": "",
        "queue_task_lane": "",
        "queue_task_next_action_class": "",
    }


def seat_priority_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    priority_status = str(row.get("seat_unblocker_priority_status") or "")
    priority_rank = row.get("seat_unblocker_priority_rank")
    action = str(row.get("seat_unblocker_action") or "")
    return (
        SEAT_PRIORITY_STATUS_ORDER.get(priority_status, 99),
        parse_int(priority_rank, 9999) if priority_rank is not None else UNQUEUED_ACTION_URGENCY.get(action, 99),
        str(row.get("symbol") or ""),
    )


def seat_queue_alignment(
    *,
    seat_action: str,
    priority_context: dict[str, Any],
) -> tuple[str, str]:
    queue_task_id = str(priority_context.get("queue_task_id") or "")
    if not queue_task_id:
        return (
            "no_queue_contract",
            "No pending adaptive lab queue task names this symbol, so the seat action currently stands on local seat-board truth only.",
        )

    queue_task_status = str(priority_context.get("queue_task_status") or "")
    next_action_class = str(priority_context.get("queue_task_next_action_class") or "")
    queue_title = str(priority_context.get("queue_task_title") or queue_task_id)
    seat_stage = SEAT_ACTION_STAGE.get(seat_action)
    queue_stage = QUEUE_ACTION_CLASS_STAGE.get(next_action_class)

    if seat_stage is None or queue_stage is None:
        return (
            "queue_alignment_unknown",
            f"Queue task `{queue_title}` exists for this symbol, but its next-action contract `{next_action_class or 'missing'}` does not map cleanly onto the current seat action `{seat_action}` yet.",
        )

    if queue_stage == seat_stage:
        return (
            f"queue_{queue_task_status}_aligned",
            f"Queue task `{queue_title}` is directionally aligned with the current seat action `{seat_action}` on the same execution stage.",
        )
    if queue_stage < seat_stage:
        return (
            f"queue_{queue_task_status}_precedes_seat_call",
            f"Queue task `{queue_title}` is still an earlier-stage evidence contract (`{next_action_class}`) than the current seat action `{seat_action}`, so queue order alone should not be read as full seat readiness.",
        )
    return (
        f"queue_{queue_task_status}_outpaces_seat_state",
        f"Queue task `{queue_title}` assumes a later-stage execution contract (`{next_action_class}`) than the current seat action `{seat_action}`, so the seat board is still lagging the queue plan.",
    )


def seat_actionability(
    *,
    seat_action: str,
    priority_status: str,
    queue_alignment_status: str,
) -> tuple[str, str]:
    if queue_alignment_status == "queue_ready_aligned":
        return (
            "queue_ready_actionable",
            f"The current seat move `{seat_action}` is backed by a ready queue contract on the same stage, so this is an immediately executable seat action.",
        )
    if queue_alignment_status == "queue_ready_precedes_seat_call":
        return (
            "queue_ready_preparatory_only",
            f"The queue is ready, but the current seat move `{seat_action}` is still ahead of what the queue contract actually proves. Treat this as preparatory, not yet a final seat-execution call.",
        )
    if queue_alignment_status == "queue_blocked_aligned":
        return (
            "blocked_by_queue_contract",
            f"The seat move `{seat_action}` is directionally right, but its matching queue contract is still blocked, so this is not immediately executable yet.",
        )
    if priority_status == "unqueued_action" and seat_action in LOCAL_ACTIONABLE_UNQUEUED_ACTIONS:
        return (
            "local_actionable_unqueued",
            f"The current seat move `{seat_action}` is locally actionable from seat-board truth, but the adaptive lab queue does not yet carry a matching symbol contract.",
        )
    return (
        "monitor_only",
        f"No stronger execution call surfaced for seat move `{seat_action}` beyond monitoring and proof maturation.",
    )


def seat_contract_gap(
    *,
    actionability_status: str,
    priority_status: str,
) -> tuple[str, str]:
    if actionability_status == "queue_ready_actionable":
        return (
            "queue_backed_actionable",
            "This seat move is actionable and already backed by a ready adaptive lab queue contract.",
        )
    if actionability_status == "local_actionable_unqueued":
        return (
            "actionable_missing_queue_contract",
            "This seat move is actionable from current seat-board truth, but it still lacks a matching adaptive lab queue contract.",
        )
    if actionability_status == "queue_ready_preparatory_only":
        return (
            "queue_backed_preparatory_only",
            "The symbol already has queue coverage, but the current queue row is still only preparatory relative to the seat call.",
        )
    if actionability_status == "blocked_by_queue_contract" or priority_status == "queue_blocked":
        return (
            "queue_contract_blocked",
            "The symbol has a queue contract, but that contract is currently blocked rather than executable.",
        )
    return (
        "no_immediate_contract_gap",
        "No additional queue-contract gap surfaced beyond the current monitoring state.",
    )


def seat_overlay_contract(
    *,
    adaptive_proof_row: dict[str, Any] | None,
    actionability_status: str,
) -> tuple[str, str]:
    runtime_overlays = unique_strings(list((adaptive_proof_row or {}).get("runtime_overlays") or []))
    if not runtime_overlays:
        return (
            "no_overlay_contract",
            "No adaptive runtime overlay contract is currently attached to this symbol beyond its existing stage and profit mode.",
        )

    overlay_read = str((adaptive_proof_row or {}).get("runtime_overlay_read") or "")
    if actionability_status == "queue_ready_actionable":
        return (
            "actionable_under_overlay_contract",
            f"This seat move is actionable, but it must still honor runtime overlays `{runtime_overlays}`. {overlay_read}".strip(),
        )
    if actionability_status == "queue_ready_preparatory_only":
        return (
            "preparatory_overlay_contract",
            f"This symbol carries runtime overlays `{runtime_overlays}`, and the current seat move remains preparatory until that overlay contract is respected. {overlay_read}".strip(),
        )
    return (
        "overlay_contract_active",
        f"This symbol carries runtime overlays `{runtime_overlays}` on the current adaptive proof surface. {overlay_read}".strip(),
    )


def seat_overlay_launch_bridge(
    *,
    adaptive_proof_row: dict[str, Any] | None,
    runner_plan_row: dict[str, Any] | None,
) -> tuple[str, str]:
    proof_overlays = unique_strings(list((adaptive_proof_row or {}).get("runtime_overlays") or []))
    if not proof_overlays:
        return (
            "no_overlay_launch_bridge_needed",
            "No adaptive runtime overlays are currently active for this symbol, so there is no launch-bridge obligation to check.",
        )
    if runner_plan_row is None:
        return (
            "overlay_contract_without_runner_plan",
            f"Adaptive proof requires runtime overlays `{proof_overlays}`, but no runner-plan surface is currently attached to this symbol.",
        )

    runtime_overlay_contract = dict(runner_plan_row.get("runtime_overlay_contract") or {})
    supported = unique_strings(list(runtime_overlay_contract.get("supported_overlays") or []))
    requested = unique_strings(list(runtime_overlay_contract.get("requested_overlays") or []))
    executable = unique_strings(list(runtime_overlay_contract.get("executable_overlays") or []))
    unsupported = unique_strings(list(runtime_overlay_contract.get("unsupported_overlays") or []))
    bridge_read = str(runtime_overlay_contract.get("read") or "")

    missing_from_bridge = [overlay for overlay in proof_overlays if overlay not in requested and overlay not in executable and overlay not in unsupported]
    unsupported_required = [overlay for overlay in proof_overlays if overlay in unsupported]
    executable_required = [overlay for overlay in proof_overlays if overlay in executable]
    supported_required = [overlay for overlay in proof_overlays if overlay in supported]

    if unsupported_required:
        return (
            "overlay_launch_manual_review_required",
            f"Runner plan still marks required overlays `{unsupported_required}` as unsupported/manual-review obligations. {bridge_read}".strip(),
        )
    if len(executable_required) == len(proof_overlays):
        return (
            "overlay_launch_bridge_supported",
            f"Runner plan already carries executable support for all required overlays `{proof_overlays}`. {bridge_read}".strip(),
        )
    if not requested and supported and len(supported_required) == len(proof_overlays):
        return (
            "overlay_launch_bridge_supported_but_unrequested",
            (
                f"Adaptive proof requires overlays `{proof_overlays}`, and the current scaffold can express them, "
                "but today's runner-plan/controller state does not request them yet. "
                f"{bridge_read}"
            ).strip(),
        )
    if not requested and not executable and not unsupported:
        return (
            "overlay_launch_bridge_unrequested",
            f"Adaptive proof requires overlays `{proof_overlays}`, but the current runner-plan surface does not request any launch overlays yet. {bridge_read}".strip(),
        )
    return (
        "overlay_launch_bridge_partial",
        f"Runner plan only partially bridges required overlays. executable=`{executable_required}`, missing=`{missing_from_bridge}`. {bridge_read}".strip(),
    )


def seat_execution_gate(
    *,
    contract_gap_status: str,
    overlay_contract_status: str,
    overlay_launch_bridge_status: str,
) -> tuple[str, str]:
    if contract_gap_status == "queue_contract_blocked":
        return (
            "blocked_by_queue_contract",
            "This seat move is not executable yet because its queue-backed contract is still blocked.",
        )
    if contract_gap_status == "actionable_missing_queue_contract":
        return (
            "actionable_but_missing_queue_contract",
            "This seat move is locally actionable, but it still needs a formal adaptive-lab queue contract before it should be treated as execution-ready.",
        )
    if contract_gap_status == "queue_backed_preparatory_only":
        return (
            "queue_backed_preparatory_only",
            "This symbol already has queue coverage, but the queue contract is still preparatory relative to the current seat call.",
        )
    if overlay_launch_bridge_status in {
        "overlay_contract_without_runner_plan",
        "overlay_launch_manual_review_required",
        "overlay_launch_bridge_unrequested",
        "overlay_launch_bridge_partial",
    }:
        return (
            "blocked_by_overlay_launch_bridge",
            "This seat move is otherwise close to executable, but proof-side overlay obligations still do not bridge cleanly into the current runner plan.",
        )
    if overlay_launch_bridge_status == "overlay_launch_bridge_supported_but_unrequested":
        return (
            "blocked_by_overlay_request_alignment",
            "This seat move already has scaffold support for its overlay contract, but the current runner-plan/controller state does not request those overlays yet.",
        )
    if contract_gap_status == "queue_backed_actionable" and overlay_contract_status == "actionable_under_overlay_contract":
        return (
            "actionable_under_overlay_contract",
            "This seat move is execution-ready, but it must launch and run under the attached adaptive overlay contract.",
        )
    if contract_gap_status == "queue_backed_actionable":
        return (
            "ready_for_seat_execution",
            "This seat move is execution-ready on the current passive evidence, with no additional queue or overlay-launch blocker surfaced here.",
        )
    if overlay_contract_status in {"overlay_contract_active", "preparatory_overlay_contract"}:
        return (
            "overlay_governed_monitor_only",
            "This symbol carries adaptive runtime overlays, but the current seat state remains monitoring/preparatory rather than execution-ready.",
        )
    return (
        "monitor_only",
        "No immediate seat-execution gate opened beyond monitoring on the current passive surface.",
    )


def build_payload(
    live_payload: dict[str, Any],
    registry_payload: dict[str, Any],
    fx_payload: dict[str, Any],
    btc_concentration_payload: dict[str, Any],
    adaptive_acceptance_payload: dict[str, Any],
    adaptive_overnight_payload: dict[str, Any],
    hungry_hippo_payload: dict[str, Any],
    crypto_warp_payload: dict[str, Any],
    eth_warp_payload: dict[str, Any],
    booked_pnl_payload: dict[str, Any],
    telemetry_payload: dict[str, Any],
    adaptive_proof_payload: dict[str, Any],
    adaptive_lab_queue_payload: dict[str, Any],
    adaptive_runner_plan_payload: dict[str, Any],
) -> dict[str, Any]:
    live_by_symbol = build_live_holder_rows(live_payload, registry_payload, booked_pnl_payload)
    fx_challengers = fx_watch_lead_map(fx_payload)
    adaptive_primary, adaptive_secondary = adaptive_challenger_rows(
        adaptive_acceptance_payload,
        adaptive_overnight_payload,
    )
    hh_challengers = hungry_hippo_challenger_map(hungry_hippo_payload)
    crypto_challengers = crypto_warp_challenger_map(crypto_warp_payload, eth_warp_payload)
    telemetry_by_lane = telemetry_rows_by_lane(telemetry_payload)
    adaptive_proof_by_symbol = adaptive_proof_rows_by_symbol(adaptive_proof_payload)
    queue_tasks_by_symbol = adaptive_lab_tasks_by_symbol(adaptive_lab_queue_payload)
    runner_plan_by_symbol = adaptive_runner_plan_by_symbol(adaptive_runner_plan_payload)

    symbols = unique_strings(
        list(live_by_symbol.keys())
        + list(fx_challengers.keys())
        + list(adaptive_primary.keys())
        + list(hh_challengers.keys())
        + list(crypto_challengers.keys())
    )

    rows: list[dict[str, Any]] = []
    for symbol in sorted(symbols):
        live_holders = [dict(row) for row in live_by_symbol.get(symbol, [])]
        primary_holder = pick_primary_live_holder(live_holders)
        additional_live = [row for row in live_holders if primary_holder is None or row.get("lane") != primary_holder.get("lane")]
        challenger = (
            adaptive_primary.get(symbol)
            or fx_challengers.get(symbol)
            or hh_challengers.get(symbol)
            or crypto_challengers.get(symbol)
        )
        all_challengers = unique_strings(
            [str((challenger or {}).get("lane_name") or "")]
            + [str(row.get("lane_name") or "") for row in adaptive_secondary.get(symbol, []) if row.get("lane_name")]
            + [str(crypto_challengers.get(symbol, {}).get("lane_name") or "")]
            + [str(hh_challengers.get(symbol, {}).get("lane_name") or "")]
            + [str(fx_challengers.get(symbol, {}).get("lane_name") or "")]
        )
        objective_proxy = live_holder_objective_proxy(
            symbol,
            primary_holder,
            seat_conflict=len(live_holders) > 1,
            btc_concentration_payload=btc_concentration_payload,
        )
        challenger_proxy = challenger_objective_proxy(challenger)
        comparison_status, comparison_read = objective_comparison_read(
            objective_proxy,
            challenger_proxy,
            primary_holder,
            challenger,
        )
        proof_integrity_status, proof_integrity_read = challenger_proof_integrity(challenger, telemetry_by_lane)
        adaptive_proof_row = adaptive_proof_by_symbol.get(symbol)
        displacement_status, displacement_read = objective_displacement_status(
            primary_holder,
            challenger,
            comparison_status,
            str(challenger_proxy.get("status") or ""),
            proof_integrity_status,
        )
        unblocker_action, unblocker_read = seat_unblocker_action(
            primary_holder=primary_holder,
            challenger=challenger,
            challenger_proxy_status=str(challenger_proxy.get("status") or ""),
            proof_integrity_status=proof_integrity_status,
            displacement_status=displacement_status,
            adaptive_proof_row=adaptive_proof_row,
        )
        priority_context = seat_unblocker_priority_context(
            symbol=symbol,
            action=unblocker_action,
            queue_tasks_by_symbol=queue_tasks_by_symbol,
        )

        row = {
            "symbol": symbol,
            "asset_class": infer_asset_class(symbol),
            "seat_verdict": seat_verdict(primary_holder, len(live_holders)),
            "next_action": next_action_for_symbol(symbol, primary_holder, len(live_holders), challenger),
            "current_live_holder_lane": str((primary_holder or {}).get("lane") or ""),
            "current_live_holder_evidence_basis": str((primary_holder or {}).get("evidence_basis") or ""),
            "current_live_holder_booked_usd": parse_float((primary_holder or {}).get("booked_usd")),
            "current_live_holder_close_count": parse_int((primary_holder or {}).get("close_count")),
            "current_live_holder_open_count": parse_int((primary_holder or {}).get("open_count")),
            "current_live_holder_operator_posture": str((primary_holder or {}).get("operator_posture") or ""),
            "live_holder_count": len(live_holders),
            "seat_conflict": len(live_holders) > 1,
            "all_live_holders": [
                {
                    "lane": str(item.get("lane") or ""),
                    "evidence_basis": str(item.get("evidence_basis") or ""),
                    "booked_usd": parse_float(item.get("booked_usd")),
                    "close_count": parse_int(item.get("close_count")),
                    "open_count": parse_int(item.get("open_count")),
                }
                for item in sorted(live_holders, key=lambda item: str(item.get("lane") or ""))
            ],
            "additional_live_holders": unique_strings([str(item.get("lane") or "") for item in additional_live]),
            "best_challenger_lane": str((challenger or {}).get("lane_name") or ""),
            "best_challenger_label": str((challenger or {}).get("label") or ""),
            "best_challenger_family": str((challenger or {}).get("family") or ""),
            "best_challenger_candidate_class": str((challenger or {}).get("candidate_class") or ""),
            "best_challenger_runtime_status": str((challenger or {}).get("runtime_status") or ""),
            "best_challenger_read": str((challenger or {}).get("challenger_read") or ""),
            "secondary_challenger_lanes": all_challengers[1:] if all_challengers else [],
            "max_profit_objective_proxy": parse_float(objective_proxy.get("score")),
            "max_profit_objective_status": str(objective_proxy.get("status") or ""),
            "max_profit_objective_read": str(objective_proxy.get("read") or ""),
            "max_profit_objective_components": dict(objective_proxy.get("components") or {}),
            "unified_objective_score": objective_proxy.get("unified_objective_score"),
            "unified_objective_verdict": str(objective_proxy.get("unified_objective_verdict") or ""),
            "best_challenger_objective_proxy": parse_float(challenger_proxy.get("score")),
            "best_challenger_objective_status": str(challenger_proxy.get("status") or ""),
            "best_challenger_objective_read": str(challenger_proxy.get("read") or ""),
            "best_challenger_objective_components": dict(challenger_proxy.get("components") or {}),
            "best_challenger_proof_integrity_status": proof_integrity_status,
            "best_challenger_proof_integrity_read": proof_integrity_read,
            "objective_comparison_status": comparison_status,
            "objective_comparison_read": comparison_read,
            "objective_displacement_status": displacement_status,
            "objective_displacement_read": displacement_read,
            "adaptive_proof_stage": str((adaptive_proof_row or {}).get("stage") or ""),
            "adaptive_profit_mode": str((adaptive_proof_row or {}).get("profit_mode") or ""),
            "adaptive_profit_mode_read": str((adaptive_proof_row or {}).get("profit_mode_read") or ""),
            "seat_unblocker_action": unblocker_action,
            "seat_unblocker_read": unblocker_read,
            "seat_unblocker_priority_rank": priority_context.get("priority_rank"),
            "seat_unblocker_priority_status": str(priority_context.get("priority_status") or ""),
            "seat_unblocker_priority_read": str(priority_context.get("priority_read") or ""),
            "seat_unblocker_queue_task_id": str(priority_context.get("queue_task_id") or ""),
            "seat_unblocker_queue_task_title": str(priority_context.get("queue_task_title") or ""),
            "seat_unblocker_queue_task_status": str(priority_context.get("queue_task_status") or ""),
            "seat_unblocker_queue_task_lane": str(priority_context.get("queue_task_lane") or ""),
            "seat_unblocker_queue_task_next_action_class": str(priority_context.get("queue_task_next_action_class") or ""),
            "why": build_row_reason(symbol, primary_holder, len(live_holders), challenger, btc_concentration_payload),
            "machine_truth": {
                "candidate_class": str((challenger or {}).get("candidate_class") or ""),
                "runtime_status": str((challenger or {}).get("runtime_status") or ""),
                "live_holder_count": len(live_holders),
                "seat_conflict": len(live_holders) > 1,
                "max_profit_objective_status": str(objective_proxy.get("status") or ""),
                "objective_comparison_status": comparison_status,
                "objective_displacement_status": displacement_status,
                "seat_unblocker_action": unblocker_action,
                "seat_unblocker_priority_status": str(priority_context.get("priority_status") or ""),
            },
        }
        queue_alignment_status, queue_alignment_read = seat_queue_alignment(
            seat_action=unblocker_action,
            priority_context=priority_context,
        )
        row["seat_queue_alignment_status"] = queue_alignment_status
        row["seat_queue_alignment_read"] = queue_alignment_read
        row["machine_truth"]["seat_queue_alignment_status"] = queue_alignment_status
        actionability_status, actionability_read = seat_actionability(
            seat_action=unblocker_action,
            priority_status=str(priority_context.get("priority_status") or ""),
            queue_alignment_status=queue_alignment_status,
        )
        row["seat_actionability_status"] = actionability_status
        row["seat_actionability_read"] = actionability_read
        row["machine_truth"]["seat_actionability_status"] = actionability_status
        contract_gap_status, contract_gap_read = seat_contract_gap(
            actionability_status=actionability_status,
            priority_status=str(priority_context.get("priority_status") or ""),
        )
        row["seat_contract_gap_status"] = contract_gap_status
        row["seat_contract_gap_read"] = contract_gap_read
        row["machine_truth"]["seat_contract_gap_status"] = contract_gap_status
        overlay_contract_status, overlay_contract_read = seat_overlay_contract(
            adaptive_proof_row=adaptive_proof_row,
            actionability_status=actionability_status,
        )
        row["seat_overlay_contract_status"] = overlay_contract_status
        row["seat_overlay_contract_read"] = overlay_contract_read
        row["adaptive_runtime_overlays"] = unique_strings(list((adaptive_proof_row or {}).get("runtime_overlays") or []))
        row["adaptive_runtime_overlay_read"] = str((adaptive_proof_row or {}).get("runtime_overlay_read") or "")
        row["machine_truth"]["seat_overlay_contract_status"] = overlay_contract_status
        overlay_launch_bridge_status, overlay_launch_bridge_read = seat_overlay_launch_bridge(
            adaptive_proof_row=adaptive_proof_row,
            runner_plan_row=runner_plan_by_symbol.get(symbol),
        )
        row["seat_overlay_launch_bridge_status"] = overlay_launch_bridge_status
        row["seat_overlay_launch_bridge_read"] = overlay_launch_bridge_read
        row["machine_truth"]["seat_overlay_launch_bridge_status"] = overlay_launch_bridge_status
        execution_gate_status, execution_gate_read = seat_execution_gate(
            contract_gap_status=contract_gap_status,
            overlay_contract_status=overlay_contract_status,
            overlay_launch_bridge_status=overlay_launch_bridge_status,
        )
        row["seat_execution_gate_status"] = execution_gate_status
        row["seat_execution_gate_read"] = execution_gate_read
        row["machine_truth"]["seat_execution_gate_status"] = execution_gate_status
        rows.append(row)

    summary = {
        "symbol_count": len(rows),
        "live_seat_symbols": [row["symbol"] for row in rows if row["current_live_holder_lane"]],
        "no_live_seat_symbols": [row["symbol"] for row in rows if not row["current_live_holder_lane"]],
        "defended_live_seat_symbols": [row["symbol"] for row in rows if row["seat_verdict"] == "defended_live_seat"],
        "provisional_live_seat_symbols": [
            row["symbol"]
            for row in rows
            if row["seat_verdict"] in {"provisional_live_seat", "contested_provisional_live_seat"}
        ],
        "contested_live_seat_symbols": [row["symbol"] for row in rows if bool(row["seat_conflict"])],
        "live_demotion_candidate_symbols": [row["symbol"] for row in rows if row["next_action"] == "live_demotion_candidate"],
        "keep_live_symbols": [row["symbol"] for row in rows if row["next_action"] == "keep_live"],
        "keep_live_but_under_audit_symbols": [
            row["symbol"]
            for row in rows
            if row["next_action"] == "keep_live_but_under_audit"
        ],
        "shadow_challenger_needed_symbols": [
            row["symbol"]
            for row in rows
            if row["next_action"] == "shadow_challenger_needed"
        ],
        "strong_but_inactive_challenger_symbols": [
            row["symbol"]
            for row in rows
            if row["best_challenger_candidate_class"] == "shadow_ready"
            and row["best_challenger_runtime_status"] in {"hold_runtime_repair_candidate", "hold_disabled_proof_candidate"}
        ],
        "next_action_counts": {
            action: sum(1 for row in rows if row["next_action"] == action)
            for action in [
                "keep_live",
                "keep_live_but_under_audit",
                "shadow_challenger_needed",
                "live_demotion_candidate",
            ]
        },
    }
    summary["defended_count"] = len(summary["defended_live_seat_symbols"])
    summary["no_live_seat_count"] = len(summary["no_live_seat_symbols"])
    scored_rows = [row for row in rows if row["current_live_holder_lane"]]
    scored_rows.sort(key=lambda row: (parse_float(row.get("max_profit_objective_proxy")), row["symbol"]), reverse=True)
    summary["objective_proxy_leaders"] = [row["symbol"] for row in scored_rows[:3]]
    summary["objective_proxy_under_audit_symbols"] = [
        row["symbol"]
        for row in rows
        if row["max_profit_objective_status"] in {"thin_edge_under_audit", "carry_dominated_or_unproven"}
    ]
    summary["challenger_comparable_symbols"] = [
        row["symbol"]
        for row in rows
        if row["best_challenger_objective_status"] == "challenger_comparable"
    ]
    summary["challenger_partial_symbols"] = [
        row["symbol"]
        for row in rows
        if row["best_challenger_objective_status"] == "challenger_partially_comparable"
    ]
    summary["objective_comparison_ready_symbols"] = [
        row["symbol"]
        for row in rows
        if row["objective_comparison_status"] in {"challenger_objective_edge", "incumbent_objective_edge", "objective_neck_and_neck"}
    ]
    summary["objective_displacement_candidate_symbols"] = [
        row["symbol"]
        for row in rows
        if row["objective_displacement_status"] == "objective_displacement_candidate"
    ]
    summary["incumbent_objective_hold_symbols"] = [
        row["symbol"]
        for row in rows
        if row["objective_displacement_status"] == "incumbent_still_leads"
    ]
    summary["comparison_incomplete_symbols"] = [
        row["symbol"]
        for row in rows
        if row["objective_displacement_status"] in {"comparison_incomplete", "comparison_not_ready", "objective_edge_but_not_launchable", "objective_edge_but_telemetry_blind"}
    ]
    summary["telemetry_blocked_displacement_symbols"] = [
        row["symbol"]
        for row in rows
        if row["objective_displacement_status"] == "objective_edge_but_telemetry_blind"
    ]
    summary["seat_unblocker_counts"] = {}
    for row in rows:
        action = str(row.get("seat_unblocker_action") or "")
        if not action:
            continue
        summary["seat_unblocker_counts"][action] = summary["seat_unblocker_counts"].get(action, 0) + 1
    prioritized_rows = sorted(rows, key=seat_priority_sort_key)
    summary["seat_unblocker_priority_status_counts"] = {}
    for row in prioritized_rows:
        status = str(row.get("seat_unblocker_priority_status") or "")
        if not status:
            continue
        summary["seat_unblocker_priority_status_counts"][status] = summary["seat_unblocker_priority_status_counts"].get(status, 0) + 1
    summary["seat_unblocker_priority_symbols"] = [row["symbol"] for row in prioritized_rows]
    summary["seat_unblocker_priority_queue"] = [
        {
            "symbol": row["symbol"],
            "priority_status": row.get("seat_unblocker_priority_status") or "",
            "priority_rank": row.get("seat_unblocker_priority_rank"),
            "seat_unblocker_action": row.get("seat_unblocker_action") or "",
            "queue_task_id": row.get("seat_unblocker_queue_task_id") or "",
            "queue_task_status": row.get("seat_unblocker_queue_task_status") or "",
            "seat_queue_alignment_status": row.get("seat_queue_alignment_status") or "",
        }
        for row in prioritized_rows
    ]
    summary["highest_priority_seat_symbol"] = prioritized_rows[0]["symbol"] if prioritized_rows else ""
    summary["seat_queue_alignment_counts"] = {}
    for row in rows:
        status = str(row.get("seat_queue_alignment_status") or "")
        if not status:
            continue
        summary["seat_queue_alignment_counts"][status] = summary["seat_queue_alignment_counts"].get(status, 0) + 1
    summary["queue_precedes_seat_symbols"] = [
        row["symbol"]
        for row in rows
        if str(row.get("seat_queue_alignment_status") or "").endswith("_precedes_seat_call")
    ]
    summary["seat_actionability_counts"] = {}
    for row in rows:
        status = str(row.get("seat_actionability_status") or "")
        if not status:
            continue
        summary["seat_actionability_counts"][status] = summary["seat_actionability_counts"].get(status, 0) + 1
    actionable_statuses = {"queue_ready_actionable", "local_actionable_unqueued"}
    actionable_rows = [row for row in prioritized_rows if str(row.get("seat_actionability_status") or "") in actionable_statuses]
    summary["actionable_seat_symbols"] = [row["symbol"] for row in actionable_rows]
    summary["highest_actionable_seat_symbol"] = actionable_rows[0]["symbol"] if actionable_rows else ""
    summary["seat_contract_gap_counts"] = {}
    for row in rows:
        status = str(row.get("seat_contract_gap_status") or "")
        if not status:
            continue
        summary["seat_contract_gap_counts"][status] = summary["seat_contract_gap_counts"].get(status, 0) + 1
    summary["actionable_unqueued_symbols"] = [
        row["symbol"]
        for row in prioritized_rows
        if str(row.get("seat_contract_gap_status") or "") == "actionable_missing_queue_contract"
    ]
    queue_backed_actionable_rows = [
        row
        for row in prioritized_rows
        if str(row.get("seat_contract_gap_status") or "") == "queue_backed_actionable"
    ]
    summary["highest_actionable_queue_backed_symbol"] = queue_backed_actionable_rows[0]["symbol"] if queue_backed_actionable_rows else ""
    summary["seat_overlay_contract_counts"] = {}
    for row in rows:
        status = str(row.get("seat_overlay_contract_status") or "")
        if not status:
            continue
        summary["seat_overlay_contract_counts"][status] = summary["seat_overlay_contract_counts"].get(status, 0) + 1
    summary["overlay_constrained_symbols"] = [
        row["symbol"]
        for row in rows
        if str(row.get("seat_overlay_contract_status") or "") != "no_overlay_contract"
    ]
    summary["actionable_overlay_constrained_symbols"] = [
        row["symbol"]
        for row in prioritized_rows
        if str(row.get("seat_overlay_contract_status") or "") == "actionable_under_overlay_contract"
    ]
    summary["seat_overlay_launch_bridge_counts"] = {}
    for row in rows:
        status = str(row.get("seat_overlay_launch_bridge_status") or "")
        if not status:
            continue
        summary["seat_overlay_launch_bridge_counts"][status] = summary["seat_overlay_launch_bridge_counts"].get(status, 0) + 1
    summary["overlay_launch_gap_symbols"] = [
        row["symbol"]
        for row in rows
        if str(row.get("seat_overlay_launch_bridge_status") or "") in {
            "overlay_contract_without_runner_plan",
            "overlay_launch_manual_review_required",
            "overlay_launch_bridge_supported_but_unrequested",
            "overlay_launch_bridge_unrequested",
            "overlay_launch_bridge_partial",
        }
    ]
    summary["seat_execution_gate_counts"] = {}
    for row in rows:
        status = str(row.get("seat_execution_gate_status") or "")
        if not status:
            continue
        summary["seat_execution_gate_counts"][status] = summary["seat_execution_gate_counts"].get(status, 0) + 1
    execution_ready_statuses = {"ready_for_seat_execution", "actionable_under_overlay_contract"}
    execution_ready_rows = [
        row
        for row in prioritized_rows
        if str(row.get("seat_execution_gate_status") or "") in execution_ready_statuses
    ]
    summary["execution_ready_seat_symbols"] = [row["symbol"] for row in execution_ready_rows]
    summary["highest_execution_ready_symbol"] = execution_ready_rows[0]["symbol"] if execution_ready_rows else ""
    summary["execution_gate_blocked_symbols"] = [
        row["symbol"]
        for row in prioritized_rows
        if str(row.get("seat_execution_gate_status") or "") in {
            "blocked_by_queue_contract",
            "blocked_by_overlay_launch_bridge",
            "blocked_by_overlay_request_alignment",
        }
    ]
    summary["execution_contract_debt_symbols"] = [
        row["symbol"]
        for row in prioritized_rows
        if str(row.get("seat_execution_gate_status") or "") == "actionable_but_missing_queue_contract"
    ]

    leadership_read = [
        "Live-seat truth should now be read per symbol, not per lane list. A positive live row does not automatically mean that symbol's seat is honestly defended.",
        (
            f"Current defended live seats are `{summary['defended_live_seat_symbols']}`; "
            f"contested or provisional live symbols are `{unique_strings(summary['contested_live_seat_symbols'] + summary['provisional_live_seat_symbols'])}`."
        ),
        (
            f"BTC is the clearest live-seat audit case: live-demotion candidates are `{summary['live_demotion_candidate_symbols']}`, "
            f"and strong-but-currently-inactive challengers are `{summary['strong_but_inactive_challenger_symbols']}`."
        ),
        (
            f"Symbols without a live seat yet are `{summary['no_live_seat_symbols']}`. "
            "Those stay in shadow proof or launch-contract follow-through until they honestly win a seat."
        ),
        (
            f"The current max-profit proxy leaders among live seats are `{summary['objective_proxy_leaders']}`; "
            f"symbols whose live holder still reads as thin-edge or carry-dominated are `{summary['objective_proxy_under_audit_symbols']}`."
        ),
        (
            f"Challenger-side objective proof is already comparable on `{summary['challenger_comparable_symbols']}`, "
            f"partial on `{summary['challenger_partial_symbols']}`, and comparison-ready live-vs-challenger on `{summary['objective_comparison_ready_symbols']}`."
        ),
        (
            f"True objective displacement candidates are `{summary['objective_displacement_candidate_symbols']}`; "
            f"incumbent-led holds are `{summary['incumbent_objective_hold_symbols']}`, and incomplete/non-launchable comparison cases are `{summary['comparison_incomplete_symbols']}`."
        ),
        (
            f"Telemetry-integrity still blocks displacement language on `{summary['telemetry_blocked_displacement_symbols']}`."
        ),
        (
            f"Current seat-unblocker actions are `{summary['seat_unblocker_counts']}`."
        ),
        (
            f"Queue-aware seat order is now `{summary['seat_unblocker_priority_symbols']}`; "
            f"priority-status mix is `{summary['seat_unblocker_priority_status_counts']}`."
        ),
        (
            f"Seat-vs-queue alignment now reads `{summary['seat_queue_alignment_counts']}`; "
            f"queue-backed rows that still precede the actual seat call are `{summary['queue_precedes_seat_symbols']}`."
        ),
        (
            f"Seat actionability now reads `{summary['seat_actionability_counts']}`; "
            f"the current actually executable seat set is `{summary['actionable_seat_symbols']}`."
        ),
        (
            f"Seat contract-gap status now reads `{summary['seat_contract_gap_counts']}`; "
            f"actionable symbols still missing queue contracts are `{summary['actionable_unqueued_symbols']}`."
        ),
        (
            f"Seat overlay-contract status now reads `{summary['seat_overlay_contract_counts']}`; "
            f"symbols carrying active overlay obligations are `{summary['overlay_constrained_symbols']}`."
        ),
        (
            f"Overlay launch-bridge status now reads `{summary['seat_overlay_launch_bridge_counts']}`; "
            f"overlay-constrained symbols still awaiting a clean overlay launch alignment are `{summary['overlay_launch_gap_symbols']}`."
        ),
        (
            f"Seat execution-gate status now reads `{summary['seat_execution_gate_counts']}`; "
            f"the current truly execution-ready seat set is `{summary['execution_ready_seat_symbols']}`."
        ),
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(LIVE_DASHBOARD_PATH.relative_to(ROOT)),
            str(REGISTRY_PATH.relative_to(ROOT)),
            str(FX_GRADUATION_PATH.relative_to(ROOT)),
            str(BTC_CONCENTRATION_PATH.relative_to(ROOT)),
            str(ADAPTIVE_ACCEPTANCE_PATH.relative_to(ROOT)),
            str(ADAPTIVE_OVERNIGHT_PATH.relative_to(ROOT)),
            str(HUNGRY_HIPPO_FORWARD_PATH.relative_to(ROOT)),
            str(CRYPTO_WARP_READINESS_PATH.relative_to(ROOT)),
            str(ETH_WARP_READINESS_PATH.relative_to(ROOT)),
            str(BOOKED_PNL_PATH.relative_to(ROOT)),
            str(TELEMETRY_ENFORCEMENT_PATH.relative_to(ROOT)),
            str(ADAPTIVE_PROOF_PATH.relative_to(ROOT)),
            str(ADAPTIVE_LAB_QUEUE_PATH.relative_to(ROOT)),
            str(ADAPTIVE_BTC_SHADOW_RUNNER_PLAN_PATH.relative_to(ROOT)),
        ],
        "summary": summary,
        "leadership_read": leadership_read,
        "rows": rows,
        "notes": [
            "This board is passive. It names the current live seat holder and best challenger per symbol; it does not auto-promote, demote, or relaunch anything.",
            "Read challenger truth in two layers: `candidate_class` is doctrinal readiness, while `runtime_status` is current execution state.",
            "Booked live performance comes from `reports/booked_pnl_breakdown_board.json`, so this board does not score seats from health/carry labels alone.",
            "The max-profit objective is a proxy for the current live holder only. It uses currently available seat-board inputs to blend realized cash, close density, evidence quality, inventory burden, seat conflict, and BTC concentration, but it is not yet a full incumbent-vs-challenger score because challengers do not expose the same comparable runtime fields consistently.",
            "Where challenger proof fields already exist, this board now emits a challenger-side objective proxy and an explicit comparison status. Treat `challenger_partially_comparable` as useful triage, not final promotion authority.",
            "Objective displacement status is narrower than objective comparison status. A challenger only becomes an `objective_displacement_candidate` when it has a real passive-score edge and is already in a launchable candidate class instead of a parked or repair-blocked posture.",
            "Telemetry-integrity can still veto displacement language. If the telemetry enforcement board flags the challenger lane for active or inherited-only enrichment debt, the seat board should downgrade the result to `objective_edge_but_telemetry_blind`.",
            "Seat unblocker action is the narrow operational translation of the current seat state. It should point to the exact next move the room needs, not just restate the classification.",
            "Seat-unblocker priority is queue-aware. When `reports/adaptive_lab_queue.json` already names the symbol with a pending ready/blocked task, that queue row becomes the ordering anchor; otherwise the board falls back to local unblocker urgency without inventing a second planning report.",
            "Seat queue alignment is stricter than queue-backed priority. A symbol can be queue-backed and still remain an earlier-stage seat case if the lab queue task is only gathering proof or launchability evidence rather than supporting the current seat call directly.",
            "Seat actionability is stricter again. `queue_ready_actionable` and `local_actionable_unqueued` are the only states that count as immediately executable seat moves; queue-ranked preparatory rows and blocked queue-aligned rows should not be treated as active seat-go signals.",
            "Seat contract-gap status is the coordination overlay on top of actionability. Use it to separate actionable rows that are already queue-backed from actionable rows that still need a formal lab-queue contract.",
            "Seat overlay-contract status is the runtime-governance overlay on top of actionability. Use it when an adaptive proof row carries `runtime_overlays` such as guarded opens, cluster-aware escape, or burst suppression that should remain visible on the seat surface.",
            "Seat overlay-launch-bridge status compares proof-board overlay obligations against the checked-in runner-plan surface. Use it to catch cases where passive doctrine requires overlays that the current launch scaffold still does not request or cannot yet express.",
            "Seat execution-gate status is the compressed go/no-go read on top of the other seat fields. Use it to distinguish truly execution-ready rows from queue debt, blocked queue contracts, and overlay-launchability blockers without creating another planner board.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Per Symbol Live Seat Board",
        "",
        "This board answers the MT5 winner/challenger question per symbol instead of by raw live-lane inventory.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- defended_count: `{summary.get('defended_count', 0)}`",
            f"- no_live_seat_count: `{summary.get('no_live_seat_count', 0)}`",
            f"- live_seat_symbols: `{summary.get('live_seat_symbols', [])}`",
            f"- no_live_seat_symbols: `{summary.get('no_live_seat_symbols', [])}`",
            f"- defended_live_seat_symbols: `{summary.get('defended_live_seat_symbols', [])}`",
            f"- contested_live_seat_symbols: `{summary.get('contested_live_seat_symbols', [])}`",
            f"- live_demotion_candidate_symbols: `{summary.get('live_demotion_candidate_symbols', [])}`",
            f"- objective_proxy_leaders: `{summary.get('objective_proxy_leaders', [])}`",
            f"- objective_proxy_under_audit_symbols: `{summary.get('objective_proxy_under_audit_symbols', [])}`",
            f"- challenger_comparable_symbols: `{summary.get('challenger_comparable_symbols', [])}`",
            f"- challenger_partial_symbols: `{summary.get('challenger_partial_symbols', [])}`",
            f"- objective_comparison_ready_symbols: `{summary.get('objective_comparison_ready_symbols', [])}`",
            f"- objective_displacement_candidate_symbols: `{summary.get('objective_displacement_candidate_symbols', [])}`",
            f"- incumbent_objective_hold_symbols: `{summary.get('incumbent_objective_hold_symbols', [])}`",
            f"- comparison_incomplete_symbols: `{summary.get('comparison_incomplete_symbols', [])}`",
            f"- telemetry_blocked_displacement_symbols: `{summary.get('telemetry_blocked_displacement_symbols', [])}`",
            f"- seat_unblocker_counts: `{summary.get('seat_unblocker_counts', {})}`",
            f"- seat_unblocker_priority_status_counts: `{summary.get('seat_unblocker_priority_status_counts', {})}`",
            f"- seat_unblocker_priority_symbols: `{summary.get('seat_unblocker_priority_symbols', [])}`",
            f"- highest_priority_seat_symbol: `{summary.get('highest_priority_seat_symbol', '')}`",
            f"- seat_queue_alignment_counts: `{summary.get('seat_queue_alignment_counts', {})}`",
            f"- queue_precedes_seat_symbols: `{summary.get('queue_precedes_seat_symbols', [])}`",
            f"- seat_actionability_counts: `{summary.get('seat_actionability_counts', {})}`",
            f"- actionable_seat_symbols: `{summary.get('actionable_seat_symbols', [])}`",
            f"- highest_actionable_seat_symbol: `{summary.get('highest_actionable_seat_symbol', '')}`",
            f"- seat_contract_gap_counts: `{summary.get('seat_contract_gap_counts', {})}`",
            f"- actionable_unqueued_symbols: `{summary.get('actionable_unqueued_symbols', [])}`",
            f"- highest_actionable_queue_backed_symbol: `{summary.get('highest_actionable_queue_backed_symbol', '')}`",
            f"- seat_overlay_contract_counts: `{summary.get('seat_overlay_contract_counts', {})}`",
            f"- overlay_constrained_symbols: `{summary.get('overlay_constrained_symbols', [])}`",
            f"- actionable_overlay_constrained_symbols: `{summary.get('actionable_overlay_constrained_symbols', [])}`",
            f"- seat_overlay_launch_bridge_counts: `{summary.get('seat_overlay_launch_bridge_counts', {})}`",
            f"- overlay_launch_gap_symbols: `{summary.get('overlay_launch_gap_symbols', [])}`",
            f"- seat_execution_gate_counts: `{summary.get('seat_execution_gate_counts', {})}`",
            f"- execution_ready_seat_symbols: `{summary.get('execution_ready_seat_symbols', [])}`",
            f"- highest_execution_ready_symbol: `{summary.get('highest_execution_ready_symbol', '')}`",
            f"- execution_gate_blocked_symbols: `{summary.get('execution_gate_blocked_symbols', [])}`",
            f"- execution_contract_debt_symbols: `{summary.get('execution_contract_debt_symbols', [])}`",
            f"- strong_but_inactive_challenger_symbols: `{summary.get('strong_but_inactive_challenger_symbols', [])}`",
            f"- next_action_counts: `{summary.get('next_action_counts', {})}`",
            "",
            "## Seat Table",
            "",
            "| Symbol | Seat verdict | Live holder | Booked USD | Live objective | Challenger objective | Proof integrity | Comparison | Displacement | Unblocker | Priority | Queue alignment | Actionability | Contract gap | Overlay contract | Overlay bridge | Execution gate | Challenger class | Challenger runtime | Next action |",
            "| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        priority_rank = row.get("seat_unblocker_priority_rank")
        priority_cell = (
            f"`{row.get('seat_unblocker_priority_status') or '-'}:{parse_int(priority_rank)}`"
            if priority_rank is not None
            else f"`{row.get('seat_unblocker_priority_status') or '-'}:{UNQUEUED_ACTION_URGENCY.get(str(row.get('seat_unblocker_action') or ''), 99)}`"
        )
        lines.append(
            f"| `{row['symbol']}` | `{row['seat_verdict']}` | `{row['current_live_holder_lane'] or '-'}"
            f"` | {parse_float(row.get('current_live_holder_booked_usd')):+.2f} | "
            f"`{parse_float(row.get('max_profit_objective_proxy')):+.2f}` / `{row.get('max_profit_objective_status') or '-'}` | "
            f"`{parse_float(row.get('best_challenger_objective_proxy')):+.2f}` / `{row.get('best_challenger_objective_status') or '-'}` | "
            f"`{row.get('best_challenger_proof_integrity_status') or '-'}` | "
            f"`{row.get('objective_comparison_status') or '-'}` | "
            f"`{row.get('objective_displacement_status') or '-'}` | "
            f"`{row.get('seat_unblocker_action') or '-'}` | "
            f"{priority_cell} | "
            f"`{row.get('seat_queue_alignment_status') or '-'}` | "
            f"`{row.get('seat_actionability_status') or '-'}` | "
            f"`{row.get('seat_contract_gap_status') or '-'}` | "
            f"`{row.get('seat_overlay_contract_status') or '-'}` | "
            f"`{row.get('seat_overlay_launch_bridge_status') or '-'}` | "
            f"`{row.get('seat_execution_gate_status') or '-'}` | "
            f"`{row['best_challenger_candidate_class'] or '-'}` | "
            f"`{row['best_challenger_runtime_status'] or '-'}` | "
            f"`{row['next_action']}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### {row['symbol']}")
        lines.append(f"- asset_class: `{row['asset_class']}`")
        lines.append(f"- seat_verdict: `{row['seat_verdict']}`")
        lines.append(f"- next_action: `{row['next_action']}`")
        lines.append(f"- current_live_holder_lane: `{row['current_live_holder_lane'] or ''}`")
        lines.append(f"- current_live_holder_evidence_basis: `{row['current_live_holder_evidence_basis'] or ''}`")
        lines.append(f"- current_live_holder_booked_usd: `{parse_float(row.get('current_live_holder_booked_usd')):+.2f}`")
        lines.append(f"- current_live_holder_close_count: `{parse_int(row.get('current_live_holder_close_count'))}`")
        lines.append(f"- current_live_holder_open_count: `{parse_int(row.get('current_live_holder_open_count'))}`")
        lines.append(f"- max_profit_objective_proxy: `{parse_float(row.get('max_profit_objective_proxy')):+.2f}`")
        lines.append(f"- max_profit_objective_status: `{row.get('max_profit_objective_status') or ''}`")
        lines.append(f"- max_profit_objective_read: {row.get('max_profit_objective_read') or '-'}")
        lines.append(f"- max_profit_objective_components: `{row.get('max_profit_objective_components', {})}`")
        lines.append(f"- best_challenger_objective_proxy: `{parse_float(row.get('best_challenger_objective_proxy')):+.2f}`")
        lines.append(f"- best_challenger_objective_status: `{row.get('best_challenger_objective_status') or ''}`")
        lines.append(f"- best_challenger_objective_read: {row.get('best_challenger_objective_read') or '-'}")
        lines.append(f"- best_challenger_objective_components: `{row.get('best_challenger_objective_components', {})}`")
        lines.append(f"- best_challenger_proof_integrity_status: `{row.get('best_challenger_proof_integrity_status') or ''}`")
        lines.append(f"- best_challenger_proof_integrity_read: {row.get('best_challenger_proof_integrity_read') or '-'}")
        lines.append(f"- objective_comparison_status: `{row.get('objective_comparison_status') or ''}`")
        lines.append(f"- objective_comparison_read: {row.get('objective_comparison_read') or '-'}")
        lines.append(f"- objective_displacement_status: `{row.get('objective_displacement_status') or ''}`")
        lines.append(f"- objective_displacement_read: {row.get('objective_displacement_read') or '-'}")
        lines.append(f"- adaptive_proof_stage: `{row.get('adaptive_proof_stage') or ''}`")
        lines.append(f"- adaptive_profit_mode: `{row.get('adaptive_profit_mode') or ''}`")
        lines.append(f"- adaptive_profit_mode_read: {row.get('adaptive_profit_mode_read') or '-'}")
        lines.append(f"- adaptive_runtime_overlays: `{row.get('adaptive_runtime_overlays', [])}`")
        lines.append(f"- adaptive_runtime_overlay_read: {row.get('adaptive_runtime_overlay_read') or '-'}")
        lines.append(f"- seat_unblocker_action: `{row.get('seat_unblocker_action') or ''}`")
        lines.append(f"- seat_unblocker_read: {row.get('seat_unblocker_read') or '-'}")
        lines.append(f"- seat_unblocker_priority_status: `{row.get('seat_unblocker_priority_status') or ''}`")
        lines.append(f"- seat_unblocker_priority_rank: `{row.get('seat_unblocker_priority_rank')}`")
        lines.append(f"- seat_unblocker_priority_read: {row.get('seat_unblocker_priority_read') or '-'}")
        lines.append(f"- seat_unblocker_queue_task_id: `{row.get('seat_unblocker_queue_task_id') or ''}`")
        lines.append(f"- seat_unblocker_queue_task_title: `{row.get('seat_unblocker_queue_task_title') or ''}`")
        lines.append(f"- seat_unblocker_queue_task_status: `{row.get('seat_unblocker_queue_task_status') or ''}`")
        lines.append(f"- seat_unblocker_queue_task_lane: `{row.get('seat_unblocker_queue_task_lane') or ''}`")
        lines.append(f"- seat_unblocker_queue_task_next_action_class: `{row.get('seat_unblocker_queue_task_next_action_class') or ''}`")
        lines.append(f"- seat_queue_alignment_status: `{row.get('seat_queue_alignment_status') or ''}`")
        lines.append(f"- seat_queue_alignment_read: {row.get('seat_queue_alignment_read') or '-'}")
        lines.append(f"- seat_actionability_status: `{row.get('seat_actionability_status') or ''}`")
        lines.append(f"- seat_actionability_read: {row.get('seat_actionability_read') or '-'}")
        lines.append(f"- seat_contract_gap_status: `{row.get('seat_contract_gap_status') or ''}`")
        lines.append(f"- seat_contract_gap_read: {row.get('seat_contract_gap_read') or '-'}")
        lines.append(f"- seat_overlay_contract_status: `{row.get('seat_overlay_contract_status') or ''}`")
        lines.append(f"- seat_overlay_contract_read: {row.get('seat_overlay_contract_read') or '-'}")
        lines.append(f"- seat_overlay_launch_bridge_status: `{row.get('seat_overlay_launch_bridge_status') or ''}`")
        lines.append(f"- seat_overlay_launch_bridge_read: {row.get('seat_overlay_launch_bridge_read') or '-'}")
        lines.append(f"- seat_execution_gate_status: `{row.get('seat_execution_gate_status') or ''}`")
        lines.append(f"- seat_execution_gate_read: {row.get('seat_execution_gate_read') or '-'}")
        lines.append(f"- live_holder_count: `{parse_int(row.get('live_holder_count'))}`")
        lines.append(f"- seat_conflict: `{str(bool(row.get('seat_conflict'))).lower()}`")
        lines.append(f"- additional_live_holders: `{row.get('additional_live_holders', [])}`")
        lines.append(f"- best_challenger_lane: `{row.get('best_challenger_lane') or ''}`")
        lines.append(f"- best_challenger_label: `{row.get('best_challenger_label') or ''}`")
        lines.append(f"- best_challenger_candidate_class: `{row.get('best_challenger_candidate_class') or ''}`")
        lines.append(f"- best_challenger_runtime_status: `{row.get('best_challenger_runtime_status') or ''}`")
        lines.append(f"- best_challenger_read: {row.get('best_challenger_read') or '-'}")
        lines.append(f"- why: {row.get('why') or '-'}")
        all_live_holders = list(row.get("all_live_holders") or [])
        if all_live_holders:
            lines.append("- all_live_holders:")
            for item in all_live_holders:
                lines.append(
                    "  - "
                    + f"`{item.get('lane')}` {item.get('evidence_basis')} "
                    + f"booked={parse_float(item.get('booked_usd')):+.2f} "
                    + f"closes={parse_int(item.get('close_count'))} "
                    + f"opens={parse_int(item.get('open_count'))}"
                )
        lines.append("")

    lines.extend(["## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(LIVE_DASHBOARD_PATH),
        load_json(REGISTRY_PATH),
        load_json(FX_GRADUATION_PATH),
        load_json(BTC_CONCENTRATION_PATH),
        load_json(ADAPTIVE_ACCEPTANCE_PATH),
        load_json(ADAPTIVE_OVERNIGHT_PATH),
        load_json(HUNGRY_HIPPO_FORWARD_PATH),
        load_json(CRYPTO_WARP_READINESS_PATH),
        load_json(ETH_WARP_READINESS_PATH),
        load_json(BOOKED_PNL_PATH),
        load_json(TELEMETRY_ENFORCEMENT_PATH),
        load_json(ADAPTIVE_PROOF_PATH),
        load_json(ADAPTIVE_LAB_QUEUE_PATH),
        load_json(ADAPTIVE_BTC_SHADOW_RUNNER_PLAN_PATH),
    )
    write_outputs(payload)
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
