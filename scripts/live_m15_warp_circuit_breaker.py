#!/usr/bin/env python3
"""
M15 Warp Live High-Frequency Circuit Breaker

Polls MT5 and the lane's state file every 5 seconds.
Enforces the -$3,500 drawdown circuit breaker.
Also guards against the empty `rearm_tokens` restart artifact observed in exc2_tight.
"""
import json
import time
import MetaTrader5 as mt5
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "penetration_lattice_live_btcusd_m15_warp_state.json"
CB_THRESHOLD = -3500.0
POLL_SECONDS = 5

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def main():
    if not mt5.initialize():
        print("MT5 initialize failed")
        return 1

    print("=" * 80)
    print("🛡️ M15 WARP LIVE CIRCUIT BREAKER 🛡️")
    print(f"State path: {STATE_PATH}")
    print(f"Circuit Breaker Threshold: ${CB_THRESHOLD}")
    print(f"Polling Frequency: {POLL_SECONDS}s")
    print("=" * 80)

    alerted_cb = False
    alerted_artifact = False

    try:
        while True:
            time.sleep(POLL_SECONDS)
            if not STATE_PATH.exists():
                continue
                
            try:
                state_text = STATE_PATH.read_text(encoding='utf-8')
                if not state_text.strip():
                    continue
                state = json.loads(state_text)
                btc = state.get("symbols", {}).get("BTCUSD", {})
            except Exception as e:
                print(f"[{utc_now_iso()}] Error reading state: {e}")
                continue

            tick = mt5.symbol_info_tick("BTCUSD")
            if not tick:
                continue

            mid = (tick.bid + tick.ask) / 2
            tickets = btc.get("open_tickets", [])
            
            # Live volume for M15 Warp is 0.01 per ticket
            effective_volume = 0.01 
            
            floating = 0.0
            for t in tickets:
                entry = t.get("entry_fill_price", t.get("entry_price", 0.0))
                if entry == 0:
                    continue
                if t.get("direction") == "BUY":
                    floating += (mid - entry) * effective_volume
                else:
                    floating += (entry - mid) * effective_volume

            realized = btc.get("realized_net_usd", 0.0)
            
            # Artifact Guard: Ensure we have rearm tokens if we have open positions
            # This prevents the exact missed-open issue seen on exc2_tight restarts
            rearm_tokens = btc.get("rearm_tokens", [])
            has_artifact = len(tickets) > 0 and len(rearm_tokens) == 0

            # Console output
            status_line = (
                f"[{utc_now_iso()}] BTC: {mid:.2f} | "
                f"Realized: ${realized:.2f} | Floating: ${floating:.2f} | "
                f"Open: {len(tickets)}"
            )
            
            if has_artifact:
                status_line += " | ⚠️ ARTIFACT DETECTED: 0 REARM TOKENS"
            
            print(status_line)

            # Circuit Breaker Logic
            if floating <= CB_THRESHOLD and not alerted_cb:
                print(f"\n🚨🚨🚨 CIRCUIT BREAKER TRIGGERED 🚨🚨🚨")
                print(f"Floating PnL ${floating:.2f} has breached threshold ${CB_THRESHOLD}!")
                print(f"Immediate manual intervention required on live_btcusd_m15_warp_941781.\n")
                alerted_cb = True
            elif floating > CB_THRESHOLD:
                alerted_cb = False

            # Artifact Logic
            if has_artifact and not alerted_artifact:
                print(f"\n⚠️ STRUCTURAL ARTIFACT DETECTED ⚠️")
                print(f"Lane has {len(tickets)} open positions but 0 rearm tokens.")
                print(f"This causes the 'probable_missed_open' alerts (skipping entries into a flat book).")
                print(f"Requires lane restart or manual token injection.\n")
                alerted_artifact = True
            elif not has_artifact:
                alerted_artifact = False

    except KeyboardInterrupt:
        print("\nCircuit Breaker Monitor stopped by user.")
    finally:
        mt5.shutdown()

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
