#!/usr/bin/env python3
"""
Coinbase Universe Scanner — Phase 3
=====================================
Scan the NEXT 100 untested coins (beyond the top 80 by volume).

Total USD pairs: ~388
Phase 1: 49 coins tested (known coins)
Phase 2: 80 coins tested (top 80 untested by volume)
Phase 3: Next 100 untested coins

Standardized test: RSI(4)<30 + 25% TP + No SL, 7 days, $48
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "coinbase_universe_scan_phase3.json"

# All coins tested in Phase 1 + Phase 2
ALREADY_TESTED = {
    # Phase 1 (49 coins)
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
    # Phase 2 (80 coins)
    "PEPE-USD", "MOG-USD", "BONK-USD", "SHIB-USD", "BNKR-USD",
    "FLOKI-USD", "TOSHI-USD", "NOM-USD", "PUMP-USD", "NOICE-USD",
    "DOGINME-USD", "PENGU-USD", "SPELL-USD", "TRU-USD", "NKN-USD",
    "ACS-USD", "XAN-USD", "TURBO-USD", "FIGHT-USD", "B3-USD",
    "IDEX-USD", "DOGE-USD", "MDT-USD", "AMP-USD", "GIGA-USD",
    "XCN-USD", "KEYCAT-USD", "TROLL-USD", "RLS-USD", "DEGEN-USD",
    "RSR-USD", "FLR-USD", "GST-USD", "TRIA-USD", "BLAST-USD",
    "ROSE-USD", "VET-USD", "FAI-USD", "VARA-USD", "VTHO-USD",
    "IMU-USD", "JASMY-USD", "XRP-USD", "HBAR-USD", "A8-USD",
    "BOBBOB-USD", "ADA-USD", "ZK-USD", "ANKR-USD", "TOWNS-USD",
    "WLFI-USD", "LINEA-USD", "SWELL-USD", "SUP-USD", "GWEI-USD",
    "USELESS-USD", "SPK-USD", "REZ-USD", "TNSR-USD", "ATH-USD",
    "KAT-USD", "ROBO-USD", "W-USD", "ACH-USD", "PIRATE-USD",
    "QI-USD", "DRIFT-USD", "ALT-USD", "L3-USD", "SENT-USD",
    "GHST-USD", "USDT-USD", "HONEY-USD", "ENA-USD", "PLUME-USD",
    "CELR-USD", "CORECHAIN-USD", "RECALL-USD", "ONDO-USD", "XYO-USD",
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
    
    net = cash - starting_cash
    wins = [t for t in trades if t["win"]]
    
    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "trades": len(trades),
        "wr": round(len(wins) / max(1, len(trades)) * 100, 1),
        "avg_trade": round(net / max(1, len(trades)), 4),
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
    print("  COINBASE UNIVERSE SCANNER — Phase 3 (next 100 coins)")
    print("=" * 80)
    
    # Fetch all products
    print("\nFetching all Coinbase products...")
    products_resp = client.list_products(get_all_products=True, product_type="SPOT")
    products = products_resp.get("products", [])
    
    # Filter to USD pairs not already tested
    print(f"\nFiltering to untested USD pairs...")
    untested_coins = []
    for p in products:
        pid = p.get("product_id", "")
        if pid.endswith("-USD") and p.get("status") == "online":
            if pid not in ALREADY_TESTED:
                untested_coins.append({
                    "id": pid,
                    "volume_24h": float(p.get("volume_24h", 0) or 0),
                    "base": p.get("base_currency_id", ""),
                })
    
    # Sort by volume (highest first)
    untested_coins.sort(key=lambda x: x["volume_24h"], reverse=True)
    
    print(f"  Untested USD pairs: {len(untested_coins)}")
    print(f"\n  Top 20 untested by volume:")
    for c in untested_coins[:20]:
        print(f"    {c['id']}: 24h vol={c['volume_24h']:.0f}")
    
    # Test next 100 coins
    coins_to_test = [c["id"] for c in untested_coins[:100]]
    print(f"\n  Testing {len(coins_to_test)} coins...")
    
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
        "untested_total": len(untested_coins),
        "coins_tested_this_phase": scanned,
        "skipped": skipped,
        "errors": errors,
        "profitable": [r for r in all_results if r["net"] > 0],
        "all_results": all_results,
    }
    
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    
    # Print summary
    print(f"\n{'='*80}")
    print(f"  PHASE 3 RESULTS — {scanned} new coins tested")
    print(f"{'='*80}")
    
    profitable = [r for r in all_results if r["net"] > 0]
    losers = [r for r in all_results if r["net"] <= 0]
    
    print(f"\n  Coins tested: {scanned}")
    print(f"  Skipped (no data): {skipped}")
    print(f"  Errors: {errors}")
    print(f"  Profitable: {len(profitable)}/{len(all_results)}")
    print(f"  Losing: {len(losers)}/{len(all_results)}")
    
    if profitable:
        print(f"\n  {'Coin':<20} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6}")
        print(f"  {'-'*50}")
        for r in profitable:
            print(f"  {r['coin']:<20} ${r['net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>7} {r['wr']:>5.1f}%")
        
        total_profit = sum(r["net"] for r in profitable)
        print(f"\n  Total profit from winners: ${total_profit:.2f}")
    
    if losers:
        total_loss = sum(r["net"] for r in losers)
        print(f"  Total loss from losers: ${total_loss:.2f}")
    
    # Combine with all previous phases
    phase1_profitable = [
        {"coin": "RAVE-USD", "net": 123.21, "return_pct": 256.7, "trades": 28, "wr": 71.4},
        {"coin": "MOG-USD", "net": 56.48, "return_pct": 117.7, "trades": 17, "wr": 70.6},
        {"coin": "A8-USD", "net": 18.89, "return_pct": 39.3, "trades": 21, "wr": 61.9},
        {"coin": "IDEX-USD", "net": 16.08, "return_pct": 33.5, "trades": 15, "wr": 60.0},
        {"coin": "BAL-USD", "net": 8.74, "return_pct": 18.2, "trades": 25, "wr": 56.0},
        {"coin": "DRIFT-USD", "net": 6.38, "return_pct": 13.3, "trades": 32, "wr": 37.5},
        {"coin": "ALEPH-USD", "net": 6.31, "return_pct": 13.1, "trades": 17, "wr": 47.1},
        {"coin": "IOTX-USD", "net": 4.25, "return_pct": 8.9, "trades": 15, "wr": 46.7},
        {"coin": "BLUR-USD", "net": 3.58, "return_pct": 7.5, "trades": 20, "wr": 35.0},
        {"coin": "SKL-USD", "net": 2.81, "return_pct": 5.9, "trades": 10, "wr": 50.0},
        {"coin": "DOGINME-USD", "net": 1.53, "return_pct": 3.2, "trades": 13, "wr": 38.5},
        {"coin": "ALT-USD", "net": 1.03, "return_pct": 2.2, "trades": 28, "wr": 39.3},
        {"coin": "DEGEN-USD", "net": 0.97, "return_pct": 2.0, "trades": 12, "wr": 50.0},
        {"coin": "IRYS-USD", "net": 0.77, "return_pct": 1.6, "trades": 48, "wr": 50.0},
        {"coin": "VTHO-USD", "net": 0.38, "return_pct": 0.8, "trades": 12, "wr": 50.0},
        {"coin": "ACS-USD", "net": 0.12, "return_pct": 0.2, "trades": 4, "wr": 50.0},
    ]
    
    all_profitable = phase1_profitable + [
        {"coin": r["coin"], "net": r["net"], "return_pct": r["return_pct"], 
         "trades": r["trades"], "wr": r["wr"]}
        for r in profitable
    ]
    all_profitable.sort(key=lambda x: x["net"], reverse=True)
    
    print(f"\n{'='*80}")
    print(f"  COMBINED RESULTS — All {49 + 80 + scanned} coins tested")
    print(f"{'='*80}")
    print(f"\n  Total profitable coins: {len(all_profitable)}")
    print(f"  Total losing coins: {36 + 71 + len(losers)}")
    print(f"\n  ALL PROFITABLE COINS:")
    print(f"  {'Coin':<20} {'Net $':>8} {'Ret%':>7} {'Trades':>7} {'WR%':>6}")
    print(f"  {'-'*50}")
    for r in all_profitable[:30]:
        print(f"  {r['coin']:<20} ${r['net']:>6.2f} {r['return_pct']:>6.1f}% {r['trades']:>7} {r['wr']:>5.1f}%")
    
    print(f"\n  Report saved to: {REPORT_PATH}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
