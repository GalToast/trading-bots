#!/usr/bin/env python3
"""
Rotation Lattice Shadow Runner
=================================
Real-time shadow runner for the validated rotation lattice strategy.

Config (from sweep + forward-shadow):
- Universe: CFG-USD, RAVE-USD, BAL-USD, SUP-USD (no NOM — trends)
- Entry: 5% relative underperformance threshold
- Exit: 0.2% mean reversion (take profit early)
- Window: 96 bars (8 hours, 5-min candles)
- Max hold: 96 bars
- Position size: $4.80 per pair (half of Kelly per-coin, since pairs share assets)

Runs alongside the Kelly shadow, tracking independent rotation signals.
No live trades — paper trading only.

Usage:
    python scripts/rotation_lattice_shadow.py --dry-run       # Preview
    python scripts/rotation_lattice_shadow.py --total-cash 20 # Run shadow
"""
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from itertools import combinations

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient
from multi_coin_isolated_runner import fetch_candles, append_jsonl

# Configuration
COINS = ["CFG-USD", "RAVE-USD", "BAL-USD", "SUP-USD"]
ENTRY_THRESHOLD = 0.05      # 5% relative underperformance
EXIT_THRESHOLD = 0.002      # 0.2% mean reversion
WINDOW = 96                  # 8 hours of 5-min bars
MAX_HOLD = 96
FEE_RATE = 0.004
SPREAD_ESTIMATE = 0.001
POSITION_SIZE = 4.80         # Per pair

# Paths
STATE_PATH = ROOT / "reports" / "rotation_shadow_state.json"
EVENT_PATH = ROOT / "reports" / "rotation_shadow_events.jsonl"
HEARTBEAT_PATH = ROOT / "reports" / "rotation_shadow_heartbeat.json"

FETCH_LOOKBACK_MINUTES = 520  # 96 bars × 5 min + buffer = ~8.7 hours
CYCLE_SECONDS = 30


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def compute_rolling_returns(candles_a, candles_b, window=WINDOW):
    """Compute relative strength series for a coin pair."""
    ts_a = {int(c["start"]): float(c["close"]) for c in candles_a}
    ts_b = {int(c["start"]): float(c["close"]) for c in candles_b}
    common_ts = sorted(set(ts_a.keys()) & set(ts_b.keys()))
    
    if len(common_ts) < window + 1:
        return []
    
    rel_strength = []
    for i in range(window, len(common_ts)):
        ts_now = common_ts[i]
        ts_then = common_ts[i - window]
        
        ret_a = (ts_a[ts_now] - ts_a[ts_then]) / ts_a[ts_then]
        ret_b = (ts_b[ts_now] - ts_b[ts_then]) / ts_b[ts_then]
        
        rel_strength.append({
            "timestamp": ts_now,
            "rs": ret_a - ret_b,
            "price_a": ts_a[ts_now],
            "price_b": ts_b[ts_then],
        })
    
    return rel_strength


def check_signal(rel_strength_data, pair_name, pair_state):
    """Check if a rotation signal should fire."""
    if not rel_strength_data:
        return None
    
    latest = rel_strength_data[-1]
    rs = latest["rs"]
    
    # No position — check entry
    if pair_state.get("position") is None:
        if rs < -ENTRY_THRESHOLD:
            return {
                "action": "open",
                "pair": pair_name,
                "entry_rs": rs,
                "entry_price_a": latest["price_a"],
                "entry_bar": len(rel_strength_data) - 1,
                "ts_utc": utc_now_iso(),
            }
    
    # Has position — check exit
    else:
        pos = pair_state["position"]
        hold = pos.get("hold", 0) + 1
        pos["hold"] = hold  # FIX: Persist incremented hold so timeout actually fires

        should_exit = False
        exit_reason = "timeout"
        
        if rs > -EXIT_THRESHOLD:
            should_exit = True
            exit_reason = "mean_reversion"
        elif hold >= MAX_HOLD:
            should_exit = True
        elif rs > 0.02:
            should_exit = True
            exit_reason = "overshoot"
        
        if should_exit:
            return {
                "action": "close",
                "pair": pair_name,
                "exit_rs": rs,
                "exit_price_a": latest["price_a"],
                "hold_bars": hold,
                "exit_reason": exit_reason,
                "ts_utc": utc_now_iso(),
            }
    
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rotation Lattice Shadow Runner")
    parser.add_argument("--total-cash", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-cycles", type=int, default=0)
    args = parser.parse_args()
    
    print("=" * 72)
    print("ROTATION LATTICE SHADOW RUNNER")
    print(f"Coins: {', '.join(c.replace('-USD','') for c in COINS)}")
    print(f"Entry: {ENTRY_THRESHOLD*100:.0f}%, Exit: {EXIT_THRESHOLD*100:.1f}%, Window: {WINDOW}, MaxHold: {MAX_HOLD}")
    print(f"Position size: ${POSITION_SIZE}/pair")
    print("=" * 72)
    print()
    
    client = CoinbaseAdvancedClient()
    
    # Load previous state
    state = None
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            print(f"  Loaded previous state (cycle {state.get('cycle', 0)})")
        except Exception:
            pass

    # Self-healing state recovery from append-only event log
    def recover_state_from_events():
        """Read event log and reconstruct per-pair stats — survives state file corruption."""
        if not EVENT_PATH.exists():
            return {}
        
        recovered = {}
        seen_closes = set()  # Deduplicate by (pair, exit_price, timestamp[:19])
        
        for line in EVENT_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            
            if evt.get("action") == "close":
                pair = evt.get("pair", "")
                ts = evt.get("ts_utc", "")[:19]
                exit_price = evt.get("exit_price", 0)
                dedup_key = (pair, exit_price, ts)
                
                if dedup_key in seen_closes:
                    continue
                seen_closes.add(dedup_key)
                
                if pair not in recovered:
                    recovered[pair] = {"closes": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
                
                pnl = evt.get("pnl", 0.0)
                recovered[pair]["closes"] += 1
                recovered[pair]["total_pnl"] += pnl
                if pnl > 0:
                    recovered[pair]["wins"] += 1
                else:
                    recovered[pair]["losses"] += 1
        
        return recovered

    print("  Checking event log for state recovery...")
    event_recovery = recover_state_from_events()
    if event_recovery:
        total_recovered_pnl = sum(r["total_pnl"] for r in event_recovery.values())
        total_recovered_closes = sum(r["closes"] for r in event_recovery.values())
        print(f"  ✅ Recovered {total_recovered_closes} closes, ${total_recovered_pnl:+.2f} PnL from event log")

    # Initialize pair states
    pairs = list(combinations(COINS, 2))
    pair_states = {}
    cycle = 0
    total_pnl = 0.0
    total_opens = 0
    total_closes = 0

    if state:
        pair_states = state.get("pairs", {})
        cycle = state.get("cycle", 0)
        total_pnl = state.get("total_pnl", 0.0)
        total_opens = state.get("total_opens", 0)
        total_closes = state.get("total_closes", 0)

    # Merge recovered stats (only if event log has more data than state)
    for pair_name, recovered in event_recovery.items():
        if pair_name not in pair_states:
            pair_states[pair_name] = {
                "pair": pair_name,
                "position": None,
                "signals": 0,
                "closes": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
            }
        
        ps = pair_states[pair_name]
        if recovered["closes"] > ps.get("closes", 0):
            ps["closes"] = recovered["closes"]
            ps["wins"] = recovered["wins"]
            ps["losses"] = recovered["losses"]
            ps["total_pnl"] = recovered["total_pnl"]
            print(f"  Recovered {pair_name}: {recovered['closes']} closes, ${recovered['total_pnl']:+.2f}")
    
    for coin_a, coin_b in pairs:
        pair_name = f"{coin_a.replace('-USD', '')}/{coin_b.replace('-USD', '')}"
        if pair_name not in pair_states:
            pair_states[pair_name] = {
                "pair": pair_name,
                "position": None,
                "signals": 0,
                "closes": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
            }
    
    mode = "DRY RUN" if args.dry_run else "LIVE SHADOW"
    print(f"  Mode: {mode}")
    print(f"  Pairs: {len(pairs)}")
    print(f"  Cycle: {cycle}")
    print()
    
    if args.dry_run:
        print("  Dry run complete. Use --total-cash to start shadow.")
        return
    
    print(f"  LIVE SHADOW STARTED: ${args.total_cash:.2f} total budget")
    print(f"  State: {STATE_PATH}")
    print(f"  Events: {EVENT_PATH}")
    print()
    
    append_jsonl(EVENT_PATH, {
        "action": "runner_start",
        "coins": COINS,
        "mode": "shadow",
        "total_cash": args.total_cash,
        "per_pair_cash": POSITION_SIZE,
        "ts_utc": utc_now_iso(),
    })
    
    try:
        while True:
            cycle += 1
            
            if args.max_cycles > 0 and cycle > args.max_cycles:
                print(f"\nMax cycles ({args.max_cycles}) reached. Stopping.")
                break
            
            try:
                now = int(time.time())
                start = now - FETCH_LOOKBACK_MINUTES * 60
                
                # Fetch candles for all coins
                all_candles = {}
                for coin in COINS:
                    candles = fetch_candles(client, coin, start, now, "FIVE_MINUTE")
                    all_candles[coin] = candles
                
                cycle_pnl = 0.0
                cycle_events = []
                
                # Check each pair
                for coin_a, coin_b in pairs:
                    pair_name = f"{coin_a.replace('-USD', '')}/{coin_b.replace('-USD', '')}"
                    
                    rs_data = compute_rolling_returns(all_candles[coin_a], all_candles[coin_b])
                    if not rs_data:
                        continue
                    
                    signal = check_signal(rs_data, pair_name, pair_states[pair_name])
                    if not signal:
                        continue
                    
                    ps = pair_states[pair_name]
                    
                    if signal["action"] == "open":
                        ps["position"] = {
                            "entry_rs": signal["entry_rs"],
                            "entry_price_a": signal["entry_price_a"],
                            "entry_bar": signal["entry_bar"],
                            "hold": 0,
                        }
                        ps["signals"] += 1
                        total_opens += 1
                        
                        cycle_events.append(signal)
                        print(f"  OPEN {pair_name}: RS={signal['entry_rs']:.3f}, price_a={signal['entry_price_a']:.6f}")
                    
                    elif signal["action"] == "close":
                        pos = ps["position"]
                        entry_price = pos["entry_price_a"]
                        exit_price = signal["exit_price_a"]
                        
                        raw_return = (exit_price - entry_price) / entry_price
                        net_return = raw_return - 2 * FEE_RATE - SPREAD_ESTIMATE
                        pnl = POSITION_SIZE * net_return
                        
                        ps["closes"] += 1
                        ps["total_pnl"] += pnl
                        total_closes += 1
                        cycle_pnl += pnl
                        
                        if pnl > 0:
                            ps["wins"] += 1
                        else:
                            ps["losses"] += 1
                        
                        ps["position"] = None
                        
                        evt = {
                            **signal,
                            "entry_price": entry_price,
                            "exit_price": exit_price,
                            "net_return_pct": round(net_return * 100, 3),
                            "pnl": round(pnl, 2),
                        }
                        cycle_events.append(evt)
                        append_jsonl(EVENT_PATH, evt)
                        print(f"  CLOSE {pair_name}: PnL=${pnl:.2f} ({net_return*100:+.2f}%), reason={signal['exit_reason']}")
                
                total_pnl += cycle_pnl

                # Increment hold counter for all active positions (bugfix: was stuck at 0)
                for pair_name, ps in pair_states.items():
                    if ps["position"] is not None:
                        ps["position"]["hold"] = ps["position"].get("hold", 0) + 1

                # Save state
                state_data = {
                    "updated_at": utc_now_iso(),
                    "cycle": cycle,
                    "total_pnl": round(total_pnl, 2),
                    "total_opens": total_opens,
                    "total_closes": total_closes,
                    "pairs": pair_states,
                }
                
                STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = STATE_PATH.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(state_data, indent=2, sort_keys=True), encoding="utf-8")
                tmp_path.replace(STATE_PATH)
                
                # Heartbeat
                hb = {
                    "updated_at": utc_now_iso(),
                    "cycle": cycle,
                    "total_pnl": round(total_pnl, 2),
                    "pid": __import__("os").getpid(),
                }
                HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
                HEARTBEAT_PATH.write_text(json.dumps(hb), encoding="utf-8")
                
                # Status line
                active = sum(1 for ps in pair_states.values() if ps["position"] is not None)
                print(f"HB#{cycle}: pnl=${total_pnl:+.2f} closes={total_closes} active={active}/{len(pairs)}", flush=True)
                
            except Exception as e:
                print(f"EXC in cycle {cycle}: {e}", flush=True)
                traceback.print_exc()
            
            time.sleep(CYCLE_SECONDS)
    
    except KeyboardInterrupt:
        print(f"\nShutting down after {cycle} cycles...", flush=True)
        append_jsonl(EVENT_PATH, {
            "action": "runner_stop",
            "total_pnl": round(total_pnl, 2),
            "total_opens": total_opens,
            "total_closes": total_closes,
            "cycle": cycle,
            "ts_utc": utc_now_iso(),
        })


if __name__ == "__main__":
    main()
