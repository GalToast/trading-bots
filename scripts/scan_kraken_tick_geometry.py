#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from kraken_spot_client import KrakenSpotClient, to_float

def main():
    client = KrakenSpotClient()
    pairs = client.asset_pairs()
    
    # We need current prices to calculate % tick size
    rest_pairs = list(pairs.keys())
    # Chunk ticker requests
    tickers = {}
    chunk_size = 50
    for i in range(0, len(rest_pairs), chunk_size):
        chunk = rest_pairs[i:i+chunk_size]
        tickers.update(client.ticker(chunk))
        
    candidates = []
    for rest_pair, meta in pairs.items():
        if meta.get("status") != "online":
            continue
        if not any(q in rest_pair for q in ["USD", "USDT", "USDC"]):
            continue
            
        tick_size = to_float(meta.get("tick_size"))
        if tick_size <= 0:
            continue
            
        ticker = tickers.get(rest_pair)
        if not ticker:
            continue
            
        price = to_float(ticker.get("c", [0])[0])
        if price <= 0:
            continue
            
        tick_bps = (tick_size / price) * 10000
        
        # 24h volume
        vol_today = to_float(ticker.get("v", [0, 0])[0])
        vwp_today = to_float(ticker.get("p", [0, 0])[0])
        vol_usd = vol_today * vwp_today
        
        if vol_usd < 10000: # Min liquidity
            continue
            
        candidates.append({
            "product": meta.get("wsname"),
            "price": price,
            "tick_size": tick_size,
            "tick_bps": tick_bps,
            "vol_24h_usd": vol_usd
        })
        
    candidates.sort(key=lambda x: x["tick_bps"], reverse=True)
    
    print(f"{'Product':<15} | {'Price':<12} | {'Tick Bps':<10} | {'Vol 24h USD':<12}")
    print("-" * 60)
    for c in candidates[:30]:
        print(f"{c['product']:<15} | {c['price']:<12.8f} | {c['tick_bps']:<10.2f} | ${c['vol_24h_usd']:<12.0f}")

if __name__ == "__main__":
    main()
