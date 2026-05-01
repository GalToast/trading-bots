#!/usr/bin/env python3
"""
Quick multi-coin RSI(3) MR backfill scan.
Tests the RAVE V2 strategy (RSI(3) < 30 → buy, 25% TP, no SL, 48-bar hold)
across multiple Coinbase coins using last 72h of 5-min candles.
"""
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from rave_rsi_mr_live_v2 import RaveRsiMrLive, fetch_candles_chunked, EVENT_PATH

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "reports" / "_multicoin_rsi3_scan_results.json"

# Coins to test - mix of existing shadow targets + popular alts
COINS = [
    "RAVE-USD", "SOL-USD", "DOGE-USD", "MOG-USD", "FARTCOIN-USD",
    "VVV-USD", "COMP-USD", "ETH-USD", "XRP-USD", "AVAX-USD",
    "NEAR-USD", "LINK-USD", "ARB-USD", "SUI-USD", "PEPE-USD",
]

def scan_coin(client, product_id, hours=72):
    """Run RSI(3) MR backtest on a single coin."""
    engine = RaveRsiMrLive(starting_cash=48.0)
    now = int(time.time())
    start = now - hours * 3600
    
    try:
        candles = fetch_candles_chunked(client, product_id, start, now)
    except Exception as e:
        return {"coin": product_id, "error": str(e), "candles": 0}
    
    if len(candles) < 20:
        return {"coin": product_id, "error": "insufficient_candles", "candles": len(candles)}
    
    # Process without event logging for speed
    import tempfile
    event_path = Path(tempfile.mkdtemp()) / "events.jsonl"
    
    for c in candles:
        engine.process_candle(c, 0.0, event_path, phase="backfill")
    
    snap = engine.snapshot()
    return {
        "coin": product_id,
        "candles": len(candles),
        "closes": snap["closes"],
        "wins": snap["wins"],
        "losses": snap["losses"],
        "win_rate": snap["win_rate"],
        "realized_pnl": snap["realized_net"],
        "total_pnl": snap["total_pnl"],
        "rsi_signals": snap["rsi_signals"],
        "total_fees": snap["total_fees"],
    }

def main():
    client = CoinbaseAdvancedClient()
    results = []
    
    print(f"Scanning {len(COINS)} coins with RSI(3) MR strategy (72h backfill)...")
    print(f"{'Coin':>15}  {'Candles':>7}  {'Trades':>6}  {'WR':>5}  {'PnL':>10}  {'Signals':>7}")
    print("-" * 65)
    
    for coin in COINS:
        result = scan_coin(client, coin)
        results.append(result)
        
        if "error" in result:
            print(f"{coin:>15}  ERROR: {result['error']}")
        else:
            print(f"{coin:>15}  {result['candles']:>7}  {result['closes']:>6}  "
                  f"{result['win_rate']:>4.0f}%  ${result['realized_pnl']:>8.2f}  "
                  f"{result['rsi_signals']:>7}")
        
        time.sleep(0.5)  # Rate limit courtesy
    
    # Sort by realized PnL
    valid = [r for r in results if "error" not in r]
    valid.sort(key=lambda x: x["realized_pnl"], reverse=True)
    
    print("\n" + "=" * 65)
    print("RANKED BY REALIZED PNL:")
    print(f"{'Coin':>15}  {'Trades':>6}  {'WR':>5}  {'PnL':>10}")
    print("-" * 40)
    for r in valid:
        flag = "🟢" if r["realized_pnl"] > 0 else "🔴"
        print(f"{flag} {r['coin']:>13}  {r['closes']:>6}  "
              f"{r['win_rate']:>4.0f}%  ${r['realized_pnl']:>8.2f}")
    
    # Save
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nResults saved to {RESULTS_PATH}")

if __name__ == "__main__":
    main()
