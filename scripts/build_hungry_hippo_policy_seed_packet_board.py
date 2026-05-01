#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
POLICY_GAP_PATH = REPORTS / "hungry_hippo_policy_gap_board.json"
REGIME_SIGNAL_PATH = REPORTS / "regime_signal.json"
REARM_PARAMS_PATH = REPORTS / "hungry_hippo_rearm_params.json"
PERSONALITY_SELECTOR_PATH = REPORTS / "hungry_hippo_personality_selector.json"
OUT_JSON = REPORTS / "hungry_hippo_policy_seed_packet_board.json"
OUT_MD = REPORTS / "hungry_hippo_policy_seed_packet_board.md"

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from hungry_hippo_symbol_profiles import default_session_profile_for_symbol, runtime_defaults_for_symbol


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def regime_rows_by_symbol(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in list(payload.get("rows") or []):
        symbol = normalize_symbol(row.get("symbol"))
        if symbol:
            out[symbol] = dict(row)
    return out


def choose_seed_action(
    *,
    regime_present: bool,
    selector_present: bool,
    rearm_present: bool,
) -> tuple[str, str]:
    if not regime_present and not selector_present and not rearm_present:
        return "seed_regime_selector_and_rearm_bundle", "No canonical policy surface exists yet."
    if not regime_present and not selector_present:
        return "seed_regime_and_selector", "Rearm exists, but canonical regime and selector policy are both missing."
    if not regime_present and selector_present:
        return "seed_regime_row_then_reconcile_selector", "Selector exists, but canonical regime truth is still missing."
    if regime_present and not selector_present:
        return "seed_selector_profile_from_regime_truth", "Regime/rearm truth exists; the missing piece is selector policy."
    if regime_present and selector_present and not rearm_present:
        return "seed_rearm_policy_from_existing_regime_and_selector", "Regime and selector exist, but rearm policy is missing."
    return "reconcile_existing_policy_surfaces", "Canonical surfaces exist but still need reconciliation."


def build_row(
    policy_row: dict[str, Any],
    regime_rows: dict[str, dict[str, Any]],
    rearm_rows: dict[str, dict[str, Any]],
    selector_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    symbol = str(policy_row.get("symbol") or "")
    regime_row = regime_rows.get(symbol)
    rearm_row = rearm_rows.get(symbol)
    selector_row = selector_rows.get(symbol)
    runtime_defaults = runtime_defaults_for_symbol(symbol)
    session_defaults = default_session_profile_for_symbol(symbol)

    regime_present = regime_row is not None
    selector_present = selector_row is not None
    rearm_present = rearm_row is not None
    seed_action, seed_rationale = choose_seed_action(
        regime_present=regime_present,
        selector_present=selector_present,
        rearm_present=rearm_present,
    )

    return {
        "symbol": symbol,
        "asset_class": str(policy_row.get("asset_class") or runtime_defaults.get("asset_class") or ""),
        "priority": str(policy_row.get("priority") or ""),
        "priority_score": int(policy_row.get("priority_score") or 0),
        "regime_row_present": regime_present,
        "selector_row_present": selector_present,
        "rearm_row_present": rearm_present,
        "current_regime_control_mode": str((regime_row or {}).get("control_mode") or ""),
        "current_regime_action_bias": str((regime_row or {}).get("action_bias") or ""),
        "current_selector_personality": str((selector_row or {}).get("personality") or ""),
        "current_selector_alpha": (selector_row or {}).get("alpha"),
        "current_rearm_guardrail_status": str((rearm_row or {}).get("canonical_guardrail_status") or ""),
        "current_rearm_variant": str((rearm_row or {}).get("rearm_variant") or ""),
        "family_default_timeframe": str(runtime_defaults.get("timeframe") or ""),
        "family_default_base_step": runtime_defaults.get("base_step"),
        "family_default_max_open_per_side": runtime_defaults.get("max_open_per_side"),
        "family_default_session_window": str(session_defaults.get("window") or ""),
        "suggested_seed_action": seed_action,
        "suggested_seed_rationale": seed_rationale,
        "evidence_source": str(policy_row.get("evidence_source") or ""),
        "evidence_mode": str(policy_row.get("evidence_mode") or ""),
        "evidence_net_usd": policy_row.get("evidence_net_usd"),
        "evidence_closes": policy_row.get("evidence_closes"),
    }


def build_payload(
    policy_gap: dict[str, Any],
    regime_signal: dict[str, Any],
    rearm_params: dict[str, Any],
    personality_selector: dict[str, Any],
) -> dict[str, Any]:
    regime_rows = regime_rows_by_symbol(regime_signal)
    rearm_rows = {normalize_symbol(key): dict(value or {}) for key, value in dict(rearm_params.get("current_state_rearm_params") or {}).items()}
    selector_rows = {normalize_symbol(key): dict(value or {}) for key, value in dict(personality_selector.get("symbol_configs") or {}).items()}

    rows = [
        build_row(row, regime_rows, rearm_rows, selector_rows)
        for row in list(policy_gap.get("rows") or [])
    ]
    rows.sort(key=lambda row: (-int(row["priority_score"]), str(row["symbol"])))

    seed_now = [row["symbol"] for row in rows if row["priority"] == "policy_seed_now"]
    seed_next = [row["symbol"] for row in rows if row["priority"] == "policy_seed_next"]
    missing_regime = [row["symbol"] for row in rows if not row["regime_row_present"]]
    missing_selector = [row["symbol"] for row in rows if not row["selector_row_present"]]
    missing_rearm = [row["symbol"] for row in rows if not row["rearm_row_present"]]

    leadership_read = [
        f"Top seed-now policy coverage is now concrete: `{seed_now or ['none']}`.",
        f"Missing surface split is explicit: `missing_regime={missing_regime or ['none']}`, `missing_selector={missing_selector or ['none']}`, `missing_rearm={missing_rearm or ['none']}`.",
        f"Immediate selector-only pickups are `{[row['symbol'] for row in rows if row['suggested_seed_action'] == 'seed_selector_profile_from_regime_truth'] or ['none']}`, which are cheaper than full regime+selector bundles.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            "reports/hungry_hippo_policy_gap_board.json",
            "reports/regime_signal.json",
            "reports/hungry_hippo_rearm_params.json",
            "reports/hungry_hippo_personality_selector.json",
        ],
        "summary": {
            "symbol_count": len(rows),
            "policy_seed_now_symbols": seed_now,
            "policy_seed_next_symbols": seed_next,
            "missing_regime_symbols": missing_regime,
            "missing_selector_symbols": missing_selector,
            "missing_rearm_symbols": missing_rearm,
        },
        "leadership_read": leadership_read,
        "rows": rows,
        "notes": [
            "This is a seed-packet surface for canonical policy coverage only; it does not change runtime behavior.",
            "Family defaults come from hungry_hippo_symbol_profiles and are seed values, not proof that the symbol is ready to launch.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Hungry Hippo Policy Seed Packet Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: show exactly which canonical policy surfaces are missing per top policy-gap symbol and what family-default seed values to start from.",
        "",
        "## Leadership Read",
        "",
    ]
    for line in list(payload.get("leadership_read") or []):
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Symbol count: `{summary.get('symbol_count', 0)}`",
            f"- Seed now: `{summary.get('policy_seed_now_symbols', [])}`",
            f"- Seed next: `{summary.get('policy_seed_next_symbols', [])}`",
            f"- Missing regime rows: `{summary.get('missing_regime_symbols', [])}`",
            f"- Missing selector rows: `{summary.get('missing_selector_symbols', [])}`",
            "",
            "## Rows",
            "",
            "| Symbol | Priority | Missing Surfaces | Suggested Seed Action | Family Defaults |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        missing_parts: list[str] = []
        if not row.get("regime_row_present"):
            missing_parts.append("regime")
        if not row.get("selector_row_present"):
            missing_parts.append("selector")
        if not row.get("rearm_row_present"):
            missing_parts.append("rearm")
        defaults = (
            f"tf={row.get('family_default_timeframe')}, "
            f"step={row.get('family_default_base_step')}, "
            f"max_open={row.get('family_default_max_open_per_side')}, "
            f"session={row.get('family_default_session_window')}"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("symbol") or ""),
                    str(row.get("priority") or ""),
                    ",".join(missing_parts) if missing_parts else "-",
                    str(row.get("suggested_seed_action") or ""),
                    defaults,
                ]
            )
            + " |"
        )
    lines.extend(["", "## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def main() -> None:
    policy_gap = load_json(POLICY_GAP_PATH)
    regime_signal = load_json(REGIME_SIGNAL_PATH)
    rearm_params = load_json(REARM_PARAMS_PATH)
    personality_selector = load_json(PERSONALITY_SELECTOR_PATH)
    payload = build_payload(policy_gap, regime_signal, rearm_params, personality_selector)
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
