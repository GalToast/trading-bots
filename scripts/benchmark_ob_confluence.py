#!/usr/bin/env python3
"""
Order Book Confluence Backtest Engine
======================================
Testing whether bid/ask imbalance from Coinbase best_bid_ask API
can filter out losing RSI entries and boost win rate.

Strategy: RSI(4)<45 entry + RSI>80 exit + Order Book Imbalance filter

Hypothesis: When RSI is oversold AND bids dominate asks, entries are safer
because the order book confirms buy pressure is present.

Note: Historical OB data isn't available, so we'll:
1. Pull LIVE OB data every 30s to build a real-time dataset
2. Use bid-ask spread width as a PROXY for historical imbalance
   (wider spread = thinner book = higher flush risk)
3. Test the confluence on live forward data
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
OB_LOG_PATH = ROOT / "reports" / "ob_imbalance_log.jsonl"
DEFAULT_REPORT_PATH = ROOT / "reports" / "ob_confluence_backtest.json"

PRODUCT = "RAVE-USD"
STARTING_CASH = 48.0
FEE_BPS = 5.0
BASELINE_RSI_EXIT_80 = 422.64


def compute_spread_proxy(candles: list[dict]) -> list[float]:
    """
    Use bid-ask spread as a proxy for book thickness.
    We'll estimate spread from candle volatility:
    - High vol candles = thinner book = wider effective spread
    - Low vol candles = thicker book = tighter spread
    
    This is a PROXY — real OB data requires live sampling.
    """
    spreads = []
    for c in candles:
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        # Intra-candle range as spread proxy
        spread_pct = (h - l) / cl * 100 if cl > 0 else 0
        spreads.append(spread_pct)
    return spreads


def run_rsi_with_spread_filter(candles, rsi_period, rsi_entry, rsi_exit, 
                                spread_threshold, max_hold=24, deploy_pct=0.95):
    """
    RSI entry + spread filter + RSI exit.
    
    spread_threshold: Only enter if spread proxy < X% (thick book, low volatility)
    This filters out entries during thin-book/high-volatility conditions.
    """
    if len(candles) < rsi_period + 10:
        return None
    
    closes = [float(c["close"]) for c in candles]
    fee_rate = FEE_BPS / 10000.0
    rsi_vals = compute_rsi(closes, rsi_period)
    spreads = compute_spread_proxy(candles)
    
    cash = STARTING_CASH
    in_position = False
    position = None
    trades = []
    
    for i in range(rsi_period + 10, len(candles) - 1):
        c = candles[i]
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        current_rsi = rsi_vals[i]
        current_spread = spreads[i]
        
        # EXIT
        if in_position and position:
            exit_price = None
            exit_reason = None
            
            if current_rsi >= rsi_exit:
                exit_price = cl
                exit_reason = "rsi_exit"
            
            if exit_price is None and (i - position["bar"]) >= max_hold:
                exit_price = cl
                exit_reason = "timeout"
            
            if exit_price is not None:
                qty = position["qty"]
                gross = (exit_price - position["entry"]) * qty
                exit_fee = exit_price * qty * fee_rate
                net = gross - position["entry_fee"] - exit_fee
                
                cash += position["entry"] * qty + net
                trades.append({"net": net, "reason": exit_reason, "win": net > 0, "entry_spread": position.get("entry_spread", 0)})
                in_position = False
                position = None
                continue
        
        # ENTRY with spread filter
        if not in_position and cash >= 10.0:
            if current_rsi < rsi_entry and current_spread < spread_threshold:
                deploy = cash * deploy_pct
                entry_fee = cl * (deploy / cl) * fee_rate
                qty = (deploy - entry_fee) / cl
                
                if qty > 0:
                    cash -= deploy
                    position = {
                        "entry": cl, "qty": qty, "bar": i, 
                        "entry_fee": entry_fee, "entry_spread": current_spread
                    }
                    in_position = True
    
    if not trades:
        return None
    
    wins = [t for t in trades if t["win"]]
    net = sum(t["net"] for t in trades)
    avg_entry_spread = sum(t.get("entry_spread", 0) for t in trades) / len(trades)
    
    return {
        "net": round(net, 2),
        "trades": len(trades),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "avg": round(net / len(trades), 4),
        "avg_entry_spread": round(avg_entry_spread, 3),
    }


def run_live_ob_sample(client, product_id):
    """Pull one OB snapshot from live best_bid_ask."""
    try:
        resp = client.best_bid_ask([product_id])
        products = resp.get("products", [])
        if products:
            p = products[0]
            bids = p.get("bids", [])
            asks = p.get("asks", [])
            
            if bids and asks:
                best_bid = float(bids[0]["price"])
                best_ask = float(asks[0]["price"])
                bid_size = float(bids[0].get("size", 0))
                ask_size = float(asks[0].get("size", 0))
                
                spread = best_ask - best_bid
                spread_pct = spread / best_bid * 100 if best_bid > 0 else 0
                imbalance = bid_size / ask_size if ask_size > 0 else float('inf')
                
                return {
                    "ts": time.time(),
                    "bid": best_bid,
                    "ask": best_ask,
                    "bid_size": bid_size,
                    "ask_size": ask_size,
                    "spread": round(spread, 6),
                    "spread_pct": round(spread_pct, 4),
                    "imbalance": round(imbalance, 2) if imbalance != float('inf') else 9999,
                }
    except Exception as e:
        return {"error": str(e), "ts": time.time()}
    return None


def main():
    client = CoinbaseAdvancedClient()
    
    # Phase 1: Live OB Sampling (2 minutes, 4 samples)
    print(f"📡 Phase 1: Live Order Book Sampling for {PRODUCT}")
    print(f"  Collecting 4 snapshots over 2 minutes...\n")
    
    ob_samples = []
    for i in range(4):
        sample = run_live_ob_sample(client, PRODUCT)
        if sample:
            ob_samples.append(sample)
            if "imbalance" in sample:
                print(f"  Sample {i+1}: Bid={sample['bid']:.4f} Ask={sample['ask']:.4f} "
                      f"BidSize={sample['bid_size']:.1f} AskSize={sample['ask_size']:.1f} "
                      f"Imbalance={sample['imbalance']:.1f}x Spread={sample['spread_pct']:.3f}%")
            else:
                print(f"  Sample {i+1}: Error — {sample.get('error', 'unknown')}")
        time.sleep(30)
    
    # Save OB samples
    if ob_samples:
        with open(OB_LOG_PATH, "a", encoding="utf-8") as f:
            for s in ob_samples:
                f.write(json.dumps(s) + "\n")
        print(f"\n  Saved {len(ob_samples)} samples to {OB_LOG_PATH}")
    
    # Phase 2: Spread Proxy Backtest
    print(f"\n{'='*80}")
    print(f"  PHASE 2: Spread Proxy Backtest (72h Historical)")
    print(f"{'='*80}\n")
    
    print(f"  Fetching 72h M5 candles for {PRODUCT}...")
    candles = fetch_candles_72h(client, PRODUCT, "FIVE_MINUTE")
    print(f"  Got {len(candles)} candles\n")
    
    results = []
    
    # Baseline: RSI(4)<45 + RSI>80 exit (no spread filter)
    print("BASELINE: RSI(4)<45 + RSI>80 exit (no spread filter)")
    result = run_rsi_with_spread_filter(candles, rsi_period=4, rsi_entry=45, rsi_exit=80, spread_threshold=999)
    if result:
        result["name"] = "RSI4_45_exit80_NO_FILTER"
        results.append(result)
        print(f"  ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t, avg_spread={result['avg_entry_spread']:.3f}%")
    
    # Spread filter sweep: Only enter during LOW volatility (thick book)
    spread_thresholds = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    
    print("\nSpread Threshold Sweep (only enter when spread proxy < X%):")
    for thresh in spread_thresholds:
        result = run_rsi_with_spread_filter(candles, rsi_period=4, rsi_entry=45, rsi_exit=80, spread_threshold=thresh)
        if result:
            result["name"] = f"RSI4_45_exit80_spread<{thresh}"
            results.append(result)
            print(f"  spread<{thresh}%: ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # Spread filter on RSI(4)<30 + RSI>80 (the $422 baseline)
    print("\nSpread Filter on RSI(4)<30 + RSI>80 (the $422 baseline):")
    for thresh in [1.0, 2.0, 3.0, 5.0]:
        result = run_rsi_with_spread_filter(candles, rsi_period=4, rsi_entry=30, rsi_exit=80, spread_threshold=thresh)
        if result:
            result["name"] = f"RSI4_30_exit80_spread<{thresh}"
            results.append(result)
            print(f"  spread<{thresh}%: ${result['net']:.2f}, {result['trades']}t, {result['wr']}%WR, ${result['avg']:.4f}/t")
    
    # Phase 3: Live OB Confluence Runner (launch background process)
    print(f"\n{'='*80}")
    print(f"  PHASE 3: Live OB Confluence Summary")
    print(f"{'='*80}\n")
    
    # Sort and print
    results.sort(key=lambda r: r["net"], reverse=True)
    
    print(f"{'Strategy':<45} {'Net $':>8} {'Trades':>7} {'Win%':>6} {'Avg/Tr':>9} {'AvgSpread':>9}")
    print(f"{'='*95}")
    for r in results:
        print(f"{r['name']:<45} ${r['net']:>6.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['avg']:>7.4f} {r['avg_entry_spread']:>6.3f}%")
    
    # Write report
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ob_samples": ob_samples,
        "backtest_results": results,
        "baseline": BASELINE_RSI_EXIT_80,
    }
    
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {out}")
    
    # Summary
    if ob_samples:
        avg_imbalance = sum(s.get("imbalance", 0) for s in ob_samples if "imbalance" in s) / len([s for s in ob_samples if "imbalance" in s])
        print(f"\n📊 Live OB Imbalance: {avg_imbalance:.1f}x (bid/ask size ratio)")
        print(f"   If > 1.0: Bids dominate = buy pressure confirmed")
        print(f"   If < 1.0: Asks dominate = flush risk elevated")
    
    if results:
        best = results[0]
        print(f"\n🏆 Best spread-filtered config: {best['name']}")
        print(f"   ${best['net']:.2f} vs baseline ${BASELINE_RSI_EXIT_80:.2f}")
        if best["net"] > BASELINE_RSI_EXIT_80:
            print(f"   ✅ SPREAD FILTER BEATS BASELINE (+${best['net']-BASELINE_RSI_EXIT_80:.2f})")
        else:
            print(f"   ❌ Spread filter didn't beat baseline")
            print(f"   The spread proxy may need live OB data to be effective")
    
    print(f"\n💡 Next step: Build live OB confluence runner that samples")
    print(f"   best_bid_ask every 30s and gates entries on imbalance > X")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
