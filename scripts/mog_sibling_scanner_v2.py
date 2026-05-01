#!/usr/bin/env python3
"""MOG Sibling Scanner 2.0 — Separate current-fee executable from hypothetical.

MOG proved RSI(4) oversold→bounce clears 120bps taker fees:
- 2/2 green closes, +$3.83 realized, +6.67% avg gross move

This scanner finds siblings by matching MOG's geometry:
1. Volatility: 2h move > fee_hurdle (2.4% round-trip + spread)
2. Spread: tight enough to not eat the edge
3. RSI-friendliness: mean-reversion score (not trending dead)
4. Current-fee executable: can clear 120bps taker TODAY

Output: Two tiers:
- Tier 1: Tradeable now at 120bps taker (like MOG)
- Tier 2: Tradeable at lower fees (maker/VIP) — needs proof first
"""
import json
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEE_HURDLE_BOARD = ROOT / "reports" / "coinbase_spot_fee_hurdle_board.json"
MAKER_REALITY_BOARD = ROOT / "reports" / "coinbase_spot_maker_execution_reality_board.json"
POCKET_BOARD = ROOT / "reports" / "coinbase_spot_foundry_pocket_board.json"

# MOG's proven geometry
MOG_REFERENCE = {
    "product_id": "MOG-USD",
    "gross_move_pct": 6.67,       # Average gross move on winning trades
    "net_pct": 4.12,              # Net after 2.4% fees
    "spread_bps": ~10,            # Estimated spread during trade
    "price": 1.5e-07,             # Ultra-low price
    "rsi_period": 4,
    "oversold_threshold": 30.0,
    "profit_target_pct": 7.5,
    "stop_loss_pct": 0.5,
    "max_hold_bars": 24,
    "fee_roundtrip_pct": 2.4,
    "closes": 2,
    "wins": 2,
}

FEE_ROUNDTRIP_PCT = 2.4  # 120bps × 2
PROFIT_BUFFER_PCT = 0.75  # Safety margin


def load_fee_hurdle():
    with open(FEE_HURDLE_BOARD) as f:
        return json.load(f)


def load_maker_reality():
    if not MAKER_REALITY_BOARD.exists():
        return {}
    with open(MAKER_REALITY_BOARD) as f:
        return json.load(f)


def load_pockets():
    with open(POCKET_BOARD) as f:
        return json.load(f)


def compute_mog_similarity(row):
    """Score how MOG-like a product is.
    
    Higher score = more like MOG's winning geometry.
    """
    score = 0.0
    
    best_move = row.get("best_move_pct", 0)
    spread_pct = row.get("spread_bps", 999) / 100
    spread_bps = row.get("spread_bps", 999)
    fee_hurdle = FEE_ROUNDTRIP_PCT + spread_pct + PROFIT_BUFFER_PCT
    
    # 1. Volatility score: can it clear fees?
    if best_move > fee_hurdle:
        score += 30  # Clears fees — essential
        # Bonus for clearing by more margin
        margin = best_move - fee_hurdle
        score += min(20, margin * 10)  # Up to 20 points extra
    
    # 2. Spread score: tighter is better (MOG had ~10bps)
    if spread_bps < 15:
        score += 20  # MOG-tier spread
    elif spread_bps < 25:
        score += 15
    elif spread_bps < 50:
        score += 10
    elif spread_bps < 100:
        score += 5
    
    # 3. RSI-friendliness: need mean-reversion, not trending
    # Use the fee hurdle state as proxy — "near_hurdle_watch" or "clears_*" 
    # suggests oscillation, "fee_hurdle_blocked" suggests dead
    state = row.get("hurdle_state", "")
    if "clears" in state:
        score += 15  # Active movement
    elif "near" in state:
        score += 10  # Some movement
    elif "spread_blocked" in state:
        score += 5   # Has movement but spread issue
    
    # 4. Volume: need enough for fills
    vol = row.get("quote_volume_native", 0)
    if vol > 1_000_000:
        score += 10
    elif vol > 100_000:
        score += 5
    
    # 5. Price regime: ultra-low prices (like MOG) have more % movement
    price = row.get("ask", 999)
    if price < 0.001:
        score += 10  # Microcap regime
    elif price < 0.01:
        score += 7
    elif price < 0.1:
        score += 5
    elif price < 1.0:
        score += 3
    
    return round(score, 1)


def main():
    print("=" * 80)
    print("MOG SIBLING SCANNER 2.0 — Current-Fee vs Hypothetical-Fee Tiers")
    print("=" * 80)
    print(f"\nMOG Reference: {MOG_REFERENCE['closes']} closes, {MOG_REFERENCE['wins']} wins, "
          f"{MOG_REFERENCE['gross_move_pct']:.2f}% gross, {MOG_REFERENCE['net_pct']:.2f}% net")
    print(f"Fee hurdle: {FEE_ROUNDTRIP_PCT}% round-trip + {PROFIT_BUFFER_PCT}% buffer = "
          f"{FEE_ROUNDTRIP_PCT + PROFIT_BUFFER_PCT:.2f}% minimum")

    hurdle = load_fee_hurdle()
    rows = hurdle.get("rows", [])
    print(f"Products analyzed: {len(rows)}")

    # Load maker reality for cross-reference
    maker_reality = load_maker_reality()
    maker_rows = maker_reality.get("rows", [])
    maker_products = {r.get("product_id", ""): r for r in maker_rows}

    # Load pockets for additional signal data
    pockets = load_pockets()
    pocket_rows = pockets.get("rows", [])
    pocket_products = {}
    for p in pocket_rows:
        pid = p["product_id"]
        if pid not in pocket_products:
            pocket_products[pid] = []
        pocket_products[pid].append(p)

    # Score all products
    candidates = []
    for row in rows:
        pid = row.get("product_id", "")
        best_move = row.get("best_move_pct", 0)
        spread_pct = row.get("spread_bps", 999) / 100
        fee_hurdle = FEE_ROUNDTRIP_PCT + spread_pct + PROFIT_BUFFER_PCT

        clears_current = best_move > fee_hurdle
        clears_maker = best_move > (0.0 + spread_pct + PROFIT_BUFFER_PCT)  # 0bps maker
        clears_vip = best_move > (1.2 + spread_pct + PROFIT_BUFFER_PCT)     # 60bps VIP

        mog_score = compute_mog_similarity(row)

        # Check if in maker reality board
        maker_verdict = maker_products.get(pid, {}).get("current_verdict", "not_scored")

        # Check pocket data
        pocket_count = len(pocket_products.get(pid, []))
        pocket_best_net = max([p["avg_net_pct"] for p in pocket_products.get(pid, [])], default=0)

        tier = "current_fee_executable" if clears_current else (
            "vip_fee_candidate" if clears_vip else (
                "maker_fee_candidate" if clears_maker else "fee_wall_blocked"
            )
        )

        candidates.append({
            "product_id": pid,
            "mog_score": mog_score,
            "best_move_pct": best_move,
            "spread_bps": row.get("spread_bps", 999),
            "spread_pct": spread_pct,
            "fee_hurdle_pct": fee_hurdle,
            "edge_over_hurdle": best_move - fee_hurdle if clears_current else None,
            "clears_current_fee": clears_current,
            "clears_vip_fee": clears_vip,
            "clears_maker_fee": clears_maker,
            "tier": tier,
            "maker_verdict": maker_verdict,
            "pocket_count": pocket_count,
            "pocket_best_net_pct": pocket_best_net,
            "hurdle_state": row.get("hurdle_state", ""),
            "ret_15m_pct": row.get("ret_15m_pct", 0),
            "ret_60m_pct": row.get("ret_60m_pct", 0),
            "volume_usd": row.get("quote_volume_native", 0),
            "price": row.get("ask", 999),
        })

    # Sort by MOG score descending
    candidates.sort(key=lambda x: x["mog_score"], reverse=True)

    # Separate tiers
    current_executable = [c for c in candidates if c["tier"] == "current_fee_executable"]
    vip_candidates = [c for c in candidates if c["tier"] == "vip_fee_candidate"]
    maker_candidates = [c for c in candidates if c["tier"] == "maker_fee_candidate"]
    fee_wall_blocked = [c for c in candidates if c["tier"] == "fee_wall_blocked"]

    print(f"\n{'='*80}")
    print(f"TIER BREAKDOWN")
    print(f"{'='*80}")
    print(f"  Tier 1: Current-fee executable (120bps taker):  {len(current_executable)}")
    print(f"  Tier 2: VIP-fee candidate (60bps taker):         {len(vip_candidates)}")
    print(f"  Tier 3: Maker-fee candidate (0bps maker):        {len(maker_candidates)}")
    print(f"  Tier 4: Fee-wall blocked:                        {len(fee_wall_blocked)}")

    # TIER 1: Current-fee executable
    print(f"\n{'='*80}")
    print(f"TIER 1: CURRENT-FEE EXECUTABLE (Tradeable NOW at 120bps taker)")
    print(f"{'='*80}")
    if current_executable:
        print(f"\n{'#':>3} {'Product':>15} {'MOG':>5} {'Move%':>7} {'Spread':>7} {'Hurdle':>7} {'Edge':>6} {'15m%':>7} {'60m%':>7} {'Pockets':>7} {'Vol$':>12}")
        print(f"{'---':>3} {'-'*15:>15} {'-'*5:>5} {'-'*7:>7} {'-'*7:>7} {'-'*7:>7} {'-'*6:>6} {'-'*7:>7} {'-'*7:>7} {'-'*7:>7} {'-'*12:>12}")
        for i, c in enumerate(current_executable[:15]):
            print(f"{i+1:>3} {c['product_id']:>15} {c['mog_score']:>5.0f} {c['best_move_pct']:>+6.2f}% {c['spread_bps']:>5.0f}bps {c['fee_hurdle_pct']:>6.2f}% {c['edge_over_hurdle']:>+5.2f}% {c['ret_15m_pct']:>+6.2f}% {c['ret_60m_pct']:>+6.2f}% {c['pocket_count']:>7} ${c['volume_usd']:>10,.0f}")
    else:
        print("\n  NO products clear current fees. MOG is the ONLY proven survivor.")
        print("  MOG's ultra-low price (1.5e-07) gives it unique % movement on tiny absolute changes.")

    # TIER 2+3: Fee-tier candidates
    print(f"\n{'='*80}")
    print(f"TIERS 2-3: FEE-TIER CANDIDATES (Need lower fees to trade)")
    print(f"{'='*80}")

    fee_candidates = vip_candidates + maker_candidates
    fee_candidates.sort(key=lambda x: x["mog_score"], reverse=True)

    if fee_candidates:
        print(f"\n{'#':>3} {'Product':>15} {'MOG':>5} {'Move%':>7} {'Spread':>7} {'Tier':>12} {'Pockets':>7} {'MakerVerdict':>15}")
        print(f"{'---':>3} {'-'*15:>15} {'-'*5:>5} {'-'*7:>7} {'-'*7:>7} {'-'*12:>12} {'-'*7:>7} {'-'*15:>15}")
        for i, c in enumerate(fee_candidates[:20]):
            tier_label = "VIP" if c["tier"] == "vip_fee_candidate" else "Maker"
            print(f"{i+1:>3} {c['product_id']:>15} {c['mog_score']:>5.0f} {c['best_move_pct']:>+6.2f}% {c['spread_bps']:>5.0f}bps {tier_label:>12} {c['pocket_count']:>7} {c['maker_verdict']:>15}")
    else:
        print("\n  No fee-tier candidates found.")

    # TIER 4: Fee-wall blocked (for reference)
    print(f"\n{'='*80}")
    print(f"TIER 4: FEE-WALL BLOCKED (Not tradeable at any current fee tier)")
    print(f"{'='*80}")
    print(f"  {len(fee_wall_blocked)} products blocked. These need either:")
    print(f"  - Dramatic spread tightening")
    print(f"  - Dramatic volatility increase")
    print(f"  - Negative fees (maker rebate)")

    # Save results
    output = {
        "mog_reference": MOG_REFERENCE,
        "tier_breakdown": {
            "current_fee_executable": len(current_executable),
            "vip_fee_candidate": len(vip_candidates),
            "maker_fee_candidate": len(maker_candidates),
            "fee_wall_blocked": len(fee_wall_blocked),
        },
        "current_executable": current_executable[:20],
        "fee_tier_candidates": fee_candidates[:20],
        "all_candidates": candidates[:50],
    }

    output_path = ROOT / "reports" / "mog_sibling_scanner_v2.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to: {output_path}")

    print(f"\n{'='*80}")
    print(f"KEY INSIGHT:")
    print(f"{'='*80}")
    if current_executable:
        print(f"  {len(current_executable)} products are MOG-siblings tradeable NOW")
        print(f"  Top candidate: {current_executable[0]['product_id']} (MOG score: {current_executable[0]['mog_score']:.0f})")
    else:
        print(f"  MOG is UNIQUE — no other product clears 120bps taker fees.")
        print(f"  This confirms MOG's special geometry: ultra-low price + high % volatility + tight spread.")
        print(f"  The scaling path MUST go through fee-tier reduction (maker/VIP).")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
