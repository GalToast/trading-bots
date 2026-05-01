#!/usr/bin/env python3
"""
M15 Ranging Filter — Assumption Audit
=======================================
@main called it: we've been optimizing parameters without validating foundation.

Testing the M15 ranging filter on FULL 14-day history with:
1. Full 14-day backtest (not just 72h hot window)
2. Out-of-sample split (train days 1-7, test days 8-14)
3. Fee stress test (40bps → 80bps → 120bps realistic slippage)
4. Spread fill simulation (bid/ask mid-price vs candle close)

If edge survives all this → it's REAL. If not → cracked foundation.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "m15_assumption_audit.json"

PRODUCT = "RAVE-USD"
STARTING_CASH = 48.0


def fetch_candles_range(client, product_id, granularity, days=14):
    """Fetch candles for full date range."""
    now = int(time.time())
    start = now - days * 24 * 3600
    gsec_map = {"FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900}
    gsec = gsec_map.get(granularity, 300)
    max_per_req = 300
    all_c = []
    seen = set()
    chunk_end = now
    
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
            time.sleep(0.15)
        except Exception as e:
            print(f"    Fetch error: {e}")
            chunk_end -= max_per_req * gsec
            time.sleep(0.5)
    
    return sorted(all_c, key=lambda x: x["time"])


def compute_rsi(closes, period=4):
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


def run_full_backtest(candles_m5, candles_m15, rsi_period, rsi_entry, rsi_exit,
                       m15_range_thresh, fee_bps, deploy_pct=0.95, use_mid_fill=False):
    """Run backtest with configurable fees and fill prices."""
    if len(candles_m5) < rsi_period + 20 or len(candles_m15) < 10:
        return None
    
    fee_rate = fee_bps / 10000.0
    closes = [c["close"] for c in candles_m5]
    rsi_vals = compute_rsi(closes, rsi_period)
    
    # M15 lookup
    m15_by_time = {c["time"]: c for c in candles_m15}
    m15_times = sorted(m15_by_time.keys())
    
    cash = STARTING_CASH
    in_position = False
    position = None
    trades = []
    ranging_bars = 0
    trending_bars = 0
    
    for i in range(rsi_period + 10, len(candles_m5) - 1):
        c = candles_m5[i]
        h = c["high"]
        l = c["low"]
        cl = c["close"]
        ts = c["time"]
        current_rsi = rsi_vals[i]
        
        # Fill price simulation
        if use_mid_fill:
            spread_estimate = (h - l) * 0.1  # 10% of range as spread
            entry_price = cl + spread_estimate / 2  # Pay ask on entry
            exit_price_adjustment = -spread_estimate / 2  # Get bid on exit
        else:
            entry_price = cl
            exit_price_adjustment = 0
        
        # M15 RANGE CHECK
        is_ranging = True
        if len(m15_times) >= 4:
            recent_m15_times = [t for t in m15_times if t <= ts][-4:]
            if len(recent_m15_times) >= 2:
                recent_m15 = [m15_by_time[t] for t in recent_m15_times]
                ranges = []
                for mc in recent_m15:
                    if mc["close"] > 0:
                        ranges.append((mc["high"] - mc["low"]) / mc["close"] * 100)
                if ranges:
                    avg_range = sum(ranges) / len(ranges)
                    is_ranging = avg_range < m15_range_thresh
        
        if is_ranging:
            ranging_bars += 1
        else:
            trending_bars += 1
        
        # EXIT
        if in_position and position:
            exit_price = None
            exit_reason = None
            
            if h >= position["entry"] * (1 + position["tp_pct"]):
                exit_price = position["entry"] * (1 + position["tp_pct"])
                exit_reason = "tp"
            elif l <= position["entry"] * (1 - position["sl_pct"]):
                exit_price = position["entry"] * (1 - position["sl_pct"])
                exit_reason = "sl"
            elif rsi_exit > 0 and current_rsi >= rsi_exit:
                exit_price = cl + exit_price_adjustment
                exit_reason = "rsi_exit"
            elif (i - position["bar"]) >= position["max_hold"]:
                exit_price = cl + exit_price_adjustment
                exit_reason = "timeout"
            
            if exit_price is not None:
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty
                entry_fee = position["entry"] * qty * fee_rate
                exit_fee = exit_price * qty * fee_rate
                net = gross - entry_fee - exit_fee
                
                cash += position["quote"] + net
                trades.append({
                    "net": net, "reason": exit_reason, "win": net > 0,
                    "is_ranging": position.get("is_ranging", False),
                    "bar": i,
                })
                in_position = False
                position = None
                continue
        
        # ENTRY with M15 ranging filter
        if not in_position and cash >= 10.0 and current_rsi < rsi_entry and is_ranging:
            deploy = cash * deploy_pct
            entry_fee = entry_price * (deploy / entry_price) * fee_rate
            qty = (deploy - entry_fee) / entry_price
            
            if qty > 0:
                cash -= deploy
                position = {
                    "entry": entry_price, "qty": qty, "bar": i, "quote": deploy,
                    "is_ranging": True, "tp_pct": 0.0, "sl_pct": 0.0,
                    "max_hold": 24
                }
                in_position = True
    
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
        "ranging_bars": ranging_bars,
        "trending_bars": trending_bars,
        "ranging_pct": round(ranging_bars / max(1, ranging_bars + trending_bars) * 100, 1),
    }


def main():
    client = CoinbaseAdvancedClient()
    
    print("=" * 80)
    print("  M15 RANGING FILTER — ASSUMPTION AUDIT")
    print("=" * 80)
    
    # Fetch full 14-day history
    print(f"\nFetching 14-day M5 candles for {PRODUCT}...")
    candles_m5 = fetch_candles_range(client, PRODUCT, "FIVE_MINUTE", days=14)
    print(f"  Got {len(candles_m5)} M5 candles")
    
    print(f"Fetching 14-day M15 candles for {PRODUCT}...")
    candles_m15 = fetch_candles_range(client, PRODUCT, "FIFTEEN_MINUTE", days=14)
    print(f"  Got {len(candles_m15)} M15 candles")
    
    if not candles_m5 or not candles_m15:
        print("ERROR: Could not fetch candles")
        return 1
    
    # Calculate date ranges
    first_ts = candles_m5[0]["time"]
    last_ts = candles_m5[-1]["time"]
    total_days = (last_ts - first_ts) / 86400
    day_7_ts = first_ts + 7 * 86400
    
    print(f"\n  Date range: {total_days:.1f} days")
    print(f"  First: {time.strftime('%Y-%m-%d', time.gmtime(first_ts))}")
    print(f"  Last:  {time.strftime('%Y-%m-%d', time.gmtime(last_ts))}")
    print(f"  Split: {time.strftime('%Y-%m-%d', time.gmtime(day_7_ts))} (day 7)")
    
    # Split into first 7 days and last 7 days
    idx_split = next((i for i, c in enumerate(candles_m5) if c["time"] > day_7_ts), len(candles_m5) // 2)
    candles_first_7 = candles_m5[:idx_split]
    candles_last_7 = candles_m5[idx_split:]
    
    m15_idx_split = next((i for i, c in enumerate(candles_m15) if c["time"] > day_7_ts), len(candles_m15) // 2)
    m15_first_7 = candles_m15[:m15_idx_split]
    m15_last_7 = candles_m15[m15_idx_split:]
    
    print(f"  First 7 days: {len(candles_first_7)} M5 candles")
    print(f"  Last 7 days:  {len(candles_last_7)} M5 candles")
    
    all_results = []
    
    # TEST 1: Full 14-day with and without M15 filter
    print(f"\n{'='*80}")
    print(f"  TEST 1: Full 14-Day Backtest (Filter vs No Filter)")
    print(f"{'='*80}")
    
    for thresh in [5.0, 7.0, 10.0]:
        # With filter
        result_with = run_full_backtest(candles_m5, candles_m15, rsi_period=4, rsi_entry=45, rsi_exit=80,
                                         m15_range_thresh=thresh, fee_bps=40)
        # Without filter
        result_without = run_full_backtest(candles_m5, candles_m15, rsi_period=4, rsi_entry=45, rsi_exit=80,
                                            m15_range_thresh=999, fee_bps=40)
        
        if result_with and result_without:
            improvement = result_with["net"] - result_without["net"]
            print(f"\n  Range<{thresh}% (fee 40bps):")
            print(f"    WITH filter:    ${result_with['net']:.2f} ({result_with['return_pct']}%), {result_with['trades']}t, {result_with['wr']}%WR, {result_with['ranging_pct']}% ranging")
            print(f"    WITHOUT filter: ${result_without['net']:.2f} ({result_without['return_pct']}%), {result_without['trades']}t")
            print(f"    Improvement: {'✅' if improvement > 0 else '❌'} ${improvement:+.2f}")
            
            all_results.append({
                "test": "full_14day",
                "threshold": thresh,
                "fee_bps": 40,
                "with_filter": result_with,
                "without_filter": result_without,
                "improvement": round(improvement, 2),
            })
    
    # TEST 2: Out-of-sample split
    print(f"\n{'='*80}")
    print(f"  TEST 2: Out-of-Sample (Train Days 1-7, Test Days 8-14)")
    print(f"{'='*80}")
    
    # Train on first 7 days
    print(f"\n  Training on first 7 days:")
    for thresh in [5.0, 7.0, 10.0]:
        result_train = run_full_backtest(candles_first_7, m15_first_7, rsi_period=4, rsi_entry=45, rsi_exit=80,
                                          m15_range_thresh=thresh, fee_bps=40)
        result_test = run_full_backtest(candles_last_7, m15_last_7, rsi_period=4, rsi_entry=45, rsi_exit=80,
                                         m15_range_thresh=thresh, fee_bps=40)
        
        if result_train and result_test:
            print(f"    Range<{thresh}%: Train=${result_train['net']:.2f} ({result_train['trades']}t) → Test=${result_test['net']:.2f} ({result_test['trades']}t)")
            
            all_results.append({
                "test": "out_of_sample",
                "threshold": thresh,
                "train_net": result_train["net"],
                "test_net": result_test["net"],
                "train_trades": result_train["trades"],
                "test_trades": result_test["trades"],
            })
    
    # TEST 3: Fee stress test
    print(f"\n{'='*80}")
    print(f"  TEST 3: Fee Stress Test (40bps → 120bps)")
    print(f"{'='*80}")
    
    for fee_bps in [40, 80, 120]:
        result = run_full_backtest(candles_m5, candles_m15, rsi_period=4, rsi_entry=45, rsi_exit=80,
                                    m15_range_thresh=10.0, fee_bps=fee_bps)
        result_no_filter = run_full_backtest(candles_m5, candles_m15, rsi_period=4, rsi_entry=45, rsi_exit=80,
                                              m15_range_thresh=999, fee_bps=fee_bps)
        
        if result and result_no_filter:
            print(f"\n  Fee {fee_bps}bps:")
            print(f"    WITH filter:    ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR)")
            print(f"    WITHOUT filter: ${result_no_filter['net']:.2f} ({result_no_filter['trades']}t)")
            print(f"    Filter still better: {'✅' if result['net'] > result_no_filter['net'] else '❌'}")
            
            all_results.append({
                "test": "fee_stress",
                "fee_bps": fee_bps,
                "with_filter": result,
                "without_filter": result_no_filter,
            })
    
    # TEST 4: Spread fill simulation
    print(f"\n{'='*80}")
    print(f"  TEST 4: Realistic Fill Prices (Spread Simulation)")
    print(f"{'='*80}")
    
    result_mid = run_full_backtest(candles_m5, candles_m15, rsi_period=4, rsi_entry=45, rsi_exit=80,
                                    m15_range_thresh=10.0, fee_bps=40, use_mid_fill=True)
    result_close = run_full_backtest(candles_m5, candles_m15, rsi_period=4, rsi_entry=45, rsi_exit=80,
                                      m15_range_thresh=10.0, fee_bps=40, use_mid_fill=False)
    
    if result_mid and result_close:
        print(f"\n  Close price fills: ${result_close['net']:.2f} ({result_close['trades']}t)")
        print(f"  Mid-price fills:   ${result_mid['net']:.2f} ({result_mid['trades']}t)")
        print(f"  Spread cost: ${result_close['net'] - result_mid['net']:.2f}")
        print(f"  Edge survives spread: {'✅' if result_mid['net'] > 0 else '❌'}")
        
        all_results.append({
            "test": "spread_fill",
            "close_fills": result_close,
            "mid_fills": result_mid,
            "spread_cost": round(result_close["net"] - result_mid["net"], 2),
        })
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  AUDIT SUMMARY")
    print(f"{'='*80}")
    
    # Check each assumption
    print(f"\n  Assumption Checks:")
    
    # 1. Does the edge survive 14 days?
    full_results = [r for r in all_results if r.get("test") == "full_14day"]
    if full_results:
        best_full = max(full_results, key=lambda r: r["with_filter"]["net"])
        survives_14d = best_full["with_filter"]["net"] > 0
        print(f"  14-day survival: {'✅ EDGE SURVIVES' if survives_14d else '❌ EDGE COLLAPSED'} (${best_full['with_filter']['net']:.2f})")
    
    # 2. Does it survive out-of-sample?
    oos_results = [r for r in all_results if r.get("test") == "out_of_sample"]
    if oos_results:
        oos_positive = all(r["test_net"] > 0 for r in oos_results)
        avg_test = sum(r["test_net"] for r in oos_results) / len(oos_results)
        print(f"  Out-of-sample: {'✅ SURVIVES' if oos_positive else '❌ OVERFIT'} (avg test: ${avg_test:.2f})")
    
    # 3. Does it survive fee stress?
    fee_results = [r for r in all_results if r.get("test") == "fee_stress"]
    if fee_results:
        survives_120 = any(r["with_filter"]["net"] > 0 for r in fee_results if r["fee_bps"] == 120)
        print(f"  Fee stress (120bps): {'✅ SURVIVES' if survives_120 else '❌ FEES KILL IT'}")
    
    # 4. Does it survive spread fills?
    spread_results = [r for r in all_results if r.get("test") == "spread_fill"]
    if spread_results:
        survives_spread = spread_results[0]["mid_fills"]["net"] > 0
        print(f"  Spread fills: {'✅ SURVIVES' if survives_spread else '❌ SPREAD KILLS IT'} (${spread_results[0]['mid_fills']['net']:.2f})")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "date_range_days": round(total_days, 1),
        "first_day": time.strftime('%Y-%m-%d', time.gmtime(first_ts)),
        "last_day": time.strftime('%Y-%m-%d', time.gmtime(last_ts)),
        "results": all_results,
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  Report: {out}")
    
    # Final verdict
    checks = []
    if full_results:
        checks.append(max(full_results, key=lambda r: r["with_filter"]["net"])["with_filter"]["net"] > 0)
    if oos_results:
        checks.append(all(r["test_net"] > 0 for r in oos_results))
    if fee_results:
        checks.append(any(r["with_filter"]["net"] > 0 for r in fee_results if r["fee_bps"] == 120))
    if spread_results:
        checks.append(spread_results[0]["mid_fills"]["net"] > 0)
    
    if all(checks):
        print(f"\n  🏆 VERDICT: THE EDGE IS REAL — survives all assumption checks!")
    elif any(checks):
        print(f"\n  ⚠️  VERDICT: EDGE IS PARTIALLY REAL — {sum(checks)}/{len(checks)} checks passed")
    else:
        print(f"\n  ❌ VERDICT: EDGE COLLAPSED — all assumption checks failed")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
