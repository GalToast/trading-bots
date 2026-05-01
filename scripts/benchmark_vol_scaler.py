#!/usr/bin/env python3
"""
Volatility Scaler Backtest — The Dynamic Ceilings
===================================================
@gemini's breakthrough: Position sizing should scale with volatility.

Pump regime (ATR% > 3%): 95% deploy → max geometric growth
Active regime (ATR% 1.5-3%): 25% deploy → safe compounding
Dead regime (ATR% < 1.5%): 0% deploy → stay in cash

This should beat both:
- No-gate baseline ($162.85 — bleeds during dead periods)
- Binary regime gate ($81.04 — misses active-period opportunities)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_coinbase_spot_rsi import fetch_candles_72h, rsi as compute_rsi
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "vol_scaler_backtest.json"

PRODUCT = "RAVE-USD"
STARTING_CASH = 48.0


def compute_atr_pct(candles, period=14):
    if len(candles) < period + 1:
        return [0.0] * len(candles)
    true_ranges = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        pc = float(candles[i-1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        true_ranges.append(tr)
    atr_vals = [0.0] * len(candles)
    if len(true_ranges) >= period:
        atr = sum(true_ranges[:period]) / period
        atr_vals[period] = atr / float(candles[period]["close"]) * 100 if float(candles[period]["close"]) > 0 else 0
        for i in range(period + 1, len(candles)):
            atr = (atr * (period - 1) + true_ranges[i - 1]) / period
            atr_vals[i] = atr / float(candles[i]["close"]) * 100 if float(candles[i]["close"]) > 0 else 0
    return atr_vals


def run_vol_scaler(candles, strategy_name, rsi_period, rsi_entry, rsi_exit,
                    tp_pct=0.0, sl_pct=0.0, max_hold=24,
                    pump_thresh=3.0, active_thresh=1.5,
                    pump_deploy=0.95, active_deploy=0.25, dead_deploy=0.0):
    """Volatility-scaling position sizing."""
    if len(candles) < rsi_period + 30:
        return None
    
    closes = [float(c["close"]) for c in candles]
    rsi_vals = compute_rsi(closes, rsi_period)
    atr_pct = compute_atr_pct(candles, 14)
    
    fee_rate = 0.0040
    cash = STARTING_CASH
    in_position = False
    position = None
    trades = []
    regime_counts = {"pump": 0, "active": 0, "dead": 0}
    regime_pnl = {"pump": 0.0, "active": 0.0, "dead": 0.0}
    
    for i in range(rsi_period + 30, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        current_rsi = rsi_vals[i]
        current_atr = atr_pct[i]
        
        # Determine regime
        if current_atr >= pump_thresh:
            regime = "pump"
            deploy_pct = pump_deploy
        elif current_atr >= active_thresh:
            regime = "active"
            deploy_pct = active_deploy
        else:
            regime = "dead"
            deploy_pct = dead_deploy
        
        regime_counts[regime] += 1
        
        # EXIT
        if in_position and position:
            exit_price = None
            exit_reason = None
            
            if tp_pct > 0 and h >= position["entry"] * (1 + tp_pct):
                exit_price = position["entry"] * (1 + tp_pct)
                exit_reason = "tp"
            elif sl_pct > 0 and l <= position["entry"] * (1 - sl_pct):
                exit_price = position["entry"] * (1 - sl_pct)
                exit_reason = "sl"
            elif rsi_exit > 0 and current_rsi >= rsi_exit:
                exit_price = cl
                exit_reason = "rsi_exit"
            elif (i - position["bar"]) >= max_hold:
                exit_price = cl
                exit_reason = "timeout"
            
            if exit_price is not None:
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty
                entry_fee = position["entry"] * qty * fee_rate
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                
                cash += position["quote"] + net
                trades.append({"net": net, "reason": exit_reason, "win": net > 0, "regime": position.get("regime", "unknown")})
                regime_pnl[position.get("regime", "unknown")] += net
                in_position = False
                position = None
                continue
        
        # ENTRY
        if not in_position and cash >= 10.0 and current_rsi < rsi_entry and deploy_pct > 0:
            deploy = cash * deploy_pct
            entry_fee = cl * (deploy / cl) * fee_rate
            qty = (deploy - entry_fee) / cl
            
            if qty > 0:
                cash -= deploy
                position = {"entry": cl, "qty": qty, "bar": i, "quote": deploy, "regime": regime}
                in_position = True
    
    if position:
        cash += position["quote"]
    
    net = cash - STARTING_CASH
    wins = [t for t in trades if t["win"]]
    
    return {
        "strategy": strategy_name,
        "net": round(net, 2),
        "return_pct": round(net / STARTING_CASH * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg": round(net / max(1, len(trades)), 4),
        "regime_counts": regime_counts,
        "regime_pnl": {k: round(v, 2) for k, v in regime_pnl.items()},
        "regime_wr": {
            k: round(sum(1 for t in trades if t.get("regime") == k and t["win"]) / max(1, sum(1 for t in trades if t.get("regime") == k)) * 100, 1)
            for k in ["pump", "active", "dead"]
        },
    }


def main():
    client = CoinbaseAdvancedClient()
    
    print(f"Fetching 72h candles for {PRODUCT}...")
    candles = fetch_candles_72h(client, PRODUCT, "FIVE_MINUTE")
    print(f"  Got {len(candles)} candles")
    
    atr_pct = compute_atr_pct(candles, 14)
    non_zero_atr = [a for a in atr_pct if a > 0]
    avg_atr = sum(non_zero_atr) / len(non_zero_atr) if non_zero_atr else 0
    max_atr = max(atr_pct) if atr_pct else 0
    min_atr = min(a for a in atr_pct if a > 0) if any(a > 0 for a in atr_pct) else 0
    print(f"  ATR% range: {min_atr:.2f}% — {max_atr:.2f}% (avg: {avg_atr:.2f}%)")
    
    # Count regime distribution
    pump_bars = sum(1 for a in atr_pct if a >= 3.0)
    active_bars = sum(1 for a in atr_pct if 1.5 <= a < 3.0)
    dead_bars = sum(1 for a in atr_pct if 0 < a < 1.5)
    total = len([a for a in atr_pct if a > 0])
    print(f"  Regime distribution: Pump={pump_bars}/{total} ({pump_bars/max(1,total)*100:.0f}%), Active={active_bars}/{total} ({active_bars/max(1,total)*100:.0f}%), Dead={dead_bars}/{total} ({dead_bars/max(1,total)*100:.0f}%)")
    
    results = []
    
    # Baselines
    print(f"\n{'='*80}")
    print(f"  BASELINES")
    print(f"{'='*80}")
    
    # No gate, no scaler
    print("\n1. No gate, no scaler (RSI(4)<45 + RSI>80)")
    result = run_vol_scaler(candles, "no_scaler_baseline", rsi_period=4, rsi_entry=45, rsi_exit=80,
                             pump_thresh=0, active_thresh=0, pump_deploy=0.95, active_deploy=0.95, dead_deploy=0.95)
    if result:
        results.append(result)
        print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR")
    
    # Binary gate (dead = 0%, else 95%)
    print("\n2. Binary gate (ATR>1.5%: 95%, else: 0%)")
    result = run_vol_scaler(candles, "binary_gate_1.5", rsi_period=4, rsi_entry=45, rsi_exit=80,
                             pump_thresh=1.5, active_thresh=0, pump_deploy=0.95, active_deploy=0.0, dead_deploy=0.0)
    if result:
        results.append(result)
        print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR")
    
    print(f"\n{'='*80}")
    print(f"  VOLATILITY SCALER SWEEP")
    print(f"{'='*80}")
    
    # Vol Scaler configs
    scaler_configs = [
        # (name, pump_thresh, active_thresh, pump_deploy, active_deploy, dead_deploy)
        ("vol_scaler_3_1.5_95_25_0", 3.0, 1.5, 0.95, 0.25, 0.0),
        ("vol_scaler_2.5_1.5_95_25_0", 2.5, 1.5, 0.95, 0.25, 0.0),
        ("vol_scaler_2_1_95_25_0", 2.0, 1.0, 0.95, 0.25, 0.0),
        ("vol_scaler_3_1.5_95_50_0", 3.0, 1.5, 0.95, 0.50, 0.0),
        ("vol_scaler_3_1.5_95_10_0", 3.0, 1.5, 0.95, 0.10, 0.0),
        ("vol_scaler_2_1.5_95_50_25", 2.0, 1.5, 0.95, 0.50, 0.25),
        ("vol_scaler_1.5_1_95_25_0", 1.5, 1.0, 0.95, 0.25, 0.0),
    ]
    
    for name, pt, at, pd, ad, dd in scaler_configs:
        print(f"\n{name}:")
        result = run_vol_scaler(candles, name, rsi_period=4, rsi_entry=45, rsi_exit=80,
                                 pump_thresh=pt, active_thresh=at, pump_deploy=pd, active_deploy=ad, dead_deploy=dd)
        if result:
            results.append(result)
            print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR")
            print(f"  Regime PnL: Pump=${result['regime_pnl']['pump']:.2f} ({result['regime_wr']['pump']}%WR), Active=${result['regime_pnl']['active']:.2f} ({result['regime_wr']['active']}%WR), Dead=${result['regime_pnl']['dead']:.2f}")
    
    # Also test with StochRSI
    print(f"\n{'='*80}")
    print(f"  STOCHRSI + VOL SCALER")
    print(f"{'='*80}")
    
    # We'd need to add StochRSI to the runner — for now, test RSI(3) ultra-fast with vol scaler
    for name, pt, at, pd, ad, dd in [("stoch_scaler_3_1.5_95_25_0", 3.0, 1.5, 0.95, 0.25, 0.0)]:
        print(f"\n{name} (RSI(3)<50 + RSI>80):")
        result = run_vol_scaler(candles, name, rsi_period=3, rsi_entry=50, rsi_exit=80,
                                 pump_thresh=pt, active_thresh=at, pump_deploy=pd, active_deploy=ad, dead_deploy=dd)
        if result:
            results.append(result)
            print(f"  ${result['net']:.2f} ({result['return_pct']}%), {result['trades']}t, {result['wr']}%WR")
    
    # Sort and summary
    results.sort(key=lambda r: r["net"], reverse=True)
    
    print(f"\n{'='*100}")
    print(f"{'Strategy':<40} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'Win%':>6} {'Pump$':>8} {'Active$':>8} {'Dead$':>8}")
    print(f"{'='*100}")
    for r in results:
        print(f"{r['strategy']:<40} ${r['net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>7} {r['wr']:>5.1f}% ${r['regime_pnl']['pump']:>6.2f} ${r['regime_pnl']['active']:>6.2f} ${r['regime_pnl']['dead']:>6.2f}")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "atr_stats": {"avg": round(avg_atr, 2), "max": round(max_atr, 2), "min": round(min_atr, 2)},
        "regime_distribution": {"pump": pump_bars, "active": active_bars, "dead": dead_bars, "total": total},
        "results": results,
        "baselines": {
            "no_scaler": results[0]["net"] if results else 0,
            "binary_gate": results[1]["net"] if len(results) > 1 else 0,
        },
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")
    
    if results:
        best = results[0]
        baseline_net = results[0]["net"] if results else 0
        for r in results:
            if "no_scaler" in r["strategy"].lower() or "baseline" in r["strategy"].lower():
                baseline_net = r["net"]
                break
        
        print(f"\n🏆 VOL SCALER CHAMPION: {best['strategy']}")
        print(f"   ${best['net']:.2f}/72h ({best['return_pct']}%)")
        
        if best["net"] > baseline_net:
            print(f"   ✅ Beat baseline by ${best['net']-baseline_net:.2f} (+{(best['net']-baseline_net)/abs(baseline_net)*100:.1f}%)")
        else:
            print(f"   Baseline was stronger at ${baseline_net:.2f}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
