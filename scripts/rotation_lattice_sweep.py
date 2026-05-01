#!/usr/bin/env python3
"""
Rotation Lattice Parameter Sweep
==================================
Sweeps entry threshold, exit threshold, rolling window, and coin subsets
to find the optimal rotation lattice configuration.

Edge thesis: When coin A underperforms coin B over a rolling window,
coin B tends to catch up (rotation oscillation).
"""
import json
import sys
import time
from pathlib import Path
from itertools import combinations

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient
from multi_coin_isolated_runner import fetch_candles

# Universe — exclude NOM (trends, doesn't rotate)
COINS_ALL = ["CFG-USD", "NOM-USD", "RAVE-USD", "BAL-USD", "SUP-USD"]
COINS_NO_NOM = ["CFG-USD", "RAVE-USD", "BAL-USD", "SUP-USD"]
GRANULARITY = "FIVE_MINUTE"
FEE_RATE = 0.004
SPREAD_ESTIMATE = 0.001
DEPLOY_PER_TRADE = 9.60


def compute_rolling_returns(candles_a, candles_b, window=20):
    """Compute relative performance: A return - B return over rolling window."""
    ts_a = {int(c["start"]): float(c["close"]) for c in candles_a}
    ts_b = {int(c["start"]): float(c["close"]) for c in candles_b}
    common_ts = sorted(set(ts_a.keys()) & set(ts_b.keys()))
    
    if len(common_ts) < window + 1:
        return []
    
    rel_strength = []
    for i in range(window, len(common_ts)):
        ts_now = common_ts[i]
        ts_then = common_ts[i - window]
        
        ret_a = (ts_a[ts_now] - ts_a[ts_then]) / ts_a[ts_then]
        ret_b = (ts_b[ts_now] - ts_b[ts_then]) / ts_b[ts_then]
        
        rel_strength.append({
            "timestamp": ts_now,
            "rs": ret_a - ret_b,
            "price_a": ts_a[ts_now],
            "price_b": ts_b[ts_now],
        })
    
    return rel_strength


def run_pair_backtest(rel_strength_data, entry_threshold, exit_threshold, max_hold, position_size):
    """Backtest rotation lattice on a single pair with given params."""
    if not rel_strength_data:
        return None
    
    position = None
    trades = []
    total_pnl = 0
    
    for i, data in enumerate(rel_strength_data):
        rs = data["rs"]
        
        if position is None and rs < -entry_threshold:
            position = {
                "entry_rs": rs,
                "entry_bar": i,
                "price_a": data["price_a"],
                "hold": 0,
            }
        
        elif position is not None:
            position["hold"] += 1
            
            should_exit = False
            exit_reason = "timeout"
            
            if rs > -exit_threshold:
                should_exit = True
                exit_reason = "mean_reversion"
            elif position["hold"] >= max_hold:
                should_exit = True
            elif rs > 0.02:  # A now significantly outperforming
                should_exit = True
                exit_reason = "overshoot"
            
            if should_exit:
                price_a_now = data["price_a"]
                entry_price = position["price_a"]
                raw_return = (price_a_now - entry_price) / entry_price
                net_return = raw_return - 2 * FEE_RATE - SPREAD_ESTIMATE
                pnl = position_size * net_return
                total_pnl += pnl
                
                trades.append({
                    "hold_bars": position["hold"],
                    "net_return_pct": net_return * 100,
                    "pnl": pnl,
                    "exit_reason": exit_reason,
                    "win": pnl > 0,
                })
                
                position = None
    
    if not trades:
        return None
    
    wins = [t for t in trades if t["win"]]
    
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
    
    # Sharpe-like metric: avg pnl / std pnl
    pnls = [t["pnl"] for t in trades]
    avg_pnl = sum(pnls) / len(pnls)
    std_pnl = (sum((p - avg_pnl)**2 for p in pnls) / len(pnls)) ** 0.5 if len(pnls) > 1 else 1
    sharpe = avg_pnl / std_pnl if std_pnl > 0 else 0
    
    return {
        "trades": len(trades),
        "pnl": round(total_pnl, 2),
        "wins": len(wins),
        "losses": len(trades) - len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "avg_pnl": round(total_pnl / len(trades), 3),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 3),
        "mean_reversion_exits": sum(1 for t in trades if t["exit_reason"] == "mean_reversion"),
        "overshoot_exits": sum(1 for t in trades if t["exit_reason"] == "overshoot"),
        "timeout_exits": sum(1 for t in trades if t["exit_reason"] == "timeout"),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rotation Lattice Parameter Sweep")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--quick", action="store_true", help="Use smaller parameter grid")
    args = parser.parse_args()
    
    print("=" * 72)
    print("ROTATION LATTICE PARAMETER SWEEP")
    print("=" * 72)
    print()
    
    # Fetch candles
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - args.days * 24 * 3600
    
    all_candles = {}
    for coin in COINS_ALL:
        print(f"Fetching {coin}...")
        candles = fetch_candles(client, coin, start, now, GRANULARITY)
        all_candles[coin] = candles
        print(f"  {len(candles)} candles")
    print()
    
    # Pre-compute all pair relative strength series
    pair_rs = {}
    pairs_all = list(combinations(COINS_ALL, 2))
    for coin_a, coin_b in pairs_all:
        pair_name = f"{coin_a.replace('-USD', '')}/{coin_b.replace('-USD', '')}"
        pair_rs[pair_name] = compute_rolling_returns(all_candles[coin_a], all_candles[coin_b], window=20)
    
    # Parameter grid
    entry_thresholds = [0.005, 0.01, 0.015, 0.02, 0.03, 0.05]
    exit_thresholds = [0.001, 0.002, 0.005, 0.01, 0.015, 0.02]
    windows = [12, 20, 48, 96]  # 1h, 1.7h, 4h, 8h
    max_holds = [24, 48, 96]
    
    if args.quick:
        entry_thresholds = [0.01, 0.02, 0.03]
        exit_thresholds = [0.002, 0.005, 0.01]
        windows = [20, 48]
        max_holds = [48]
    
    results = []
    total_configs = len(entry_thresholds) * len(exit_thresholds) * len(windows) * len(max_holds)
    print(f"Testing {total_configs} parameter combinations across {len(pairs_all)} pairs...")
    print()
    
    best_overall = {"pnl": -999999, "config": None, "pair_results": []}
    
    for entry_th in entry_thresholds:
        for exit_th in exit_thresholds:
            for window in windows:
                for max_hold in max_holds:
                    # Re-compute RS series for this window
                    pair_rs_window = {}
                    for coin_a, coin_b in pairs_all:
                        pair_name = f"{coin_a.replace('-USD', '')}/{coin_b.replace('-USD', '')}"
                        pair_rs_window[pair_name] = compute_rolling_returns(
                            all_candles[coin_a], all_candles[coin_b], window=window
                        )
                    
                    pair_results = []
                    total_pnl = 0
                    total_trades = 0
                    total_wins = 0
                    
                    for pair_name, rs_data in pair_rs_window.items():
                        r = run_pair_backtest(rs_data, entry_th, exit_th, max_hold, DEPLOY_PER_TRADE)
                        if r and r["trades"] > 5:  # Minimum trades for statistical significance
                            pair_results.append({"pair": pair_name, **r})
                            total_pnl += r["pnl"]
                            total_trades += r["trades"]
                            total_wins += r["wins"]
                    
                    if not pair_results:
                        continue
                    
                    wr = total_wins / total_trades * 100 if total_trades > 0 else 0
                    positive_pairs = sum(1 for p in pair_results if p["pnl"] > 0)
                    
                    config = {
                        "entry_threshold": entry_th,
                        "exit_threshold": exit_th,
                        "window": window,
                        "max_hold": max_hold,
                    }
                    
                    result = {
                        "config": config,
                        "total_pnl": round(total_pnl, 2),
                        "total_trades": total_trades,
                        "win_rate": round(wr, 1),
                        "positive_pairs": positive_pairs,
                        "total_pairs": len(pair_results),
                        "pair_results": sorted(pair_results, key=lambda x: x["pnl"], reverse=True),
                    }
                    
                    results.append(result)
                    
                    if total_pnl > best_overall["pnl"]:
                        best_overall = {
                            "pnl": total_pnl,
                            "config": config,
                            "pair_results": sorted(pair_results, key=lambda x: x["pnl"], reverse=True),
                        }
    
    # Sort all results by PnL
    results.sort(key=lambda x: x["total_pnl"], reverse=True)
    
    print("=" * 72)
    print(f"TOP 20 CONFIGURATIONS (out of {len(results)} tested)")
    print("=" * 72)
    print()
    print(f"{'#':>3} | {'Entry':>6} | {'Exit':>6} | {'Win':>4} | {'MaxHold':>7} | {'PnL':>8} | {'Trades':>6} | {'WR':>5} | {'PosPairs':>8}")
    print("-" * 72)
    
    for i, r in enumerate(results[:20]):
        c = r["config"]
        print(f"{i+1:>3} | {c['entry_threshold']:>5.1%} | {c['exit_threshold']:>5.1%} | {c['window']:>4} | {c['max_hold']:>7} | ${r['total_pnl']:>7.2f} | {r['total_trades']:>6} | {r['win_rate']:>4.0f}% | {r['positive_pairs']}/{r['total_pairs']}")
    
    print()
    print("=" * 72)
    print("BEST CONFIGURATION — PER-PAIR BREAKDOWN")
    print("=" * 72)
    bc = best_overall["config"]
    print(f"\nEntry: {bc['entry_threshold']*100:.1f}%, Exit: {bc['exit_threshold']*100:.2f}%, Window: {bc['window']} bars, MaxHold: {bc['max_hold']}")
    print(f"Total PnL: ${best_overall['pnl']:.2f}")
    print()
    print(f"{'Pair':<12} | {'Trades':>6} | {'PnL':>8} | {'WR':>5} | {'Avg PnL':>8} | {'Max DD':>8} | {'MR Exits':>8}")
    print("-" * 72)
    for pr in best_overall["pair_results"]:
        print(f"{pr['pair']:<12} | {pr['trades']:>6} | ${pr['pnl']:>7.2f} | {pr['win_rate']:>4.0f}% | ${pr['avg_pnl']:>7.3f} | ${pr['max_drawdown']:>7.2f} | {pr['mean_reversion_exits']:>8}")
    
    # Also test NO-NOM universe with best config
    print()
    print("=" * 72)
    print("BEST CONFIG APPLIED TO NO-NOM UNIVERSE (CFG, RAVE, BAL, SUP)")
    print("=" * 72)
    
    nonom_pairs = list(combinations(COINS_NO_NOM, 2))
    nonom_results = []
    nonom_total_pnl = 0
    nonom_total_trades = 0
    nonom_total_wins = 0
    
    for coin_a, coin_b in nonom_pairs:
        pair_name = f"{coin_a.replace('-USD', '')}/{coin_b.replace('-USD', '')}"
        rs_data = compute_rolling_returns(all_candles[coin_a], all_candles[coin_b], window=bc["window"])
        r = run_pair_backtest(rs_data, bc["entry_threshold"], bc["exit_threshold"], bc["max_hold"], DEPLOY_PER_TRADE)
        if r and r["trades"] > 5:
            r["pair"] = pair_name
            nonom_results.append(r)
            nonom_total_pnl += r["pnl"]
            nonom_total_trades += r["trades"]
            nonom_total_wins += r["wins"]
    
    nonom_results.sort(key=lambda x: x["pnl"], reverse=True)
    nonom_wr = nonom_total_wins / nonom_total_trades * 100 if nonom_total_trades > 0 else 0
    
    print(f"\n{'Pair':<12} | {'Trades':>6} | {'PnL':>8} | {'WR':>5} | {'Avg PnL':>8} | {'Max DD':>8}")
    print("-" * 72)
    for pr in nonom_results:
        print(f"{pr['pair']:<12} | {pr['trades']:>6} | ${pr['pnl']:>7.2f} | {pr['win_rate']:>4.0f}% | ${pr['avg_pnl']:>7.3f} | ${pr['max_drawdown']:>7.2f}")
    print(f"\nNo-NOM Total: ${nonom_total_pnl:.2f} ({nonom_total_trades} trades, {nonom_wr:.0f}% WR)")
    
    # Save full results
    output = {
        "days": args.days,
        "total_configs_tested": len(results),
        "best_config": bc,
        "best_total_pnl": round(best_overall["pnl"], 2),
        "best_pair_results": best_overall["pair_results"],
        "nonom_results": nonom_results,
        "nonom_total_pnl": round(nonom_total_pnl, 2),
        "nonom_total_trades": nonom_total_trades,
        "nonom_wr": round(nonom_wr, 1),
        "top_20": [
            {
                "config": r["config"],
                "total_pnl": r["total_pnl"],
                "total_trades": r["total_trades"],
                "win_rate": r["win_rate"],
                "positive_pairs": f"{r['positive_pairs']}/{r['total_pairs']}",
            }
            for r in results[:20]
        ],
    }
    
    report_path = ROOT / "reports" / "rotation_lattice_sweep.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    
    md_path = ROOT / "reports" / "rotation_lattice_sweep.md"
    md_lines = [
        "# Rotation Lattice Parameter Sweep",
        f"\nTested {len(results)} configurations over {args.days} days",
        f"\n## Best Configuration",
        f"\nEntry: {bc['entry_threshold']*100:.1f}%, Exit: {bc['exit_threshold']*100:.2f}%, Window: {bc['window']} bars, MaxHold: {bc['max_hold']}",
        f"Total PnL: ${best_overall['pnl']:.2f}",
        f"\n| Pair | Trades | PnL | WR | Avg PnL | Max DD |",
        f"|------|--------|-----|----|---------|--------|",
    ]
    for pr in best_overall["pair_results"]:
        md_lines.append(f"| {pr['pair']} | {pr['trades']} | ${pr['pnl']:.2f} | {pr['win_rate']}% | ${pr['avg_pnl']:.3f} | ${pr['max_drawdown']:.2f} |")
    
    md_lines.extend([
        f"\n## No-NOM Universe",
        f"\nTotal: ${nonom_total_pnl:.2f} ({nonom_total_trades} trades, {nonom_wr:.0f}% WR)",
    ])
    for pr in nonom_results:
        md_lines.append(f"| {pr['pair']} | {pr['trades']} | ${pr['pnl']:.2f} | {pr['win_rate']}% | ${pr['avg_pnl']:.3f} | ${pr['max_drawdown']:.2f} |")
    
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    
    print(f"\nFull results: {report_path}")
    print(f"Markdown: {md_path}")


if __name__ == "__main__":
    main()
