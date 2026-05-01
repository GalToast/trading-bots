#!/usr/bin/env python3
"""MOG Sibling Scanner — Find products with the same fee-clearing geometry.

The MOG RSI(4) trade proved that oversold microcaps with 2h mean-reversion
bounces can clear 120bps/side fees. This scanner finds siblings.

Geometry that works:
- RSI(4) on 5m candles, oversold <30
- Subsequent 2h (24 bar) move >2.5% gross (to clear 2.4% fees)
- Spread <20bps (tight enough to not eat the edge)
- At least 3 historical signals (not tiny sample)
"""
import json
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POCKET_BOARD = ROOT / "reports" / "coinbase_spot_foundry_pocket_board.json"
FEE_HURDLE_BOARD = ROOT / "reports" / "coinbase_spot_fee_hurdle_board.json"

def load_pockets():
    with open(POCKET_BOARD) as f:
        return json.load(f)

def load_fee_hurdle():
    with open(FEE_HURDLE_BOARD) as f:
        return json.load(f)

def main():
    print("=" * 80)
    print("MOG SIBLING SCANNER — Fee-Clearing Microcap Geometry")
    print("=" * 80)

    # Load fee hurdle board for spread/volatility data
    hurdle = load_fee_hurdle()
    hurdle_rows = hurdle.get("rows", [])
    print(f"\nFee hurdle products: {len(hurdle_rows)}")

    # Load foundry pockets for historical signal data
    pockets = load_pockets()
    pocket_rows = pockets.get("rows", [])
    print(f"Foundry pockets: {len(pocket_rows)}")

    # Aggregate by product
    product_pockets = {}
    for p in pocket_rows:
        pid = p["product_id"]
        if pid not in product_pockets:
            product_pockets[pid] = []
        product_pockets[pid].append(p)

    # Find products with:
    # 1. Spread < 20bps
    # 2. At least 3 pocket signals (historical evidence)
    # 3. At least one pocket with avg_net > 0 (positive expectancy)
    # 4. Low price regime (microcap-like)

    # Get spread data from hurdle board
    product_spreads = {}
    for h in hurdle_rows:
        pid = h.get("product_id", "")
        spread = h.get("spread_bps_proxy", 999)
        state = h.get("state", "unknown")
        price = h.get("ask", 999)
        product_spreads[pid] = {
            "spread": spread,
            "state": state,
            "price": price,
        }

    print(f"\nProducts with spread data: {len(product_spreads)}")

    # Scan for MOG-like geometry
    mog_siblings = []
    for pid, pockets_list in product_pockets.items():
        spread_info = product_spreads.get(pid, {})
        spread = spread_info.get("spread", 999)
        price = spread_info.get("price", 999)
        state = spread_info.get("state", "unknown")

        # Filter criteria
        if spread > 20:
            continue
        if len(pockets_list) < 3:
            continue

        # Check for positive expectancy pockets
        positive_pockets = [p for p in pockets_list if p.get("avg_net_pct", 0) > 0]
        if not positive_pockets:
            continue

        # Calculate aggregate metrics
        best_net = max(p["avg_net_pct"] for p in pockets_list)
        avg_net = np.mean([p["avg_net_pct"] for p in pockets_list])
        total_signals = sum(p["signals"] for p in pockets_list)
        best_win_rate = max(p["win_rate_pct"] for p in pockets_list)
        best_pocket = max(pockets_list, key=lambda x: x["avg_net_pct"])

        mog_siblings.append({
            "product_id": pid,
            "spread_bps": spread,
            "price": price,
            "state": state,
            "pocket_count": len(pockets_list),
            "total_signals": total_signals,
            "best_net_pct": best_net,
            "avg_net_pct": avg_net,
            "best_win_rate": best_win_rate,
            "best_pocket": f"{best_pocket['trigger']}->{best_pocket.get('exit','?')}",
            "best_pocket_signals": best_pocket["signals"],
            "best_pocket_wins": best_pocket.get("wins", 0),
        })

    # Relax filter and show all products with any pockets + spread data
    print(f"\n{'='*80}")
    print(f"ALL PRODUCTS WITH POCKETS + SPREAD DATA (relaxed view)")
    print(f"{'='*80}")

    all_candidates = []
    for pid, pockets_list in product_pockets.items():
        spread_info = product_spreads.get(pid, {})
        spread = spread_info.get("spread", 999)
        price = spread_info.get("price", 999)
        state = spread_info.get("state", "unknown")

        positive_pockets = [p for p in pockets_list if p.get("avg_net_pct", 0) > 0]
        best_net = max(p["avg_net_pct"] for p in pockets_list)
        total_signals = sum(p["signals"] for p in pockets_list)

        all_candidates.append({
            "product_id": pid,
            "spread_bps": spread,
            "price": price,
            "state": state,
            "pocket_count": len(pockets_list),
            "positive_pockets": len(positive_pockets),
            "total_signals": total_signals,
            "best_net_pct": best_net,
        })

    all_candidates.sort(key=lambda x: x["total_signals"], reverse=True)

    print(f"\nTotal products with pockets: {len(all_candidates)}")
    print(f"With spread<20bps: {sum(1 for c in all_candidates if c['spread_bps']<=20)}")
    print(f"With spread<30bps: {sum(1 for c in all_candidates if c['spread_bps']<=30)}")
    print(f"With spread<50bps: {sum(1 for c in all_candidates if c['spread_bps']<=50)}")
    print(f"With 3+ pockets: {sum(1 for c in all_candidates if c['pocket_count']>=3)}")
    print(f"With 3+ pockets AND spread<30bps: {sum(1 for c in all_candidates if c['pocket_count']>=3 and c['spread_bps']<=30)}")

    print(f"\n{'#':>3} {'Product':>15} {'Spread':>7} {'Price':>12} {'Pockets':>7} {'Pos':>4} {'Signals':>8} {'BestNet':>8}")
    print(f"{'---':>3} {'-'*15:>15} {'-'*7:>7} {'-'*12:>12} {'-'*7:>7} {'-'*4:>4} {'-'*8:>8} {'-'*8:>8}")

    for i, c in enumerate(all_candidates[:30]):
        flag = "✅" if c["spread_bps"]<=20 and c["pocket_count"]>=3 else ""
        print(f"{i+1:>3} {c['product_id']:>15} {c['spread_bps']:>5.1f}bps {c['price']:>12.2e} {c['pocket_count']:>7} {c['positive_pockets']:>4} {c['total_signals']:>8} {c['best_net_pct']:>+6.2f}% {flag}")

    # MOG reference
    print(f"\n{'='*80}")
    print(f"MOG REFERENCE (the proven winner):")
    print(f"  Price: 1.5e-07 | Spread: ~10bps | RSI(4) oversold→bounce")
    print(f"  1 close: +6.67% gross → +4.12% net after 2.4% fees")
    print(f"  Exit was TIMEOUT (2h), not 7.5% target → means bounce is fast")
    print(f"{'='*80}")

    # Top 5 recommendations
    print(f"\nTOP 5 MOG SIBLINGS TO TEST:")
    for i, s in enumerate(mog_siblings[:5]):
        print(f"\n  {i+1}. {s['product_id']}")
        print(f"     Spread: {s['spread_bps']:.1f}bps | Price: {s['price']:.2e}")
        print(f"     Best pocket: {s['best_pocket']} ({s['best_net_pct']:.2f}% net, {s['best_win_rate']:.0f}% win)")
        print(f"     Total signals across all pockets: {s['total_signals']}")
        print(f"     Recommended: RSI(4) oversold<30, target>5%, stop<1%, max_hold=24")

    # Save results
    output_path = ROOT / "reports" / "mog_sibling_scan.json"
    with open(output_path, "w") as f:
        json.dump({"siblings": mog_siblings, "mog_reference": {
            "price": 1.5e-07,
            "spread_bps": 10,
            "rsi_period": 4,
            "oversold": 30,
            "profit_target": 7.5,
            "stop_loss": 0.5,
            "max_hold_bars": 24,
            "proven_net": 4.12,
        }}, f, indent=2)

    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
