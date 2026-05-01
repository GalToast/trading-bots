#!/usr/bin/env python3
"""Titan 10.8: Multi-Mode Playbook Bot (Adversarial L2 Edition).

Reads Kraken Live Radar and a sidecar L2 Wall Tape to dynamically
classify assets into Playbooks and adjust execution aggression.

Playbooks:
- Imbalance Scalper: Maker entry if L1_OBI > 0.85. 
- Momentum Sniper: Taker entry if ask_down > 20bps.
- Institutional Sweep: Taker entry if L10_Depth drops 50% (Institution swept).

Execution Aggression:
- Confidence 0.5 (Maker): Used when OBI is strong but depth is thin.
- Confidence 1.0 (Taker): Used when L10_OBI > 0.85 (Institutional Wall).
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class MultiModePlaybookBot:
    def __init__(self, radar_path: Path, depth_path: Path, log_path: Path):
        self.radar_path = radar_path
        self.depth_path = depth_path
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.open_positions: Dict[str, Dict[str, Any]] = {}
        self.price_history: Dict[str, List[float]] = {}
        self.last_l2_data: Dict[str, Dict[str, Any]] = {}
        self.l2_history: Dict[str, List[Dict[str, Any]]] = {}

    def log_event(self, action: str, pid: str, playbook: str, details: Dict[str, Any]):
        evt = {
            "ts_utc": utc_now_iso(),
            "action": action,
            "product_id": pid,
            "playbook": playbook,
            **details
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, sort_keys=True) + "\n")
        print(f"[{utc_now_iso()}] {action.upper()} | {pid} | {playbook} | {details}")

    def refresh_l2_context(self):
        """Reads the sidecar L2 tape to get structural context."""
        if not self.depth_path.exists(): return
        try:
            # Get last line
            with open(self.depth_path, "rb") as f:
                f.seek(-2048, 2)
                last_line = f.readlines()[-1].decode("utf-8")
                data = json.loads(last_line)
                products_l2 = data.get("products", {})
                self.last_l2_data = products_l2
                
                # Keep a short history for Institutional Sweep detection
                for pid, l2_data in products_l2.items():
                    if pid not in self.l2_history:
                        self.l2_history[pid] = []
                    self.l2_history[pid].append(l2_data)
                    if len(self.l2_history[pid]) > 10:
                        self.l2_history[pid].pop(0)
        except: pass

    def evaluate_triggers(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pid = row["product_id"]
        pid_clean = pid.replace("-", "").replace("/", "")
        
        bid = float(row.get("bid", 0.0))
        ask = float(row.get("ask", 0.0))
        if bid <= 0 or ask <= 0: return None
        
        # Validated Admission Gate: MER >= 2.5 and Spread >= 50bps
        spread_bps = ((ask - bid) / bid) * 10000.0
        # Kraken Maker fee tier 0 is 16bps, round trip = 32bps
        maker_edge_ratio = spread_bps / 32.0
        
        if spread_bps < 50.0 or maker_edge_ratio < 2.5:
            return None

        
        # 1. Base L1 OBI
        bid_depth = float(row.get("bid_depth_usd", 0.0))
        ask_depth = float(row.get("ask_depth_usd", 0.0))
        total_depth = bid_depth + ask_depth
        l1_obi = bid_depth / total_depth if total_depth > 0 else 0.5
        
        # 2. L2 Context (Confidence Scalar)
        l2 = self.last_l2_data.get(pid_clean, {})
        l10_obi = l2.get("l10_obi", 0.5)
        l10_ask_usd = l2.get("l10_ask_usd", 0.0)
        l10_bid_usd = l2.get("l10_bid_usd", 0.0)
        
        # Confidence Score: 0.0 to 1.0
        # High L10 OBI = High Confidence
        confidence = 0.5 + (l10_obi - 0.5) if l10_obi > 0.5 else 0.5
        
        # Playbook Selection
        
        # Institutional Sweep: Check if L10 depth dropped > 50% recently
        l2_hist = self.l2_history.get(pid_clean, [])
        if len(l2_hist) >= 5:
            # We look back 5 snapshots (~10 seconds)
            past_l2 = l2_hist[-5]
            past_ask_usd = past_l2.get("l10_ask_usd", 0.0)
            if past_ask_usd > 0 and l10_ask_usd < (past_ask_usd * 0.5):
                # Ask wall got demolished, sweep!
                return {"playbook": "Institutional_Sweep", "confidence": 1.0}
            past_bid_usd = past_l2.get("l10_bid_usd", 0.0)
            if past_bid_usd > 0 and l10_bid_usd < (past_bid_usd * 0.5):
                # Bid wall got demolished (dump), counter-trade?
                # Vulture reclaim concept: hit the bid
                pass
                
        if l1_obi > 0.90:
            return {"playbook": "Imbalance_Scalper", "confidence": confidence}
            
        ask_down = float(row.get("ask_down_bps", 0.0))
        if ask_down > 25.0:
            return {"playbook": "Momentum_Sniper", "confidence": 1.0}
            
        return None

    def execute_entry(self, pid: str, playbook: str, confidence: float, row: Dict[str, Any]):
        if pid in self.open_positions: return
            
        bid = float(row["bid"])
        ask = float(row["ask"])
        
        # Adversarial Execution Choice
        if confidence >= 0.7:
            # High Confidence -> Taker Entry (Hit the ask)
            entry_price = ask
            fee_bps = 60.0
            mode = "TAKER"
        else:
            # Low Confidence -> Maker Entry (Sit on bid)
            entry_price = bid
            fee_bps = 16.0
            mode = "MAKER"
            
        self.open_positions[pid] = {
            "entry_price": entry_price,
            "playbook": playbook,
            "entry_fee_bps": fee_bps,
            "mode": mode,
            "ticks_held": 0
        }
        
        self.log_event("shadow_entry", pid, playbook, {
            "mode": mode,
            "price": entry_price,
            "confidence": round(confidence, 2)
        })

    def execute_exit(self, pid: str, row: Dict[str, Any]):
        if pid not in self.open_positions: return
            
        pos = self.open_positions[pid]
        pos["ticks_held"] += 1
        
        bid = float(row["bid"])
        ask = float(row["ask"])
        
        # Target: +40bps net
        target_pnl_bps = 40.0
        
        # Try Maker Exit at ASK
        exit_price = ask
        exit_fee_bps = 16.0
        
        gross_bps = (exit_price - pos["entry_price"]) / pos["entry_price"] * 10000.0
        net_bps = gross_bps - pos["entry_fee_bps"] - exit_fee_bps
        
        if net_bps >= target_pnl_bps or pos["ticks_held"] >= 30:
            reason = "take_profit" if net_bps >= target_pnl_bps else "ttl_timeout"
            
            # Force Taker close on timeout
            if reason == "ttl_timeout":
                exit_price = bid
                exit_fee_bps = 60.0
                gross_bps = (exit_price - pos["entry_price"]) / pos["entry_price"] * 10000.0
                net_bps = gross_bps - pos["entry_fee_bps"] - exit_fee_bps

            self.log_event("shadow_exit", pid, pos["playbook"], {
                "exit_price": exit_price,
                "net_bps": round(net_bps, 2),
                "reason": reason,
                "ticks_held": pos["ticks_held"]
            })
            del self.open_positions[pid]

    def run_loop(self, poll_seconds: float):
        print(f"--- TITAN 10.8 MULTI-MODE PLAYBOOK BOT ACTIVE (L2 AWARE) ---")
        
        while True:
            if not self.radar_path.exists():
                time.sleep(poll_seconds)
                continue
                
            self.refresh_l2_context()
            
            try:
                data = json.loads(self.radar_path.read_text(encoding="utf-8"))
                rows = data.get("rows", [])
                
                # Minimum liquidity filter
                valid_rows = [r for r in rows if float(r.get("volume_24h_base", 0)) * float(r.get("bid", 0)) > 20000]
                
                for row in valid_rows:
                    pid = row["product_id"]
                    self.execute_exit(pid, row)
                    
                    decision = self.evaluate_triggers(row)
                    if decision:
                        self.execute_entry(pid, decision["playbook"], decision["confidence"], row)
                        
            except Exception as e:
                print(f"Error: {e}")
                
            time.sleep(poll_seconds)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--radar-path", type=Path, default=ROOT / "reports" / "kraken_spot_live_radar.json")
    parser.add_argument("--depth-path", type=Path, default=ROOT / "reports" / "cache" / "kraken_l2_wall_tape.jsonl")
    parser.add_argument("--log-path", type=Path, default=ROOT / "reports" / "multimode_playbook_events.jsonl")
    args = parser.parse_args()
    
    bot = MultiModePlaybookBot(args.radar_path, args.depth_path, args.log_path)
    bot.run_loop(2.0)

if __name__ == "__main__":
    main()
