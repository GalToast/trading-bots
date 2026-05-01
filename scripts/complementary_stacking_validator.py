"""
Complementary Stacking Validator

Analyzes which strategy pairs on the same coin are truly complementary
(low correlation, different firing conditions) vs redundant (high correlation,
same firing conditions).

Works offline using existing report data. No live API or MT5 required.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = SCRIPT_DIR.parent / "reports"

EDGE_REGISTRY = REPORTS_DIR / "edge_registry.json"
OPTIMAL_ASSIGNMENT = REPORTS_DIR / "optimal_coin_strategy_assignment.json"
MULTI_STRATEGY = REPORTS_DIR / "multi_strategy_portfolio_results.json"
ROUTER_BOARD = REPORTS_DIR / "coinbase_spot_hypergrowth_router_board.json"

OUTPUT_MD = REPORTS_DIR / "complementary_stacking_validator.md"
OUTPUT_JSON = REPORTS_DIR / "complementary_stacking_validator.json"

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

CORR_LOW = 0.30       # Below this => low correlation
CORR_HIGH = 0.70      # Above this => high correlation
CORR_NEG = -0.20      # Below this => negative correlation

FIRE_OVERLAP_LOW = 0.30   # Below => different firing conditions
FIRE_OVERLAP_HIGH = 0.70  # Above => same firing conditions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return json.load(f)


def safe_div(a, b, default=0.0):
    return a / b if b else default


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

def extract_strategies(registry: dict) -> dict:
    """Return {strategy_name: {category, entry_logic, coins, params, ...}}"""
    return registry.get("strategies", {})


def extract_multi_strategy_correlations(multi: dict) -> dict:
    """
    Flatten all correlation dicts from multi_strategy_portfolio_results.
    Returns dict of "StrategyA<->StrategyB": correlation_value
    """
    all_corrs: dict[str, float] = {}
    for section in ("equal_allocation", "optimized_allocation", "rave_only"):
        sec = multi.get(section, {})
        corrs = sec.get("correlations", {})
        for pair, val in corrs.items():
            all_corrs[pair] = float(val)
    return all_corrs


def extract_multi_strategy_individual(multi: dict) -> list[dict]:
    """Collect all individual strategy entries across all sections."""
    results = []
    for section in ("equal_allocation", "optimized_allocation", "rave_only"):
        sec = multi.get(section, {})
        for entry in sec.get("individual", []):
            entry_copy = dict(entry)
            entry_copy["_section"] = section
            results.append(entry_copy)
    return results


def extract_router_stacking(router: dict) -> list[dict]:
    """Return rows that have stacking info (primary + secondary lanes)."""
    rows = router.get("rows", [])
    stacking = []
    for row in rows:
        if row.get("secondary_family") and row["secondary_family"] != "other":
            stacking.append(row)
    return stacking


def compute_fire_overlap(strategy_a: dict, strategy_b: dict, coin: str) -> float:
    """
    Estimate firing condition overlap between two strategies on a given coin.
    Uses signal count ratio and entry-logic similarity as a proxy.

    Returns 0.0 (no overlap) to 1.0 (complete overlap).
    """
    coin_a = strategy_a.get("coins", {}).get(coin, {})
    coin_b = strategy_b.get("coins", {}).get(coin, {})

    signals_a = coin_a.get("signals", 0)
    signals_b = coin_b.get("signals", 0)

    if isinstance(signals_a, str) or isinstance(signals_b, str):
        # "verified" strings from rsi_mean_reversion - cannot compute
        return -1.0

    if signals_a == 0 or signals_b == 0:
        return 0.0

    # Overlap proxy: smaller / larger signal count ratio
    # If both fire ~same number of times, they likely fire under similar conditions
    signal_ratio = min(signals_a, signals_b) / max(signals_a, signals_b)

    # Entry logic similarity penalty
    entry_a = strategy_a.get("entry_logic", "").lower()
    entry_b = strategy_b.get("entry_logic", "").lower()

    # Category overlap
    cat_a = strategy_a.get("category", "")
    cat_b = strategy_b.get("category", "")
    category_same = 1.0 if cat_a == cat_b else 0.0

    # Heuristic: signal_ratio is the main driver, category adjusts
    # Same category => higher overlap; different => lower
    overlap = signal_ratio * 0.6 + category_same * 0.4

    return min(overlap, 1.0)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_pair(correlation: float, fire_overlap: float) -> dict:
    """Classify a strategy pair based on correlation and firing overlap."""
    # Handle missing data
    if correlation is None:
        corr_label = "unknown"
    elif correlation < CORR_NEG:
        corr_label = "negative"
    elif correlation < CORR_LOW:
        corr_label = "low"
    elif correlation < CORR_HIGH:
        corr_label = "moderate"
    else:
        corr_label = "high"

    if fire_overlap < 0:
        fire_label = "unknown"
    elif fire_overlap < FIRE_OVERLAP_LOW:
        fire_label = "different"
    elif fire_overlap < FIRE_OVERLAP_HIGH:
        fire_label = "partial"
    else:
        fire_label = "same"

    # Composite classification
    if corr_label == "negative":
        classification = "OPPOSING"
        score = abs(correlation) if correlation is not None else 0.0
    elif corr_label in ("low",) and fire_label in ("different",):
        # Best case: low correlation + different firing
        classification = "COMPLEMENTARY"
        score = (1.0 - abs(correlation)) * 0.5 + (1.0 - fire_overlap) * 0.5 if correlation is not None and fire_overlap >= 0 else 0.7
    elif corr_label in ("low",) and fire_label == "partial":
        # Low correlation is strong signal even if some firing overlap
        classification = "COMPLEMENTARY"
        score = (1.0 - abs(correlation)) * 0.6 + (1.0 - fire_overlap) * 0.4 if correlation is not None and fire_overlap >= 0 else 0.55
    elif corr_label == "moderate" and fire_label in ("different",):
        classification = "COMPLEMENTARY"
        score = (1.0 - abs(correlation)) * 0.5 + (1.0 - fire_overlap) * 0.5 if correlation is not None and fire_overlap >= 0 else 0.45
    elif corr_label == "moderate" and fire_label == "partial":
        classification = "PARTIALLY_COMPLEMENTARY"
        score = 0.2
    elif corr_label in ("low", "moderate") and fire_label == "same":
        classification = "PARTIALLY_COMPLEMENTARY"
        score = 0.15
    elif corr_label == "high" and fire_label == "same":
        classification = "REDUNDANT"
        score = -(abs(correlation) + fire_overlap) / 2.0
    elif corr_label == "high" and fire_label in ("different", "partial"):
        classification = "CONDITIONALLY_REDUNDANT"
        score = -0.2
    elif corr_label == "unknown" and fire_label in ("different", "unknown"):
        classification = "COMPLEMENTARY"
        score = 0.5
    else:
        classification = "NEUTRAL"
        score = 0.0

    return {
        "classification": classification,
        "correlation_label": corr_label,
        "fire_overlap_label": fire_label,
        "score": round(score, 4),
    }


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyze() -> dict:
    registry = load_json(EDGE_REGISTRY)
    optimal = load_json(OPTIMAL_ASSIGNMENT)
    multi = load_json(MULTI_STRATEGY)
    router = load_json(ROUTER_BOARD)

    strategies = extract_strategies(registry)

    # Inject theil_sen metadata (present in optimal assignment but not edge registry)
    if "theil_sen" not in strategies:
        theil_coins = optimal.get("results", {})
        theil_coin_data = {}
        for coin, coin_res in theil_coins.items():
            ts = coin_res.get("theil_sen_best", {})
            if ts:
                theil_coin_data[coin] = {
                    "net_pnl": ts.get("net_pnl", 0),
                    "signals": ts.get("trades", 0),
                    "trades": ts.get("trades", 0),
                    "win_rate": ts.get("win_rate", 0),
                }
        strategies["theil_sen"] = {
            "category": "trend_following",
            "entry_logic": "Theil-Sen robust linear regression slope turns positive",
            "coins": theil_coin_data,
            "params": "Per-coin optimized (see optimal_coin_strategy_assignment.json)",
        }

    corr_map = extract_multi_strategy_correlations(multi)
    individual_strats = extract_multi_strategy_individual(multi)
    stacking_rows = extract_router_stacking(router)

    # ---- Build per-coin strategy list ----
    coin_strategies: dict[str, list[str]] = {}
    for strat_name, strat_data in strategies.items():
        for coin in strat_data.get("coins", {}):
            coin_strategies.setdefault(coin, []).append(strat_name)

    # Also add strategies from optimal assignment (momentum, theil_sen)
    for coin in optimal.get("coins_tested", []):
        coin_strategies.setdefault(coin, []).append("momentum")
        coin_strategies.setdefault(coin, []).append("theil_sen")

    # Deduplicate
    for coin in coin_strategies:
        coin_strategies[coin] = list(set(coin_strategies[coin]))

    # ---- Analyze pairs ----
    pair_analyses = []
    stack_recommendations = []

    for coin, strat_list in sorted(coin_strategies.items()):
        if len(strat_list) < 2:
            continue

        for i in range(len(strat_list)):
            for j in range(i + 1, len(strat_list)):
                sa = strat_list[i]
                sb = strat_list[j]
                pair_key = f"{coin}: {sa} <-> {sb}"

                # Fetch strategy data first (needed for both correlation estimation and fire overlap)
                strat_a_data = strategies.get(sa, {})
                strat_b_data = strategies.get(sb, {})

                # 1. Lookup correlation
                # The multi_strategy results contain cross-coin correlations
                # (e.g. "RAVE Momentum <-> BAL Momentum"), not same-coin strategy pairs.
                # We use these to estimate same-coin correlation by matching strategy
                # type and checking if either strategy appears on this coin.
                correlation = None
                for mk, mv in corr_map.items():
                    # Parse "STRATEGY_A <-> STRATEGY_B" format
                    if "<->" not in mk:
                        continue
                    parts = [p.strip() for p in mk.split("<->")]
                    if len(parts) != 2:
                        continue

                    # Extract strategy type from names like "RAVE Momentum", "IOTX BB Rev"
                    # Strategy type is usually the last word(s)
                    def extract_strategy_type(name):
                        # Map known patterns
                        name_lower = name.lower()
                        if "momentum" in name_lower:
                            return "momentum"
                        if "rsi" in name_lower or "mean rev" in name_lower:
                            return "rsi_mean_reversion"
                        if "bb" in name_lower:
                            return "bollinger"
                        return name_lower.split()[-1]

                    type_a = extract_strategy_type(parts[0])
                    type_b = extract_strategy_type(parts[1])

                    # Match if strategy types align
                    sa_match = sa == type_a or sa == type_b
                    sb_match = sb == type_a or sb == type_b
                    cross_match = (sa == type_a and sb == type_b) or \
                                  (sa == type_b and sb == type_a)

                    if cross_match:
                        # Use cross-coin correlation as a rough proxy
                        # Same-coin correlation tends to be higher than cross-coin
                        # Apply a small upward adjustment
                        correlation = round(min(mv * 1.3, 0.95), 4)
                        break

                # If still no correlation found, estimate from category similarity
                if correlation is None:
                    cat_a = strat_a_data.get("category", "")
                    cat_b = strat_b_data.get("category", "")
                    if cat_a == cat_b:
                        # Same family strategies tend to correlate moderately
                        correlation = 0.55
                    elif (cat_a == "breakout" and cat_b == "trend_following") or \
                         (cat_a == "trend_following" and cat_b == "breakout"):
                        correlation = 0.40
                    elif (cat_a == "mean_reversion" and cat_b in ("breakout", "trend_following", "breakout")) or \
                         (cat_b == "mean_reversion" and cat_a in ("breakout", "trend_following")):
                        correlation = -0.10
                    elif cat_a == "time_based" or cat_b == "time_based":
                        correlation = 0.15
                    elif cat_a == "hybrid" or cat_b == "hybrid":
                        correlation = 0.25
                    else:
                        correlation = 0.20

                # 2. Compute firing overlap
                fire_overlap = compute_fire_overlap(strat_a_data, strat_b_data, coin)

                # 3. Get per-coin performance for each strategy
                perf_a = strat_a_data.get("coins", {}).get(coin, {})
                perf_b = strat_b_data.get("coins", {}).get(coin, {})

                pnl_a = perf_a.get("net_pnl", 0) if isinstance(perf_a.get("net_pnl", 0), (int, float)) else 0
                pnl_b = perf_b.get("net_pnl", 0) if isinstance(perf_b.get("net_pnl", 0), (int, float)) else 0
                wr_a = perf_a.get("win_rate", 0) if isinstance(perf_a.get("win_rate", 0), (int, float)) else 0
                wr_b = perf_b.get("win_rate", 0) if isinstance(perf_b.get("win_rate", 0), (int, float)) else 0
                sig_a = perf_a.get("signals", 0) if isinstance(perf_a.get("signals", 0), (int, float)) else 0
                sig_b = perf_b.get("signals", 0) if isinstance(perf_b.get("signals", 0), (int, float)) else 0

                # 4. Classify
                classification = classify_pair(correlation, fire_overlap)

                # 5. Stacking recommendation
                combined_pnl = pnl_a + pnl_b
                if classification["classification"] == "COMPLEMENTARY":
                    recommendation = "STACK"
                elif classification["classification"] == "OPPOSING":
                    recommendation = "STACK_HEDGE"
                elif classification["classification"] == "REDUNDANT":
                    recommendation = "SOLO_BEST"
                elif classification["classification"] == "CONDITIONALLY_REDUNDANT":
                    recommendation = "SOLO_BEST"
                elif classification["classification"] == "PARTIALLY_COMPLEMENTARY":
                    recommendation = "STACK_CAUTIOUS"
                else:
                    recommendation = "EVALUATE"

                entry = {
                    "coin": coin,
                    "strategy_a": sa,
                    "strategy_b": sb,
                    "category_a": strat_a_data.get("category", "unknown"),
                    "category_b": strat_b_data.get("category", "unknown"),
                    "correlation": correlation,
                    "fire_overlap": round(fire_overlap, 4) if fire_overlap >= 0 else None,
                    "pnl_a": pnl_a,
                    "pnl_b": pnl_b,
                    "win_rate_a": wr_a,
                    "win_rate_b": wr_b,
                    "signals_a": sig_a,
                    "signals_b": sig_b,
                    "combined_pnl": round(combined_pnl, 2),
                    "classification": classification["classification"],
                    "score": classification["score"],
                    "recommendation": recommendation,
                }
                pair_analyses.append(entry)

    # ---- Router-level stacking policy cross-reference ----
    for row in stacking_rows:
        coin = row.get("coin", "")
        primary = row.get("primary_family", "")
        secondary = row.get("secondary_family", "")
        policy = row.get("same_coin_stack_policy", "")

        if not coin or not secondary or secondary == "other":
            continue

        # Find matching pair analysis
        matched = None
        for pa in pair_analyses:
            if pa["coin"] == coin:
                fam_a = pa.get("category_a", "")
                fam_b = pa.get("category_b", "")
                if (primary in fam_a and secondary in fam_b) or \
                   (secondary in fam_a and primary in fam_b):
                    matched = pa
                    break

        stack_recommendations.append({
            "coin": coin,
            "primary_family": primary,
            "secondary_family": secondary,
            "router_policy": policy,
            "router_decision": row.get("admission_decision", ""),
            "max_live_lanes": row.get("max_live_lanes", 1),
            "matched_pair": matched,
        })

    # ---- Rank by complementarity score ----
    pair_analyses.sort(key=lambda x: x["score"], reverse=True)

    # ---- Summary stats ----
    counts = {}
    for pa in pair_analyses:
        c = pa["classification"]
        counts[c] = counts.get(c, 0) + 1

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": {
            "total_pairs_analyzed": len(pair_analyses),
            "classification_counts": counts,
            "stack_count": sum(1 for p in pair_analyses if p["recommendation"] in ("STACK", "STACK_HEDGE", "STACK_CAUTIOUS")),
            "solo_count": sum(1 for p in pair_analyses if p["recommendation"] == "SOLO_BEST"),
        },
        "pair_analyses": pair_analyses,
        "router_stacking_cross_reference": stack_recommendations,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_markdown(report: dict) -> str:
    lines: list[str] = []

    def heading(text, level=1):
        prefix = "#" * level
        lines.append(f"{prefix} {text}")

    def blank():
        lines.append("")

    heading("Complementary Stacking Validator", 1)
    blank()
    lines.append(f"*Generated: {report['generated_at']}*")
    blank()

    # ---- Summary ----
    heading("Summary", 2)
    summary = report["summary"]
    lines.append(f"- **Total strategy pairs analyzed:** {summary['total_pairs_analyzed']}")
    lines.append(f"- **Recommended to STACK:** {summary['stack_count']}")
    lines.append(f"- **Recommended SOLO (best only):** {summary['solo_count']}")
    blank()

    if summary["classification_counts"]:
        lines.append("| Classification | Count |")
        lines.append("|---|---|")
        for cls, count in sorted(summary["classification_counts"].items()):
            lines.append(f"| {cls} | {count} |")
        blank()

    # ---- Ranked pair analyses ----
    heading("Strategy Pair Rankings (by complementarity score)", 2)
    blank()

    if not report["pair_analyses"]:
        lines.append("*No pairs with multiple strategies found on the same coin.*")
        blank()
    else:
        # Group by classification
        by_class: dict[str, list] = {}
        for pa in report["pair_analyses"]:
            by_class.setdefault(pa["classification"], []).append(pa)

        for classification in ("COMPLEMENTARY", "PARTIALLY_COMPLEMENTARY", "OPPOSING",
                               "NEUTRAL", "CONDITIONALLY_REDUNDANT", "REDUNDANT"):
            items = by_class.get(classification, [])
            if not items:
                continue

            heading(f"{classification}", 3)
            blank()
            for pa in items:
                lines.append(f"**{pa['coin']}** | {pa['strategy_a']} vs {pa['strategy_b']}")
                lines.append(f"- Score: `{pa['score']}` | Recommendation: **{pa['recommendation']}**")
                lines.append(f"- Correlation: {pa['correlation']} | Fire overlap: {pa['fire_overlap']}")
                lines.append(f"- PnL: {pa['strategy_a']}=${pa['pnl_a']:.2f}, {pa['strategy_b']}=${pa['pnl_b']:.2f} | Combined: **${pa['combined_pnl']:.2f}**")
                lines.append(f"- Win rates: {pa['win_rate_a']}% / {pa['win_rate_b']}% | Signals: {pa['signals_a']} / {pa['signals_b']}")
                blank()

    # ---- Router cross-reference ----
    if report["router_stacking_cross_reference"]:
        heading("Router Board Stacking Policy Cross-Reference", 2)
        blank()
        lines.append("| Coin | Primary | Secondary | Router Policy | Recommendation |")
        lines.append("|---|---|---|---|---|")
        for ref in report["router_stacking_cross_reference"]:
            matched_rec = ref["matched_pair"]["recommendation"] if ref["matched_pair"] else "N/A"
            lines.append(
                f"| {ref['coin']} | {ref['primary_family']} | {ref['secondary_family']} "
                f"| {ref['router_policy'] or '—'} | {matched_rec} |"
            )
        blank()

    # ---- Recommendations ----
    heading("Actionable Recommendations", 2)
    blank()

    stack_pairs = [p for p in report["pair_analyses"] if p["recommendation"] == "STACK"]
    stack_hedge = [p for p in report["pair_analyses"] if p["recommendation"] == "STACK_HEDGE"]
    stack_cautious = [p for p in report["pair_analyses"] if p["recommendation"] == "STACK_CAUTIOUS"]
    solo = [p for p in report["pair_analyses"] if p["recommendation"] == "SOLO_BEST"]

    if stack_pairs:
        heading("STACK these pairs (complementary edges)", 3)
        for p in stack_pairs:
            lines.append(f"- **{p['coin']}**: {p['strategy_a']} + {p['strategy_b']} (combined PnL: ${p['combined_pnl']:.2f})")
        blank()

    if stack_hedge:
        heading("STACK AS HEDGE (opposing edges for drawdown protection)", 3)
        for p in stack_hedge:
            lines.append(f"- **{p['coin']}**: {p['strategy_a']} + {p['strategy_b']} (negative correlation hedges risk)")
        blank()

    if stack_cautious:
        heading("STACK CAUTIOUSLY (partial complementarity)", 3)
        for p in stack_cautious:
            lines.append(f"- **{p['coin']}**: {p['strategy_a']} + {p['strategy_b']} (monitor overlap)")
        blank()

    if solo:
        heading("Keep SOLO (redundant - run only the stronger one)", 3)
        for p in solo:
            winner = p["strategy_a"] if p["pnl_a"] >= p["pnl_b"] else p["strategy_b"]
            winner_pnl = max(p["pnl_a"], p["pnl_b"])
            lines.append(f"- **{p['coin']}**: {winner} (${winner_pnl:.2f}) > {p['strategy_b'] if winner == p['strategy_a'] else p['strategy_a']}")
        blank()

    # ---- Notes ----
    heading("Methodology Notes", 2)
    blank()
    lines.append("- **Correlation**: Cross-coin correlations from multi_strategy_portfolio_results.json matched by strategy type and adjusted upward as a same-coin proxy. Where no match exists, estimated from category similarity (same family = higher, mean-reversion vs breakout = negative/near-zero).")
    lines.append("- **Fire overlap**: Heuristic based on signal-count ratio and strategy category similarity. Lower = strategies fire at different times.")
    lines.append("- **COMPLEMENTARY**: Low correlation + different firing conditions = diversification benefit.")
    lines.append("- **REDUNDANT**: High correlation + same firing = running both wastes capital on the same edge.")
    lines.append("- **OPPOSING**: Negative correlation = natural hedge, good for drawdown protection.")
    lines.append("- All analysis is offline, based on existing 30d backtest reports.")
    blank()

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Complementary Stacking Validator")
    print("=" * 60)

    # Validate inputs
    missing = []
    for p, name in [
        (EDGE_REGISTRY, "edge_registry.json"),
        (OPTIMAL_ASSIGNMENT, "optimal_coin_strategy_assignment.json"),
        (MULTI_STRATEGY, "multi_strategy_portfolio_results.json"),
        (ROUTER_BOARD, "coinbase_spot_hypergrowth_router_board.json"),
    ]:
        if not p.exists():
            missing.append(name)
        else:
            print(f"  [OK] {name}")

    if missing:
        print(f"\n[ERROR] Missing required reports: {', '.join(missing)}")
        print(f"Expected directory: {REPORTS_DIR}")
        sys.exit(1)

    print()

    # Run analysis
    report = analyze()
    summary = report["summary"]
    print(f"Pairs analyzed: {summary['total_pairs_analyzed']}")
    print(f"Classification breakdown:")
    for cls, count in sorted(summary["classification_counts"].items()):
        print(f"  {cls}: {count}")
    print(f"Recommend STACK: {summary['stack_count']}")
    print(f"Recommend SOLO:  {summary['solo_count']}")
    print()

    # Write outputs
    md = generate_markdown(report)

    with open(OUTPUT_MD, "w") as f:
        f.write(md)
    print(f"[WRITE] {OUTPUT_MD}")

    with open(OUTPUT_JSON, "w") as f:
        json.dump(report, f, indent=2)
    print(f"[WRITE] {OUTPUT_JSON}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
