#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

LAUNCH_SAFETY_PATH = REPORTS / "hungry_hippo_launch_safety_validation.json"
DEPLOYMENT_GATE_PATH = REPORTS / "hungry_hippo_deployment_safety_gate_board.json"
RESEARCH_BOARD_PATH = REPORTS / "hungry_hippo_research_contribution_board.json"
PORTABILITY_BOARD_PATH = REPORTS / "hungry_hippo_symbol_portability_board.json"
POLICY_GAP_BOARD_PATH = REPORTS / "hungry_hippo_policy_gap_board.json"
OFFENSIVE_BOARD_PATH = REPORTS / "offensive_extreme_closure_shadow_board.json"
ETH_COMPARISON_PATH = REPORTS / "eth_m5_first_pilot_comparison_board.json"
ETH_CONTROL_STATE_PATH = REPORTS / "penetration_lattice_shadow_ethusd_m5_step14_control_state.json"
ETH_CONTROL_GATE_PATH = REPORTS / "eth_m5_control_proof_gate_board.json"
ETH_COEFFICIENT_ANALYSIS_PATH = REPORTS / "eth_step14_coefficient_analysis.md"
ETH_RETUNED_STATE_PATH = REPORTS / "penetration_lattice_shadow_ethusd_m5_step3p0_retuned_shadow_state.json"
RESET_ALERTS_PATH = REPORTS / "reset_rate_alerts.json"
AUTHORITY_STACK_PATH = REPORTS / "theory_authority_stack_board.md"
FRESH_WINDOW_BUCKET_PATH = REPORTS / "fresh_window_bucket_board.md"
CLOSURE_FIREWALL_PATH = REPORTS / "closure_firewall_board.md"
VALIDATED_THEORY_QUEUE_PATH = REPORTS / "validated_theory_queue.md"
GBP_CLOSURE_REPAIR_COMPARE_PATH = REPORTS / "gbpusd_closure_repair_compare.md"
BTC_DOWNTREND_CONFIG_PATH = CONFIGS / "hungry_hippo_btcusd_m15_sell_tight_shadow.json"
BTC_DOWNTREND_V2_CONFIG_PATH = CONFIGS / "hungry_hippo_btcusd_m15_sell_tight_v2_retuned.json"
BTC_SELL_TIGHT_COMPARISON_PATH = REPORTS / "btc_sell_tight_comparison_latest.json"
ETH_PROOF_GATE_BUILDER = ROOT / "scripts" / "build_eth_m5_control_proof_gate_board.py"

OUTPUT_JSON_PATH = REPORTS / "hungry_hippo_next_action_board.json"
OUTPUT_MD_PATH = REPORTS / "hungry_hippo_next_action_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return load_json(path)


def load_optional_repo_json(path_str: str | None) -> Any | None:
    if not path_str:
        return None
    path = (ROOT / path_str).resolve()
    if not path.exists():
        return None
    return load_json(path)


def parse_eth_step14_coefficient_analysis(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}

    def extract_float(pattern: str) -> float | None:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return None

    def extract_int(pattern: str) -> int | None:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    option_prefix = r"(?:\*\*Option {label} \(step ~?\$|### Option {label}:\s*Step ~?\$)"
    recommended_step = extract_float(option_prefix.format(label="A") + r"(\d+(?:\.\d+)?)")
    alternate_step = extract_float(option_prefix.format(label="B") + r"(\d+(?:\.\d+)?)")
    minimum_closes = extract_int(r"Run for (\d+)\+ closes minimum")

    return {
        "recommended_option": "Option A" if recommended_step is not None else "",
        "recommended_step_usd": recommended_step,
        "alternate_step_usd": alternate_step,
        "minimum_proof_closes": minimum_closes,
        "kill_option_available": "Option C: Accept negative proof and kill lane" in text,
    }


def merge_btc_config(primary: dict[str, Any] | None, fallback: dict[str, Any] | None) -> dict[str, Any] | None:
    if primary is None and fallback is None:
        return None
    merged = dict(fallback or {})
    merged.update(primary or {})
    merged_meta = dict(((fallback or {}).get("hungry_hippo_metadata") or {}))
    merged_meta.update(((primary or {}).get("hungry_hippo_metadata") or {}))
    if "guardrails" not in merged_meta and ((fallback or {}).get("hungry_hippo_metadata") or {}).get("guardrails") is not None:
        merged_meta["guardrails"] = dict((((fallback or {}).get("hungry_hippo_metadata") or {}).get("guardrails") or {}))
    merged["hungry_hippo_metadata"] = merged_meta
    return merged


def active_state_path_from_config(payload: dict[str, Any] | None) -> str:
    config = payload or {}
    restart_args = list(config.get("restart_args") or [])
    for idx, item in enumerate(restart_args):
        if str(item) == "--state-path" and idx + 1 < len(restart_args):
            return str(restart_args[idx + 1])
    return str(config.get("state_path") or "")


def infer_symbol_from_config_path(config_path: str) -> str:
    name = Path(str(config_path or "")).name.upper()
    match = re.search(r"HUNGRY_HIPPO_([A-Z0-9]+)", name)
    if not match:
        return ""
    return str(match.group(1) or "")


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_builder(script_path: Path) -> None:
    subprocess.run([sys.executable, str(script_path)], check=True, cwd=ROOT)


def refresh_inputs() -> None:
    # Refresh the direct ETH proof dependency so partial rebuilds do not publish stale machine truth.
    run_builder(ETH_PROOF_GATE_BUILDER)


def parse_iso_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def summarize_btc_forward_proof(btc_config: dict[str, Any] | None, btc_state: dict[str, Any] | None) -> dict[str, Any]:
    metadata = dict(((btc_config or {}).get("hungry_hippo_metadata") or {}))
    guardrails = dict(metadata.get("guardrails") or {})
    state_payload = btc_state or {}
    runner = dict(state_payload.get("runner") or {})
    symbol_state = dict(((state_payload.get("symbols") or {}).get("BTCUSD") or {}))

    realized_closes = int(symbol_state.get("realized_closes") or 0)
    realized_net_usd = round(float(symbol_state.get("realized_net_usd") or 0.0), 2)
    anchor_resets = int(symbol_state.get("anchor_resets") or 0)
    resets_per_close = round(anchor_resets / realized_closes, 4) if realized_closes > 0 else None

    started_at = parse_iso_timestamp(str(runner.get("started_at") or ""))
    updated_at = parse_iso_timestamp(str(state_payload.get("updated_at") or runner.get("heartbeat_at") or ""))
    reset_rate_per_hour = None
    if started_at and updated_at and updated_at > started_at:
        elapsed_hours = (updated_at - started_at).total_seconds() / 3600.0
        if elapsed_hours > 0:
            reset_rate_per_hour = round(anchor_resets / elapsed_hours, 4)

    stale_after_seconds = int((btc_config or {}).get("stale_after_seconds") or 0)
    runtime_stale = False
    if stale_after_seconds > 0 and updated_at is not None:
        runtime_stale = (datetime.now(timezone.utc) - updated_at).total_seconds() > stale_after_seconds

    return {
        "proof_started": realized_closes > 0 or anchor_resets > 0 or abs(realized_net_usd) > 0,
        "runtime_stale": runtime_stale,
        "realized_closes": realized_closes,
        "realized_net_usd": realized_net_usd,
        "anchor_resets": anchor_resets,
        "resets_per_close": resets_per_close,
        "reset_rate_per_hour": reset_rate_per_hour,
        "max_resets_per_close": float(guardrails.get("max_resets_per_close")) if guardrails.get("max_resets_per_close") is not None else None,
        "max_resets_per_hour": float(guardrails.get("max_resets_per_hour")) if guardrails.get("max_resets_per_hour") is not None else None,
    }


def summarize_btc_close_mix(comparison_payload: dict[str, Any] | None) -> dict[str, Any]:
    close_mix = dict(((comparison_payload or {}).get("v2_close_mix") or {}))
    harvest_share = close_mix.get("harvest_share")
    return {
        "total_close_events": int(close_mix.get("total_close_events") or 0),
        "harvest_closes": int(close_mix.get("harvest_closes") or 0),
        "escape_tier2_surgical_closes": int(close_mix.get("escape_tier2_surgical_closes") or 0),
        "harvest_share": float(harvest_share) if harvest_share is not None else None,
        "close_mix_status": str(close_mix.get("close_mix_status") or ""),
        "all_closes_escape_dominated": bool(close_mix.get("all_closes_escape_dominated")),
    }


def parse_gbp_closure_repair_compare(markdown: str) -> dict[str, Any]:
    next_action_match = re.search(r"Next action:\s*`([^`]+)`", markdown)
    paired_live_match = re.search(r"Paired experiment live:\s*`([^`]+)`", markdown)
    baseline_present_match = re.search(r"### shadow_gbpusd_tick_forward[\s\S]*?- Present:\s*`([^`]+)`", markdown)
    no_escape_present_match = re.search(r"### shadow_gbpusd_tick_forward_no_escape[\s\S]*?- Present:\s*`([^`]+)`", markdown)
    return {
        "next_action": next_action_match.group(1) if next_action_match else "",
        "paired_experiment_live": (paired_live_match.group(1).lower() == "true") if paired_live_match else None,
        "baseline_present": (baseline_present_match.group(1).lower() == "true") if baseline_present_match else None,
        "no_escape_present": (no_escape_present_match.group(1).lower() == "true") if no_escape_present_match else None,
    }


def summarize_eth_retuned_shadow(state_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = state_payload or {}
    symbol_state = dict(((payload.get("symbols") or {}).get("ETHUSD") or {}))
    if not symbol_state:
        return {}
    realized_closes = int(symbol_state.get("realized_closes") or 0)
    realized_net_usd = round(float(symbol_state.get("realized_net_usd") or 0.0), 2)
    open_count = len(list(symbol_state.get("open_tickets") or []))
    avg_per_close = round(realized_net_usd / realized_closes, 4) if realized_closes > 0 else None
    runner = dict(payload.get("runner") or {})
    return {
        "step3p0_closes": realized_closes,
        "step3p0_net": realized_net_usd,
        "step3p0_open_count": open_count,
        "step3p0_avg_per_close": avg_per_close,
        "step3p0_heartbeat_at": str(runner.get("heartbeat_at") or ""),
    }


def config_row(
    payload: dict[str, Any],
    config_name: str,
    *,
    symbol: str = "",
    preferred_terms: list[str] | None = None,
) -> dict[str, Any]:
    preferred_terms = [str(term).lower() for term in (preferred_terms or []) if str(term).strip()]
    for row in list(payload.get("rows") or []):
        if Path(str(row.get("config_path") or "")).name == config_name:
            return row
    symbol_key = str(symbol or "").upper()
    symbol_candidates = []
    if symbol_key:
        for row in list(payload.get("rows") or []):
            config_path = str(row.get("config_path") or "")
            if infer_symbol_from_config_path(config_path) == symbol_key:
                symbol_candidates.append(row)
        if preferred_terms:
            for row in symbol_candidates:
                config_path_lower = str(row.get("config_path") or "").lower()
                if all(term in config_path_lower for term in preferred_terms):
                    return row
        if symbol_candidates:
            return symbol_candidates[0]
    raise KeyError(f"config row not found: {config_name}")


def symbol_row(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return row
    raise KeyError(f"symbol row not found: {symbol}")


def pilot_row(payload: dict[str, Any], pilot: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if str(row.get("pilot") or "") == pilot:
            return row
    raise KeyError(f"pilot row not found: {pilot}")


def first_pilot_row(payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(payload.get("summary") or {})
    first_pilot = str(summary.get("first_pilot") or "")
    if first_pilot:
        return pilot_row(payload, first_pilot)
    rows = list(payload.get("rows") or [])
    if rows:
        return dict(rows[0])
    raise KeyError("offensive board has no pilot rows")


def select_portability_candidate(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    portability = payload or {}
    rows = [dict(row) for row in list(portability.get("rows") or [])]
    if not rows:
        return None

    indexed = {str(row.get("symbol") or "").upper(): row for row in rows}
    priority_groups = [
        ("ready_for_shadow_discussion", list(((portability.get("summary") or {}).get("ready_for_shadow_discussion_symbols") or [])),
        ),
        ("portable_waiting_forward_proof", list(((portability.get("summary") or {}).get("waiting_forward_proof_symbols") or []))),
        ("portable_missing_launch_contract", list(((portability.get("summary") or {}).get("missing_launch_contract_symbols") or []))),
        ("portable_guardrail_blocked", list(((portability.get("summary") or {}).get("guardrail_blocked_symbols") or []))),
        ("portable_missing_policy", list(((portability.get("summary") or {}).get("missing_policy_symbols") or []))),
    ]
    for expected_status, symbols in priority_groups:
        for symbol in symbols:
            row = indexed.get(str(symbol or "").upper())
            if row and str(row.get("generalization_status") or "") == expected_status:
                return row

    return rows[0]


def portability_action(
    portability_payload: dict[str, Any] | None,
    nas100_cfg: dict[str, Any],
    nas100_row: dict[str, Any],
    nas100_demoted: bool,
) -> dict[str, Any] | None:
    candidate = select_portability_candidate(portability_payload)
    if candidate is None:
        return None

    symbol = str(candidate.get("symbol") or "").upper()
    symbol_slug = symbol.lower()
    status = str(candidate.get("generalization_status") or "")
    machine_truth = {
        "symbol": symbol,
        "asset_class": candidate.get("asset_class"),
        "generalization_status": status,
        "highest_leverage_gap": candidate.get("highest_leverage_gap"),
        "deployment_verdict": candidate.get("deployment_verdict"),
        "guardrail_status": candidate.get("guardrail_status"),
        "surface_coverage_complete": bool(candidate.get("surface_coverage_complete")),
        "launch_contract_count": int(candidate.get("launch_contract_count") or 0),
        "enabled_launch_contract_count": int(candidate.get("enabled_launch_contract_count") or 0),
    }
    hard_block_reasons = list(candidate.get("hard_block_reasons") or [])
    if hard_block_reasons:
        machine_truth["hard_block_reasons"] = hard_block_reasons
    note = str(candidate.get("note") or "")
    if note:
        machine_truth["portability_note"] = note

    if symbol == "NAS100":
        machine_truth.update(
            {
                "launch_verdict": nas100_cfg["verdict"],
                "proof_closes": nas100_row["proof_closes"],
                "ratio_to_atr": nas100_row["ratio_to_atr"],
                "nas100_demoted_by_fresh_window": nas100_demoted,
            }
        )

    if status == "ready_for_shadow_discussion":
        return {
            "category": "test_next",
            "action": f"treat_{symbol_slug}_as_the_leading_portable_shadow_discussion_candidate",
            "rationale": f"{symbol} is currently the cleanest cross-symbol expansion seam because the portability stack says policy, guardrails, and runnable contract coverage already exist; the honest remaining job is fresh shadow proof, not more platform wiring.",
            "machine_truth": machine_truth,
            "advance_when": f"{symbol} stays spread-robust and positive under fresh shadow proof with no new guardrail or launch-contract contradictions.",
            "kill_when": f"{symbol} loses portability status, picks up a new guardrail contradiction, or gets treated as live-ready before fresh shadow proof exists.",
        }

    if status == "portable_waiting_forward_proof":
        action = f"treat_{symbol_slug}_as_the_leading_portable_waiting_forward_proof_candidate"
        rationale = f"{symbol} is the current cleanest cross-symbol expansion seam because the portability stack says the family, policy surface, and runnable contract already exist; the remaining blocker is fresh forward proof."
        advance_when = f"{symbol} accumulates fresh forward proof without guardrail drift and stays positive enough to graduate from portability to real shadow discussion."
        kill_when = f"{symbol} is treated as a clean expansion story before the forward-proof debt is cleared."
        if symbol == "NAS100" and nas100_demoted:
            action = "keep_nas100_as_the_leading_portable_forward_proof_candidate_but_not_a_clean_expansion_story_yet"
            rationale = "NAS100 is still the only portable_waiting_forward_proof symbol, but the fresh-window authority says the current control window remains closure-dominated, so it can stay the top portability seam without being described as already clean."
            advance_when = "a fresh control-window bucket read stops showing closure dominance and NAS100 remains positive under forward shadow conditions."
            kill_when = "the room turns NAS100 portability into a clean-expansion story before the fresh-window closure diagnosis is cleared."
        return {
            "category": "test_next",
            "action": action,
            "rationale": rationale,
            "machine_truth": machine_truth,
            "advance_when": advance_when,
            "kill_when": kill_when,
        }

    if status == "portable_missing_launch_contract":
        return {
            "category": "fix_now",
            "action": f"add_a_checked_in_{symbol_slug}_launch_contract_before_claiming_full_stack_portability",
            "rationale": f"{symbol} already has family support and policy coverage, but it is not a real multi-symbol candidate until there is at least one checked-in runnable shadow or live launch contract.",
            "machine_truth": machine_truth,
            "advance_when": f"{symbol} has at least one checked-in launch contract that survives the current launch-safety validator.",
            "kill_when": f"{symbol} gets counted as portable in the full-stack sense without any runnable checked-in contract.",
        }

    if status == "portable_guardrail_blocked":
        return {
            "category": "fix_now",
            "action": f"repair_{symbol_slug}_guardrail_alignment_before_using_it_as_a_portable_candidate",
            "rationale": f"{symbol} proves the family is portable, but the current guardrail and live-gate truth still block honest rollout, so the leverage is alignment repair rather than expansion storytelling.",
            "machine_truth": machine_truth,
            "advance_when": f"{symbol} clears the current guardrail contradiction or hard-block surface and still retains runnable contract coverage.",
            "kill_when": f"{symbol} is presented as the next cross-symbol candidate while its guardrail or live-gate block is still active.",
        }

    return {
        "category": "fix_now",
        "action": f"add_{symbol_slug}_canonical_policy_surface_before_counting_it_as_full_stack_portability",
        "rationale": f"{symbol} already maps cleanly to a Hungry Hippo runtime family, but the missing piece is canonical policy coverage, not more symbol parsing or one-off config folklore.",
        "machine_truth": machine_truth,
        "advance_when": f"{symbol} has a checked-in canonical guardrail and regime policy surface that can feed the same governance stack as existing symbols.",
        "kill_when": f"{symbol} gets treated as a serious cross-symbol candidate before canonical policy coverage exists.",
    }


def count_asset_classes(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        asset_class = str(row.get("asset_class") or "unknown").lower()
        counts[asset_class] = counts.get(asset_class, 0) + 1
    return counts


def describe_seed_now_focus(seed_now_symbols: list[str], seed_now_asset_counts: dict[str, int]) -> str:
    if not seed_now_symbols:
        return "there is no immediate seed-now policy set"
    if len(seed_now_asset_counts) == 1:
        asset_class, count = next(iter(seed_now_asset_counts.items()))
        return f"the immediate seed-now policy set is currently concentrated in {asset_class} ({count})"
    return f"the immediate seed-now policy set spans {seed_now_asset_counts}"


def portfolio_doctrine_action(
    portability_payload: dict[str, Any] | None,
    policy_gap_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    portability = portability_payload or {}
    policy_gap = policy_gap_payload or {}
    portability_summary = dict(portability.get("summary") or {})
    policy_summary = dict(policy_gap.get("summary") or {})
    policy_rows = [dict(row) for row in list(policy_gap.get("rows") or [])]
    if not portability_summary and not policy_rows:
        return None

    seed_now_rows = [row for row in policy_rows if str(row.get("priority") or "") == "policy_seed_now"]
    seed_next_rows = [row for row in policy_rows if str(row.get("priority") or "") == "policy_seed_next"]
    seed_now_asset_counts = count_asset_classes(seed_now_rows)
    seed_next_asset_counts = count_asset_classes(seed_next_rows)
    seed_now_symbols = [str(row.get("symbol") or "") for row in seed_now_rows if str(row.get("symbol") or "")]
    seed_next_symbols = [str(row.get("symbol") or "") for row in seed_next_rows if str(row.get("symbol") or "")]
    waiting_forward_symbols = list(portability_summary.get("waiting_forward_proof_symbols") or [])
    lead_forward_symbol = str(waiting_forward_symbols[0] or "") if waiting_forward_symbols else ""
    seed_now_is_single_asset_class = len(seed_now_asset_counts) == 1 and bool(seed_now_asset_counts)
    seed_now_focus = describe_seed_now_focus(seed_now_symbols, seed_now_asset_counts)

    return {
        "category": "design_now",
        "action": "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout",
        "rationale": f"Hungry Hippo is getting closer to family portability, but a tiny-account rollout still fails if symbol count is mistaken for survivability. The current truth says there is only one waiting-forward-proof seam, while {seed_now_focus}, so the honest doctrine is a balance-growth unlock ladder: prove one capital-efficient lead symbol first, then add one new symbol only when equity can absorb its own drawdown budget.",
        "machine_truth": {
            "family_portable_count": int(portability_summary.get("family_portable_count") or 0),
            "surface_coverage_complete_count": int(portability_summary.get("surface_coverage_complete_count") or 0),
            "waiting_forward_proof_symbols": waiting_forward_symbols,
            "lead_forward_proof_symbol": lead_forward_symbol,
            "guardrail_blocked_symbol_count": len(list(portability_summary.get("guardrail_blocked_symbols") or [])),
            "missing_policy_symbol_count": int(policy_summary.get("missing_policy_symbol_count") or 0),
            "policy_seed_now_symbols": seed_now_symbols,
            "policy_seed_now_asset_class_counts": seed_now_asset_counts,
            "policy_seed_now_single_asset_class": seed_now_is_single_asset_class,
            "policy_seed_next_symbols": seed_next_symbols,
            "policy_seed_next_asset_class_counts": seed_next_asset_counts,
        },
        "advance_when": "the room defines an unlock ladder with equity or drawdown gates for symbol #2, #3, and later additions, and each new symbol is added only after the currently active set proves it can carry the extra drawdown budget.",
        "kill_when": "family_portable_count, the policy-seed list, or a short green streak gets treated as permission to run many unresolved symbols simultaneously on one small account.",
    }


def build_payload(
    launch_safety: dict[str, Any],
    deployment_gate: dict[str, Any],
    research_board: dict[str, Any],
    offensive_board: dict[str, Any],
    eth_comparison: dict[str, Any],
    eth_control_state: dict[str, Any],
    eth_control_gate: dict[str, Any],
    reset_alerts: dict[str, Any],
    authority_stack_text: str,
    fresh_window_text: str,
    closure_firewall_text: str,
    validated_theory_queue_text: str,
    gbp_closure_repair_compare: dict[str, Any],
    btc_downtrend_config: dict[str, Any] | None = None,
    btc_downtrend_state: dict[str, Any] | None = None,
    btc_sell_tight_comparison: dict[str, Any] | None = None,
    eth_coefficient_analysis: dict[str, Any] | None = None,
    eth_retuned_state: dict[str, Any] | None = None,
    portability_board: dict[str, Any] | None = None,
    policy_gap_board: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eth_state = dict(((eth_control_state.get("symbols") or {}).get("ETHUSD") or {}))
    eth_meta = dict(eth_control_state.get("metadata") or {})
    eth_row = symbol_row(deployment_gate, "ETHUSD")
    nas100_row = symbol_row(deployment_gate, "NAS100")
    gbpusd_row = symbol_row(deployment_gate, "GBPUSD")
    xauusd_cfg = config_row(
        launch_safety,
        "hungry_hippo_xauusd_consolidation_shadow.json",
        symbol="XAUUSD",
        preferred_terms=["consolidation"],
    )
    eth_step5_cfg = config_row(
        launch_safety,
        "hungry_hippo_ethusd_m5_step5_shadow.json",
        symbol="ETHUSD",
        preferred_terms=["step5"],
    )
    btc_m15_cfg = config_row(
        launch_safety,
        "hungry_hippo_btcusd_m15_sell_tight_shadow.json",
        symbol="BTCUSD",
        preferred_terms=["sell_tight"],
    )
    btc_m5_cfg = config_row(
        launch_safety,
        "hungry_hippo_btcusd_m5_step200_shadow.json",
        symbol="BTCUSD",
        preferred_terms=["step200"],
    )
    gbp_cfg = config_row(
        launch_safety,
        "hungry_hippo_gbpusd_deploy.json",
        symbol="GBPUSD",
        preferred_terms=["deploy"],
    )
    nas100_cfg = config_row(
        launch_safety,
        "hungry_hippo_nas100_m15_breakout_buy_shadow.json",
        symbol="NAS100",
        preferred_terms=["breakout", "buy"],
    )
    first_pilot = first_pilot_row(offensive_board)

    reset_safe = int(reset_alerts.get("lanes_killed") or 0) == 0 and int(reset_alerts.get("reset_rate_limit") or 0) == 6
    eth_closes = int(eth_state.get("realized_closes") or 0)
    eth_net = float(eth_state.get("realized_net_usd") or 0.0)
    eth_step = float(eth_meta.get("step") or 0.0)
    eth_alpha = float(eth_meta.get("raw_close_alpha") or 0.0)
    eth_gate_summary = dict(eth_control_gate.get("summary") or {})
    eth_gate_runtime = dict(eth_control_gate.get("control_runtime") or {})
    eth_gate_infra = dict(eth_control_gate.get("infra_alignment") or {})
    eth_gate_verdict = str(eth_gate_summary.get("verdict") or "")
    eth_target_closes = int(eth_gate_summary.get("target_closes") or 0)
    eth_avg_per_close = float(eth_gate_summary.get("avg_per_close") or 0.0)
    eth_negative_proof_complete = (
        eth_gate_verdict == "blocked_by_negative_expectancy"
        and eth_target_closes > 0
        and eth_closes >= eth_target_closes
    )
    eth_coeff = dict(eth_coefficient_analysis or {})
    eth_retuned_truth = summarize_eth_retuned_shadow(eth_retuned_state)
    eth_recommended_step_usd = eth_coeff.get("recommended_step_usd")
    eth_alternate_step_usd = eth_coeff.get("alternate_step_usd")
    eth_recommended_min_closes = eth_coeff.get("minimum_proof_closes")
    eth_registry_lane_found = bool(eth_gate_infra.get("registry_lane_found"))
    eth_control_board_matches_launch_surface = bool(eth_gate_infra.get("control_board_matches_launch_surface"))
    authority_lower = authority_stack_text.lower()
    fresh_window_lower = fresh_window_text.lower()
    closure_lower = closure_firewall_text.lower()
    validated_lower = validated_theory_queue_text.lower()
    if eth_gate_infra:
        eth_has_infra_contradiction = bool(eth_gate_infra.get("surface_alignment_blocked"))
    else:
        eth_has_infra_contradiction = "contradiction" in validated_lower and "eth m5 step5_v1 is dead" in validated_lower
    nas100_demoted = (
        "demote from `cleanest next expansion seam`" in authority_lower
        or "nas100 should drop from \"cleanest next expansion seam\"" in fresh_window_lower
    )
    btc_wait_filesystem_proof = (
        "watch for fresh filesystem proof" in authority_lower
        or "hold judgment until those surfaces refresh" in fresh_window_lower
    )
    btc_forward = summarize_btc_forward_proof(btc_downtrend_config, btc_downtrend_state)
    btc_close_mix = summarize_btc_close_mix(btc_sell_tight_comparison)
    btc_reset_rate_above_hourly_guardrail = (
        btc_forward["max_resets_per_hour"] is not None
        and btc_forward["reset_rate_per_hour"] is not None
        and btc_forward["reset_rate_per_hour"] > btc_forward["max_resets_per_hour"]
    )
    btc_reset_ratio_above_guardrail = (
        btc_forward["max_resets_per_close"] is not None
        and btc_forward["resets_per_close"] is not None
        and btc_forward["resets_per_close"] > btc_forward["max_resets_per_close"]
    )
    gbp_is_bucket_diagnosis = (
        "bucket diagnosis lane" in authority_lower
        or "closure-policy diagnosis lane" in fresh_window_lower
        or "closure policy is overwhelming it" in closure_lower
    )
    gbp_compare_next_action = str(gbp_closure_repair_compare.get("next_action") or "")
    gbp_pair_live = gbp_closure_repair_compare.get("paired_experiment_live")
    gbp_no_escape_present = gbp_closure_repair_compare.get("no_escape_present")
    actions: list[dict[str, Any]] = []

    top_eth_action = {
        "category": "observe_now",
        "action": "keep_eth_m5_step14_control_running_as_the_single_proof_lane",
        "rationale": "ETH control is the only active spread-safe, reset-safe proof lane, but it remains research-only and blocked from live discussion until the aligned runtime produces enough positive forward proof to be trusted as a baseline.",
        "machine_truth": {
            "eth_step": eth_step,
            "eth_alpha": eth_alpha,
            "eth_closes": eth_closes,
            "eth_realized_net_usd": round(eth_net, 2),
            "deployment_gate_verdict": eth_row["deployment_verdict"],
            "effective_spread_status": eth_row["effective_spread_status"],
            "comparison_status": eth_comparison.get("comparison_status"),
            "reset_stack_safe": reset_safe,
            "eth_gate_verdict": eth_gate_verdict,
            "infra_surface_contradiction": eth_has_infra_contradiction,
        },
        "advance_when": "realized_closes >= 25, realized_net_usd > 0, and reset-rate stays below the 6/hour kill threshold while the control remains a fixed spread-safe shape.",
        "kill_when": "reset-rate breaches the kill-switch threshold, realized_net_usd turns negative over a meaningful sample, or control drift reintroduces mixed geometry truth.",
    }
    if eth_negative_proof_complete and eth_recommended_step_usd is not None:
        top_eth_action = {
            "category": "decide_now",
            "action": "decide_eth_step14_negative_proof_response_kill_or_launch_retuned_shadow",
            "rationale": "ETH step14 is no longer an honest proof-accumulation job: the aligned control already cleared its proof window and stayed structurally negative, so the next decision is whether to kill the disproved control or launch a fresh retuned shadow lane with economically meaningful spacing.",
            "machine_truth": {
                "eth_step": eth_step,
                "eth_alpha": eth_alpha,
                "eth_closes": eth_closes,
                "eth_target_closes": eth_target_closes,
                "eth_realized_net_usd": round(eth_net, 2),
                "eth_avg_per_close": round(eth_avg_per_close, 4),
                "comparison_status": eth_comparison.get("comparison_status"),
                "eth_gate_verdict": eth_gate_verdict,
                "recommended_retune_step_usd": eth_recommended_step_usd,
                "alternate_retune_step_usd": eth_alternate_step_usd,
                "recommended_min_shadow_closes": eth_recommended_min_closes,
                "kill_option_available": bool(eth_coeff.get("kill_option_available")),
                **eth_retuned_truth,
            },
            "advance_when": "the room explicitly chooses one branch: kill the disproved step14 control, or launch a new retuned ETH shadow lane around the recommended step with a fresh 25+ close proof contract.",
            "kill_when": "the room keeps treating additional step14 negative closes as if they were missing evidence, or changes both step and offensive-closure behavior in the same experiment.",
        }
    elif eth_gate_verdict == "blocked_by_surface_alignment":
        if not eth_registry_lane_found:
            top_eth_action = {
                "category": "fix_now",
                "action": "register_eth_m5_step14_control_and_repoint_the_proof_board_to_the_same_lane",
                "rationale": "The ETH proof sample is still split across a checked-in step14 control config, a missing registry launch lane, and an older judged proof file, so the fastest honest unblock is to register one launch lane and judge that same lineage.",
                "machine_truth": {
                    "eth_gate_verdict": eth_gate_verdict,
                    "heartbeat_at": eth_gate_runtime.get("heartbeat_at"),
                    "heartbeat_age_seconds": eth_gate_runtime.get("heartbeat_age_seconds"),
                    "runtime_stale": eth_gate_runtime.get("runtime_stale"),
                    "registry_lane_found": eth_gate_infra.get("registry_lane_found"),
                    "config_enabled": eth_gate_infra.get("config_enabled"),
                    "registry_enabled": eth_gate_infra.get("registry_enabled"),
                    "enabled_alignment_ok": eth_gate_infra.get("enabled_alignment_ok"),
                    "control_board_matches_launch_surface": eth_gate_infra.get("control_board_matches_launch_surface"),
                    "control_state_registered_launch_lane": eth_gate_infra.get("control_state_registered_launch_lane"),
                    "control_state_orphaned_from_registry": eth_gate_infra.get("control_state_orphaned_from_registry"),
                    "declared_step_alignment_ok": eth_gate_infra.get("declared_step_alignment_ok"),
                    "control_board_declared_step": eth_gate_infra.get("control_board_declared_step"),
                    "comparison_status": eth_comparison.get("comparison_status"),
                    "infra_surface_contradiction": eth_has_infra_contradiction,
                },
                "advance_when": "the step14 control exists as a real registry lane, the proof board points at that same lane, and the ETH proof gate no longer reports blocked_by_surface_alignment.",
                "kill_when": "the room keeps citing the current ETH closes as proof while the judged proof file is still orphaned from the launch surface or while the launch lane is still missing from registry.",
            }
        elif not eth_control_board_matches_launch_surface:
            top_eth_action = {
                "category": "fix_now",
                "action": "retire_orphan_eth_m5_proof_artifact_and_restore_registered_step14_control_runtime",
                "rationale": "The step14 control is now wired in config, registry, and watchdog, but the room is still judging an older orphan proof file instead of the registered control lane, so the next honest move is to restore the registered runtime and cut the judged proof surface over to that lineage.",
                "machine_truth": {
                    "eth_gate_verdict": eth_gate_verdict,
                    "heartbeat_at": eth_gate_runtime.get("heartbeat_at"),
                    "heartbeat_age_seconds": eth_gate_runtime.get("heartbeat_age_seconds"),
                    "runtime_stale": eth_gate_runtime.get("runtime_stale"),
                    "registry_lane_found": eth_gate_infra.get("registry_lane_found"),
                    "config_enabled": eth_gate_infra.get("config_enabled"),
                    "registry_enabled": eth_gate_infra.get("registry_enabled"),
                    "enabled_alignment_ok": eth_gate_infra.get("enabled_alignment_ok"),
                    "control_board_matches_launch_surface": eth_gate_infra.get("control_board_matches_launch_surface"),
                    "control_state_registered_launch_lane": eth_gate_infra.get("control_state_registered_launch_lane"),
                    "control_state_orphaned_from_registry": eth_gate_infra.get("control_state_orphaned_from_registry"),
                    "declared_step_alignment_ok": eth_gate_infra.get("declared_step_alignment_ok"),
                    "control_board_declared_step": eth_gate_infra.get("control_board_declared_step"),
                    "comparison_status": eth_comparison.get("comparison_status"),
                    "infra_surface_contradiction": eth_has_infra_contradiction,
                },
                "advance_when": "the registered step14 control lane writes the judged state/event surface, the proof board points at that same lineage, and the ETH proof gate no longer reports blocked_by_surface_alignment.",
                "kill_when": "the room keeps citing the orphan step14 artifact as current proof or the registered lane still fails to produce fresh state after wiring is in place.",
            }
        else:
            top_eth_action = {
                "category": "fix_now",
                "action": "reconcile_eth_m5_control_to_one_canonical_surface_before_using_it_as_the_proof_lane",
                "rationale": "The ETH proof sample is not honest enough to cite yet because config, registry, and the proof board still disagree about which control lineage is real, and the current step14 proof file is not the canonical checked-in launch lane.",
                "machine_truth": {
                    "eth_gate_verdict": eth_gate_verdict,
                    "heartbeat_at": eth_gate_runtime.get("heartbeat_at"),
                    "heartbeat_age_seconds": eth_gate_runtime.get("heartbeat_age_seconds"),
                    "runtime_stale": eth_gate_runtime.get("runtime_stale"),
                    "registry_lane_found": eth_gate_infra.get("registry_lane_found"),
                    "config_enabled": eth_gate_infra.get("config_enabled"),
                    "registry_enabled": eth_gate_infra.get("registry_enabled"),
                    "enabled_alignment_ok": eth_gate_infra.get("enabled_alignment_ok"),
                    "control_board_matches_launch_surface": eth_gate_infra.get("control_board_matches_launch_surface"),
                    "control_state_registered_launch_lane": eth_gate_infra.get("control_state_registered_launch_lane"),
                    "control_state_orphaned_from_registry": eth_gate_infra.get("control_state_orphaned_from_registry"),
                    "declared_step_alignment_ok": eth_gate_infra.get("declared_step_alignment_ok"),
                    "control_board_declared_step": eth_gate_infra.get("control_board_declared_step"),
                    "step5_declared_step": eth_gate_infra.get("step5_declared_step"),
                    "comparison_status": eth_comparison.get("comparison_status"),
                    "infra_surface_contradiction": eth_has_infra_contradiction,
                },
                "advance_when": "config, registry, and the proof board point at the same ETH control lineage and the proof gate no longer reports blocked_by_surface_alignment.",
                "kill_when": "the room keeps citing the current ETH closes as proof while config, registry, and board surfaces still disagree about the control being judged or while the step14 file remains an orphan artifact.",
            }
    elif eth_gate_verdict == "blocked_by_stale_runtime":
        top_eth_action = {
            "category": "verify_now",
            "action": "verify_or_restore_eth_m5_step14_control_runtime_before_treating_it_as_the_proof_lane",
            "rationale": "The ETH control snapshot is positive but stale; the room should not keep citing old proof numbers if the lane is dead or not updating.",
            "machine_truth": {
                "eth_gate_verdict": eth_gate_verdict,
                "heartbeat_at": eth_gate_runtime.get("heartbeat_at"),
                "heartbeat_age_seconds": eth_gate_runtime.get("heartbeat_age_seconds"),
                "runtime_stale": eth_gate_runtime.get("runtime_stale"),
                "eth_closes": eth_closes,
                "eth_realized_net_usd": round(eth_net, 2),
                "comparison_status": eth_comparison.get("comparison_status"),
                "infra_surface_contradiction": eth_has_infra_contradiction,
            },
            "advance_when": "the ETH control lane is alive again with a fresh heartbeat and the proof board no longer reports stale runtime.",
            "kill_when": "the room keeps using a stale ETH snapshot as current proof or tries to launch the offensive A/B before control runtime is live and normalized again.",
        }
    elif eth_gate_verdict == "blocked_by_control_normalization":
        top_eth_action = {
            "category": "fix_now",
            "action": "normalize_eth_m5_step14_runtime_geometry_and_accumulate_honest_control_proof",
            "rationale": "The registered ETH step14 control is finally the judged lane, but the live runtime is still not an honest fixed-step control because the buy/sell ladder has drifted materially and the first close is negative.",
            "machine_truth": {
                "eth_gate_verdict": eth_gate_verdict,
                "heartbeat_at": eth_gate_runtime.get("heartbeat_at"),
                "heartbeat_age_seconds": eth_gate_runtime.get("heartbeat_age_seconds"),
                "runtime_stale": eth_gate_runtime.get("runtime_stale"),
                "geometry_normalized": eth_gate_runtime.get("geometry_normalized"),
                "effective_buy_distance": eth_gate_runtime.get("effective_buy_distance"),
                "effective_sell_distance": eth_gate_runtime.get("effective_sell_distance"),
                "buy_drift_ratio": eth_gate_runtime.get("buy_drift_ratio"),
                "sell_drift_ratio": eth_gate_runtime.get("sell_drift_ratio"),
                "eth_closes": eth_closes,
                "eth_realized_net_usd": round(eth_net, 2),
                "comparison_status": eth_comparison.get("comparison_status"),
                "infra_surface_contradiction": eth_has_infra_contradiction,
            },
            "advance_when": "the registered step14 runtime keeps a fresh heartbeat, the buy/sell ladder stays close to declared step14 geometry, and the control sample grows toward 25 closes with positive realized net.",
            "kill_when": "the room treats the current drifted runtime as a clean control, or the control keeps bleeding without geometry normalization while people still treat it as baseline proof.",
        }
    actions.append(top_eth_action)

    if int((launch_safety.get("summary") or {}).get("blocking_enabled_config_count") or 0) > 0:
        actions.append(
            {
            "category": "fix_now",
            "action": "disable_or_park_enabled_configs_that_fail_the_current_launch_contract",
            "rationale": "The repo still has enabled Hungry Hippo configs that fail direct launch-safety checks, which is the fastest avoidable loss seam because these are not theory problems first; they are broken launch surfaces.",
            "machine_truth": {
                "blocking_enabled_config_count": int((launch_safety.get("summary") or {}).get("blocking_enabled_config_count") or 0),
                "eth_step5_fail_reasons": eth_step5_cfg["hard_fail_reasons"],
                "btc_m15_fail_reasons": btc_m15_cfg["hard_fail_reasons"],
                "gbpusd_fail_reasons": gbp_cfg["hard_fail_reasons"],
                "xauusd_fail_reasons": xauusd_cfg["hard_fail_reasons"],
            },
            "advance_when": "the launch validator returns zero enabled hard-fail configs, especially after removing unsupported crypto escape flags and missing escape-hatch drift.",
            "kill_when": "the room starts treating failing checked-in configs as merely advisory instead of honoring the validator as a hard preflight.",
            }
        )

    if gbp_compare_next_action == "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair":
        actions.append(
            {
                "category": "fix_now",
                "action": gbp_compare_next_action,
                "rationale": "GBP closure repair is no longer blocked by theory ambiguity; it is blocked because the no-escape companion lane has not produced state yet, so the paired experiment is not actually running.",
                "machine_truth": {
                    "best_overall_contribution": research_board.get("best_overall_contribution"),
                    "gbpusd_gate_verdict": gbpusd_row["deployment_verdict"],
                    "gbpusd_guardrail_status": gbpusd_row["guardrail_status"],
                    "gbpusd_proof_closes": gbpusd_row["proof_closes"],
                    "gbpusd_is_bucket_diagnosis": gbp_is_bucket_diagnosis,
                    "gbp_compare_next_action": gbp_compare_next_action,
                    "gbp_closure_pair_live": gbp_pair_live,
                    "gbp_no_escape_present": gbp_no_escape_present,
                },
                "advance_when": "the no-escape companion lane writes state and reports offensive_closure_enabled=false so the paired closure-repair read becomes honest.",
                "kill_when": "the room judges closure repair from the baseline lane alone before the no-escape companion exists as a real paired experiment.",
            }
        )
    elif gbp_compare_next_action == "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape":
        actions.append(
            {
                "category": "watch_now",
                "action": gbp_compare_next_action,
                "rationale": "The paired GBP experiment is finally live, so the honest next move is no longer launch plumbing or abstract bucket theory; it is to accumulate fresh paired closes and compare the baseline lane against the no-escape control.",
                "machine_truth": {
                    "best_overall_contribution": research_board.get("best_overall_contribution"),
                    "gbpusd_gate_verdict": gbpusd_row["deployment_verdict"],
                    "gbpusd_guardrail_status": gbpusd_row["guardrail_status"],
                    "gbpusd_proof_closes": gbpusd_row["proof_closes"],
                    "gbpusd_is_bucket_diagnosis": gbp_is_bucket_diagnosis,
                    "gbp_compare_next_action": gbp_compare_next_action,
                    "gbp_closure_pair_live": gbp_pair_live,
                    "gbp_no_escape_present": gbp_no_escape_present,
                },
                "advance_when": "both GBP lanes keep writing fresh state, the no-escape companion stays offensive_closure_enabled=false, and the room has enough paired forward closes to compare baseline vs no-escape honestly.",
                "kill_when": "the room jumps back to promotion or closure-repair claims before the paired baseline-vs-no-escape sample is large enough to judge.",
            }
        )
    else:
        actions.append(
            {
                "category": "diagnose_now",
                "action": "treat_gbpusd_alpha_half_as_bucket_diagnosis_before_any_promotion_or_default_story",
                "rationale": "GBPUSD still matters because the harvest bucket looks alive, but the newest authority surfaces say closure tax dominates and promotion language is dishonest until that closure leak is repaired.",
                "machine_truth": {
                    "best_overall_contribution": research_board.get("best_overall_contribution"),
                    "gbpusd_gate_verdict": gbpusd_row["deployment_verdict"],
                    "gbpusd_guardrail_status": gbpusd_row["guardrail_status"],
                    "gbpusd_proof_closes": gbpusd_row["proof_closes"],
                    "gbpusd_is_bucket_diagnosis": gbp_is_bucket_diagnosis,
                    "gbp_compare_next_action": gbp_compare_next_action,
                    "gbp_closure_pair_live": gbp_pair_live,
                    "gbp_no_escape_present": gbp_no_escape_present,
                },
                "advance_when": "fresh forward buckets show harvest, offensive-close, and forced-unwind separately and closure tax stops dominating the lane net.",
                "kill_when": "the room resumes closest-live or FX-default storytelling before the closure buckets are actually repaired.",
            }
        )

    btc_action = {
        "category": "watch_now",
        "action": "wait_for_filesystem_confirmed_post_launch_proof_before_judging_btc_m15_sell_tight",
        "rationale": "The reconciled BTC sell-tight config may still be valuable for less losses, but the newest narrow boards say the visible state/event surfaces have not refreshed under the current claimed launch yet.",
        "machine_truth": {
            "btc_symbol_gate": symbol_row(deployment_gate, "BTCUSD")["deployment_verdict"],
            "btc_launch_verdict": btc_m15_cfg["verdict"],
            "btc_wait_filesystem_proof": btc_wait_filesystem_proof,
            "btc_fail_reasons": btc_m15_cfg["hard_fail_reasons"],
        },
        "advance_when": "post-launch state/event files refresh and print current-window harvest or other fresh forward evidence under the reconciled probe.",
        "kill_when": "legacy closure losses or stale files get treated as proof that the newly reconciled probe already failed.",
    }
    if btc_forward["proof_started"]:
        btc_action = {
            "category": "watch_now",
            "action": "continue_btc_m15_sell_tight_v2_forward_proof_and_watch_reset_behavior",
            "rationale": "BTC sell-tight v2 is already printing fresh forward data, so the honest job is no longer filesystem confirmation; it is judging whether the retune stabilizes reset behavior quickly enough to avoid another failed less-losses verdict.",
            "machine_truth": {
                "btc_symbol_gate": symbol_row(deployment_gate, "BTCUSD")["deployment_verdict"],
                "btc_launch_verdict": btc_m15_cfg["verdict"],
                "btc_forward_proof_started": True,
                "btc_wait_filesystem_proof": False,
                "btc_runtime_stale": btc_forward["runtime_stale"],
                "btc_realized_closes": btc_forward["realized_closes"],
                "btc_realized_net_usd": btc_forward["realized_net_usd"],
                "btc_anchor_resets": btc_forward["anchor_resets"],
                "btc_resets_per_close": btc_forward["resets_per_close"],
                "btc_reset_rate_per_hour": btc_forward["reset_rate_per_hour"],
                "btc_max_resets_per_close": btc_forward["max_resets_per_close"],
                "btc_max_resets_per_hour": btc_forward["max_resets_per_hour"],
                "btc_total_close_events": btc_close_mix["total_close_events"],
                "btc_harvest_closes": btc_close_mix["harvest_closes"],
                "btc_escape_tier2_surgical_closes": btc_close_mix["escape_tier2_surgical_closes"],
                "btc_harvest_share": btc_close_mix["harvest_share"],
                "btc_close_mix_status": btc_close_mix["close_mix_status"],
                "btc_all_closes_escape_dominated": btc_close_mix["all_closes_escape_dominated"],
                "btc_fail_reasons": btc_m15_cfg["hard_fail_reasons"],
            },
            "advance_when": "fresh BTC v2 closes keep accumulating, reset behavior settles back inside guardrails, and the sample recovers into positive net over the initial proof window.",
            "kill_when": "the hourly reset pace stays above guardrails after startup noise should have passed, the reset-per-close ratio blows past its guardrail, or the fresh sample stays negative long enough to invalidate the retune.",
        }
        if btc_close_mix["close_mix_status"] == "zero_harvest_all_escape_so_far":
            btc_action["rationale"] = "BTC sell-tight v2 is already printing fresh forward data, but every realized close so far is escape_tier2_surgical with zero harvest closes, so the honest next move is to watch for actual close_ticket harvests instead of calling cleaner losses a win."
            btc_action["advance_when"] = "fresh BTC v2 closes keep accumulating, at least some close_ticket harvest closes appear, the close mix stops being all-escape, and reset behavior stays inside guardrails."
            btc_action["kill_when"] = "the sample stays all-escape with zero harvest after a meaningful close window, the hourly reset pace stays above guardrails after startup noise should have passed, or the fresh sample stays negative long enough to invalidate the retune."
        elif btc_reset_rate_above_hourly_guardrail:
            btc_action["rationale"] = "BTC sell-tight v2 is already live, and the first sample is negative with an hourly reset pace still above guardrails, so the honest next move is to watch whether startup churn settles before calling the retune an improvement."
        elif btc_reset_ratio_above_guardrail:
            btc_action["rationale"] = "BTC sell-tight v2 is already live, and the first sample is negative with resets per close still above guardrails, so the honest next move is to watch whether the retune actually reduces churn before calling it an improvement."
        elif btc_forward["realized_net_usd"] <= 0:
            btc_action["rationale"] = "BTC sell-tight v2 is already live and far below the v1 churn disaster, but the early sample is still negative, so the honest next move is to keep collecting fresh proof rather than pretending the retune is validated."
    actions.append(btc_action)

    actions.append(
        {
            "category": "test_next",
            "action": "prepare_eth_m5_offensive_closure_ab_only_after_control_normalization",
            "rationale": "Offensive extreme closure is still the cleanest non-runtime less-losses theory, but the first honest blocker is positive proof on the aligned ETH control arm, not promotion or mixed-geometry storytelling.",
            "machine_truth": {
                "comparison_status": eth_comparison.get("comparison_status"),
                "recommended_control_step": ((eth_comparison.get("normalization_recommendation") or {}).get("recommended_control_step")),
                "first_pilot_status": first_pilot["status"],
                "primary_success": ((offensive_board.get("experiment_protocol") or {}).get("primary_success") or []),
                "eth_gate_verdict": eth_gate_verdict,
            },
            "advance_when": "the room has either kept or replaced the ETH control arm with a positive baseline, and control arm and variant arm use the same ETH M5 spread-safe shape with geometry adaptation posture held constant."
            if eth_negative_proof_complete and eth_recommended_step_usd is not None
            else "the aligned ETH control arm has enough positive proof to serve as a trustworthy baseline, and control arm and variant arm use the same ETH M5 spread-safe shape with geometry adaptation posture held constant.",
            "kill_when": "the experiment changes both step and closure behavior at once, the room skips the kill-vs-retune decision after negative proof, or the variant only looks better because the market got easier.",
        }
    )

    portability_next = portability_action(portability_board, nas100_cfg, nas100_row, nas100_demoted)
    if portability_next is None:
        nas100_action = {
                "category": "test_next",
                "action": "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves",
                "rationale": "NAS100 is still worth watching, but the newer fresh-window authority says the current control window remains closure-dominated, so it should not be described as the cleanest next expansion seam right now.",
                "machine_truth": {
                    "launch_verdict": nas100_cfg["verdict"],
                    "deployment_gate_verdict": nas100_row["deployment_verdict"],
                    "proof_closes": nas100_row["proof_closes"],
                    "ratio_to_atr": nas100_row["ratio_to_atr"],
                    "guardrail_status": nas100_row["guardrail_status"],
                    "nas100_demoted_by_fresh_window": nas100_demoted,
                },
                "advance_when": "a fresh control-window bucket read stops showing closure dominance and the lane remains positive under forward shadow conditions.",
                "kill_when": "the room turns one surviving research-only config into a clean-expansion story before the closure diagnosis is cleared.",
            }
        if not nas100_demoted:
            nas100_action["action"] = "treat_nas100_m15_breakout_buy_as_the_only_clean_research_only_shadow_candidate"
            nas100_action["rationale"] = "NAS100 breakout-buy is the only checked-in Hungry Hippo config currently surviving the launch validator without a hard fail, which makes it the cleanest next expansion seam once the proof lane and config cleanup are stable."
            nas100_action["advance_when"] = "manual-review concerns are explicitly accepted and the lane remains spread-robust, reset-safe, and positive under forward shadow conditions."
            nas100_action["kill_when"] = "the lane degrades into micro-step churn, or manual-review conditions are treated as automatic promotability."
        portability_next = nas100_action
    actions.append(portability_next)

    portfolio_next = portfolio_doctrine_action(portability_board, policy_gap_board)
    if portfolio_next is not None:
        actions.append(portfolio_next)

    portfolio_seed_now_focus = ""
    if portfolio_next is not None:
        portfolio_truth = dict(portfolio_next.get("machine_truth") or {})
        portfolio_seed_now_focus = describe_seed_now_focus(
            list(portfolio_truth.get("policy_seed_now_symbols") or []),
            dict(portfolio_truth.get("policy_seed_now_asset_class_counts") or {}),
        )

    actions.append(
        {
            "category": "do_not_promote",
            "action": "do_not_graduate_btc_or_eth_archival_shapes_or_hard_blocked_symbols_to_live",
            "rationale": "Several shapes still look profitable in fragments, but the current gate and validator both say those fragments are not honest live candidates yet.",
            "machine_truth": {
                "btc_m5_step200_verdict": btc_m5_cfg["verdict"],
                "btc_symbol_gate": symbol_row(deployment_gate, "BTCUSD")["deployment_verdict"],
                "eth_archival_config_verdict": eth_step5_cfg["verdict"],
                "eth_live_discussion_block": eth_row["deployment_verdict"],
                "hard_block_symbols": ((deployment_gate.get("summary") or {}).get("hard_block_symbols") or []),
            },
            "advance_when": "a lane clears both the direct config contract and the symbol-level deployment gate with fresh forward proof.",
            "kill_when": "sample-size theater or archival shelf results get used as substitutes for current spread-safe forward proof.",
        }
    )

    rows = []
    for idx, row in enumerate(actions, start=1):
        row_with_priority = dict(row)
        row_with_priority["priority"] = idx
        rows.append(row_with_priority)

    leadership_read = [
        "The right next stack is not one thing; it is one proof lane, one cleanup wave, one honest next experiment, and a longer list of things that must not be promoted yet.",
        (
            (
                "ETH M5 step14 remains the first honest surface-reconciliation job because the step14 control must be registered and judged on the same proof lane before runtime freshness and any same-shape A/B matter."
                if not eth_registry_lane_found
                else "ETH M5 step14 remains the first honest surface-reconciliation job because the registered step14 lane exists now, but the room is still judging an orphan proof artifact instead of that lane."
                if not eth_control_board_matches_launch_surface
                else "ETH M5 step14 remains the first honest control-normalization job because runtime ladder normalization and proof quality are still missing even after the repo surface was reconciled."
                if eth_gate_verdict == "blocked_by_control_normalization"
                else "ETH M5 step14 remains the first honest surface-reconciliation job because surface reconciliation comes before runtime freshness and before any same-shape A/B."
            )
            if eth_gate_verdict == "blocked_by_surface_alignment"
            else "ETH M5 step14 is now the first honest decision fork because the aligned control already cleared its proof window with negative expectancy, so the room must choose kill-or-retune before pretending more of the same proof will help."
            if eth_negative_proof_complete and eth_recommended_step_usd is not None
            else "ETH M5 step14 remains the first honest proof-accumulation job because the launch/proof path is finally aligned and geometry is clean enough to judge, but the control sample still has not turned into positive proof."
            if eth_gate_verdict == "blocked_by_negative_expectancy"
            else "ETH M5 step14 remains the first honest proof-lane job because the launch/proof path is finally aligned and the heartbeat is fresh, but the control still needs more honest proof before the closure A/B belongs near the front of the queue."
        ),
        (
            "ETH M5 step14 is now a decision fork, not a passive proof-accumulation job: the aligned control has already cleared its proof window with negative expectancy, so the honest next move is kill-or-retune around the published ~$3.00 shadow candidate before any offensive A/B talk. GBP remains a bucket-diagnosis lane, BTC sell-tight stays on harvest-vs-escape watch, and NAS100 stays research-only until fresh-window closure dominance improves."
            if eth_negative_proof_complete and eth_recommended_step_usd is not None
            else "GBP is a bucket-diagnosis lane, BTC sell-tight is an active forward-proof watch with zero harvest closes and all closes still escape_tier2_surgical, and NAS100 should stay research-only until fresh-window closure dominance is reduced."
            if btc_forward["proof_started"] and btc_close_mix["close_mix_status"] == "zero_harvest_all_escape_so_far"
            else "GBP is a bucket-diagnosis lane, BTC sell-tight is an active forward-proof watch with a negative early sample, and NAS100 should stay research-only until fresh-window closure dominance is reduced."
            if btc_forward["proof_started"]
            else "GBP is a bucket-diagnosis lane, BTC sell-tight is a wait-for-filesystem-proof lane, and NAS100 should stay research-only until fresh-window closure dominance is reduced."
        ),
        (
            f"Small-account scaling still needs a balance-growth unlock ladder, not many simultaneous symbols: current portability says `family_portable=19`, but {portfolio_seed_now_focus} while the only waiting-forward-proof seam is NAS100."
            if portfolio_next is not None
            else None
        ),
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(LAUNCH_SAFETY_PATH.relative_to(ROOT)),
            str(DEPLOYMENT_GATE_PATH.relative_to(ROOT)),
            str(RESEARCH_BOARD_PATH.relative_to(ROOT)),
            str(PORTABILITY_BOARD_PATH.relative_to(ROOT)),
            str(POLICY_GAP_BOARD_PATH.relative_to(ROOT)),
            str(OFFENSIVE_BOARD_PATH.relative_to(ROOT)),
            str(ETH_COMPARISON_PATH.relative_to(ROOT)),
            str(ETH_CONTROL_STATE_PATH.relative_to(ROOT)),
            str(ETH_CONTROL_GATE_PATH.relative_to(ROOT)),
            str(ETH_COEFFICIENT_ANALYSIS_PATH.relative_to(ROOT)),
            str(ETH_RETUNED_STATE_PATH.relative_to(ROOT)),
            str(RESET_ALERTS_PATH.relative_to(ROOT)),
            str(AUTHORITY_STACK_PATH.relative_to(ROOT)),
            str(FRESH_WINDOW_BUCKET_PATH.relative_to(ROOT)),
            str(CLOSURE_FIREWALL_PATH.relative_to(ROOT)),
            str(VALIDATED_THEORY_QUEUE_PATH.relative_to(ROOT)),
            str(BTC_SELL_TIGHT_COMPARISON_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [item for item in leadership_read if item],
        "summary": {
            "action_count": len(rows),
            "top_priority_action": rows[0]["action"],
            "blocking_enabled_config_count": int((launch_safety.get("summary") or {}).get("blocking_enabled_config_count") or 0),
            "eth_control_closes": eth_closes,
            "eth_control_realized_net_usd": round(eth_net, 2),
        },
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Hungry Hippo Next Action Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: rank the safest strong next moves across observation, cleanup, theory testing, and anti-promotion so the room can stop merging multiple partial boards by hand.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    summary = dict(payload.get("summary") or {})
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Action count: `{summary.get('action_count', 0)}`")
    lines.append(f"- Top priority action: `{summary.get('top_priority_action', '')}`")
    lines.append(f"- Blocking enabled configs: `{summary.get('blocking_enabled_config_count', 0)}`")
    lines.append(f"- ETH control closes: `{summary.get('eth_control_closes', 0)}`")
    lines.append(f"- ETH control realized net USD: `{summary.get('eth_control_realized_net_usd', 0)}`")
    top_truth = dict((list(payload.get("rows") or [{}])[0]).get("machine_truth") or {})
    if "step3p0_closes" in top_truth:
        lines.append(f"- ETH retuned shadow closes: `{top_truth.get('step3p0_closes', 0)}`")
    if "step3p0_net" in top_truth:
        lines.append(f"- ETH retuned shadow realized net USD: `{top_truth.get('step3p0_net', 0)}`")

    lines.extend(["", "## Ranked Actions", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### {row['priority']}. {row['action']}")
        lines.append(f"- Category: `{row['category']}`")
        lines.append(f"- Rationale: {row['rationale']}")
        machine_truth = dict(row.get("machine_truth") or {})
        if machine_truth:
            truth_parts = [f"{key}={value}" for key, value in machine_truth.items()]
            lines.append(f"- Machine truth: `{'; '.join(truth_parts)}`")
        lines.append(f"- Advance when: {row['advance_when']}")
        lines.append(f"- Kill when: {row['kill_when']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    refresh_inputs()
    btc_config = merge_btc_config(
        load_optional_json(BTC_DOWNTREND_CONFIG_PATH),
        load_optional_json(BTC_DOWNTREND_V2_CONFIG_PATH),
    )

    payload = build_payload(
        load_json(LAUNCH_SAFETY_PATH),
        load_json(DEPLOYMENT_GATE_PATH),
        load_json(RESEARCH_BOARD_PATH),
        load_json(OFFENSIVE_BOARD_PATH),
        load_json(ETH_COMPARISON_PATH),
        load_json(ETH_CONTROL_STATE_PATH),
        load_json(ETH_CONTROL_GATE_PATH),
        load_json(RESET_ALERTS_PATH),
        load_text(AUTHORITY_STACK_PATH),
        load_text(FRESH_WINDOW_BUCKET_PATH),
        load_text(CLOSURE_FIREWALL_PATH),
        load_text(VALIDATED_THEORY_QUEUE_PATH),
        parse_gbp_closure_repair_compare(load_text(GBP_CLOSURE_REPAIR_COMPARE_PATH)),
        btc_config,
        load_optional_repo_json(active_state_path_from_config(btc_config)),
        load_optional_json(BTC_SELL_TIGHT_COMPARISON_PATH),
        parse_eth_step14_coefficient_analysis(load_text(ETH_COEFFICIENT_ANALYSIS_PATH) if ETH_COEFFICIENT_ANALYSIS_PATH.exists() else ""),
        load_optional_json(ETH_RETUNED_STATE_PATH),
        load_optional_json(PORTABILITY_BOARD_PATH),
        load_optional_json(POLICY_GAP_BOARD_PATH),
    )
    write_outputs(payload)
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
