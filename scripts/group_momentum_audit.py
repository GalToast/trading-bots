import json
import time
import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# Group by similarity (e.g. Meme, Infrastructure, DePin)
MEMES = ["MOG-USD", "DOGINME-USD", "FARTCOIN-USD"]
INFRA = ["ALEPH-USD", "BAL-USD", "SKL-USD"]
PRODUCTS = MEMES + INFRA

def fetch_candles(client, pid, start, end):
    chunk_sec = 300 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="ONE_MINUTE")
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands: break
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600 # 72 hours

    print("Fetching 72h M1 data for Group-Momentum Audit...")
    group_returns = {}
    for pid in PRODUCTS:
        print(f"  {pid}...", end="\r")
        cands = fetch_candles(client, pid, start, now)
        if not cands: continue
        rets = []
        for i in range(1, len(cands)):
            rets.append((float(cands[i]["close"]) - float(cands[i-1]["close"])) / float(cands[i-1]["close"]))
        group_returns[pid] = rets

    # Align
    min_len = min(len(r) for r in group_returns.values())
    for pid in group_returns:
        group_returns[pid] = group_returns[pid][-min_len:]

    print("\n\n--- GROUP CONFLUENCE (Lag 1) ---")
    
    for name, group in [("MEMES", MEMES), ("INFRA", INFRA)]:
        print(f"\nAnalyzing Group: {name}")
        
        # Calculate Group Average Momentum (Excluding each member to avoid self-correlation)
        for target in group:
            if target not in group_returns: continue
            
            others = [group_returns[p] for p in group if p != target and p in group_returns]
            if not others: continue
            
            avg_others_lead = []
            for k in range(min_len - 1):
                avg_val = sum(o[k] for o in others) / len(others)
                avg_others_lead.append(avg_val)
            
            target_lag = group_returns[target][1:]
            
            # Pearson
            mean1 = sum(avg_others_lead)/len(avg_others_lead); mean2 = sum(target_lag)/len(target_lag)
            num = sum((avg_others_lead[k]-mean1)*(target_lag[k]-mean2) for k in range(len(avg_others_lead)))
            den = math.sqrt(sum((avg_others_lead[k]-mean1)**2 for k in range(len(avg_others_lead))) * sum((target_lag[k]-mean2)**2 for k in range(len(avg_others_lead))))
            corr = num/den if den > 0 else 0
            
            print(f"  Group Leaders -> {target:12s} | Corr={corr:.2f}")

if __name__ == "__main__":
    main()
