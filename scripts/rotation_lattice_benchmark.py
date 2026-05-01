#!/usr/bin/env python3
"""
Rotation Lattice Benchmark
=============================
Bets on mean-reversion in relative performance between coin pairs.

Edge thesis: When coin A underperforms coin B over a rolling window,
coin B tends to catch up (rotation oscillation). This is orthogonal to
directional price moves — both coins can be trending up, but one lags.

Mechanism (long-only spot):
1. Compute rolling returns for all coin pairs over window W
2. When pair (A, B) shows A underperforming B by > threshold, go long A
   (betting on catch-up rotation)
3. Exit when relative strength reverts to mean, or timeout

Usage:
    python scripts/rotation_lattice_benchmark.py --days 60
    python scripts/rotation_lattice_benchmark.py --days 30
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from itertools import combinations

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient
from multi_coin_isolated_runner import fetch_candles

# Universe
COINS = ["CFG-USD", "NOM-USD", "RAVE-USD", "BAL-USD", "SUP-USD"]
GRANULARITY = "FIVE_MINUTE"  # 5-min bars
FEE_RATE = 0.004  # 40bps Coinbase Advanced taker
SPREAD_ESTIMATE = 0.001  # 10bps spread (conservative for alts)
DEPLOY_PER_TRADE = 9.60  # Kelly allocation per coin


def compute_rolling_returns(candles_a, candles_b, window=20):
    """Compute relative performance: A return - B return over rolling window."""
    returns_a = []
    returns_b = []
    
    # Align candles by timestamp
    ts_a = {int(c["start"]): float(c["close"]) for c in candles_a}
    ts_b = {int(c["start"]): float(c["close"]) for c in candles_b}
    
    common_ts = sorted(set(ts_a.keys()) & set(ts_b.keys()))
    
    if len(common_ts) < window + 1:
        return [], [], []
    
    rel_strength = []
    
    for i in range(window, len(common_ts)):
        ts_now = common_ts[i]
        ts_then = common_ts[i - window]
        
        price_a_now = ts_a[ts_now]
        price_a_then = ts_a[ts_then]
        price_b_now = ts_b[ts_now]
        price_b_then = ts_b[ts_then]
        
        ret_a = (price_a_now - price_a_then) / price_a_then
        ret_b = (price_b_now - price_b_then) / price_b_then
        
        # Relative strength: how much A is outperforming B
        rs = ret_a - ret_b
        rel_strength.append({
            "timestamp": ts_now,
            "rs": rs,
            "ret_a": ret_a,
            "ret_b": ret_b,
            "price_a": price_a_now,
            "price_b": price_b_now,
        })
    
    return rel_strength


def run_pair_backtest(pair_name, rel_strength_data, 
                      entry_threshold=0.02, exit_threshold=0.005,
                      max_hold=48, position_size=DEPLOY_PER_TRADE):
    """Backtest rotation lattice on a single pair."""
    if not rel_strength_data:
        return {"trades": 0, "pnl": 0, "closes": 0, "wins": 0, "losses": 0}
    
    # Compute attractors (KDE on relative strength)
    rs_values = [r["rs"] for r in rel_strength_data]
    mean_rs = sum(rs_values) / len(rs_values)
    std_rs = (sum((r - mean_rs)**2 for r in rs_values) / len(rs_values)) ** 0.5
    
    trades = []
    position = None
    total_pnl = 0
    
    for i, data in enumerate(rel_strength_data):
        rs = data["rs"]
        
        # Entry: A has underperformed B by more than threshold (rs is very negative)
        if position is None and rs < -entry_threshold:
            # Go long the underperformer (A)
            position = {
                "entry_rs": rs,
                "entry_bar": i,
                "price_a": data["price_a"],
                "hold": 0,
            }
        
        # Exit: relative strength reverted toward mean
        elif position is not None:
            position["hold"] += 1
            
            # Exit conditions
            should_exit = False
            exit_reason = "timeout"
            
            if rs > -exit_threshold:
                should_exit = True
                exit_reason = "mean_reversion"
            elif position["hold"] >= max_hold:
                should_exit = True
            elif rs > mean_rs + std_rs:
                # Overshot to the other side — A now outperforming
                should_exit = True
                exit_reason = "overshoot"
            
            if should_exit:
                price_a_now = data["price_a"]
                entry_price = position["price_a"]
                
                # Raw return
                raw_return = (price_a_now - entry_price) / entry_price
                
                # Apply fees (round trip)
                net_return = raw_return - 2 * FEE_RATE - SPREAD_ESTIMATE
                
                pnl = position_size * net_return
                total_pnl += pnl
                
                trades.append({
                    "entry_bar": position["entry_bar"],
                    "exit_bar": i,
                    "entry_rs": position["entry_rs"],
                    "exit_rs": rs,
                    "hold_bars": position["hold"],
                    "raw_return_pct": raw_return * 100,
                    "net_return_pct": net_return * 100,
                    "pnl": pnl,
                    "exit_reason": exit_reason,
                    "win": pnl > 0,
                })
                
                position = None
    
    if not trades:
        return {
            "pair": pair_name,
            "trades": 0,
            "pnl": 0,
            "closes": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_pnl": 0,
            "max_drawdown": 0,
            "mean_reversion_exits": 0,
            "overshoot_exits": 0,
            "timeout_exits": 0,
        }
    
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    
    # Max drawdown
    equity = 0
    peak = 0
    max_dd = 0
    for t in trades:
        equity += t["pnl"]
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    
    mr_exits = sum(1 for t in trades if t["exit_reason"] == "mean_reversion")
    oo_exits = sum(1 for t in trades if t["exit_reason"] == "overshoot")
    to_exits = sum(1 for t in trades if t["exit_reason"] == "timeout")
    
    return {
        "pair": pair_name,
        "trades": len(trades),
        "pnl": round(total_pnl, 2),
        "closes": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "avg_pnl": round(total_pnl / len(trades), 2),
        "max_drawdown": round(max_dd, 2),
        "mean_reversion_exits": mr_exits,
        "overshoot_exits": oo_exits,
        "timeout_exits": to_exits,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rotation Lattice Benchmark")
    parser.add_argument("--days", type=int, default=60, help="Days of history to fetch")
    parser.add_argument("--window", type=int, default=20, help="Rolling return window (bars)")
    parser.add_argument("--entry-threshold", type=float, default=0.02, help="Entry RS threshold (default 2%)")
    parser.add_argument("--exit-threshold", type=float, default=0.005, help="Exit RS threshold (default 0.5%)")
    parser.add_argument("--max-hold", type=int, default=48, help="Max hold bars")
    parser.add_argument("--position-size", type=float, default=DEPLOY_PER_TRADE)
    args = parser.parse_args()
    
    print("=" * 72)
    print("ROTATION LATTICE BENCHMARK")
    print(f"Coins: {', '.join(COINS)}")
    print(f"Days: {args.days}, Window: {args.window} bars")
    print(f"Entry threshold: {args.entry_threshold*100:.1f}%, Exit: {args.exit_threshold*100:.2f}%")
    print("=" * 72)
    print()
    
    # Fetch candles for all coins
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - args.days * 24 * 3600
    
    all_candles = {}
    for coin in COINS:
        print(f"Fetching {coin}...")
        candles = fetch_candles(client, coin, start, now, GRANULARITY)
        all_candles[coin] = candles
        print(f"  {len(candles)} candles")
    
    print()
    
    # Test all pairs
    results = []
    pairs = list(combinations(COINS, 2))
    
    for coin_a, coin_b in pairs:
        pair_name = f"{coin_a.replace('-USD', '')}/{coin_b.replace('-USD', '')}"
        print(f"Testing {pair_name}...", end=" ")
        
        rel_strength = compute_rolling_returns(
            all_candles[coin_a], all_candles[coin_b], 
            window=args.window
        )
        
        result = run_pair_backtest(
            pair_name, rel_strength,
            entry_threshold=args.entry_threshold,
            exit_threshold=args.exit_threshold,
            max_hold=args.max_hold,
            position_size=args.position_size,
        )
        
        results.append(result)
        print(f"  trades={result['trades']}, pnl=${result['pnl']:.2f}, wr={result['win_rate']:.0f}%")
    
    print()
    
    # Summary
    print("=" * 72)
    print("RESULTS")
    print("=" * 72)
    print(f"\n| {'Pair':<12} | {'Trades':>6} | {'PnL':>8} | {'WR':>5} | {'Avg PnL':>8} | {'Max DD':>8} |")
    print(f"|{'-'*14}|{'-'*8}|{'-'*10}|{'-'*7}|{'-'*10}|{'-'*10}|")
    
    for r in sorted(results, key=lambda x: x["pnl"], reverse=True):
        print(f"| {r['pair']:<12} | {r['trades']:>6} | ${r['pnl']:>7.2f} | {r['win_rate']:>4.0f}% | ${r['avg_pnl']:>7.2f} | ${r['max_drawdown']:>7.2f} |")
    
    # Total portfolio
    total_pnl = sum(r["pnl"] for r in results)
    total_trades = sum(r["trades"] for r in results)
    total_wins = sum(r["wins"] for r in results)
    overall_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    
    print(f"|{'-'*14}|{'-'*8}|{'-'*10}|{'-'*7}|{'-'*10}|{'-'*10}|")
    print(f"| {'TOTAL':<12} | {total_trades:>6} | ${total_pnl:>7.2f} | {overall_wr:>4.0f}% | | |")
    print()
    
    # Positive pairs
    positive = [r for r in results if r["pnl"] > 0]
    print(f"Positive pairs: {len(positive)}/{len(results)} ({len(positive)/len(results)*100:.0f}%)")
    
    if positive:
        print(f"\nTop 3 performers:")
        for r in sorted(positive, key=lambda x: x["pnl"], reverse=True)[:3]:
            print(f"  {r['pair']}: ${r['pnl']:.2f} ({r['win_rate']}% WR, {r['trades']} trades)")
            print(f"    Mean reversion exits: {r['mean_reversion_exits']}, Overshoot: {r['overshoot_exits']}, Timeout: {r['timeout_exits']}")
    
    # Save results
    output = {
        "coins": COINS,
        "days": args.days,
        "window": args.window,
        "entry_threshold": args.entry_threshold,
        "exit_threshold": args.exit_threshold,
        "max_hold": args.max_hold,
        "fee_rate": FEE_RATE,
        "spread_estimate": SPREAD_ESTIMATE,
        "results": results,
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "positive_pairs": len(positive),
        "overall_wr": round(overall_wr, 1),
    }
    
    report_path = ROOT / "reports" / "rotation_lattice_benchmark.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    
    # Markdown report
    md_lines = [
        "# Rotation Lattice Benchmark",
        f"\nCoins: {', '.join(COINS)}",
        f"Days: {args.days}, Window: {args.window} bars",
        f"Entry threshold: {args.entry_threshold*100:.1f}%, Exit: {args.exit_threshold*100:.2f}%",
        f"Fee: {FEE_RATE*100:.0f}bps/side, Spread: {SPREAD_ESTIMATE*100:.0f}bps",
        f"\n## Results",
        f"\n| Pair | Trades | PnL | WR | Avg PnL | Max DD |",
        f"|------|--------|-----|----|---------|--------|",
    ]
    
    for r in sorted(results, key=lambda x: x["pnl"], reverse=True):
        md_lines.append(f"| {r['pair']} | {r['trades']} | ${r['pnl']:.2f} | {r['win_rate']}% | ${r['avg_pnl']:.2f} | ${r['max_drawdown']:.2f} |")
    
    md_lines.extend([
        f"\n**Total PnL:** ${total_pnl:.2f}",
        f"**Positive pairs:** {len(positive)}/{len(results)}",
        f"**Overall win rate:** {overall_wr:.1f}%",
    ])
    
    md_path = ROOT / "reports" / "rotation_lattice_benchmark.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    
    print(f"\nReport saved: {report_path}")
    print(f"Markdown: {md_path}")


if __name__ == "__main__":
    main()
