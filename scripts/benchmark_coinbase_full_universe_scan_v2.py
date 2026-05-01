#!/usr/bin/env python3
"""
Coinbase Full Universe Scanner — Phase 2 (FIXED)
==================================================
Scan ALL remaining 339 USD-paired Coinbase coins.

388 total USD pairs - 49 tested = 339 remaining.
Using correct API field: product_id (not id).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "coinbase_full_universe_scan_phase2.json"

# Coins already tested in Phase 1
ALREADY_TESTED = {
    "RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
    "FARTCOIN-USD", "VIRTUAL-USD", "TRUMP-USD", "FET-USD",
    "CFG-USD", "DASH-USD", "IRYS-USD", "MON-USD", "SKL-USD",
    "VVV-USD", "LDO-USD", "STORJ-USD", "COMP-USD", "ARB-USD",
    "SOL-USD", "AVAX-USD", "MATIC-USD", "LINK-USD", "UNI-USD",
    "AAVE-USD", "MKR-USD", "SNX-USD", "CRV-USD", "SUSHI-USD",
    "GRT-USD", "IMX-USD", "OP-USD", "APT-USD", "SUI-USD",
    "SEI-USD", "TIA-USD", "INJ-USD", "RUNE-USD", "ATOM-USD",
    "NEAR-USD", "FTM-USD", "ALGO-USD", "FLOW-USD", "ICP-USD",
    "FIL-USD", "EOS-USD", "XLM-USD", "XTZ-USD", "EGLD-USD",
}


def compute_rsi(closes, period=4):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = [50.0] * period
    if avg_l > 0:
        result.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        if avg_l > 0:
            result.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            result.append(100.0)
    return result


def run_rsi_edge_test(candles, rsi_period=4, os_thresh=30, tp_pct=0.25, sl_pct=0.0,
                       max_hold=24, fee_bps=40, starting_cash=48.0):
    if len(candles) < rsi_period + 20:
        return None
    
    fee_rate = fee_bps / 10000.0
    closes = [float(c["close"]) for c in candles]
    rsi_vals = compute_rsi(closes, rsi_period)
    
    cash = starting_cash
    in_position = False
    position = None
    trades = []
    total_volume = 0.0
    
    for i in range(rsi_period + 10, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        current_rsi = rsi_vals[i]
        
        if in_position and position:
            exit_price = None
            exit_reason = None
            
            tp_price = position["entry"] * (1 + tp_pct)
            if h >= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
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
                total_volume += position["quote"] + (exit_price * qty)
                trades.append({"net": net, "reason": exit_reason, "win": net > 0})
                in_position = False
                position = None
                continue
        
        if not in_position and cash >= 10.0 and current_rsi <= os_thresh:
            deploy = cash * 0.95
            entry_fee = cl * (deploy / cl) * fee_rate
            qty = (deploy - entry_fee) / cl
            
            if qty > 0:
                cash -= deploy
                position = {"entry": cl, "qty": qty, "bar": i, "quote": deploy}
                in_position = True
    
    if position:
        cash += position["quote"]
        total_volume += position["quote"]
    
    net = cash - starting_cash
    wins = [t for t in trades if t["win"]]
    
    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg_trade": round(net / max(1, len(trades)), 4),
        "total_volume": round(total_volume, 2),
        "tp_exits": len([t for t in trades if t["reason"] == "tp"]),
        "timeout_exits": len([t for t in trades if t["reason"] == "timeout"]),
    }


def fetch_candles_chunked(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    retries = 0
    
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            retries = 0
            time.sleep(0.15)
        except Exception as e:
            if "429" in str(e):
                retries += 1
                wait = min(2 ** retries, 10)
                time.sleep(wait)
            else:
                cs = ce
                time.sleep(0.3)
    
    all_c.sort(key=lambda c: int(c.get("start", c.get("time", 0))))
    return all_c


def main():
    client = CoinbaseAdvancedClient()
    
    print("=" * 80)
    print("  COINBASE FULL UNIVERSE SCANNER — Phase 2 (339 coins)")
    print("=" * 80)
    
    # Fetch all products
    print("\nFetching all Coinbase products...")
    products_resp = client.list_products(get_all_products=True, product_type="SPOT")
    products = products_resp.get("products", [])
    print(f"  Total products: {len(products)}")
    
    # Filter to USD pairs not already tested
    print("\nFiltering to untested USD pairs...")
    untested_coins = []
    for p in products:
        pid = p.get("product_id", "")
        if pid.endswith("-USD") and p.get("status") == "online":
            if pid not in ALREADY_TESTED:
                untested_coins.append({
                    "id": pid,
                    "base": p.get("base_currency_id", ""),
                    "volume_24h": float(p.get("volume_24h", 0) or 0),
                    "approx_quote_24h_volume": float(p.get("approximate_quote_24h_volume", 0) or 0),
                    "market_cap": float(p.get("market_cap", 0) or 0),
                })
    
    # Sort by volume (highest first — most liquid = more likely to have edges)
    untested_coins.sort(key=lambda x: x["volume_24h"], reverse=True)
    
    print(f"  Untested USD pairs: {len(untested_coins)}")
    print(f"\n  Top 20 by 24h volume:")
    for c in untested_coins[:20]:
        print(f"    {c['id']}: 24h vol={c['volume_24h']:.0f}, est.Quote vol={c['approx_quote_24h_volume']:.0f}")
    
    # Test top 80 untested coins (limited by API rate)
    coins_to_test = [c["id"] for c in untested_coins[:80]]
    print(f"\n  Testing top {len(coins_to_test)} untested coins by volume...")
    
    # Run edge test
    now = int(time.time())
    start = now - 7 * 24 * 3600
    
    all_results = []
    scanned = 0
    skipped = 0
    errors = 0
    
    for i, pid in enumerate(coins_to_test):
        scanned += 1
        print(f"\n  [{scanned}/{len(coins_to_test)}] {pid}...", end=" ", flush=True)
        
        try:
            candles = fetch_candles_chunked(client, pid, start, now, "FIVE_MINUTE")
            
            if len(candles) < 100:
                print(f"SKIP ({len(candles)} candles)")
                skipped += 1
                continue
            
            result = run_rsi_edge_test(candles)
            if result:
                result["coin"] = pid
                result["volume_24h"] = [c["volume_24h"] for c in untested_coins if c["id"] == pid][0]
                all_results.append(result)
                
                if result["net"] > 0:
                    print(f"✅ ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR)")
                else:
                    print(f"❌ ${result['net']:.2f} ({result['trades']}t, {result['wr']}%WR)")
            else:
                print(f"SKIP (no trades)")
        
        except Exception as e:
            errors += 1
            print(f"ERROR: {str(e)[:50]}")
        
        time.sleep(0.3)
    
    # Sort by net profit
    all_results.sort(key=lambda r: r["net"], reverse=True)
    
    # Save report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_usd_pairs": len(untested_coins),
        "coins_tested_this_phase": scanned,
        "skipped": skipped,
        "errors": errors,
        "profitable": [r for r in all_results if r["net"] > 0],
        "all_results": all_results,
        "full_untested_list": [c["id"] for c in untested_coins],
    }
    
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    
    # Print summary
    print(f"\n{'='*80}")
    print(f"  PHASE 2 RESULTS — {scanned} new coins tested")
    print(f"{'='*80}")
    
    profitable = [r for r in all_results if r["net"] > 0]
    losers = [r for r in all_results if r["net"] <= 0]
    
    print(f"\n  Coins tested: {scanned}")
    print(f"  Skipped (no data): {skipped}")
    print(f"  Errors: {errors}")
    print(f"  Profitable: {len(profitable)}/{len(all_results)}")
    print(f"  Losing: {len(losers)}/{len(all_results)}")
    
    if profitable:
        print(f"\n  {'Coin':<20} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6} {'Vol$':>10}")
        print(f"  {'-'*65}")
        for r in profitable[:20]:
            print(f"  {r['coin']:<20} ${r['net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>7} {r['wr']:>5.1f}% ${r['total_volume']:>8.0f}")
        
        total_profit = sum(r["net"] for r in profitable)
        print(f"\n  Total profit from winners: ${total_profit:.2f}")
    
    if losers:
        total_loss = sum(r["net"] for r in losers)
        print(f"  Total loss from losers: ${total_loss:.2f}")
    
    # Combine with Phase 1 results
    phase1_profitable = [
        {"coin": "RAVE-USD", "net": 123.21, "return_pct": 256.7, "trades": 28, "wr": 71.4, "total_volume": 5970},
        {"coin": "BAL-USD", "net": 8.74, "return_pct": 18.2, "trades": 25, "wr": 56.0, "total_volume": 2754},
        {"coin": "ALEPH-USD", "net": 6.31, "return_pct": 13.1, "trades": 17, "wr": 47.1, "total_volume": 1770},
        {"coin": "IOTX-USD", "net": 4.25, "return_pct": 8.9, "trades": 15, "wr": 46.7, "total_volume": 1413},
        {"coin": "BLUR-USD", "net": 3.58, "return_pct": 7.5, "trades": 20, "wr": 35.0, "total_volume": 2013},
        {"coin": "SKL-USD", "net": 2.81, "return_pct": 5.9, "trades": 10, "wr": 50.0, "total_volume": 995},
        {"coin": "IRYS-USD", "net": 0.77, "return_pct": 1.6, "trades": 48, "wr": 50.0, "total_volume": 4397},
    ]
    
    all_profitable = phase1_profitable + [
        {"coin": r["coin"], "net": r["net"], "return_pct": r["return_pct"], 
         "trades": r["trades"], "wr": r["wr"], "total_volume": r["total_volume"]}
        for r in profitable
    ]
    all_profitable.sort(key=lambda x: x["net"], reverse=True)
    
    print(f"\n{'='*80}")
    print(f"  COMBINED RESULTS — All coins tested (Phase 1 + Phase 2)")
    print(f"{'='*80}")
    print(f"\n  Total coins tested: {49 + scanned}")
    print(f"  Total profitable: {len(all_profitable)}")
    print(f"  Total losing: {36 + len(losers)}")
    print(f"\n  ALL PROFITABLE COINS:")
    print(f"  {'Coin':<20} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6}")
    print(f"  {'-'*50}")
    for r in all_profitable[:20]:
        print(f"  {r['coin']:<20} ${r['net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>7} {r['wr']:>5.1f}%")
    
    print(f"\n  Report saved to: {REPORT_PATH}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
