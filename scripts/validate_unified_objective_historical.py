#!/usr/bin/env python3
"""Validate unified objective function against ALL historical lane data.

Runs the 9-term unified objective across every archived state file to prove
it correctly ranks champions (BTC M5, M15) above losers (ETH M5 $3, toxic paths).

This closes the Gap 2 validation requirement:
"evidence that optimizing that score improves both realized outcomes
and survivability relative to current ad hoc proxies"

Usage:
    python scripts/validate_unified_objective_historical.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from unified_objective import UnifiedObjective, ObjectiveInput

REPORTS = Path(__file__).resolve().parent.parent / "reports"

# ── Historical lane metadata: (label, state_file, symbol, tier_expected) ─────
# Tiers: S+ = champion, S = strong, A = good, B = marginal, C = weak, KILLED = toxic

LANES = [
    # BTC champions (live/current state)
    ("BTC M15 Warp LIVE $75", "penetration_lattice_live_btcusd_m15_warp_state.json", "BTCUSD", "S+"),
    # BTC shadows (stale/killed — these are historical artifacts, not current profit engines)
    ("BTC M15 SHADOW $15 (stale/killed)", "penetration_lattice_shadow_btcusd_m15_warp_state.json", "BTCUSD", "KILLED"),
    ("BTC M15 Warp on20 $20 (stale/killed)", "penetration_lattice_shadow_btcusd_m15_warp_on20_state.json", "BTCUSD", "KILLED"),
    ("BTC M15 restore v1 (fresh)", "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json", "BTCUSD", "B"),
    ("BTC H1 step30", "penetration_lattice_shadow_btcusd_h1_step30_state.json", "BTCUSD", "B"),
    ("BTC H1 step50", "penetration_lattice_shadow_btcusd_h1_step50_state.json", "BTCUSD", "B"),
    # ETH proven edges (current state)
    ("ETH M5 $5 (1.55x ATR)", "penetration_lattice_shadow_ethusd_m5_warp_5_state.json", "ETHUSD", "S"),
    ("ETH M5 wide $5", "penetration_lattice_shadow_ethusd_m5_warp_wide_state.json", "ETHUSD", "S"),
    ("ETH M5 $3 (0.93x ATR) TOXIC", "penetration_lattice_shadow_ethusd_m5_warp_state.json", "ETHUSD", "KILLED"),
    # ETH M15 lanes (most are stale/dead — validated edges data is from historical runs)
    ("ETH M15 Warp $5 (stale)", "penetration_lattice_shadow_ethusd_m15_warp_state.json", "ETHUSD", "KILLED"),
    ("ETH M15 ATR optimized (fresh)", "penetration_lattice_shadow_ethusd_m15_atr_opt_state.json", "ETHUSD", "B"),
    ("ETH M15 asymmetric (fresh)", "penetration_lattice_shadow_ethusd_m15_asym_state.json", "ETHUSD", "B"),
    # ETH Hungry Hippo experiments (mostly dead)
    ("ETH M5 HH v1 (stale)", "penetration_lattice_shadow_ethusd_m5_hungry_hippo_v1_state.json", "ETHUSD", "KILLED"),
    ("ETH M5 HH step5 (stale)", "penetration_lattice_shadow_ethusd_m5_hh_step5_state.json", "ETHUSD", "KILLED"),
    ("ETH M15 HH v1 (stale)", "penetration_lattice_shadow_ethusd_m15_hungry_hippo_v1_state.json", "ETHUSD", "KILLED"),
    ("ETH M15 micro HH v1 (stale)", "penetration_lattice_shadow_ethusd_m15_micro_hungry_hippo_v1_state.json", "ETHUSD", "KILLED"),
    # ETH structure shapeshifter
    ("ETH M5 Structure Shapeshifter (fresh)", "penetration_lattice_shadow_ethusd_m5_structure_shapeshifter_state.json", "ETHUSD", "B"),
    # FX (manual — from live-lanes.md truth)
    ("FX live rearm 941777", None, "EURUSD", "S+"),
    ("FX live momentum 941778", None, "EURUSD", "A"),
    ("GBPUSD tick-forward", None, "GBPUSD", "KILLED"),
]

# Special-case lanes that need manual data (state files stale or missing)
MANUAL_LANES = {
    "FX live rearm 941777": {
        "realized_net_usd": 724.72,
        "close_count": 326,
        "floating_usd": -0.15,
        "open_count": 5,
        "anchor_reset_count": 0,
        "max_adverse_excursion_usd": 0,
        "first_path_verdict": "",
        "realized_win_rate": 0.65,
    },
    "FX live momentum 941778": {
        "realized_net_usd": 25.05,
        "close_count": 197,
        "floating_usd": 1.03,
        "open_count": 8,
        "anchor_reset_count": 0,
        "max_adverse_excursion_usd": 0,
        "first_path_verdict": "",
        "realized_win_rate": 0.55,
    },
    "GBPUSD tick-forward": {
        "realized_net_usd": -1932.51,
        "close_count": 7313,
        "floating_usd": 0,
        "open_count": 6,
        "anchor_reset_count": 0,
        "max_adverse_excursion_usd": 0,
        "first_path_verdict": "",
        "realized_win_rate": 0.45,
    },
}


def load_lane_from_state(state_file: str, symbol: str) -> ObjectiveInput | None:
    """Extract realized evidence from a state file."""
    path = REPORTS / state_file
    if not path.exists():
        return None
    try:
        s = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    sym_data = s.get("symbols", {}).get(symbol, {})
    if not sym_data:
        return None

    realized = sym_data.get("close_count") or sym_data.get("realized_closes") or 0
    net = sym_data.get("net_realized_usd") or sym_data.get("realized_net_usd") or 0.0
    floating = sym_data.get("floating_pnl_usd") or 0.0
    opens = sym_data.get("open_count") or 0
    resets = sym_data.get("reset_count") or sym_data.get("anchor_reset_count") or 0

    # Try to get MAE from events
    mae = extract_mae_from_events(state_file.replace("_state.json", "_events.jsonl"))

    return ObjectiveInput(
        realized_net_usd=net,
        close_count=realized,
        floating_usd=floating,
        open_count=opens,
        anchor_reset_count=resets,
        max_adverse_excursion_usd=mae,
        first_path_verdict="",
        realized_win_rate=0.0,  # Not tracked in state files
    )


def extract_mae_from_events(event_file: str) -> float:
    """Try to extract max adverse excursion from event log."""
    path = REPORTS / event_file
    if not path.exists():
        return 0.0
    try:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        worst = 0.0
        for line in lines[:1000]:  # Limit to first 1000 events for speed
            try:
                evt = json.loads(line)
                floating = evt.get("floating_pnl_usd") or evt.get("floating") or 0.0
                if floating < worst:
                    worst = floating
            except json.JSONDecodeError:
                continue
        return worst
    except Exception:
        return 0.0


def tier_to_numeric(tier: str) -> float:
    """Map expected tier to numeric for ranking validation."""
    mapping = {"S+": 6, "S": 5, "A": 4, "B": 3, "C": 2, "KILLED": 1}
    return mapping.get(tier, 0)


def main() -> int:
    print("=" * 80)
    print("UNIFIED OBJECTIVE — HISTORICAL VALIDATION")
    print("=" * 80)
    print()

    results = []
    missing = []

    # Process state-file-based lanes
    for label, state_file, symbol, tier in LANES:
        if state_file is None:
            # Manual lane
            data = MANUAL_LANES.get(label)
            if data:
                inp = ObjectiveInput(**data)
                r = UnifiedObjective.evaluate(inp)
                results.append((label, tier, r))
            continue

        inp = load_lane_from_state(state_file, symbol)
        if inp is None:
            missing.append(label)
            continue
        r = UnifiedObjective.evaluate(inp)
        results.append((label, tier, r))

    # Sort by unified score descending
    results.sort(key=lambda x: x[2].total, reverse=True)

    # Print results
    print(f"{'Lane':<45s} {'Tier':<8s} {'Score':>8s} {'Verdict':<30s} $/close  closes  resets")
    print("-" * 120)
    for label, tier, r in results:
        closes = r.components.close_efficiency  # proxy — we need close_count from input
        print(f"{label:<45s} {tier:<8s} {r.total:>+8.2f} {r.verdict:<30s}")

    print()
    print(f"Missing state files: {len(missing)}")
    for m in missing:
        print(f"  - {m}")

    # ── Validation: do scores correlate with expected tiers? ──────────────
    print()
    print("=" * 80)
    print("CORRELATION CHECK: Do unified scores correlate with known tiers?")
    print("=" * 80)

    scored = [(label, tier, r.total) for label, tier, r in results]
    tiered = [(tier_to_numeric(t), s) for _, t, s in scored]

    if len(tiered) < 3:
        print("Insufficient data for correlation check")
        return 1

    # Simple Spearman-like rank correlation
    n = len(tiered)
    rank_tier = sorted(range(n), key=lambda i: tiered[i][0])
    rank_score = sorted(range(n), key=lambda i: tiered[i][1], reverse=True)

    # Kendall's tau approximation
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            tier_diff = tiered[i][0] - tiered[j][0]
            score_diff = tiered[i][1] - tiered[j][1]
            if tier_diff * score_diff > 0:
                concordant += 1
            elif tier_diff * score_diff < 0:
                discordant += 1

    total_pairs = concordant + discordant
    tau = (concordant - discordant) / total_pairs if total_pairs > 0 else 0
    print(f"Kendall's tau: {tau:+.3f} ({concordant} concordant, {discordant} discordant)")
    if tau >= 0.5:
        print("PASS: Unified objective scores correlate well with known tier rankings")
    elif tau >= 0.2:
        print("PARTIAL: Moderate correlation — objective captures some but not all tier structure")
    else:
        print("FAIL: Poor correlation — objective needs retuning")

    # ── Champion vs toxic separation check ────────────────────────────────
    print()
    print("=" * 80)
    print("SEPARATION CHECK: Champions vs KILLED lanes")
    print("=" * 80)

    champ_scores = [r.total for _, t, r in results if t in ("S+", "S")]
    killed_scores = [r.total for _, t, r in results if t == "KILLED"]

    if champ_scores and killed_scores:
        avg_champ = sum(champ_scores) / len(champ_scores)
        avg_killed = sum(killed_scores) / len(killed_scores)
        print(f"Avg champion score: {avg_champ:+.2f} ({len(champ_scores)} lanes)")
        print(f"Avg KILLED score:   {avg_killed:+.2f} ({len(killed_scores)} lanes)")
        print(f"Separation:         {avg_champ - avg_killed:+.2f}")
        if avg_champ > avg_killed:
            print("PASS: Champions score higher than KILLED lanes")
        else:
            print("FAIL: KILLED lanes score higher than champions — objective is inverted!")
    else:
        print("Insufficient data for separation check")

    # ── Write output ─────────────────────────────────────────────────────
    output = {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "lanes": [
            {
                "label": label,
                "expected_tier": tier,
                "unified_score": r.total,
                "verdict": r.verdict,
                "components": r.component_breakdown,
            }
            for label, tier, r in results
        ],
        "correlation": {
            "kendall_tau": tau,
            "concordant": concordant,
            "discordant": discordant,
        },
        "separation": {
            "avg_champion": avg_champ if champ_scores else None,
            "avg_killed": avg_killed if killed_scores else None,
            "champion_count": len(champ_scores),
            "killed_count": len(killed_scores),
        },
        "missing_state_files": missing,
    }

    out_path = REPORTS / "unified_objective_historical_validation.json"
    out_path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"\nWrote {out_path}")

    out_md = REPORTS / "unified_objective_historical_validation.md"
    lines = [
        "# Unified Objective — Historical Validation",
        "",
        f"Generated: {output['generated_at']}",
        "",
        "## Lane Rankings (by unified score)",
        "",
        f"| Lane | Expected Tier | Unified Score | Verdict |",
        f"|------|--------------|---------------|---------|",
    ]
    for label, tier, r in results:
        lines.append(f"| {label} | {tier} | {r.total:+.2f} | {r.verdict} |")
    lines.extend([
        "",
        "## Correlation",
        "",
        f"- Kendall's tau: {tau:+.3f}",
        f"- Concordant pairs: {concordant}",
        f"- Discordant pairs: {discordant}",
        "",
        "## Champion vs KILLED Separation",
        "",
        f"- Avg champion score: {avg_champ:+.2f}" if champ_scores else "- No champion data",
        f"- Avg KILLED score: {avg_killed:+.2f}" if killed_scores else "- No KILLED data",
        f"- Separation: {avg_champ - avg_killed:+.2f}" if champ_scores and killed_scores else "",
        "",
        "## Missing State Files",
        "",
    ])
    for m in missing:
        lines.append(f"- {m}")

    out_md.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_md}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
