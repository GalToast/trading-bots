#!/usr/bin/env python3
"""
Rotation Lattice Forward-Shadow Validation
=============================================
Tests the best rotation lattice config on out-of-sample data.

Config from sweep: Entry 5%, Exit 0.2%, Window 96 bars, MaxHold 96
Universe: CFG, RAVE, BAL, SUP (no NOM — trends, doesn't rotate)

Method: Train on first 45 days, test on last 15 days.
If the edge survives out-of-sample, it's structural, not curve-fit.
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

COINS = ["CFG-USD", "RAVE-USD", "BAL-USD", "SUP-USD"]
GRANULARITY = "FIVE_MINUTE"
FEE_RATE = 0.004
SPREAD_ESTIMATE = 0.001
DEPLOY_PER_TRADE = 9.60

# Best config from sweep
ENTRY_THRESHOLD = 0.05    # 5%
EXIT_THRESHOLD = 0.002    # 0.2%
WINDOW = 96               # 8 hours
MAX_HOLD = 96


def compute_rolling_returns(candles_a, candles_b, window=96):
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


def run_pair_backtest(rel_strength_data):
    position = None
    trades = []
    total_pnl = 0
    
    for i, data in enumerate(rel_strength_data):
        rs = data["rs"]
        
        if position is None and rs < -ENTRY_THRESHOLD:
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
            
            if rs > -EXIT_THRESHOLD:
                should_exit = True
                exit_reason = "mean_reversion"
            elif position["hold"] >= MAX_HOLD:
                should_exit = True
            elif rs > 0.02:
                should_exit = True
                exit_reason = "overshoot"
            
            if should_exit:
                price_a_now = data["price_a"]
                entry_price = position["price_a"]
                raw_return = (price_a_now - entry_price) / entry_price
                net_return = raw_return - 2 * FEE_RATE - SPREAD_ESTIMATE
                pnl = DEPLOY_PER_TRADE * net_return
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
    }


def main():
    print("=" * 72)
    print("ROTATION LATTICE FORWARD-SHADOW VALIDATION")
    print(f"Config: Entry={ENTRY_THRESHOLD*100:.0f}%, Exit={EXIT_THRESHOLD*100:.1f}%, Window={WINDOW}, MaxHold={MAX_HOLD}")
    print(f"Universe: {', '.join(c.replace('-USD','') for c in COINS)}")
    print("=" * 72)
    print()
    
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    
    # Fetch 60 days
    start_60d = now - 60 * 24 * 3600
    print("Fetching 60 days of candles...")
    all_candles = {}
    for coin in COINS:
        candles = fetch_candles(client, coin, start_60d, now, GRANULARITY)
        all_candles[coin] = candles
        print(f"  {coin}: {len(candles)} candles")
    print()
    
    # Split: first 45 days (train), last 15 days (test)
    split_point = now - 15 * 24 * 3600
    
    train_candles = {}
    test_candles = {}
    for coin in COINS:
        train_candles[coin] = [c for c in all_candles[coin] if int(c["start"]) < split_point]
        test_candles[coin] = [c for c in all_candles[coin] if int(c["start"]) >= split_point]
        print(f"  {coin}: train={len(train_candles[coin])}, test={len(test_candles[coin])}")
    
    print()
    
    pairs = list(combinations(COINS, 2))
    
    print("=" * 72)
    print("IN-SAMPLE RESULTS (first 45 days)")
    print("=" * 72)
    
    in_sample_results = []
    for coin_a, coin_b in pairs:
        pair_name = f"{coin_a.replace('-USD', '')}/{coin_b.replace('-USD', '')}"
        rs = compute_rolling_returns(train_candles[coin_a], train_candles[coin_b], window=WINDOW)
        r = run_pair_backtest(rs)
        if r:
            r["pair"] = pair_name
            in_sample_results.append(r)
    
    in_sample_results.sort(key=lambda x: x["pnl"], reverse=True)
    
    print(f"\n{'Pair':<12} | {'Trades':>6} | {'PnL':>8} | {'WR':>5} | {'Avg PnL':>8} | {'Max DD':>8}")
    print("-" * 72)
    for r in in_sample_results:
        print(f"{r['pair']:<12} | {r['trades']:>6} | ${r['pnl']:>7.2f} | {r['win_rate']:>4.0f}% | ${r['avg_pnl']:>7.3f} | ${r['max_drawdown']:>7.2f}")
    
    total_is = sum(r["pnl"] for r in in_sample_results)
    total_trades_is = sum(r["trades"] for r in in_sample_results)
    print(f"\nIn-sample total: ${total_is:.2f} ({total_trades_is} trades)")
    
    print()
    print("=" * 72)
    print("OUT-OF-SAMPLE RESULTS (last 15 days — FORWARD SHADOW)")
    print("=" * 72)
    
    oos_results = []
    for coin_a, coin_b in pairs:
        pair_name = f"{coin_a.replace('-USD', '')}/{coin_b.replace('-USD', '')}"
        rs = compute_rolling_returns(test_candles[coin_a], test_candles[coin_b], window=WINDOW)
        r = run_pair_backtest(rs)
        if r:
            r["pair"] = pair_name
            oos_results.append(r)
    
    oos_results.sort(key=lambda x: x["pnl"], reverse=True)
    
    print(f"\n{'Pair':<12} | {'Trades':>6} | {'PnL':>8} | {'WR':>5} | {'Avg PnL':>8} | {'Max DD':>8}")
    print("-" * 72)
    for r in oos_results:
        print(f"{r['pair']:<12} | {r['trades']:>6} | ${r['pnl']:>7.2f} | {r['win_rate']:>4.0f}% | ${r['avg_pnl']:>7.3f} | ${r['max_drawdown']:>7.2f}")
    
    total_oos = sum(r["pnl"] for r in oos_results)
    total_trades_oos = sum(r["trades"] for r in oos_results)
    print(f"\nOut-of-sample total: ${total_oos:.2f} ({total_trades_oos} trades)")
    
    # Verdict
    print()
    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    
    positive_is = sum(1 for r in in_sample_results if r["pnl"] > 0)
    positive_oos = sum(1 for r in oos_results if r["pnl"] > 0)
    
    # Project OOS to 60d equivalent
    oos_60d_proj = total_oos * (60 / 15)
    
    print(f"\nIn-sample:  ${total_is:.2f} ({positive_is}/{len(in_sample_results)} pairs positive)")
    print(f"Out-of-sample: ${total_oos:.2f} ({positive_oos}/{len(oos_results)} pairs positive)")
    print(f"OOS projected to 60d: ${oos_60d_proj:.2f}")
    print()
    
    if total_oos > 0 and positive_oos >= len(oos_results) / 2:
        print("✅ EDGE SURVIVES OUT-OF-SAMPLE — structural, not curve-fit")
        print(f"   The rotation lattice generates ${oos_60d_proj:.0f}/60d even on unseen data.")
    elif total_oos > 0:
        print("⚠️  EDGE PARTIALLY SURVIVES — positive overall but some pairs degraded")
        print(f"   OOS PnL: ${total_oos:.2f}. Worth monitoring but not fully robust.")
    else:
        print("❌ EDGE FAILED OUT-OF-SAMPLE — likely curve-fit to in-sample period")
        print(f"   OOS PnL: ${total_oos:.2f}. Do NOT promote to live.")
    
    # Save results
    output = {
        "config": {
            "entry_threshold": ENTRY_THRESHOLD,
            "exit_threshold": EXIT_THRESHOLD,
            "window": WINDOW,
            "max_hold": MAX_HOLD,
        },
        "in_sample": {
            "days": 45,
            "total_pnl": round(total_is, 2),
            "total_trades": total_trades_is,
            "positive_pairs": positive_is,
            "results": in_sample_results,
        },
        "out_of_sample": {
            "days": 15,
            "total_pnl": round(total_oos, 2),
            "total_trades": total_trades_oos,
            "positive_pairs": positive_oos,
            "projected_60d": round(oos_60d_proj, 2),
            "results": oos_results,
        },
        "verdict": "PASS" if total_oos > 0 and positive_oos >= len(oos_results) / 2 else ("PARTIAL" if total_oos > 0 else "FAIL"),
    }
    
    report_path = ROOT / "reports" / "rotation_lattice_forward_shadow.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    
    md_path = ROOT / "reports" / "rotation_lattice_forward_shadow.md"
    verdict_text = "✅ EDGE SURVIVES OUT-OF-SAMPLE" if output["verdict"] == "PASS" else ("⚠️ PARTIAL" if output["verdict"] == "PARTIAL" else "❌ FAILED")
    md_lines = [
        f"# Rotation Lattice Forward-Shadow Validation",
        f"\n**Verdict: {verdict_text}**",
        f"\nConfig: Entry {ENTRY_THRESHOLD*100:.0f}%, Exit {EXIT_THRESHOLD*100:.1f}%, Window {WINDOW}, MaxHold {MAX_HOLD}",
        f"\n## In-Sample (45 days)",
        f"\nTotal: ${total_is:.2f} ({positive_is}/{len(in_sample_results)} pairs positive)",
        f"\n| Pair | Trades | PnL | WR | Avg PnL | Max DD |",
        f"|------|--------|-----|----|---------|--------|",
    ]
    for r in in_sample_results:
        md_lines.append(f"| {r['pair']} | {r['trades']} | ${r['pnl']:.2f} | {r['win_rate']}% | ${r['avg_pnl']:.3f} | ${r['max_drawdown']:.2f} |")
    
    md_lines.extend([
        f"\n## Out-of-Sample (15 days — forward shadow)",
        f"\nTotal: ${total_oos:.2f} ({positive_oos}/{len(oos_results)} pairs positive)",
        f"Projected to 60d: ${oos_60d_proj:.2f}",
        f"\n| Pair | Trades | PnL | WR | Avg PnL | Max DD |",
        f"|------|--------|-----|----|---------|--------|",
    ])
    for r in oos_results:
        md_lines.append(f"| {r['pair']} | {r['trades']} | ${r['pnl']:.2f} | {r['win_rate']}% | ${r['avg_pnl']:.3f} | ${r['max_drawdown']:.2f} |")
    
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    
    print(f"\nFull results: {report_path}")
    print(f"Markdown: {md_path}")


if __name__ == "__main__":
    main()
