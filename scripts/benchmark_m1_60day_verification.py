#!/usr/bin/env python3
"""
60-Day M1 RSI(3) + 54-bar + 25%TP Verification
=================================================
Independent replication of @main's M1 finding.

Strategy: M1 RSI(3)<30 + 25% TP + No SL + 54-bar hold + 95% compound
Testing on 60-day history to confirm it's not a hot window outlier.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "m1_60day_verification.json"

PRODUCT = "RAVE-USD"
STARTING_CASH = 48.0


def fetch_candles_range(client, product_id, granularity, days=60):
    """Fetch candles for full date range with rate limit handling."""
    now = int(time.time())
    start = now - days * 24 * 3600
    gsec_map = {"ONE_MINUTE": 60, "FIVE_MINUTE": 300}
    gsec = gsec_map.get(granularity, 60)
    max_per_req = 300
    all_c = []
    seen = set()
    chunk_end = now
    
    retries = 0
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        try:
            resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
            raw = resp.get("candles") or []
            if not raw:
                break
            for c in raw:
                t = int(c.get("start", c.get("time", 0)))
                if t not in seen:
                    seen.add(t)
                    all_c.append({
                        "time": t,
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                        "volume": float(c.get("volume", 0)),
                    })
            chunk_end = chunk_start - 1
            retries = 0
            time.sleep(0.2)
        except Exception as e:
            if "429" in str(e):
                retries += 1
                wait = 2 ** retries
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                chunk_end -= max_per_req * gsec
            else:
                print(f"    Fetch error: {e}")
                chunk_end -= max_per_req * gsec
                time.sleep(0.5)
    
    return sorted(all_c, key=lambda x: x["time"])


def compute_rsi(closes, period=3):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result = []
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        result.append(100 - 100 / (1 + rs))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))
        else:
            result.append(100.0)
    return [50.0] * period + result


def run_m1_strategy(candles, rsi_period, rsi_entry, tp_pct, max_hold, fee_bps=40, deploy_pct=0.95):
    """Run M1 RSI strategy with compound sizing."""
    if len(candles) < rsi_period + 60:
        return None
    
    fee_rate = fee_bps / 10000.0
    closes = [c["close"] for c in candles]
    rsi_vals = compute_rsi(closes, rsi_period)
    
    # Get fee tiers
    def get_fee_rate(volume):
        if volume >= 50000: return 0.0015
        elif volume >= 10000: return 0.0025
        else: return fee_rate
    
    cash = STARTING_CASH
    in_position = False
    position = None
    trades = []
    total_volume = 0.0
    total_fees = 0.0
    
    for i in range(rsi_period + 60, len(candles) - 1):
        c = candles[i]
        h = c["high"]
        l = c["low"]
        cl = c["close"]
        current_rsi = rsi_vals[i]
        current_fee = get_fee_rate(total_volume)
        
        # EXIT
        if in_position and position:
            exit_price = None
            exit_reason = None
            
            # TP check
            if h >= position["entry"] * (1 + tp_pct):
                exit_price = position["entry"] * (1 + tp_pct)
                exit_reason = "tp"
            # Timeout check
            elif (i - position["bar"]) >= max_hold:
                exit_price = cl
                exit_reason = "timeout"
            
            if exit_price is not None:
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty
                entry_fee = position["entry"] * qty * current_fee
                exit_fee = exit_price * qty * current_fee
                net = gross - entry_fee - exit_fee
                
                cash += position["quote"] + net
                total_volume += position["quote"] + (exit_price * qty)
                total_fees += entry_fee + exit_fee
                
                trades.append({
                    "net": net, "reason": exit_reason, "win": net > 0,
                    "hold_bars": i - position["bar"],
                })
                in_position = False
                position = None
                continue
        
        # ENTRY
        if not in_position and cash >= 10.0 and current_rsi < rsi_entry:
            deploy = cash * deploy_pct
            entry_fee = cl * (deploy / cl) * current_fee
            qty = (deploy - entry_fee) / cl
            
            if qty > 0:
                cash -= deploy
                position = {
                    "entry": cl, "qty": qty, "bar": i, "quote": deploy,
                }
                in_position = True
    
    # Close open position
    if position:
        cash += position["quote"]
    
    net = cash - STARTING_CASH
    wins = [t for t in trades if t["win"]]
    
    return {
        "net": round(net, 2),
        "return_pct": round(net / STARTING_CASH * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg": round(net / max(1, len(trades)), 4),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "final_cash": round(cash, 2),
        "tp_exits": len([t for t in trades if t["reason"] == "tp"]),
        "timeout_exits": len([t for t in trades if t["reason"] == "timeout"]),
    }


def main():
    client = CoinbaseAdvancedClient()
    
    print("=" * 80)
    print("  60-DAY M1 RSI(3) VERIFICATION")
    print("=" * 80)
    
    # Fetch 60-day M1 data
    print(f"\nFetching 60-day M1 candles for {PRODUCT}...")
    candles = fetch_candles_range(client, PRODUCT, "ONE_MINUTE", days=60)
    print(f"  Got {len(candles)} M1 candles")
    
    if not candles:
        print("ERROR: Could not fetch candles")
        return 1
    
    # Calculate date ranges
    first_ts = candles[0]["time"]
    last_ts = candles[-1]["time"]
    total_days = (last_ts - first_ts) / 86400
    print(f"\n  Date range: {total_days:.1f} days")
    print(f"  First: {time.strftime('%Y-%m-%d', time.gmtime(first_ts))}")
    print(f"  Last:  {time.strftime('%Y-%m-%d', time.gmtime(last_ts))}")
    
    # Split into periods for analysis
    day_11_ts = first_ts + 11 * 86400
    day_30_ts = first_ts + 30 * 86400
    
    idx_11 = next((i for i, c in enumerate(candles) if c["time"] > day_11_ts), len(candles) // 5)
    idx_30 = next((i for i, c in enumerate(candles) if c["time"] > day_30_ts), len(candles) // 2)
    
    candles_11d = candles[:idx_11]
    candles_30d = candles[:idx_30]
    candles_60d = candles
    
    results = []
    
    # TEST 1: Replicate @main's 11-day result
    print(f"\n{'='*80}")
    print(f"  TEST 1: Replicate 11-day result (M1 RSI(3)<30 + 54-bar + 25%TP)")
    print(f"{'='*80}")
    
    result_11d = run_m1_strategy(candles_11d, rsi_period=3, rsi_entry=30, tp_pct=0.25, max_hold=54)
    if result_11d:
        print(f"\n  11 days: ${result_11d['net']:.2f} ({result_11d['return_pct']}%), {result_11d['trades']}t, {result_11d['wr']}%WR")
        print(f"  TP exits: {result_11d['tp_exits']}, Timeout exits: {result_11d['timeout_exits']}")
        print(f"  vs @main's $251.61: {'✅ MATCH' if abs(result_11d['net'] - 251.61) < 50 else '❌ DIFFERENT'}")
        results.append({"period": "11d", **result_11d})
    
    # TEST 2: 30-day result
    print(f"\n{'='*80}")
    print(f"  TEST 2: 30-day result (M1 RSI(3)<30 + 54-bar + 25%TP)")
    print(f"{'='*80}")
    
    result_30d = run_m1_strategy(candles_30d, rsi_period=3, rsi_entry=30, tp_pct=0.25, max_hold=54)
    if result_30d:
        print(f"\n  30 days: ${result_30d['net']:.2f} ({result_30d['return_pct']}%), {result_30d['trades']}t, {result_30d['wr']}%WR")
        print(f"  TP exits: {result_30d['tp_exits']}, Timeout exits: {result_30d['timeout_exits']}")
        print(f"  Daily avg: ${result_30d['net']/30:.2f}/day")
        print(f"  Monthly projection: ${result_30d['net']/30*30:.2f}/month")
        results.append({"period": "30d", **result_30d})
    
    # TEST 3: Full 60-day result
    print(f"\n{'='*80}")
    print(f"  TEST 3: Full 60-day result (M1 RSI(3)<30 + 54-bar + 25%TP)")
    print(f"{'='*80}")
    
    result_60d = run_m1_strategy(candles_60d, rsi_period=3, rsi_entry=30, tp_pct=0.25, max_hold=54)
    if result_60d:
        print(f"\n  60 days: ${result_60d['net']:.2f} ({result_60d['return_pct']}%), {result_60d['trades']}t, {result_60d['wr']}%WR")
        print(f"  TP exits: {result_60d['tp_exits']}, Timeout exits: {result_60d['timeout_exits']}")
        print(f"  Daily avg: ${result_60d['net']/total_days:.2f}/day")
        print(f"  Monthly projection: ${result_60d['net']/total_days*30:.2f}/month")
        results.append({"period": "60d", **result_60d})
    
    # TEST 4: Fee tier impact analysis
    print(f"\n{'='*80}")
    print(f"  TEST 4: Fee tier impact (60-day, different fee levels)")
    print(f"{'='*80}")
    
    for fee_bps in [40, 25, 15]:
        result = run_m1_strategy(candles_60d, rsi_period=3, rsi_entry=30, tp_pct=0.25, max_hold=54, fee_bps=fee_bps)
        if result:
            print(f"\n  {fee_bps}bps fees: ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR)")
            results.append({"period": f"60d_fee{fee_bps}", **result})
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  VERIFICATION SUMMARY")
    print(f"{'='*80}")
    
    print(f"\n  {'Period':<10} {'Net $':>8} {'Return%':>8} {'Trades':>7} {'Win%':>6} {'$/day':>8} {'TP%':>6}")
    print(f"  {'-'*60}")
    for r in results:
        period = r.get("period", "?")
        days = {"11d": 11, "30d": 30, "60d": total_days}.get(period, total_days)
        if isinstance(days, float):
            days = total_days
        per_day = r["net"] / days if days > 0 else 0
        tp_pct = r.get("tp_exits", 0) / max(1, r["trades"]) * 100
        print(f"  {period:<10} ${r['net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>7} {r['wr']:>5.1f}% ${per_day:>6.2f} {tp_pct:>5.1f}%")
    
    # Verdict
    if result_60d and result_60d["net"] > 0:
        print(f"\n  🏆 EDGE SURVIVES 60 DAYS — ${result_60d['net']:.2f} ({result_60d['return_pct']}%)")
        print(f"  Daily avg: ${result_60d['net']/total_days:.2f}/day")
        print(f"  Space launch: APPROVED ✅")
    elif result_60d:
        print(f"\n  ❌ EDGE COLLAPSED on 60-day data — ${result_60d['net']:.2f}")
        print(f"  Space launch: DENIED ❌")
    else:
        print(f"\n  ⚠️  Could not complete 60-day test")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_days": round(total_days, 1),
        "results": results,
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report: {out}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
