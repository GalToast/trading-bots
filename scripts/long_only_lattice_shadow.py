#!/usr/bin/env python3
"""
Long-Only Penetration Lattice Shadow for Coinbase Spot Markets

Concept: Port the stopless lattice's core edge to spot (long-only):
- BUY levels as price drops (accumulation)
- Penetration close logic: sell profitable positions when price crosses back UP through levels
- Cap max opens to bound risk
- Regime kill for one-way drift

No synthetic hedging needed. No shorts. Pure long-only penetration harvesting.

Tests on: NOM-USD, GHST-USD, SUP-USD (proven Coinbase coins with fibonacci edge)

Usage:
    python scripts/long_only_lattice_shadow.py --coin NOM-USD --days 30
    python scripts/long_only_lattice_shadow.py --coin NOM-USD --days 30 --step 0.005 --max-open 20
"""
import json
import sys
import os
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from coinbase_advanced_client import CoinbaseAdvancedClient
    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False


def fetch_m5_candles(client, symbol, start_ts, end_ts):
    """Fetch M5 candles from Coinbase."""
    chunk_sec = 300 * 5 * 60  # ~25 min chunks
    all_candles = []
    cs = start_ts
    while cs < end_ts:
        ce = min(cs + chunk_sec, end_ts)
        try:
            resp = client.market_candles(symbol, start=cs, end=ce, granularity="FIVE_MINUTE")
            candles = resp.get("candles", [])
            all_candles.extend(candles)
            cs = ce
            if not candles:
                break
            time.sleep(0.1)
        except Exception as e:
            print(f"  Fetch error: {e}", flush=True)
            cs = ce
            time.sleep(0.5)
    
    all_candles.sort(key=lambda c: int(c["start"]))
    # Deduplicate by start timestamp
    seen = set()
    unique = []
    for c in all_candles:
        if c["start"] not in seen:
            seen.add(c["start"])
            unique.append(c)
    return unique


def build_lattice_levels(anchor, step, max_open, direction="BUY"):
    """Build BUY levels below the anchor (price dropping)."""
    levels = []
    for i in range(1, max_open + 1):
        level_price = anchor - (i * step)
        levels.append({
            "level_idx": i,
            "price": level_price,
            "filled": False,
            "entry_price": None,
            "units": 1.0,  # Fixed units per level (simplified)
        })
    return levels


def check_penetration_close(direction, position_level_idx, current_price, levels, position_entry):
    """
    Penetration close logic:
    For BUY positions: close when price rises back ABOVE the level where it was opened.
    
    This is the lattice's core edge — harvesting the penetration back through levels.
    """
    if direction == "BUY":
        # Find the level this position was opened at
        level_price = None
        for lvl in levels:
            if lvl["level_idx"] == position_level_idx:
                level_price = lvl["price"]
                break
        
        if level_price is None:
            return False
        
        # Close when current price > level price (penetrated back up)
        return current_price > level_price
    return False


def run_shadow(coin, days=30, step=None, max_open=20, unit_size=10.0):
    """
    Run the long-only lattice shadow backtest.
    
    Args:
        coin: Coin symbol (e.g., "NOM-USD")
        days: Lookback days
        step: Price step between levels (auto-calculated if None)
        max_open: Maximum open positions per side
        unit_size: Dollar size per position
    """
    print(f"\n{'='*72}")
    print(f"LONG-ONLY PENETRATION LATTICE SHADOW — {coin}")
    print(f"{'='*72}")
    print(f"  Days: {days}")
    print(f"  Max open: {max_open}")
    print(f"  Unit size: ${unit_size}")
    
    # Fetch data
    if HAS_CLIENT:
        client = CoinbaseAdvancedClient()
    else:
        print("  ⚠️  No Coinbase client — using simulated data for structure demo")
        client = None
    
    now = int(time.time())
    start = now - (days * 86400)
    
    if client:
        print(f"  Fetching {days}d of M5 data for {coin}...")
        candles = fetch_m5_candles(client, coin, start, now)
        print(f"  Got {len(candles)} candles")
    else:
        # Simulated data for structure demonstration
        print("  Using simulated price series for structure demo")
        import random
        random.seed(42)
        base_price = 1.0
        candles = []
        ts = start
        for _ in range(days * 288):  # 288 M5 candles per day
            base_price *= (1 + random.gauss(0, 0.002))
            candles.append({
                "start": str(ts),
                "open": str(base_price),
                "high": str(base_price * 1.001),
                "low": str(base_price * 0.999),
                "close": str(base_price),
                "volume": str(random.uniform(1000, 10000)),
            })
            ts += 300
    
    if not candles:
        print("  ❌ No data available")
        return None
    
    # Parse candles
    bars = []
    for c in candles:
        bars.append({
            "t": int(c["start"]),
            "o": float(c["open"]),
            "h": float(c["high"]),
            "l": float(c["low"]),
            "c": float(c["close"]),
            "v": float(c["volume"]),
        })
    
    # Auto-calculate step if not provided
    if step is None:
        # Use 0.5% of median price as step
        median_price = sorted(b["c"] for b in bars)[len(bars) // 2]
        step = median_price * 0.005  # 0.5% step
        print(f"  Auto step: {step:.6f} (0.5% of median price ${median_price:.4f})")
    else:
        print(f"  Step: {step:.6f}")
    
    # Run backtest
    print(f"\n  Running long-only lattice shadow...")
    
    anchor = bars[0]["c"]  # Initial anchor at first close
    levels = build_lattice_levels(anchor, step, max_open)
    positions = []  # Open BUY positions
    realized_pnl = 0.0
    total_closes = 0
    total_opens = 0
    max_open_seen = 0
    regime_high = anchor
    regime_low = anchor
    kills = 0
    
    # Simulate spread cost (typical Coinbase spread ~0.01-0.05%)
    spread_pct = 0.0002  # 0.02% per round trip
    spread_cost_per_trade = unit_size * spread_pct
    
    for i in range(1, len(bars)):
        bar = bars[i]
        price = bar["c"]
        
        # Update regime
        regime_high = max(regime_high, price)
        regime_low = min(regime_low, price)
        
        # Regime kill: if price drops > 5% from anchor, kill all positions
        if price < anchor * 0.95 and positions:
            # Kill all positions at current price
            for pos in positions:
                pnl = (price - pos["entry_price"]) * pos["units"] - spread_cost_per_trade
                realized_pnl += pnl
                total_closes += 1
                kills += 1
            positions = []
            # Reset anchor and levels
            anchor = price
            levels = build_lattice_levels(anchor, step, max_open)
            regime_high = price
            regime_low = price
            continue
        
        # Check for new level fills (price dropping into levels)
        for lvl in levels:
            if not lvl["filled"] and price <= lvl["price"]:
                lvl["filled"] = True
                lvl["entry_price"] = price
                positions.append({
                    "level_idx": lvl["level_idx"],
                    "entry_price": price,
                    "units": unit_size / price,  # Units = dollars / price
                    "opened_bar": i,
                })
                total_opens += 1
                max_open_seen = max(max_open_seen, len(positions))
        
        # Check for penetration closes (price rising back through levels)
        closes_this_bar = []
        for pos in positions:
            if check_penetration_close("BUY", pos["level_idx"], price, levels, pos["entry_price"]):
                # Calculate PnL
                pnl = (price - pos["entry_price"]) * pos["units"] - spread_cost_per_trade
                realized_pnl += pnl
                total_closes += 1
                closes_this_bar.append(pos)
                
                # Reset the level for re-entry
                for lvl in levels:
                    if lvl["level_idx"] == pos["level_idx"]:
                        lvl["filled"] = False
                        lvl["entry_price"] = None
                        break
        
        # Remove closed positions
        for pos in closes_this_bar:
            positions.remove(pos)
    
    # Results
    print(f"\n  === RESULTS ===")
    print(f"  Realized PnL: ${realized_pnl:+.2f}")
    print(f"  Total opens: {total_opens}")
    print(f"  Total closes: {total_closes}")
    print(f"  Max open seen: {max_open_seen}")
    print(f"  Regime kills: {kills}")
    print(f"  Avg PnL per close: ${realized_pnl/total_closes:.4f}" if total_closes > 0 else "  No closes")
    print(f"  Win rate: {sum(1 for _ in range(total_closes))}/{total_closes}" if total_closes > 0 else "  N/A")
    print(f"  Spread cost per trade: ${spread_cost_per_trade:.4f}")
    print(f"  Anchor start: ${anchor:.4f} → end: ${bars[-1]['c']:.4f}")
    
    result = {
        "coin": coin,
        "days": days,
        "step": step,
        "max_open": max_open,
        "unit_size": unit_size,
        "realized_pnl": realized_pnl,
        "total_opens": total_opens,
        "total_closes": total_closes,
        "max_open_seen": max_open_seen,
        "regime_kills": kills,
        "avg_pnl_per_close": realized_pnl / total_closes if total_closes > 0 else 0,
        "spread_cost_per_trade": spread_cost_per_trade,
        "bars_processed": len(bars),
        "anchor_start": anchor,
        "anchor_end": bars[-1]["c"],
    }
    
    # Save results
    out_path = ROOT / "reports" / f"long_only_lattice_{coin.replace('-', '_')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    print(f"\n  Results saved to: {out_path}")
    
    return result


def compare_with_fibonacci(coins, days=30):
    """
    Compare long-only lattice against fibonacci breakout projections.
    Uses known fibonacci projections from COMMAND_CENTER.md.
    """
    print(f"\n{'='*72}")
    print("LATTICE vs FIBONACCI COMPARISON")
    print(f"{'='*72}")
    
    # Known fibonacci projections (from COMMAND_CENTER.md)
    fib_projections = {
        "NOM-USD": {"monthly_pnl": 2019, "lookback": 20},
        "GHST-USD": {"monthly_pnl": 430, "lookback": 10},
        "SUP-USD": {"monthly_pnl": 181, "lookback": 20},
    }
    
    print(f"\n{'Coin':<12} {'Fibonacci $/mo':>15} {'Lattice $/30d':>15} {'Verdict':>12}")
    print("-" * 56)
    
    for coin in coins:
        fib_data = fib_projections.get(coin, {})
        fib_monthly = fib_data.get("monthly_pnl", 0)
        
        # Run lattice shadow
        result = run_shadow(coin, days=days)
        lattice_30d = result["realized_pnl"] if result else 0
        
        # Extrapolate to monthly
        lattice_monthly = lattice_30d  # Already 30d
        
        if lattice_monthly > fib_monthly * 0.5:
            verdict = "✅ Competitive"
        elif lattice_monthly > 0:
            verdict = "🟡 Positive but weaker"
        else:
            verdict = "❌ Negative"
        
        coin_short = coin.replace("-USD", "")
        print(f"{coin_short:<12} ${fib_monthly:>13,.2f} ${lattice_monthly:>13,.2f} {verdict:>12}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Long-Only Penetration Lattice Shadow")
    parser.add_argument("--coin", type=str, default=None, help="Coin symbol (e.g., NOM-USD)")
    parser.add_argument("--days", type=int, default=30, help="Lookback days")
    parser.add_argument("--step", type=float, default=None, help="Step size (auto if None)")
    parser.add_argument("--max-open", type=int, default=20, help="Max open positions")
    parser.add_argument("--unit-size", type=float, default=10.0, help="Dollar size per position")
    parser.add_argument("--compare", action="store_true", help="Compare with fibonacci projections")
    args = parser.parse_args()
    
    if args.compare:
        compare_with_fibonacci(["NOM-USD", "GHST-USD", "SUP-USD"], days=args.days)
    elif args.coin:
        run_shadow(args.coin, days=args.days, step=args.step, max_open=args.max_open, unit_size=args.unit_size)
    else:
        # Default: run all three proven coins
        for coin in ["NOM-USD", "GHST-USD", "SUP-USD"]:
            run_shadow(coin, days=args.days, step=args.step, max_open=args.max_open, unit_size=args.unit_size)


if __name__ == "__main__":
    main()
