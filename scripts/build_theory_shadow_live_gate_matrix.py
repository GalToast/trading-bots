#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

PROMOTION_GATE_PATH = REPORTS / "shadow_to_live_promotion_gate_board.json"
OUTPUT_JSON_PATH = REPORTS / "theory_shadow_live_gate_matrix.json"
OUTPUT_MD_PATH = REPORTS / "theory_shadow_live_gate_matrix.md"


FAMILY_BY_CANDIDATE = {
    "ETHUSD M5 step14 normalized control": "hungry_hippo / crypto_m5_control",
    "GBPUSD alpha=0.5 FX harvest path": "fx_harvest",
    "NAS100 asym breakout family lane": "hungry_hippo / index_asymmetry",
    "BTCUSD M15 sell-tight downtrend shape": "hungry_hippo / btc_downtrend_control",
    "BTCUSD M5 step200 salvage probe": "crypto_salvage_probe",
    "US30 asym breakout family lane": "hungry_hippo / index_asymmetry",
}

BENCHMARK_BY_CANDIDATE = {
    "ETHUSD M5 step14 normalized control": "same ETH M5 step14 control with runtime freshness restored and offensive closure OFF",
    "GBPUSD alpha=0.5 FX harvest path": "fresh bucketed forward read where harvest stays positive and closure tax is reduced",
    "NAS100 asym breakout family lane": "its own forward shadow inside the intended session window",
    "BTCUSD M15 sell-tight downtrend shape": "existing BTC M15 baseline or bullish-hold behavior during the same SELL regime",
    "BTCUSD M5 step200 salvage probe": "repeatable shadow performance over a materially larger sample than 2 closes",
    "US30 asym breakout family lane": "direct US30 asym-family forward proof after guardrails unblock",
}

VARIABLE_BY_CANDIDATE = {
    "ETHUSD M5 step14 normalized control": "offensive closure ON/OFF on the same normalized step14 shape",
    "GBPUSD alpha=0.5 FX harvest path": "closure policy and unwind behavior, not entry geometry",
    "NAS100 asym breakout family lane": "NAS100 breakout-buy geometry in its own family and window",
    "BTCUSD M15 sell-tight downtrend shape": "sell-tight bounce-reversal controller under SELL bias",
    "BTCUSD M5 step200 salvage probe": "wider M5 salvage step with the current escape stack held constant",
    "US30 asym breakout family lane": "US30 asym breakout geometry after guardrail unblock",
}

NEXT_STAGE_BY_STAGE = {
    "tested_theory_waiting_for_clean_control": "shadow",
    "tested_theory_waiting_for_positive_control_proof": "shadow",
    "closure_policy_diagnosis_before_live": "validated_shadow",
    "research_only_shadow_candidate": "shadow",
    "shadow_config_exists_needs_reconcile": "shadow",
    "shadow_probe_ready_low_sample": "validated_shadow",
    "positive_shadow_guardrail_blocked": "validated_shadow",
}

VALIDATED_SHADOW_GATES = {
    "ETHUSD M5 step14 normalized control": [
        "normalized step14 runtime stays fresh and honest for at least 25 realized closes",
        "positive realized net survives without reset storm or geometry collapse",
        "variant-vs-control comparison changes only offensive closure and nothing else",
        "winning arm improves profit quality and loss containment together, not one at the expense of the other",
    ],
    "GBPUSD alpha=0.5 FX harvest path": [
        "fresh bucketed forward sample keeps the harvest bucket positive",
        "offensive-close and forced-unwind buckets stop dominating lane net",
        "selector-vs-live contradiction is resolved so proof path and runtime path match",
        "the repaired closure path survives an adverse segment without reintroducing closure-tax blowout",
    ],
    "NAS100 asym breakout family lane": [
        "forward shadow remains positive inside the intended session window",
        "spread/reset behavior stays clean under current escape contract",
        "manual review accepts the lane as family-specific proof rather than universal geometry",
        "window continuity survives more than one local regime patch",
    ],
    "BTCUSD M15 sell-tight downtrend shape": [
        "shadow config is reconciled to the intended handoff truth first",
        "fresh shadow closes accumulate under SELL/bounce_reversal conditions",
        "loss control is better than the bullish-hold alternative during the same hostile regime",
        "proof completes without disturbing the incumbent live BTC M15 baseline",
    ],
    "BTCUSD M5 step200 salvage probe": [
        "sample expands well beyond 2 closes while staying repeatably positive",
        "BTC hold gate and buy-realignment blocker clear",
        "launch-contract failures are resolved on the proof path",
        "edge remains compelling after sample expansion rather than collapsing toward baseline noise",
    ],
    "US30 asym breakout family lane": [
        "guardrail blockade is resolved first",
        "direct forward proof exists on US30 itself rather than by family association",
        "spread/reset behavior stays clean under forward conditions",
        "the lane clears the same manual-review standard applied to NAS100 before promotion talk resumes",
    ],
}

LIVE_GATES = {
    "ETHUSD M5 step14 normalized control": [
        "validated shadow is built on the same normalized step14 runtime path planned for live",
        "current control or winning variant stays family-local and forward-positive through a hostile segment",
        "no stale-runtime, geometry-drift, or comparison-contamination blocker remains open",
    ],
    "GBPUSD alpha=0.5 FX harvest path": [
        "bucket-repaired lane stays positive on mixed net after closure tax is repaired",
        "alpha=0.5 geometry remains intact and runtime contradictions are gone",
        "promotion argument relies on fresh bucketed proof rather than copied shelf stats",
    ],
    "NAS100 asym breakout family lane": [
        "validated shadow remains positive in the intended session window",
        "manual-review acceptance remains explicit",
        "promotion language stays scoped to NAS100 and does not claim universal index proof",
    ],
    "BTCUSD M15 sell-tight downtrend shape": [
        "validated shadow repeatedly beats the bullish-hold alternative on loss containment during SELL bias",
        "runtime path matches the proven shadow config",
        "promotion does not degrade the incumbent positive BTC family benchmark",
    ],
    "BTCUSD M5 step200 salvage probe": [
        "sample becomes statistically meaningful and repeatable",
        "hold-gate and launch-governance blockers are closed",
        "the lane still looks superior after larger-sample scrutiny instead of only in tiny-sample optics",
    ],
    "US30 asym breakout family lane": [
        "validated shadow exists under the current runtime path",
        "guardrails remain clear during proof and launch review",
        "promotion case is based on US30-local proof, not borrowed NAS100 momentum",
    ],
}

DISQUALIFIERS = {
    "ETHUSD M5 step14 normalized control": "runtime stays stale, ladder geometry still looks step5-like, or the A/B changes more than offensive closure",
    "GBPUSD alpha=0.5 FX harvest path": "harvest bucket stops being positive or closure buckets still swamp mixed net after repair",
    "NAS100 asym breakout family lane": "post-breakout reversals erase the edge or the room starts universalizing the family proof",
    "BTCUSD M15 sell-tight downtrend shape": "config drift remains unresolved or proof only looks good by skipping hostile SELL segments",
    "BTCUSD M5 step200 salvage probe": "sample stays tiny, launch verdict remains failed, or the hold gate stays active",
    "US30 asym breakout family lane": "guardrails stay blocked or direct US30 proof never materializes",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_payload(promotion_gate: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for gate_row in list(promotion_gate.get("rows") or []):
        candidate = str(gate_row.get("candidate") or "")
        current_stage = str(gate_row.get("current_stage") or "")
        rows.append(
            {
                "priority": int(gate_row.get("priority") or 0),
                "candidate": candidate,
                "family": FAMILY_BY_CANDIDATE.get(candidate, "unknown"),
                "current_stage": current_stage,
                "next_honest_stage": NEXT_STAGE_BY_STAGE.get(current_stage, "shadow"),
                "benchmark_to_beat": BENCHMARK_BY_CANDIDATE.get(candidate, ""),
                "single_changed_variable": VARIABLE_BY_CANDIDATE.get(candidate, ""),
                "gate_to_next_stage": list(gate_row.get("promotion_gate") or []),
                "gate_to_validated_shadow": VALIDATED_SHADOW_GATES.get(candidate, []),
                "gate_to_live": LIVE_GATES.get(candidate, []),
                "current_truth": dict(gate_row.get("machine_truth") or {}),
                "blocking_issue": str(gate_row.get("blocking_issue") or ""),
                "instant_disqualifier": DISQUALIFIERS.get(candidate, ""),
            }
        )

    return {
        "generated_at": utc_now_iso(),
        "sources": [str(PROMOTION_GATE_PATH.relative_to(ROOT))],
        "leadership_read": [
            "No current candidate honestly deserves live language yet; each one still has a specific gate between theory, shadow, validated shadow, and live.",
            "ETH step14 is still the cleanest path for testing a loss-reduction idea, but the honest remaining gate is positive proof on the aligned control lane rather than more control-restoration work.",
            "GBP is a bucket-diagnosis lane now, NAS100 is the cleanest research-only profit-expansion seam, and BTC sell-tight remains a less-losses shadow proof problem rather than a promotion story.",
        ],
        "global_rules": [
            {
                "rule": "family_firewall",
                "read": "proof must stay inside the same family, timeframe, and runtime path; borrowed wins do not count",
            },
            {
                "rule": "control_before_variant",
                "read": "normalize or restore the active control before testing a variant on top of it",
            },
            {
                "rule": "bucketed_truth",
                "read": "loss-reduction ideas must separate harvest, offensive close, and forced unwind before claiming edge or failure",
            },
            {
                "rule": "spread_and_runtime_honesty",
                "read": "a shape below current spread safety or running with stale/drifted runtime cannot be treated as valid proof",
            },
            {
                "rule": "single_changed_variable",
                "read": "every graduation claim must name one mechanism change and one benchmark to beat",
            },
        ],
        "stage_model": {
            "tested_theory_to_shadow": [
                "name the evidence family",
                "name the benchmark to beat",
                "change one variable only",
                "write a kill condition before the run",
                "define the concrete shadow harness",
            ],
            "shadow_to_validated_shadow": [
                "use a fresh runtime path that matches the proof story",
                "accumulate enough forward sample to escape tiny-sample theater",
                "keep reset, spread, and floating-loss behavior inside bounds",
                "beat the family-local control or baseline on profit quality and loss containment",
            ],
            "validated_shadow_to_live": [
                "match the planned live runtime path to the proven shadow path",
                "close governance, contradiction, and guardrail blockers",
                "survive at least one adverse regime segment without collapsing the thesis",
            ],
        },
        "closest_live_candidate": str((promotion_gate.get("summary") or {}).get("closest_current_live_candidate") or ""),
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Theory Shadow Live Gate Matrix",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: give the room one compact, current ladder for graduating ideas from tested theory to shadow to validated shadow to live.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Global Rules", ""])
    for row in list(payload.get("global_rules") or []):
        lines.append(f"- `{row['rule']}`: {row['read']}")

    lines.extend(["", "## Stage Model", ""])
    for stage, gates in dict(payload.get("stage_model") or {}).items():
        lines.append(f"### {stage}")
        lines.append("")
        for gate in list(gates or []):
            lines.append(f"- {gate}")
        lines.append("")

    lines.extend(
        [
            "## Candidate Gates",
            "",
            f"- Closest live candidate: `{payload.get('closest_live_candidate', '')}`",
            "",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(f"### P{int(row['priority'])} - {row['candidate']}")
        lines.append("")
        lines.append(f"- Family: `{row['family']}`")
        lines.append(f"- Current stage: `{row['current_stage']}`")
        lines.append(f"- Next honest stage: `{row['next_honest_stage']}`")
        lines.append(f"- Benchmark to beat: `{row['benchmark_to_beat']}`")
        lines.append(f"- Single changed variable: `{row['single_changed_variable']}`")
        lines.append(f"- Current truth: `{'; '.join(f'{k}={v}' for k, v in dict(row.get('current_truth') or {}).items())}`")
        lines.append(f"- Blocking issue: `{row['blocking_issue']}`")
        lines.append(f"- Gate to next stage: `{'; '.join(list(row.get('gate_to_next_stage') or []))}`")
        lines.append(f"- Gate to validated shadow: `{'; '.join(list(row.get('gate_to_validated_shadow') or []))}`")
        lines.append(f"- Gate to live: `{'; '.join(list(row.get('gate_to_live') or []))}`")
        lines.append(f"- Instant disqualifier: `{row['instant_disqualifier']}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(load_json(PROMOTION_GATE_PATH))
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
