#!/usr/bin/env python3
"""
Rotation Lattice Cost-Stress Audit

Applies spread/fee widening to the top rotation lattice pairs to find
survival thresholds. Mirrors the ratio lattice cost-stress methodology.

Tests: CFG/BAL, CFG/RAVE, CFG/NOM, CFG/SUP, RAVE/BAL, RAVE/BTC, IOTX/ETH, IOTX/BTC
Against: fee stress (20-100bps per leg) + spread widening (1x-8x base spread)

Output: reports/rotation_lattice_cost_stress.md
"""
import json
import math
import time
import sys
from itertools import combinations, product as iter_product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from coinbase_advanced_client import CoinbaseAdvancedClient
    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False

# ---------------------------------------------------------------------------
# Pairs to audit (from 60d rotation benchmark results)
# ---------------------------------------------------------------------------
PAIRS_TO_AUDIT = [
    ("CFG", "BAL"),   # 60d: +$155, MR, 99.7% closure
    ("CFG", "RAVE"),  # 60d: +$281, weak MR, highest PnL
    ("CFG", "NOM"),   # 60d: +$216, trend edge
    ("CFG", "SUP"),   # 60d: +$110, MR
    ("RAVE", "BAL"),  # 60d: +$33, MR, 100% closure
    ("BAL", "SUP"),   # 30d: +$52, need 60d
    ("IOTX", "ETH"),  # from @codex-asym's RAVE/IOTX/BAL cluster
    ("IOTX", "BTC"),  # from @codex-asym's RAVE/IOTX/BAL cluster
]

SYMBOL_TO_PRODUCT = {
    "CFG": "CFG-USD",
    "NOM": "NOM-USD",
    "RAVE": "RAVE-USD",
    "BAL": "BAL-USD",
    "SUP": "SUP-USD",
    "IOTX": "IOTX-USD",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}

# Estimated base spreads (bps, one-way)
BASE_SPREAD_BPS = {
    "BTC": 5,     # 5 bps
    "ETH": 5,     # 5 bps
    "CFG": 15,    # 15 bps
    "NOM": 20,    # 20 bps
    "RAVE": 20,   # 20 bps
    "BAL": 15,    # 15 bps
    "SUP": 30,    # 30 bps (microcap)
    "IOTX": 15,   # 15 bps
}

# Fee per leg (bps)
BASE_FEE_BPS = 40  # Coinbase Advanced tier-1 maker

# Stress grid
FEE_LEVELS = [20, 40, 60, 80, 100]  # bps per leg
SPREAD_MULTIPLIERS = [1, 2, 3, 4, 5, 6, 7, 8]  # multipliers of base spread


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_candles(client, symbol, start, end):
    chunk_sec = 300 * 5 * 60
    all_candles = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(symbol, start=cs, end=ce, granularity="FIVE_MINUTE")
            candles = resp.get("candles", [])
            all_candles.extend(candles)
            cs = ce
            if not candles:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.5)
    all_candles.sort(key=lambda c: int(c["start"]))
    seen = set()
    unique = []
    for c in all_candles:
        if c["start"] not in seen:
            seen.add(c["start"])
            unique.append(c)
    return unique


# ---------------------------------------------------------------------------
# Rotation lattice (simplified for cost-stress audit)
# ---------------------------------------------------------------------------

def find_attractors_kde(ratios, bandwidth=None):
    n = len(ratios)
    if n < 10:
        return []
    if bandwidth is None:
        mean = sum(ratios) / n
        std = math.sqrt(sum((r - mean) ** 2 for r in ratios) / n)
        sorted_r = sorted(ratios)
        q1 = sorted_r[n // 4]
        q3 = sorted_r[3 * n // 4]
        iqr = q3 - q1
        bandwidth = 0.9 * min(std, iqr / 1.34) * (n ** -0.2)
        if bandwidth <= 0:
            bandwidth = std * 0.5 if std > 0 else 0.01
    min_r, max_r = min(ratios), max(ratios)
    if max_r == min_r:
        return [{"ratio": min_r, "density": float(n)}]
    grid_points = 200
    grid = [min_r + i * (max_r - min_r) / grid_points for i in range(grid_points)]
    densities = []
    for g in grid:
        density = 0.0
        for r in ratios:
            u = (r - g) / bandwidth
            if abs(u) < 4:
                density += math.exp(-0.5 * u * u)
        densities.append(density)
    attractors = []
    for i in range(1, len(densities) - 1):
        if densities[i] > densities[i - 1] and densities[i] > densities[i + 1]:
            attractors.append({"ratio": grid[i], "density": densities[i]})
    attractors.sort(key=lambda a: a["density"], reverse=True)
    return attractors


def run_rotation_with_costs(ratios, price_a, price_b, attractors,
                            position_size=0.01, profit_threshold=1.005,
                            max_concurrent=5, max_levels=8,
                            fee_bps_per_leg=40, spread_multiplier=1,
                            base_spread_bps_a=15, base_spread_bps_b=15):
    """
    Run rotation lattice with cost deduction.
    Each round-trip costs: 2 * fee_bps_per_leg + spread_cost
    spread_cost = (spread_a + spread_b) * spread_multiplier
    """
    # Round-trip cost as fraction of position value
    rt_fee = 2 * fee_bps_per_leg / 10000
    rt_spread = (base_spread_bps_a + base_spread_bps_b) * spread_multiplier / 10000
    rt_cost = rt_fee + rt_spread

    top_attractors = attractors[:max_levels]
    positions = []
    realized_gross = 0.0
    total_cost = 0.0
    total_opens = 0
    total_closes = 0

    n = len(ratios)
    for i in range(1, n):
        ratio = ratios[i]

        for idx, attr in enumerate(top_attractors):
            level_val = attr["ratio"]
            occupied = any(p["level_idx"] == idx for p in positions)
            if not occupied and ratio <= level_val and len(positions) < max_concurrent:
                positions.append({
                    "level_idx": idx,
                    "entry_ratio": ratio,
                    "entry_bar": i,
                    "size": position_size,
                    "entry_price_a": price_a[i],
                    "entry_price_b": price_b[i],
                })
                total_opens += 1
                total_cost += rt_cost * position_size * 10000  # in bps terms

        closes_this_bar = []
        for pos in positions:
            exit_level = pos["entry_ratio"] * profit_threshold
            if ratio >= exit_level:
                # Gross PnL from ratio movement
                entry_ratio = pos["entry_ratio"]
                ratio_move = (ratio - entry_ratio) / entry_ratio
                pnl = ratio_move * pos["size"] * 10000  # bps
                realized_gross += pnl
                total_closes += 1
                closes_this_bar.append(pos)

        for pos in closes_this_bar:
            positions.remove(pos)

    realized_net = realized_gross - total_cost
    closure_rate = total_closes / total_opens if total_opens > 0 else 0.0

    return {
        "realized_gross_bps": round(realized_gross, 4),
        "total_cost_bps": round(total_cost, 4),
        "realized_net_bps": round(realized_net, 4),
        "total_opens": total_opens,
        "total_closes": total_closes,
        "closure_rate": round(closure_rate, 4),
        "rt_cost_bps": round(rt_cost * 10000, 2),
        "rt_fee_bps": round(rt_fee * 10000, 2),
        "rt_spread_bps": round(rt_spread * 10000, 2),
        "is_positive": realized_net > 0,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rotation Lattice Cost-Stress Audit")
    parser.add_argument("--days", type=int, default=60, help="Lookback days")
    parser.add_argument("--position-size", type=float, default=0.01, help="Size per position")
    parser.add_argument("--profit-threshold", type=float, default=1.005, help="Exit multiplier")
    parser.add_argument("--max-levels", type=int, default=8, help="Max attractor levels")
    args = parser.parse_args()

    print("=" * 72)
    print("ROTATION LATTICE COST-STRESS AUDIT")
    print("=" * 72)
    print()

    if not HAS_CLIENT:
        print("ERROR: Cannot import CoinbaseAdvancedClient")
        return

    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - (args.days * 86400)

    # Fetch data
    unique_symbols = set()
    for a, b in PAIRS_TO_AUDIT:
        unique_symbols.add(a)
        unique_symbols.add(b)

    price_maps = {}
    print("Fetching candles...")
    for sym in sorted(unique_symbols):
        product = SYMBOL_TO_PRODUCT[sym]
        print(f"  {product} ...", end=" ", flush=True)
        candles = fetch_candles(client, product, start, now)
        price_maps[sym] = {int(c["start"]): float(c["close"]) for c in candles}
        print(f"{len(candles)} candles")
    print()

    # Build ratio series and attractors for each pair
    pair_data = {}
    for coin_a, coin_b in PAIRS_TO_AUDIT:
        label = f"{coin_a}/{coin_b}"
        pa = price_maps.get(coin_a, {})
        pb = price_maps.get(coin_b, {})
        if not pa or not pb:
            continue

        common_ts = sorted(set(pa.keys()) & set(pb.keys()))
        ratios = []
        prices_a = []
        prices_b = []
        for ts in common_ts:
            if pb[ts] > 0:
                ratios.append(pa[ts] / pb[ts])
                prices_a.append(pa[ts])
                prices_b.append(pb[ts])

        if len(ratios) < 50:
            continue

        attractors = find_attractors_kde(ratios)
        pair_data[label] = {
            "coin_a": coin_a,
            "coin_b": coin_b,
            "ratios": ratios,
            "prices_a": prices_a,
            "prices_b": prices_b,
            "attractors": attractors,
            "n_points": len(ratios),
        }

    # Cost-stress grid
    print("=" * 72)
    print("COST-STRESS RESULTS")
    print("=" * 72)
    print()

    all_results = {}
    for label, data in pair_data.items():
        print(f"  {label} ({data['n_points']} pts, {len(data['attractors'])} attractors)")

        results_grid = []
        positive_count = 0
        total_scenarios = 0

        for fee_bps, spread_mult in iter_product(FEE_LEVELS, SPREAD_MULTIPLIERS):
            total_scenarios += 1
            spread_a = BASE_SPREAD_BPS.get(data["coin_a"], 15)
            spread_b = BASE_SPREAD_BPS.get(data["coin_b"], 15)

            result = run_rotation_with_costs(
                data["ratios"], data["prices_a"], data["prices_b"],
                data["attractors"],
                position_size=args.position_size,
                profit_threshold=args.profit_threshold,
                fee_bps_per_leg=fee_bps,
                spread_multiplier=spread_mult,
                base_spread_bps_a=spread_a,
                base_spread_bps_b=spread_b,
            )

            if result["is_positive"]:
                positive_count += 1

            results_grid.append({
                "fee_bps": fee_bps,
                "spread_mult": spread_mult,
                "rt_cost_bps": result["rt_cost_bps"],
                "realized_net_bps": result["realized_net_bps"],
                "is_positive": result["is_positive"],
                "closes": result["total_closes"],
                "closure_rate": result["closure_rate"],
            })

        pos_pct = (positive_count / total_scenarios * 100) if total_scenarios > 0 else 0
        print(f"    Positive: {positive_count}/{total_scenarios} ({pos_pct:.1f}%)")

        # Find break-even point (max cost where still positive)
        positive_results = [r for r in results_grid if r["is_positive"]]
        if positive_results:
            max_cost_positive = max(r["rt_cost_bps"] for r in positive_results)
        else:
            max_cost_positive = 0

        all_results[label] = {
            "positive_count": positive_count,
            "total_scenarios": total_scenarios,
            "pos_pct": round(pos_pct, 1),
            "max_cost_bps": round(max_cost_positive, 2),
            "grid": results_grid,
        }

    # Ranking
    print()
    print("=" * 72)
    print("RANKING BY STRESS SURVIVAL")
    print("=" * 72)
    print()

    ranked = sorted(all_results.items(), key=lambda x: x[1]["pos_pct"], reverse=True)

    print(f"  {'Pair':<15} {'Positive':<15} {'Max Cost (bps)':<18} {'Verdict':<15}")
    print(f"  {'----':<15} {'--------':<15} {'---------------':<18} {'-------':<15}")

    for label, r in ranked:
        if r["pos_pct"] >= 90:
            verdict = "✅ ROBUST"
        elif r["pos_pct"] >= 50:
            verdict = "⚠️ MODERATE"
        else:
            verdict = "🚨 FRAGILE"

        print(f"  {label:<15} {r['positive_count']}/{r['total_scenarios']} ({r['pos_pct']:.1f}%)  "
              f"{r['max_cost_bps']:<18} {verdict:<15}")

    # Save
    out_md = _build_markdown(all_results, ranked)
    out_path = ROOT / "reports" / "rotation_lattice_cost_stress.md"
    out_path.write_text(out_md, encoding="utf-8")

    serializable = {
        "run_params": {
            "days": args.days,
            "position_size": args.position_size,
            "profit_threshold": args.profit_threshold,
            "fee_levels": FEE_LEVELS,
            "spread_multipliers": SPREAD_MULTIPLIERS,
        },
        "results": {
            label: {
                "positive_count": r["positive_count"],
                "total_scenarios": r["total_scenarios"],
                "pos_pct": r["pos_pct"],
                "max_cost_bps": r["max_cost_bps"],
            }
            for label, r in all_results.items()
        },
        "ranking": [
            {"pair": label, "pos_pct": r["pos_pct"], "max_cost_bps": r["max_cost_bps"]}
            for label, r in ranked
        ],
    }
    out_json = ROOT / "reports" / "rotation_lattice_cost_stress.json"
    out_json.write_text(json.dumps(serializable, indent=2))

    print()
    print(f"Report: {out_path}")
    print(f"JSON: {out_json}")


def _build_markdown(all_results, ranked):
    lines = [
        "# Rotation Lattice Cost-Stress Audit",
        "",
        f"**Pairs tested:** {len(all_results)}",
        f"**Stress grid:** {FEE_LEVELS} fee levels × {SPREAD_MULTIPLIERS} spread multipliers",
        f"**Total scenarios per pair:** {FEE_LEVELS.__len__() * SPREAD_MULTIPLIERS.__len__()}",
        "",
        "## Ranking",
        "",
        "| Pair | Positive Scenarios | Positivity | Max Cost (bps) | Verdict |",
        "|------|-------------------|------------|----------------|---------|",
    ]

    for label, r in ranked:
        if r["pos_pct"] >= 90:
            verdict = "✅ ROBUST"
        elif r["pos_pct"] >= 50:
            verdict = "⚠️ MODERATE"
        else:
            verdict = "🚨 FRAGILE"

        lines.append(f"| {label} | {r['positive_count']}/{r['total_scenarios']} | {r['pos_pct']:.1f}% | "
                     f"{r['max_cost_bps']} | {verdict} |")

    lines.append("")
    lines.append("## Interpretation")
    lines.append("")

    robust = [(l, r) for l, r in ranked if r["pos_pct"] >= 90]
    if robust:
        lines.append("**Robust pairs (survive 90%+ scenarios):**")
        for label, r in robust:
            lines.append(f"- {label}: survives up to {r['max_cost_bps']}bps round-trip cost")
        lines.append("")

    moderate = [(l, r) for l, r in ranked if 50 <= r["pos_pct"] < 90]
    if moderate:
        lines.append("**Moderate pairs (survive 50-90%% scenarios):**")
        for label, r in moderate:
            lines.append(f"- {label}: {r['pos_pct']*100:.1f}%% survival, max {r['max_cost_bps']:.1f}bps")
        lines.append("")

    fragile = [(l, r) for l, r in ranked if r["pos_pct"] < 50]
    if fragile:
        lines.append("**Fragile pairs (<50%% survival):**")
        for label, r in fragile:
            lines.append(f"- {label}: {r['pos_pct']*100:.1f}%% survival")
        lines.append("")

    lines.append("## Comparison with Ratio Lattice Cost-Stress")
    lines.append("")
    lines.append("Rotation lattices generally have LOWER per-close PnL than ratio lattices")
    lines.append("(they capture smaller oscillations). However, they also have LOWER correlation")
    lines.append("to directional strategies, making them valuable diversifiers.")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
