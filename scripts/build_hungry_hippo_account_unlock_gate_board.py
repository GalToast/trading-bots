#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - optional runtime dependency
    mt5 = None


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DOCS = ROOT / "docs"

NEXT_ACTION_PATH = REPORTS / "hungry_hippo_next_action_board.json"
PORTABILITY_BOARD_PATH = REPORTS / "hungry_hippo_symbol_portability_board.json"
POLICY_GAP_BOARD_PATH = REPORTS / "hungry_hippo_policy_gap_board.json"
POLICY_SEED_PACKET_PATH = REPORTS / "hungry_hippo_policy_seed_packet_board.json"

INTELLIGENT_VOLUME_DESIGN_PATH = DOCS / "intelligent_volume_sizing_design.md"
INTELLIGENT_VOLUME_SYSTEM_PATH = DOCS / "intelligent-volume-system.md"

OUTPUT_JSON_PATH = REPORTS / "hungry_hippo_account_unlock_gate_board.json"
OUTPUT_MD_PATH = REPORTS / "hungry_hippo_account_unlock_gate_board.md"

MAX_PORTFOLIO_RISK_PCT = 0.10
MAX_SYMBOL_SHARE_OF_PORTFOLIO_RISK = 0.40
MAX_SYMBOL_RISK_PCT_OF_EQUITY = MAX_PORTFOLIO_RISK_PCT * MAX_SYMBOL_SHARE_OF_PORTFOLIO_RISK
DRAWDOWN_FREEZE_PCT = 0.05
DRAWDOWN_REDUCE_PCT = 0.08
DRAWDOWN_BLOCK_PCT = 0.10

PRIORITY_WEIGHT = {
    "policy_seed_now": 4,
    "policy_seed_next": 3,
    "policy_research_queue": 2,
    "policy_defer": 1,
}

CAPITAL_EFFICIENCY_TIER = {
    "fx": "high",
    "crypto": "medium",
    "index": "low",
    "commodity": "low",
}

CAPITAL_EFFICIENCY_RANK = {
    "high": 0,
    "medium": 1,
    "low": 2,
    "unknown": 3,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def find_action_row(payload: dict[str, Any], action: str) -> dict[str, Any] | None:
    for row in list(payload.get("rows") or []):
        if str(row.get("action") or "") == action:
            return dict(row)
    return None


def symbol_row(payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    for row in list(payload.get("rows") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return dict(row)
    return None


def policy_seed_rows(
    policy_seed_payload: dict[str, Any],
    portability_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in list(policy_seed_payload.get("rows") or []):
        clean_row = dict(row)
        if str(clean_row.get("priority") or "") not in {"policy_seed_now", "policy_seed_next"}:
            continue
        port_row = symbol_row(portability_payload, str(clean_row.get("symbol") or ""))
        generalization_status = str((port_row or {}).get("generalization_status") or "")
        if port_row is not None and generalization_status != "portable_missing_policy":
            continue
        rows.append(clean_row)
    return rows


def pick_lead_symbol(
    next_action_payload: dict[str, Any],
    portability_payload: dict[str, Any],
) -> dict[str, Any] | None:
    ladder_row = find_action_row(next_action_payload, "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout")
    machine_truth = dict((ladder_row or {}).get("machine_truth") or {})
    lead_symbol = str(machine_truth.get("lead_forward_proof_symbol") or "")
    if lead_symbol:
        row = symbol_row(portability_payload, lead_symbol)
        if row is not None:
            return row

    portability_summary = dict(portability_payload.get("summary") or {})
    for symbol in list(portability_summary.get("waiting_forward_proof_symbols") or []):
        row = symbol_row(portability_payload, str(symbol))
        if row is not None:
            return row
    for symbol in list(portability_summary.get("ready_for_shadow_discussion_symbols") or []):
        row = symbol_row(portability_payload, str(symbol))
        if row is not None:
            return row
    rows = list(portability_payload.get("rows") or [])
    return dict(rows[0]) if rows else None


def missing_launch_contract_rows(
    portability_payload: dict[str, Any],
    *,
    promotable_only: bool,
    include_manual_review: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in list(portability_payload.get("rows") or []):
        clean_row = dict(row)
        if str(clean_row.get("generalization_status") or "") != "portable_missing_launch_contract":
            continue
        deployment_verdict = str(clean_row.get("deployment_verdict") or "")
        guardrail_status = str(clean_row.get("guardrail_status") or "")
        if not include_manual_review and deployment_verdict == "manual_review":
            continue
        if promotable_only:
            if deployment_verdict != "cleared_for_shadow_discussion":
                continue
            if guardrail_status != "promotable_now":
                continue
        rows.append(clean_row)
    return rows


def promotable_missing_launch_contract_rows(portability_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return missing_launch_contract_rows(
        portability_payload,
        promotable_only=True,
    )


def secondary_missing_launch_contract_rows(portability_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return missing_launch_contract_rows(
        portability_payload,
        promotable_only=False,
        include_manual_review=False,
    )


def ready_for_shadow_discussion_rows(portability_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in list(portability_payload.get("rows") or []):
        clean_row = dict(row)
        if str(clean_row.get("generalization_status") or "") != "ready_for_shadow_discussion":
            continue
        if str(clean_row.get("deployment_verdict") or "") != "cleared_for_shadow_discussion":
            continue
        if str(clean_row.get("guardrail_status") or "") != "promotable_now":
            continue
        rows.append(clean_row)
    return rows


def margin_cost_usd(margin_snapshot: dict[str, Any] | None, symbol: str) -> float:
    if not margin_snapshot:
        return float("inf")
    row = dict(margin_snapshot.get(str(symbol or "").upper()) or {})
    buy = row.get("buy_margin")
    sell = row.get("sell_margin")
    values = [float(value) for value in (buy, sell) if value is not None]
    return min(values) if values else float("inf")


def collect_margin_snapshot(symbols: list[str]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    if mt5 is None:
        return snapshot
    if not mt5.initialize():
        return snapshot
    try:
        for symbol in symbols:
            clean_symbol = str(symbol or "").upper()
            if not clean_symbol:
                continue
            info = mt5.symbol_info(clean_symbol)
            tick = mt5.symbol_info_tick(clean_symbol)
            if info is None or tick is None:
                continue
            volume_min = float(info.volume_min or 0.0)
            if volume_min <= 0:
                continue
            ask = float(tick.ask or 0.0)
            bid = float(tick.bid or 0.0)
            snapshot[clean_symbol] = {
                "volume_min": volume_min,
                "buy_margin": mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, clean_symbol, volume_min, ask),
                "sell_margin": mt5.order_calc_margin(mt5.ORDER_TYPE_SELL, clean_symbol, volume_min, bid),
                "currency_margin": str(info.currency_margin or ""),
            }
    finally:
        mt5.shutdown()
    return snapshot


def sort_seed_rows(rows: list[dict[str, Any]], margin_snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return sorted(
        [dict(row) for row in rows],
        key=lambda row: (
            PRIORITY_WEIGHT.get(str(row.get("priority") or ""), 0),
            -margin_cost_usd(margin_snapshot, str(row.get("symbol") or "")),
            float(row.get("priority_score") or 0),
            float(row.get("evidence_closes") or 0),
            float(row.get("evidence_net_usd") or 0.0),
        ),
        reverse=True,
    )


def sort_launch_contract_rows(rows: list[dict[str, Any]], margin_snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return sorted(
        [dict(row) for row in rows],
        key=lambda row: (
            CAPITAL_EFFICIENCY_RANK.get(capital_efficiency_tier(str(row.get("asset_class") or "")), 3),
            margin_cost_usd(margin_snapshot, str(row.get("symbol") or "")),
            str(row.get("symbol") or ""),
        ),
    )


def pick_next_seed(
    rows: list[dict[str, Any]],
    used_symbols: set[str],
    used_asset_classes: set[str],
    *,
    prefer_new_asset_class: bool,
    preferred_asset_classes: set[str] | None = None,
    margin_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    remaining = [dict(row) for row in sort_seed_rows(rows, margin_snapshot) if str(row.get("symbol") or "").upper() not in used_symbols]
    if not remaining:
        return None
    if preferred_asset_classes:
        preferred = [row for row in remaining if str(row.get("asset_class") or "").lower() in preferred_asset_classes]
        if preferred:
            remaining = preferred
    if prefer_new_asset_class:
        for row in remaining:
            if str(row.get("asset_class") or "").lower() not in used_asset_classes:
                return row
    return remaining[0]


def pick_next_launch_contract(
    rows: list[dict[str, Any]],
    used_symbols: set[str],
    *,
    preferred_asset_classes: set[str] | None = None,
    margin_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    remaining = [
        dict(row)
        for row in sort_launch_contract_rows(rows, margin_snapshot)
        if str(row.get("symbol") or "").upper() not in used_symbols
    ]
    if not remaining:
        return None
    if preferred_asset_classes:
        preferred = [row for row in remaining if str(row.get("asset_class") or "").lower() in preferred_asset_classes]
        if preferred:
            return preferred[0]
    return remaining[0]


def describe_seed_blocker(seed_row: dict[str, Any]) -> tuple[str, str]:
    action = str(seed_row.get("suggested_seed_action") or "")
    if action == "seed_regime_and_selector":
        return (
            "blocked_missing_regime_and_selector",
            "Canonical regime and selector policy are both missing, even though rearm truth already exists.",
        )
    if action == "seed_regime_row_then_reconcile_selector":
        return (
            "blocked_missing_regime_and_rearm_alignment",
            "Selector truth exists, but regime truth is still missing and the symbol cannot be unlocked honestly yet.",
        )
    if action == "seed_selector_profile_from_regime_truth":
        return (
            "blocked_missing_selector",
            "Regime and rearm truth exist, but the selector surface is still missing.",
        )
    if action == "reconcile_existing_policy_surfaces":
        return (
            "blocked_policy_reconciliation",
            "Canonical surfaces exist, but they still need reconciliation before the symbol belongs in the unlock ladder.",
        )
    return (
        "blocked_missing_policy_bundle",
        "Canonical policy coverage is still incomplete.",
    )


def describe_launch_contract_blocker(symbol_row: dict[str, Any]) -> tuple[str, str]:
    symbol = str(symbol_row.get("symbol") or "")
    deployment_verdict = str(symbol_row.get("deployment_verdict") or "")
    if deployment_verdict == "missing":
        return (
            "blocked_missing_launch_contract_followthrough",
            f"Policy exists for `{symbol}`, but deployment and runnable launch-contract coverage are still incomplete.",
        )
    if deployment_verdict == "manual_review":
        reasons = list(symbol_row.get("manual_review_reasons") or [])
        suffix = f" ({', '.join(reasons)})" if reasons else ""
        return (
            "blocked_launch_contract_manual_review",
            f"Policy exists for `{symbol}`, but launch-contract follow-through is still blocked by manual review{suffix}.",
        )
    return (
        "blocked_missing_launch_contract_followthrough",
        f"Canonical policy and guardrail review are already clear for `{symbol}`, but there is still no checked-in runnable launch contract.",
    )


def describe_ready_for_shadow_discussion_blocker(symbol_row: dict[str, Any]) -> tuple[str, str]:
    return (
        "blocked_waiting_forward_shadow_proof",
        f"`{str(symbol_row.get('symbol') or '')}` now has policy, guardrails, and a checked-in launch contract, but it still needs a fresh forward shadow sample before it belongs in the small-account unlock ladder.",
    )


def build_slot_row(
    slot: int,
    symbol: str,
    asset_class: str,
    source_status: str,
    current_status: str,
    blocker_reason: str,
    unlock_when: str,
    kill_when: str,
    machine_truth: dict[str, Any],
) -> dict[str, Any]:
    return {
        "slot": slot,
        "symbol": symbol,
        "asset_class": asset_class,
        "source_status": source_status,
        "current_status": current_status,
        "blocker_reason": blocker_reason,
        "unlock_when": unlock_when,
        "kill_when": kill_when,
        "machine_truth": machine_truth,
    }


def capital_efficiency_tier(asset_class: str) -> str:
    return CAPITAL_EFFICIENCY_TIER.get(str(asset_class or "").lower(), "unknown")


def build_payload(
    next_action_payload: dict[str, Any],
    portability_payload: dict[str, Any],
    policy_gap_payload: dict[str, Any],
    policy_seed_payload: dict[str, Any],
    margin_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ladder_row = find_action_row(next_action_payload, "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout")
    ladder_truth = dict((ladder_row or {}).get("machine_truth") or {})
    proof_lead_row = pick_lead_symbol(next_action_payload, portability_payload)
    ready_rows = ready_for_shadow_discussion_rows(portability_payload)
    launch_contract_rows = promotable_missing_launch_contract_rows(portability_payload)
    secondary_launch_contract_gap_rows = secondary_missing_launch_contract_rows(portability_payload)
    seed_rows = policy_seed_rows(policy_seed_payload, portability_payload)
    seed_now_rows = [dict(row) for row in seed_rows if str(row.get("priority") or "") == "policy_seed_now"]
    starter_ready_row = pick_next_launch_contract(
        ready_rows,
        set(),
        preferred_asset_classes={"fx"},
        margin_snapshot=margin_snapshot,
    )
    starter_contract_row = pick_next_launch_contract(
        launch_contract_rows,
        set(),
        preferred_asset_classes={"fx"},
        margin_snapshot=margin_snapshot,
    )
    starter_row = starter_ready_row or starter_contract_row or pick_next_seed(
        seed_now_rows or seed_rows,
        set(),
        set(),
        prefer_new_asset_class=False,
        preferred_asset_classes={"fx"},
        margin_snapshot=margin_snapshot,
    )
    if starter_row is None and proof_lead_row is not None:
        starter_row = {
            "symbol": str(proof_lead_row.get("symbol") or ""),
            "asset_class": str(proof_lead_row.get("asset_class") or ""),
            "priority": "proof_lead_fallback",
            "priority_score": 0,
            "suggested_seed_action": "wait_for_forward_proof",
            "family_default_timeframe": "",
            "family_default_base_step": None,
            "evidence_net_usd": None,
            "evidence_closes": None,
        }

    rows: list[dict[str, Any]] = []
    used_symbols: set[str] = set()
    used_asset_classes: set[str] = set()

    if starter_row is not None:
        lead_symbol = str(starter_row.get("symbol") or "")
        lead_asset_class = str(starter_row.get("asset_class") or "")
        used_symbols.add(lead_symbol.upper())
        used_asset_classes.add(lead_asset_class.lower())
        starter_from_proof_lead = bool(proof_lead_row) and str(proof_lead_row.get("symbol") or "").upper() == lead_symbol.upper()
        starter_from_ready_portability = starter_ready_row is not None and str(starter_ready_row.get("symbol") or "").upper() == lead_symbol.upper()
        starter_from_launch_contract = starter_contract_row is not None and str(starter_contract_row.get("symbol") or "").upper() == lead_symbol.upper()
        lead_status = (
            str(proof_lead_row.get("generalization_status") or "")
            if starter_from_proof_lead and proof_lead_row is not None
            else str(starter_ready_row.get("generalization_status") or "")
            if starter_from_ready_portability and starter_ready_row is not None
            else str(starter_contract_row.get("generalization_status") or "")
            if starter_from_launch_contract and starter_contract_row is not None
            else "portable_missing_policy"
            if str(starter_row.get("priority") or "").startswith("policy_seed")
            else "starter_candidate"
        )
        blocker_reason = "No fully-ready lead symbol exists yet."
        current_status = "blocked_until_first_honest_candidate_exists"
        unlock_when = "Fresh forward proof clears the lead symbol, the control window is no longer closure-dominated, and active-set drawdown remains below the 5% freeze threshold."
        kill_when = "A short green streak, portability alone, or unresolved closure dominance gets treated as permission to activate the lead symbol on a tiny account."
        if starter_from_proof_lead and lead_status == "portable_waiting_forward_proof":
            current_status = "blocked_waiting_forward_proof"
            blocker_reason = "Lead slot is still waiting on fresh forward proof and cannot be treated as live-ready yet."
        elif starter_from_ready_portability and starter_ready_row is not None:
            current_status, blocker_reason = describe_ready_for_shadow_discussion_blocker(starter_ready_row)
            unlock_when = "A checked-in shadow contract for this symbol is live on current code, it accumulates a clean forward shadow sample without violating the drawdown freeze gate, and the active set still stays below the 5% freeze threshold."
            kill_when = "A starter symbol is treated as unlocked just because the stack is complete on paper while forward shadow proof is still missing."
        elif starter_from_launch_contract and starter_contract_row is not None:
            current_status, blocker_reason = describe_launch_contract_blocker(starter_contract_row)
            unlock_when = "A checked-in launch contract exists for this symbol, it passes the current Hungry Hippo launch-safety validator, the starter lane clears shadow proof honestly, and active-set floating drawdown stays below the 5% freeze threshold."
            kill_when = "A small-account starter is assumed ready just because policy and guardrails cleared while the runnable launch contract is still missing."
        elif str(starter_row.get("priority") or "") == "policy_seed_now":
            current_status = "blocked_small_account_starter_missing_policy"
            blocker_reason = "Small-account starter should favor capital-efficient symbols, but the best current starter candidate is still missing canonical policy coverage."
            unlock_when = "Canonical regime and selector policy are seeded for this starter candidate, the starter lane clears shadow proof honestly, and active-set floating drawdown stays below the 5% freeze threshold."
            kill_when = "A capital-efficient symbol gets promoted to starter status before its missing policy surfaces are seeded and proved."
        rows.append(
            build_slot_row(
                1,
                lead_symbol,
                lead_asset_class,
                "ready_for_shadow_discussion" if starter_from_ready_portability else "launch_contract_followthrough" if starter_from_launch_contract else "small_account_starter" if not starter_from_proof_lead else "portability",
                current_status,
                blocker_reason,
                unlock_when,
                kill_when,
                {
                    "generalization_status": lead_status,
                    "capital_efficiency_tier": capital_efficiency_tier(lead_asset_class),
                    "estimated_min_lot_margin_usd": margin_cost_usd(margin_snapshot, lead_symbol),
                    "proof_lead_symbol": str((proof_lead_row or {}).get("symbol") or ""),
                    "starter_from_proof_lead": starter_from_proof_lead,
                    "starter_from_ready_for_shadow_discussion": starter_from_ready_portability,
                    "starter_from_launch_contract_followthrough": starter_from_launch_contract,
                    "starter_priority": starter_row.get("priority"),
                    "starter_suggested_seed_action": starter_row.get("suggested_seed_action"),
                    "highest_leverage_gap": (starter_ready_row or starter_contract_row or proof_lead_row or {}).get("highest_leverage_gap"),
                    "guardrail_status": (starter_ready_row or starter_contract_row or proof_lead_row or {}).get("guardrail_status"),
                    "deployment_verdict": (starter_ready_row or starter_contract_row or proof_lead_row or {}).get("deployment_verdict"),
                    "launch_contract_count": int((starter_ready_row or starter_contract_row or proof_lead_row or {}).get("launch_contract_count") or 0),
                    "lead_from_next_action": str(ladder_truth.get("lead_forward_proof_symbol") or "") == str((proof_lead_row or {}).get("symbol") or ""),
                },
            )
        )

    slot2_ready = pick_next_launch_contract(
        ready_rows,
        used_symbols,
        preferred_asset_classes={"fx"},
        margin_snapshot=margin_snapshot,
    )
    slot2_contract = pick_next_launch_contract(
        launch_contract_rows,
        used_symbols,
        preferred_asset_classes={"fx"},
        margin_snapshot=margin_snapshot,
    )
    slot2 = slot2_ready or slot2_contract or pick_next_seed(seed_rows, used_symbols, used_asset_classes, prefer_new_asset_class=False, preferred_asset_classes={"fx"}, margin_snapshot=margin_snapshot)
    if slot2 is not None:
        symbol = str(slot2.get("symbol") or "")
        asset_class = str(slot2.get("asset_class") or "")
        used_symbols.add(symbol.upper())
        used_asset_classes.add(asset_class.lower())
        if slot2_ready is not None and symbol.upper() == str(slot2_ready.get("symbol") or "").upper():
            status, blocker = describe_ready_for_shadow_discussion_blocker(slot2_ready)
            source_status = "ready_for_shadow_discussion"
            unlock_when = "Slot #1 is live and positive over a meaningful forward sample, this second starter-ready FX symbol also survives its first forward shadow window, and combined active-set floating drawdown stays below 5% of equity."
            kill_when = "A second starter-ready FX lane is treated as unlocked before the first slot proves it can compound inside the tiny-account drawdown budget."
            machine_truth = {
                "generalization_status": slot2_ready.get("generalization_status"),
                "highest_leverage_gap": slot2_ready.get("highest_leverage_gap"),
                "guardrail_status": slot2_ready.get("guardrail_status"),
                "deployment_verdict": slot2_ready.get("deployment_verdict"),
                "capital_efficiency_tier": capital_efficiency_tier(asset_class),
                "estimated_min_lot_margin_usd": margin_cost_usd(margin_snapshot, symbol),
                "launch_contract_count": int(slot2_ready.get("launch_contract_count") or 0),
            }
        elif slot2_contract is not None and symbol.upper() == str(slot2_contract.get("symbol") or "").upper():
            status, blocker = describe_launch_contract_blocker(slot2_contract)
            source_status = "launch_contract_followthrough"
            unlock_when = "Slot #1 is live and positive over a meaningful forward sample, this second cleared FX symbol has a checked-in validated launch contract, and the combined active-set floating drawdown stays below 5% of equity."
            kill_when = "A second cheap FX lane is added before the first slot proves it can compound without consuming the small-account drawdown budget."
            machine_truth = {
                "generalization_status": slot2_contract.get("generalization_status"),
                "highest_leverage_gap": slot2_contract.get("highest_leverage_gap"),
                "guardrail_status": slot2_contract.get("guardrail_status"),
                "deployment_verdict": slot2_contract.get("deployment_verdict"),
                "capital_efficiency_tier": capital_efficiency_tier(asset_class),
                "estimated_min_lot_margin_usd": margin_cost_usd(margin_snapshot, symbol),
                "launch_contract_count": int(slot2_contract.get("launch_contract_count") or 0),
            }
        else:
            status, blocker = describe_seed_blocker(slot2)
            source_status = "policy_seed_packet"
            unlock_when = "Slot #1 is live and positive over a meaningful forward sample, trailing active-set floating drawdown stays below 5% of equity, and the second FX lane fits inside the remaining portfolio risk budget after the 4%-of-equity per-symbol sub-cap."
            kill_when = "A second symbol is added before the first symbol proves it can compound without consuming the full small-account drawdown budget."
            machine_truth = {
                "priority": slot2.get("priority"),
                "priority_score": slot2.get("priority_score"),
                "suggested_seed_action": slot2.get("suggested_seed_action"),
                "capital_efficiency_tier": capital_efficiency_tier(asset_class),
                "estimated_min_lot_margin_usd": margin_cost_usd(margin_snapshot, symbol),
                "family_default_timeframe": slot2.get("family_default_timeframe"),
                "family_default_base_step": slot2.get("family_default_base_step"),
                "evidence_net_usd": slot2.get("evidence_net_usd"),
                "evidence_closes": slot2.get("evidence_closes"),
            }
        rows.append(
            build_slot_row(
                2,
                symbol,
                asset_class,
                source_status,
                status,
                blocker,
                unlock_when,
                kill_when,
                machine_truth,
            )
        )

    slot3_contract = pick_next_launch_contract(
        secondary_launch_contract_gap_rows,
        used_symbols,
        preferred_asset_classes={"fx"},
        margin_snapshot=margin_snapshot,
    )
    slot3 = slot3_contract or pick_next_seed(seed_rows, used_symbols, used_asset_classes, prefer_new_asset_class=False, preferred_asset_classes={"fx"}, margin_snapshot=margin_snapshot)
    if slot3 is not None:
        symbol = str(slot3.get("symbol") or "")
        asset_class = str(slot3.get("asset_class") or "")
        used_symbols.add(symbol.upper())
        used_asset_classes.add(asset_class.lower())
        if slot3_contract is not None and symbol.upper() == str(slot3_contract.get("symbol") or "").upper():
            status, blocker = describe_launch_contract_blocker(slot3_contract)
            source_status = "launch_contract_followthrough"
            machine_truth = {
                "generalization_status": slot3_contract.get("generalization_status"),
                "highest_leverage_gap": slot3_contract.get("highest_leverage_gap"),
                "guardrail_status": slot3_contract.get("guardrail_status"),
                "deployment_verdict": slot3_contract.get("deployment_verdict"),
                "capital_efficiency_tier": capital_efficiency_tier(asset_class),
                "estimated_min_lot_margin_usd": margin_cost_usd(margin_snapshot, symbol),
                "launch_contract_count": int(slot3_contract.get("launch_contract_count") or 0),
            }
        else:
            status, blocker = describe_seed_blocker(slot3)
            source_status = "policy_seed_packet"
            machine_truth = {
                "priority": slot3.get("priority"),
                "priority_score": slot3.get("priority_score"),
                "suggested_seed_action": slot3.get("suggested_seed_action"),
                "capital_efficiency_tier": capital_efficiency_tier(asset_class),
                "estimated_min_lot_margin_usd": margin_cost_usd(margin_snapshot, symbol),
                "family_default_timeframe": slot3.get("family_default_timeframe"),
                "family_default_base_step": slot3.get("family_default_base_step"),
                "evidence_net_usd": slot3.get("evidence_net_usd"),
                "evidence_closes": slot3.get("evidence_closes"),
                "fx_first_small_account_preference": True,
            }
        rows.append(
            build_slot_row(
                3,
                symbol,
                asset_class,
                source_status,
                status,
                blocker + " Slot #3 still prefers another capital-efficient FX lane before heavier-margin classes on a tiny account.",
                "Slots #1 and #2 are both proven under forward conditions, combined floating drawdown stays below 5% of equity, and the third FX lane still fits inside the 10% portfolio cap without forcing the account into heavier-margin classes too early.",
                "A third lane is taken from a heavier-margin class before the account can honestly afford it just because cross-class breadth sounds safer.",
                machine_truth,
            )
        )

    slot4 = pick_next_seed(seed_rows, used_symbols, used_asset_classes, prefer_new_asset_class=False, preferred_asset_classes={"fx"}, margin_snapshot=margin_snapshot)
    if slot4 is None:
        slot4 = pick_next_seed(seed_rows, used_symbols, used_asset_classes, prefer_new_asset_class=False, margin_snapshot=margin_snapshot)
    if slot4 is not None:
        used_symbols.add(str(slot4.get("symbol") or "").upper())
        used_asset_classes.add(str(slot4.get("asset_class") or "").lower())
        symbol = str(slot4.get("symbol") or "")
        asset_class = str(slot4.get("asset_class") or "")
        status, blocker = describe_seed_blocker(slot4)
        rows.append(
            build_slot_row(
                4,
                symbol,
                asset_class,
                "policy_seed_packet",
                status,
                blocker,
                "Slots #1-#3 are already proven, the active set still sits below the 5% freeze threshold with reserve budget to spare, and adding a fourth lane does not breach the 10% portfolio cap or push the book into unaffordable margin classes too early.",
                "A fourth lane is added because the account is larger on paper while the active set has not actually demonstrated spare drawdown capacity.",
                {
                    "priority": slot4.get("priority"),
                    "priority_score": slot4.get("priority_score"),
                    "suggested_seed_action": slot4.get("suggested_seed_action"),
                    "capital_efficiency_tier": capital_efficiency_tier(asset_class),
                    "estimated_min_lot_margin_usd": margin_cost_usd(margin_snapshot, symbol),
                    "family_default_timeframe": slot4.get("family_default_timeframe"),
                    "family_default_base_step": slot4.get("family_default_base_step"),
                    "evidence_net_usd": slot4.get("evidence_net_usd"),
                    "evidence_closes": slot4.get("evidence_closes"),
                },
            )
        )

    slot5 = pick_next_seed(seed_rows, used_symbols, used_asset_classes, prefer_new_asset_class=False, preferred_asset_classes={"crypto"}, margin_snapshot=margin_snapshot)
    if slot5 is None:
        slot5 = pick_next_seed(seed_rows, used_symbols, used_asset_classes, prefer_new_asset_class=False, margin_snapshot=margin_snapshot)
    if slot5 is not None:
        symbol = str(slot5.get("symbol") or "")
        asset_class = str(slot5.get("asset_class") or "")
        status, blocker = describe_seed_blocker(slot5)
        rows.append(
            build_slot_row(
                5,
                symbol,
                asset_class,
                "policy_seed_packet",
                status,
                blocker + " Slot #5 prefers smaller nailed-down crypto lanes before heavier-margin index or commodity classes.",
                "Slots #1-#4 are already proven, the active set still sits below the 5% freeze threshold, and the first crypto add is one of the smaller portable names with enough policy and proof progress to justify medium-efficiency expansion before heavy-margin classes.",
                "A heavy-margin class is added before the first smaller crypto lane is actually nailed down.",
                {
                    "priority": slot5.get("priority"),
                    "priority_score": slot5.get("priority_score"),
                    "suggested_seed_action": slot5.get("suggested_seed_action"),
                    "capital_efficiency_tier": capital_efficiency_tier(asset_class),
                    "estimated_min_lot_margin_usd": margin_cost_usd(margin_snapshot, symbol),
                    "family_default_timeframe": slot5.get("family_default_timeframe"),
                    "family_default_base_step": slot5.get("family_default_base_step"),
                    "evidence_net_usd": slot5.get("evidence_net_usd"),
                    "evidence_closes": slot5.get("evidence_closes"),
                    "small_crypto_before_heavy_margin": True,
                },
            )
        )

    current_unlocked_slots = 0
    for row in rows:
        if str(row.get("current_status") or "").startswith("blocked"):
            break
        current_unlocked_slots += 1

    portability_summary = dict(portability_payload.get("summary") or {})
    policy_gap_summary = dict(policy_gap_payload.get("summary") or {})
    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(NEXT_ACTION_PATH.relative_to(ROOT)),
            str(PORTABILITY_BOARD_PATH.relative_to(ROOT)),
            str(POLICY_GAP_BOARD_PATH.relative_to(ROOT)),
            str(POLICY_SEED_PACKET_PATH.relative_to(ROOT)),
            str(INTELLIGENT_VOLUME_DESIGN_PATH.relative_to(ROOT)),
            str(INTELLIGENT_VOLUME_SYSTEM_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "current_unlocked_slot_count": current_unlocked_slots,
            "planned_slot_count": len(rows),
            "growth_ladder_symbols": [str(row.get("symbol") or "") for row in rows],
            "lead_symbol": str((rows[0] if rows else {}).get("symbol") or ""),
            "proof_lead_symbol": str((proof_lead_row or {}).get("symbol") or ""),
            "proof_lead_estimated_min_lot_margin_usd": margin_cost_usd(margin_snapshot, str((proof_lead_row or {}).get("symbol") or "")),
            "ready_for_shadow_discussion_symbols": [str(row.get("symbol") or "") for row in sort_launch_contract_rows(ready_rows, margin_snapshot)],
            "promotable_missing_launch_contract_symbols": [str(row.get("symbol") or "") for row in sort_launch_contract_rows(launch_contract_rows, margin_snapshot)],
            "seed_now_policy_symbols": list(policy_gap_summary.get("policy_seed_now_symbols") or []),
            "seed_next_policy_symbols": list(policy_gap_summary.get("policy_seed_next_symbols") or []),
            "max_portfolio_risk_pct": MAX_PORTFOLIO_RISK_PCT,
            "max_symbol_risk_pct_of_equity": round(MAX_SYMBOL_RISK_PCT_OF_EQUITY, 4),
            "drawdown_freeze_pct": DRAWDOWN_FREEZE_PCT,
            "drawdown_reduce_pct": DRAWDOWN_REDUCE_PCT,
            "drawdown_block_pct": DRAWDOWN_BLOCK_PCT,
            "same_class_seed_now_warning": len({str(row.get("asset_class") or "").lower() for row in seed_now_rows}) == 1 and bool(seed_now_rows),
            "policy_seed_now_asset_class_counts": {
                asset_class: sum(1 for row in seed_now_rows if str(row.get("asset_class") or "").lower() == asset_class)
                for asset_class in sorted({str(row.get("asset_class") or "").lower() for row in seed_now_rows if str(row.get("asset_class") or "")})
            },
            "starter_doctrine": "starter_ready_fx_forward_proof_then_fx_first_until_heavier_margin_classes_are_affordable",
        },
        "leadership_read": [
            f"No symbol is honestly unlocked today: the lead slot is still `{str((rows[0] if rows else {}).get('current_status') or 'unavailable')}`.",
            f"Proof lead and small-account starter are not the same thing right now: proof lead is `{str((proof_lead_row or {}).get('symbol') or 'none')}` at about `${margin_cost_usd(margin_snapshot, str((proof_lead_row or {}).get('symbol') or '')):.2f}` min-lot margin, while the starter ladder is `{[str(row.get('symbol') or '') for row in rows]}` and is ordered by full-stack follow-through first, then actual margin cost and remaining policy debt.",
            f"Account-growth gates are grounded in existing repo risk doctrine: freeze near `{DRAWDOWN_FREEZE_PCT:.0%}`, reduce near `{DRAWDOWN_REDUCE_PCT:.0%}`, block near `{DRAWDOWN_BLOCK_PCT:.0%}`, with a `{MAX_SYMBOL_RISK_PCT_OF_EQUITY:.0%}`-of-equity per-symbol sub-cap inside the `{MAX_PORTFOLIO_RISK_PCT:.0%}` portfolio cap.",
            f"Immediate starter-ready follow-through is `{[str(row.get('symbol') or '') for row in sort_launch_contract_rows(ready_rows, margin_snapshot)]}`, missing-launch-contract follow-through is `{[str(row.get('symbol') or '') for row in sort_launch_contract_rows(launch_contract_rows, margin_snapshot)]}`, and remaining seed-now policy coverage is `{list(policy_gap_summary.get('policy_seed_now_symbols') or [])}`.",
        ],
        "rows": rows,
        "notes": [
            "This board is an account-growth doctrine surface. It does not authorize live deployment by itself.",
            "Slot order is driven by current portability, policy-gap, and seed-packet truth, then constrained by existing drawdown and portfolio-risk doctrine.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Hungry Hippo Account Unlock Gate Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: translate the balance-growth unlock doctrine into a concrete slot order and drawdown-gated ladder for symbol additions.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Summary", ""])
    lines.append(f"- Current unlocked slots: `{summary.get('current_unlocked_slot_count', 0)}`")
    lines.append(f"- Planned slot count: `{summary.get('planned_slot_count', 0)}`")
    lines.append(f"- Growth ladder: `{summary.get('growth_ladder_symbols', [])}`")
    lines.append(f"- Ready-for-shadow-discussion symbols: `{summary.get('ready_for_shadow_discussion_symbols', [])}`")
    lines.append(f"- Launch-contract follow-through symbols: `{summary.get('promotable_missing_launch_contract_symbols', [])}`")
    lines.append(f"- Max portfolio risk: `{summary.get('max_portfolio_risk_pct', 0):.0%}`")
    lines.append(f"- Max symbol risk of equity: `{summary.get('max_symbol_risk_pct_of_equity', 0):.0%}`")
    lines.append(f"- Drawdown freeze / reduce / block: `{summary.get('drawdown_freeze_pct', 0):.0%} / {summary.get('drawdown_reduce_pct', 0):.0%} / {summary.get('drawdown_block_pct', 0):.0%}`")

    lines.extend(["", "## Unlock Ladder", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### Slot {row['slot']}: {row['symbol']}")
        lines.append(f"- Asset class: `{row['asset_class']}`")
        lines.append(f"- Source status: `{row['source_status']}`")
        lines.append(f"- Current status: `{row['current_status']}`")
        lines.append(f"- Blocker: {row['blocker_reason']}")
        truth = dict(row.get("machine_truth") or {})
        if truth:
            lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in truth.items())}`")
        lines.append(f"- Unlock when: {row['unlock_when']}")
        lines.append(f"- Kill when: {row['kill_when']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    next_action = load_json(NEXT_ACTION_PATH)
    portability = load_json(PORTABILITY_BOARD_PATH)
    policy_gap = load_json(POLICY_GAP_BOARD_PATH)
    policy_seed = load_json(POLICY_SEED_PACKET_PATH)
    symbols = [
        str((find_action_row(next_action, "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout") or {}).get("machine_truth", {}).get("lead_forward_proof_symbol") or "")
    ]
    symbols.extend(
        str(row.get("symbol") or "")
        for row in promotable_missing_launch_contract_rows(portability)
    )
    symbols.extend(str(row.get("symbol") or "") for row in list(policy_seed.get("rows") or []))
    payload = build_payload(
        next_action,
        portability,
        policy_gap,
        policy_seed,
        collect_margin_snapshot(symbols),
    )
    write_outputs(payload)
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
