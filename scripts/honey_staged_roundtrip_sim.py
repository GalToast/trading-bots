#!/usr/bin/env python3
"""
HONEY-USD Staged Roundtrip Simulation (Antigravity Edition).
Directly addresses codex's 'funding-grade gate' by simulating a BUY-entry -> SELL-exit cycle.

Logic:
1. Detect a trigger (spread >= 150, depths >= 15, move >= 20).
2. 'Place' a Maker BUY order at BID + 0.10 offset.
3. Wait up to 120s for the market ASK to hit our BUY price (fill-proxy).
4. Once filled, 'Place' a Maker SELL order at ASK - 0.10 offset.
5. Wait up to 120s for the market BID to hit our SELL price (exit-fill-proxy).
6. Calculate realized net profit/loss including fees (25bps maker).

Usage:
    python scripts/honey_staged_roundtrip_sim.py --max-cycles 3
"""
import sys, json, time, argparse; sys.path.insert(0, 'scripts')
from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import compute_spread_bps
from datetime import datetime, timezone

def utc_now(): return datetime.now(timezone.utc).isoformat()

def run_sim(max_cycles=3, trigger_spread=150, trigger_depth=15, trigger_move=20):
    c = KrakenSpotClient()
    print(f"--- HONEY-USD Staged Roundtrip Sim starting (max_cycles={max_cycles}) ---")
    
    results = []
    
    for cycle in range(1, max_cycles + 1):
        print(f"\n[Cycle {cycle}/{max_cycles}] Searching for trigger...")
        entry_filled = False
        exit_filled = False
        
        # Step 1: Detect Trigger
        prev_ask = None
        prev_bid = None
        
        search_start = time.time()
        while time.time() - search_start < 300: # 5 min timeout per cycle
            try:
                tk = c.ticker(['HONEYUSD'])
                t = tk.get('HONEYUSD', {})
                bid = to_float((t.get('b') or [None])[0])
                ask = to_float((t.get('a') or [None])[0])
                
                d = c.depth('HONEYUSD', count=5)
                book = d.get('HONEYUSD', d.get('HONEY/USD', {}))
                bids = book.get('bids', [])
                asks = book.get('asks', [])
                bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
                ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0
                
                sp = compute_spread_bps(bid, ask) if bid > 0 and ask > 0 else 0
                
                ask_down = max(0, (prev_ask - ask) / prev_ask * 10000) if prev_ask else 0
                bid_up = max(0, (bid - prev_bid) / prev_bid * 10000) if prev_bid else 0
                
                if sp >= trigger_spread and bid_d >= trigger_depth and ask_d >= trigger_depth:
                    if ask_down >= trigger_move or bid_up >= trigger_move:
                        print(f"  🔥 TRIGGER DETECTED: spread={sp:.0f}bps ask_down={ask_down:.1f}bps bid_up={bid_up:.1f}bps")
                        
                        # Step 2: Place simulated BUY entry
                        entry_price = bid + 0.10
                        print(f"  🛒 SIM ENTRY: Maker BUY at {entry_price:.5f} (bid was {bid:.5f})")
                        
                        entry_wait_start = time.time()
                        while time.time() - entry_wait_start < 120: # 120s TTL for entry
                            time.sleep(1)
                            tk_now = c.ticker(['HONEYUSD'])
                            t_now = tk_now.get('HONEYUSD', {})
                            current_ask = to_float((t_now.get('a') or [None])[0])
                            current_bid = to_float((t_now.get('b') or [None])[0])
                            
                            # Fill if ASK hits our BUY price (someone sold to us)
                            if current_ask <= entry_price:
                                print(f"  ✅ ENTRY FILLED: Ask {current_ask:.5f} hit Buy {entry_price:.5f} in {time.time()-entry_wait_start:.1f}s")
                                entry_filled = True
                                break
                            
                        if not entry_filled:
                            print(f"  ❌ ENTRY TIMEOUT: Buying at {entry_price:.5f} but Ask stayed at {current_ask:.5f}")
                            break # Go to next cycle
                            
                        # Step 3: Place simulated SELL exit
                        exit_price = current_ask - 0.10 # Exit at the ask we were filled at (or current)
                        # Ensure exit price is above entry + fee (25bps * 2 = 50bps)
                        min_exit = entry_price * 1.0050
                        if exit_price < min_exit:
                             print(f"  ⚠️ EXIT PRICE {exit_price:.5f} BELOW FEE FLOOR {min_exit:.5f}. Adjusting to floor.")
                             exit_price = min_exit
                             
                        print(f"  🏷️ SIM EXIT: Maker SELL at {exit_price:.5f} (target net floor active)")
                        
                        exit_wait_start = time.time()
                        while time.time() - exit_wait_start < 120: # 120s TTL for exit
                            time.sleep(1)
                            tk_now = c.ticker(['HONEYUSD'])
                            t_now = tk_now.get('HONEYUSD', {})
                            current_bid = to_float((t_now.get('b') or [None])[0])
                            
                            # Fill if BID hits our SELL price (someone bought from us)
                            if current_bid >= exit_price:
                                print(f"  ✅ EXIT FILLED: Bid {current_bid:.5f} hit Sell {exit_price:.5f} in {time.time()-exit_wait_start:.1f}s")
                                exit_filled = True
                                break
                        
                        if not exit_filled:
                            print(f"  ❌ EXIT TIMEOUT: Selling at {exit_price:.5f} but Bid stayed at {current_bid:.5f}")
                        
                        # Record result
                        res = {
                            'cycle': cycle,
                            'entry_price': entry_price,
                            'exit_price': exit_price if exit_filled else None,
                            'entry_time': round(time.time() - entry_wait_start, 1) if entry_filled else None,
                            'exit_time': round(time.time() - exit_wait_start, 1) if exit_filled else None,
                            'success': exit_filled,
                            'net_pct': round(((exit_price / entry_price) - 1.0) * 10000 - 50, 2) if exit_filled else None
                        }
                        results.append(res)
                        break # Finished this cycle's trade attempt
                        
                prev_ask = ask
                prev_bid = bid
                time.sleep(1)
            except Exception as e:
                print(f"  Error in sim loop: {e}")
                time.sleep(1)
                
    # Summary
    print("\n--- SIMULATION SUMMARY ---")
    for r in results:
        status = "✅ SUCCESS" if r['success'] else "❌ FAILED"
        net = f"{r['net_pct']}bps" if r['net_pct'] is not None else "N/A"
        print(f"Cycle {r['cycle']}: {status} | Net: {net} | Entry: {r['entry_time']}s | Exit: {r['exit_time']}s")
        
    out_path = 'reports/cache/honey_staged_roundtrip_sim_results.json'
    with open(out_path, 'w') as f:
        json.dump({'ts': utc_now(), 'results': results}, f, indent=2)
    print(f"\n✅ Results saved to {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-cycles', type=int, default=3)
    args = parser.parse_args()
    run_sim(max_cycles=args.max_cycles)
