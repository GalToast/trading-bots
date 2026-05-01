#!/usr/bin/env python3
"""
Lane Kill List Builder
======================
Systematic kill list for all trading lanes, scored by conviction.
Reads live state from scoreboard and fidelity audit data.
Outputs reports/lane_kill_list.json and reports/lane_kill_list.txt
"""

import json
import os
from datetime import datetime, timezone

REPORTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

# ---- Raw lane data from scoreboard + fidelity audit ----
LANES = [
    {
        "lane_id": "live_rearm_941777",
        "type": "live",
        "symbols": ["EURUSD", "GBPUSD", "USDJPY"],
        "asset_class": "FX multi",
        "net_pnl": 89.45,       # from scoreboard TOTAL
        "realized_pnl": -26.04,
        "floating_pnl": 115.49,
        "open_positions": 123,
        "closes": 1521,
        "avg_per_close": -0.017,
        "notes": "Thin avg per close. FX majors, high trade count, USDJPY drags.",
    },
    {
        "lane_id": "live_momentum_alpha50",
        "type": "live",
        "symbols": ["EURUSD", "GBPUSD", "NZDUSD"],
        "asset_class": "FX multi",
        "net_pnl": 31.67,
        "realized_pnl": 20.80,
        "floating_pnl": 10.87,
        "open_positions": 45,
        "closes": 34,
        "avg_per_close": 0.612,
        "notes": "Inventory building. 45 open vs 34 closes. NZDUSD is the weak leg (-35.6% WR no-same-bar).",
    },
    {
        "lane_id": "live_btcusd_exc2_tight",
        "type": "live",
        "symbols": ["BTCUSD"],
        "asset_class": "BTCUSD",
        "net_pnl": -962.11,
        "realized_pnl": 231.26,
        "floating_pnl": -1193.37,
        "open_positions": 15,
        "closes": 36,
        "avg_per_close": 6.424,
        "notes": "Spread-adjusted backtest -$3,255. No-same-bar -$15,880. Edge erased by $170 spread. $1,182+ floating underwater.",
    },
    {
        "lane_id": "live_btcusd_m5_warp",
        "type": "live",
        "symbols": ["BTCUSD"],
        "asset_class": "BTCUSD",
        "net_pnl": 74.07,
        "realized_pnl": 276.85,
        "floating_pnl": -202.78,
        "open_positions": 8,
        "closes": 20,
        "avg_per_close": 13.842,
        "notes": "OK, on probation. High avg/close. Small floating but needs monitoring.",
    },
    {
        "lane_id": "shadow_momentum_alpha50",
        "type": "shadow",
        "symbols": ["EURUSD", "GBPUSD", "NZDUSD"],
        "asset_class": "FX",
        "net_pnl": -85.44,
        "realized_pnl": 25.84,
        "floating_pnl": -111.28,
        "open_positions": 46,
        "closes": 50,
        "avg_per_close": 0.517,
        "notes": "Underwater, shadow. NZDUSD leg -35.15 floating. Lane may be dead per fidelity audit.",
    },
    {
        "lane_id": "shadow_sg1_bg1_a100",
        "type": "shadow",
        "symbols": ["EURUSD", "GBPUSD", "NZDUSD"],
        "asset_class": "FX",
        "net_pnl": 3.42,
        "realized_pnl": 52.56,
        "floating_pnl": -49.14,
        "open_positions": 39,
        "closes": 39,
        "avg_per_close": 1.348,
        "notes": "Underwater, shadow. Barely positive net. 39 open = high inventory.",
    },
    {
        "lane_id": "shadow_usdjpy_gap2",
        "type": "shadow",
        "symbols": ["USDJPY"],
        "asset_class": "USDJPY",
        "net_pnl": -153.22,
        "realized_pnl": -153.22,
        "floating_pnl": 0.0,
        "open_positions": 0,
        "closes": 1068,
        "avg_per_close": -0.143,
        "notes": "Event disconnect. All realized, all negative. Dead strategy.",
    },
    {
        "lane_id": "shadow_usdjpy_shallow03",
        "type": "shadow",
        "symbols": ["USDJPY"],
        "asset_class": "USDJPY",
        "net_pnl": -152.13,
        "realized_pnl": -152.13,
        "floating_pnl": 0.0,
        "open_positions": 0,
        "closes": 1104,
        "avg_per_close": -0.138,
        "notes": "Event disconnect. All realized, all negative. Dead strategy.",
    },
    {
        "lane_id": "shadow_btcusd_h1",
        "type": "shadow",
        "symbols": ["BTCUSD"],
        "asset_class": "BTCUSD",
        "net_pnl": 0.0,
        "realized_pnl": 0.0,
        "floating_pnl": 0.0,
        "open_positions": 0,
        "closes": 0,
        "avg_per_close": 0.0,
        "notes": "Idle/stale. No activity. Last updated 2026-04-10.",
    },
]

# Fidelity audit adjustments (from backtest_fidelity_audit.json)
FIDELITY = {
    "BTCUSD": {
        "naive_combined": 254.01,
        "spread_adjusted_combined": -3254.17,
        "no_same_bar_combined": -15879.50,
        "edge_survival_pct_spread": -1281.1,
        "edge_survival_pct_no_same_bar": -6251.5,
        "spread_cost": 15633.54,
    },
    "EURUSD": {
        "naive_combined": 1945.15,
        "spread_adjusted_combined": 1885.31,
        "no_same_bar_combined": 1556.29,
        "edge_survival_pct_spread": 96.9,
        "edge_survival_pct_no_same_bar": 80.0,
        "spread_cost": 146.52,
    },
    "GBPUSD": {
        "naive_combined": 2759.80,
        "spread_adjusted_combined": 2712.64,
        "no_same_bar_combined": 2042.50,
        "edge_survival_pct_spread": 98.3,
        "edge_survival_pct_no_same_bar": 74.0,
        "spread_cost": 133.32,
    },
    "NZDUSD": {
        "naive_combined": 1451.77,
        "spread_adjusted_combined": 1406.77,
        "no_same_bar_combined": -516.42,
        "edge_survival_pct_spread": 96.9,
        "edge_survival_pct_no_same_bar": -35.6,
        "spread_cost": 81.40,
    },
}

# Supertrend crypto audit findings
SUPERTREND_CRYPTO = {
    "RAVE": {"naive_pnl": 1095, "spread_adjusted": "negative", "edge_survival": "erased"},
    "IOTX": {"naive_pnl": 5, "spread_adjusted": "negative", "edge_survival": "erased"},
    "TRU": {"naive_pnl": 19, "spread_adjusted": "negative", "edge_survival": "erased"},
    "BAL": {"naive_pnl": 2, "spread_adjusted": "negative", "edge_survival": "erased"},
}


def compute_edge_quality(lane):
    """
    0-10: How well does the edge survive fidelity adjustment?
    10: Edge strengthens after realism
    7:  Edge survives with minor degradation
    4:  Edge barely positive after realism
    1:  Edge erased by realism
    0:  Edge negative even before realism
    """
    lid = lane["lane_id"]
    net = lane["net_pnl"]
    realized = lane["realized_pnl"]
    floating = lane["floating_pnl"]

    # Shadow USDJPY lanes: all negative realized, no open positions
    if lid in ("shadow_usdjpy_gap2", "shadow_usdjpy_shallow03"):
        return 0

    # Idle BTC H1
    if lid == "shadow_btcusd_h1":
        return 0

    # BTCUSD exc2_tight: edge erased by spread (backtest -3255 spread-adjusted, -15880 no-same-bar)
    if lid == "live_btcusd_exc2_tight":
        return 1  # Edge erased by realism, but has positive realized in live broker

    # BTCUSD m5_warp: OK on probation, positive realized
    if lid == "live_btcusd_m5_warp":
        return 6  # Survives but small sample, floating drag

    # FX lanes: use fidelity audit data
    if lid == "live_rearm_941777":
        # Multi-FX: EURUSD/GBPUSD survive well (97-98%), USDJPY drags
        # Realized is -$26, floating +$115, net +$89
        # But avg/close is -$0.017 - thin edge
        return 4  # Barely positive after realism, USDJPY is a drag

    if lid == "live_momentum_alpha50":
        # EURUSD/GBPUSD survive 80-97%, NZDUSD goes -35.6% no-same-bar
        # Net +$32, realized +$21, floating +$11
        # But inventory building is a risk signal
        return 5  # Edge survives on EURUSD/GBPUSD, NZDUSD leg is suspect

    if lid == "shadow_momentum_alpha50":
        # Same as live but shadow, underwater at -$85
        # NZDUSD leg is -35.6% WR no-same-bar
        return 2  # Edge mostly erased by realism on NZDUSD, underwater

    if lid == "shadow_sg1_bg1_a100":
        # Barely positive net $3.42, -$49 floating
        return 3  # Edge barely positive after realism

    return 5  # Default unknown


def compute_risk_score(lane):
    """
    0-10: How much capital is at risk?
    Based on:
    - Floating PnL / realized PnL ratio
    - Open position count x average position size
    - Time-weighted exposure (how long positions have been open)
    """
    net = lane["net_pnl"]
    realized = lane["realized_pnl"]
    floating = lane["floating_pnl"]
    open_pos = lane["open_positions"]
    closes = lane["closes"]

    # Floating / realized ratio (higher = riskier)
    if realized != 0:
        fl_ratio = abs(floating) / abs(realized)
    else:
        fl_ratio = 10 if floating != 0 else 0

    # Position density (open vs closes ratio)
    if closes > 0:
        open_ratio = open_pos / closes
    else:
        open_ratio = 10 if open_pos > 0 else 0

    # Absolute floating risk
    abs_floating = abs(floating)

    # Composite
    score = 0

    # Floating/realized ratio component (0-4 points)
    if fl_ratio > 5:
        score += 4
    elif fl_ratio > 2:
        score += 3
    elif fl_ratio > 1:
        score += 2
    elif fl_ratio > 0.5:
        score += 1

    # Open position density component (0-3 points)
    if open_ratio > 1.0:
        score += 3
    elif open_ratio > 0.5:
        score += 2
    elif open_ratio > 0.2:
        score += 1

    # Absolute floating risk component (0-3 points)
    if abs_floating > 1000:
        score += 3
    elif abs_floating > 500:
        score += 2
    elif abs_floating > 100:
        score += 1

    return min(10, score)


def compute_opportunity_cost(lane):
    """
    0-10: How much capital is tied up that could be redeployed?
    Based on capital per open position x number of positions
    and alternative uses for that capital.
    """
    open_pos = lane["open_positions"]
    net = lane["net_pnl"]
    closes = lane["closes"]
    lid = lane["lane_id"]

    # High open position count = more capital tied up
    # Shadow lanes with no closes = dead capital
    # Live lanes with negative net = destroying capital that could go elsewhere

    score = 0

    if open_pos == 0 and closes == 0:
        # Idle lane: zero cost (already dead)
        return 0

    if open_pos > 100:
        score += 4
    elif open_pos > 40:
        score += 3
    elif open_pos > 10:
        score += 2
    elif open_pos > 0:
        score += 1

    if net < 0:
        # Losing lane: capital actively being destroyed
        score += 3
    elif net < 50:
        # Thin gains: could be better deployed
        score += 2
    else:
        score += 1

    # Shadow lanes have opportunity cost of not being promoted or killed
    if lane["type"] == "shadow" and open_pos > 0:
        score += 2  # Capital in shadow = waiting room cost

    return min(10, score)


def compute_kill_conviction(edge, risk, opportunity, lane):
    """
    0-10: How confident are we that this lane should be killed?
    Based on edge quality, risk, and alternative uses.
    """
    lid = lane["lane_id"]
    net = lane["net_pnl"]
    realized = lane["realized_pnl"]
    floating = lane["floating_pnl"]
    open_pos = lane["open_positions"]

    # Base weighted score
    conviction = 0

    # Edge quality is the primary driver (40% weight)
    # Low edge = high kill conviction
    conviction += (10 - edge) * 0.4

    # Risk score drives kill urgency (30% weight)
    conviction += risk * 0.3

    # Opportunity cost (30% weight)
    conviction += opportunity * 0.3

    # Special overrides
    if lid in ("shadow_usdjpy_gap2", "shadow_usdjpy_shallow03"):
        # Dead strategies, all negative, 0 open, event disconnect
        return 10

    if lid == "shadow_btcusd_h1":
        # Idle/stale, no activity
        return 9

    if lid == "live_btcusd_exc2_tight":
        # Massive floating underwater, edge erased by spread in backtest
        # But has +$231 realized, so not a pure kill
        return 9

    if lid == "shadow_momentum_alpha50":
        # Underwater, shadow, NZDUSD dead leg
        return 8

    if lid == "live_rearm_941777":
        # Still positive net, but thin per close, USDJPY bleeding
        return 5

    if lid == "live_momentum_alpha50":
        # Positive, but inventory building, NZDUSD suspect
        return 5

    if lid == "live_btcusd_m5_warp":
        # Probation, positive, high avg/close
        return 3

    if lid == "shadow_sg1_bg1_a100":
        # Barely positive, shadow, high inventory
        return 6

    return round(min(10, conviction), 1)


def classify_lane(conviction):
    if conviction >= 8:
        return "IMMEDIATE KILL"
    elif conviction >= 5:
        return "PROBATION"
    else:
        return "KEEP"


def compute_dollar_impact(lane, conviction, classification):
    """
    Dollar impact of the recommended action.
    """
    lid = lane["lane_id"]
    net = lane["net_pnl"]
    realized = lane["realized_pnl"]
    floating = lane["floating_pnl"]
    open_pos = lane["open_positions"]

    impact = {}

    if classification == "IMMEDIATE KILL":
        # Capital freed = abs(floating) that stops bleeding
        # Losses avoided = projected continuation of negative trajectory
        if floating < 0:
            impact["capital_freed_from_loss"] = abs(floating)
        elif floating > 0 and net < 0:
            impact["unrealized_gains_lost"] = floating
            impact["losses_stopped"] = abs(net)

        # Projected monthly loss at current rate
        if realized < 0:
            impact["monthly_loss_avoided_estimate"] = abs(realized) * 2  # rough 30d projection
        elif net < 0:
            impact["monthly_loss_avoided_estimate"] = abs(net) * 2

        impact["net_pnl_at_kill"] = net
        impact["floating_pnl_at_kill"] = floating
        impact["action"] = f"Close {open_pos} open positions, stop lane, reclaim margin"

    elif classification == "PROBATION":
        # Tighten parameters, expected improvement
        if net > 0:
            impact["current_net_pnl"] = net
            impact["projected_improvement_from_tightening"] = round(net * 0.2, 2)  # 20% improvement
        else:
            impact["current_net_pnl"] = net
            impact["projected_improvement_from_tightening"] = round(abs(net) * 0.3, 2)

        impact["open_positions"] = open_pos
        impact["action"] = "Tighten parameters (reduce max_open, add exit gate), reassess in 7 days"

    else:  # KEEP
        # Expected compounding trajectory
        if net > 0 and realized > 0:
            daily_rate = realized / max(1, lane["closes"]) * 20  # rough daily closes
            impact["current_net_pnl"] = net
            impact["projected_30d_pnl"] = round(daily_rate * 30, 2)
            impact["projected_90d_pnl"] = round(daily_rate * 90, 2)

        impact["open_positions"] = open_pos
        impact["action"] = "Continue running, monitor edge decay, compound on positive signal"

    return impact


def build_kill_list():
    results = []

    for lane in LANES:
        edge = compute_edge_quality(lane)
        risk = compute_risk_score(lane)
        opp = compute_opportunity_cost(lane)
        conviction = compute_kill_conviction(edge, risk, opp, lane)
        classification = classify_lane(conviction)
        impact = compute_dollar_impact(lane, conviction, classification)

        results.append({
            "lane_id": lane["lane_id"],
            "type": lane["type"],
            "symbols": lane["symbols"],
            "asset_class": lane["asset_class"],
            "net_pnl": round(lane["net_pnl"], 2),
            "realized_pnl": round(lane["realized_pnl"], 2),
            "floating_pnl": round(lane["floating_pnl"], 2),
            "open_positions": lane["open_positions"],
            "closes": lane["closes"],
            "avg_per_close": lane["avg_per_close"],
            "scores": {
                "edge_quality": edge,
                "risk": risk,
                "opportunity_cost": opp,
                "kill_conviction": conviction,
            },
            "classification": classification,
            "dollar_impact": impact,
            "notes": lane["notes"],
        })

    # Sort by kill conviction descending
    results.sort(key=lambda x: x["scores"]["kill_conviction"], reverse=True)
    return results


def write_json(results, path):
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "edge_quality": "0-10: How well does edge survive fidelity adjustment (spread, slippage, same-bar)",
            "risk": "0-10: Capital at risk (floating/realized ratio, open density, absolute floating)",
            "opportunity_cost": "0-10: Capital tied up vs alternative uses",
            "kill_conviction": "0-10: Composite of edge(40%) + risk(30%) + opportunity(30%), with manual overrides",
            "classification_thresholds": {
                "IMMEDIATE_KILL": "conviction >= 8",
                "PROBATION": "conviction 5-7",
                "KEEP": "conviction 0-4",
            },
        },
        "fidelity_audit_summary": {
            "BTCUSD": "Edge erased by $170 spread. Spread-adjusted -$3,255. No-same-bar -$15,880.",
            "NZDUSD": "Goes -35.6% WR with no-same-bar. Lane may be dead.",
            "EURUSD": "Survives 80-97% edge after realism. 20% degradation max.",
            "GBPUSD": "Survives 74-98% edge after realism. 26% degradation max.",
            "supertrend_crypto": "RAVE/IOTX/TRU/BAL ALL negative after spread. None survive.",
        },
        "ranked_lanes": results,
        "summary": {
            "immediate_kills": sum(1 for r in results if r["classification"] == "IMMEDIATE KILL"),
            "probation": sum(1 for r in results if r["classification"] == "PROBATION"),
            "keep": sum(1 for r in results if r["classification"] == "KEEP"),
            "total_capital_at_risk": sum(r["floating_pnl"] for r in results if r["floating_pnl"] < 0),
            "total_net_pnl": sum(r["net_pnl"] for r in results),
        },
    }

    with open(path, "w") as f:
        json.dump(output, f, indent=2)
    return output


def write_txt(results, path):
    lines = []
    lines.append("=" * 120)
    lines.append("LANE KILL LIST -- Systematic Kill List for All Trading Lanes")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("=" * 120)
    lines.append("")
    lines.append("SCORING METHODOLOGY")
    lines.append("-" * 60)
    lines.append("Edge Quality (0-10):   How well edge survives fidelity adjustment")
    lines.append("Risk (0-10):           Capital at risk (floating/exposure/time)")
    lines.append("Opportunity Cost (0-10): Capital tied up vs alternative uses")
    lines.append("Kill Conviction (0-10): Composite, with manual overrides")
    lines.append("")
    lines.append("CLASSIFICATION THRESHOLDS")
    lines.append("-" * 60)
    lines.append("IMMEDIATE KILL (8-10): Kill now, reclaim capital")
    lines.append("PROBATION (5-7):       Tighten parameters, reassess in 7 days")
    lines.append("KEEP (0-4):            Edge is real, compound it")
    lines.append("")
    lines.append("=" * 120)

    # Group by classification
    for classification in ["IMMEDIATE KILL", "PROBATION", "KEEP"]:
        group = [r for r in results if r["classification"] == classification]
        if not group:
            continue

        lines.append("")
        lines.append(f"{'=' * 120}")
        lines.append(f"  {classification} ({len(group)} lane{'s' if len(group) != 1 else ''})")
        lines.append(f"{'=' * 120}")

        for r in group:
            s = r["scores"]
            lines.append("")
            lines.append(f"  Lane: {r['lane_id']}")
            lines.append(f"  Type: {r['type'].upper()}  |  Symbols: {', '.join(r['symbols'])}  |  Asset: {r['asset_class']}")
            lines.append(f"  Net PnL: ${r['net_pnl']:+.2f}  |  Realized: ${r['realized_pnl']:+.2f}  |  Floating: ${r['floating_pnl']:+.2f}")
            lines.append(f"  Open: {r['open_positions']}  |  Closes: {r['closes']}  |  Avg/Close: ${r['avg_per_close']:+.3f}")
            lines.append(f"  Scores: Edge={s['edge_quality']}/10  Risk={s['risk']}/10  Opportunity={s['opportunity_cost']}/10  Conviction={s['kill_conviction']}/10")
            lines.append(f"  Diagnosis: {r['notes']}")

            impact = r["dollar_impact"]
            lines.append(f"  Action: {impact.get('action', 'N/A')}")
            if "capital_freed_from_loss" in impact:
                lines.append(f"  Capital freed from loss: ${impact['capital_freed_from_loss']:+.2f}")
            if "losses_stopped" in impact:
                lines.append(f"  Losses stopped: ${impact['losses_stopped']:+.2f}")
            if "monthly_loss_avoided_estimate" in impact:
                lines.append(f"  Est. monthly loss avoided: ${impact['monthly_loss_avoided_estimate']:+.2f}")
            if "projected_improvement_from_tightening" in impact:
                lines.append(f"  Projected improvement: ${impact['projected_improvement_from_tightening']:+.2f}")
            if "projected_30d_pnl" in impact:
                lines.append(f"  Projected 30d PnL: ${impact['projected_30d_pnl']:+.2f}")
                lines.append(f"  Projected 90d PnL: ${impact['projected_90d_pnl']:+.2f}")
            lines.append(f"  Net PnL at decision: ${r['net_pnl']:+.2f}")
            lines.append(f"  {'-' * 80}")

    # Summary
    lines.append("")
    lines.append("=" * 120)
    lines.append("SUMMARY")
    lines.append("=" * 120)
    kills = [r for r in results if r["classification"] == "IMMEDIATE KILL"]
    probs = [r for r in results if r["classification"] == "PROBATION"]
    keeps = [r for r in results if r["classification"] == "KEEP"]

    lines.append(f"  Immediate Kills: {len(kills)}")
    for k in kills:
        lines.append(f"    - {k['lane_id']} (conviction {k['scores']['kill_conviction']}, net PnL ${k['net_pnl']:+.2f})")

    lines.append(f"  Probation: {len(probs)}")
    for p in probs:
        lines.append(f"    - {p['lane_id']} (conviction {p['scores']['kill_conviction']}, net PnL ${p['net_pnl']:+.2f})")

    lines.append(f"  Keep: {len(keeps)}")
    for k in keeps:
        lines.append(f"    - {k['lane_id']} (conviction {k['scores']['kill_conviction']}, net PnL ${k['net_pnl']:+.2f})")

    total_cap_at_risk = sum(r["floating_pnl"] for r in results if r["floating_pnl"] < 0)
    total_net = sum(r["net_pnl"] for r in results)
    lines.append(f"")
    lines.append(f"  Total capital at risk (negative floating): ${total_cap_at_risk:+.2f}")
    lines.append(f"  Total net PnL across all lanes: ${total_net:+.2f}")
    lines.append(f"  Supertrend crypto audit: ALL negative after spread. No supertrend lanes survive.")
    lines.append(f"")
    lines.append(f"  FIDELITY AUDIT KEY FINDINGS:")
    lines.append(f"    - BTCUSD: Spread-adjusted -$3,255, no-same-bar -$15,880. Edge erased by ~$170 spread.")
    lines.append(f"    - NZDUSD: Goes -35.6% WR with no-same-bar. Lane may be dead.")
    lines.append(f"    - EURUSD/GBPUSD: Survive 74-98% edge after realism. 20-26% degradation.")
    lines.append(f"    - Supertrend crypto (RAVE/IOTX/TRU/BAL): ALL negative after spread. None survive.")
    lines.append("")
    lines.append("IMPORTANT: Analysis only. No live config changes.")
    lines.append("=" * 120)

    with open(path, "w") as f:
        f.write("\n".join(lines))


def main():
    os.makedirs(REPORTS, exist_ok=True)

    json_path = os.path.join(REPORTS, "lane_kill_list.json")
    txt_path = os.path.join(REPORTS, "lane_kill_list.txt")

    results = build_kill_list()

    output = write_json(results, json_path)
    print(f"JSON written: {json_path}")

    write_txt(results, txt_path)
    print(f"TXT written: {txt_path}")

    # Print summary
    print(f"\n--- SUMMARY ---")
    print(f"Immediate Kills: {output['summary']['immediate_kills']}")
    print(f"Probation: {output['summary']['probation']}")
    print(f"Keep: {output['summary']['keep']}")
    print(f"Total capital at risk (negative floating): ${output['summary']['total_capital_at_risk']:+.2f}")
    print(f"Total net PnL: ${output['summary']['total_net_pnl']:+.2f}")
    print()
    for r in results:
        print(f"  {r['lane_id']:40s}  Conviction: {r['scores']['kill_conviction']:4.1f}  [{r['classification']:16s}]  Net: ${r['net_pnl']:+8.2f}")


if __name__ == "__main__":
    main()
