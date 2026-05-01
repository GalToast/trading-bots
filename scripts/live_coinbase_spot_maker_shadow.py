#!/usr/bin/env python3
"""
Coinbase Spot Maker Shadow Runner — Thin Wrapper

Uses the same maker geometry as Kraken but with:
- Coinbase API (coinbase_advanced_client.py)
- Coinbase fees: 60bps maker, 120bps taker
- Coinbase products: SPX-USD, FLOCK-USD, ZAMA-USD

This is a SHADOW runner — no real orders. Tracks what WOULD happen
if we deployed maker orders on Coinbase with the proven Kraken geometry.

Usage:
  python scripts/live_coinbase_spot_maker_shadow.py \
    --state-path reports/coinbase_maker_shadow_state.json \
    --events-path reports/coinbase_maker_shadow_events.jsonl \
    --max-quote-usd 8.0 \
    --products SPX-USD FLOCK-USD ZAMA-USD
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_client import CoinbaseAdvancedClient
from toxicity_filter import ToxicityFilter
from death_spiral_prevention import LossTracker
from profit_mode_classifier import classify_profit_mode

REPORTS = ROOT / "reports"
DEFAULT_STATE = REPORTS / "coinbase_maker_shadow_state.json"
DEFAULT_EVENTS = REPORTS / "coinbase_maker_shadow_events.jsonl"
MAKER_OPPORTUNITY_PATH = REPORTS / "kraken_maker_opportunity_board.json"
BEAR_VELOCITY_PATH = REPORTS / "kraken_spot_bear_velocity_board.json"
SHADOW_LOG_PATH = REPORTS / "neural_harpoon_shadow_log.jsonl"
LOSS_TRACKER_STATE_PATH = REPORTS / "coinbase_maker_loss_tracker_state.json"

# Coinbase fees
MAKER_FEE_BPS = 60.0
TAKER_FEE_BPS = 120.0
ROUND_TRIP_BPS = MAKER_FEE_BPS + TAKER_FEE_BPS

# Products with positive edge (from reality board)
DEFAULT_PRODUCTS = ["KAT-USD", "APE-USD", "ENS-USD", "KSM-USD", "FARTCOIN-USD", "SPX-USD", "FLOCK-USD", "ZAMA-USD"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v or default)
    except (TypeError, ValueError):
        return default


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def append_jsonl(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def fetch_ticks(client: CoinbaseAdvancedClient, product_ids: list[str]) -> dict[str, dict]:
    """Fetch best bid/ask for products via best_bid_ask endpoint."""
    try:
        result = client.best_bid_ask(product_ids)
        pricebooks = result.get("pricebooks", [])
        ticks = {}
        now = int(time.time())
        for book in pricebooks:
            pid = book.get("product_id", "")
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if bids and asks:
                bid = to_float(bids[0].get("price"))
                ask = to_float(asks[0].get("price"))
                if bid > 0 and ask > 0:
                    ticks[pid] = {"bid": bid, "ask": ask, "ts": now}
        return ticks
    except Exception as e:
        print(f"[WARN] Fetch ticks failed: {e}")
        return {}


def fetch_l2_data(client: CoinbaseAdvancedClient, pid: str) -> dict:
    """Fetch L2 pricebook and return imbalance + best bid depth."""
    try:
        # PATH FIX: Must use params for correct JWT signing in _request
        resp = client._request("GET", "/api/v3/brokerage/product_book", params={"product_id": pid, "limit": 20}, signed=True)
        book = resp.get("pricebook", {})
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return {"imb": 0.0, "bid_depth_usd": 0.0}
        
        best_bid_size = float(bids[0]["size"])
        best_bid_price = float(bids[0]["price"])
        bid_depth_usd = best_bid_size * best_bid_price
        
        cum_bid = sum(float(b["size"]) for b in bids)
        cum_ask = sum(float(a["size"]) for a in asks)
        if (cum_bid + cum_ask) == 0:
            return {"imb": 0.0, "bid_depth_usd": bid_depth_usd}
        
        imb = (cum_bid - cum_ask) / (cum_bid + cum_ask)
        return {"imb": imb, "bid_depth_usd": bid_depth_usd}
    except Exception:
        return {"imb": 0.0, "bid_depth_usd": 0.0}


def compute_spread_bps(bid: float, ask: float) -> float:
    if bid <= 0 or ask <= 0 or ask <= bid:
        return 0.0
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 10000


class ShadowState:
    """Tracks shadow lane state — what WOULD happen with real orders."""
    
    def __init__(self, products: list[str], max_quote_usd: float):
        self.products = products
        self.max_quote_usd = max_quote_usd
        self.cash = 100.0
        self.initial_cash = 100.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.net_pct = 0.0
        self.gross_pct = 0.0
        self.ghost_marks = 0
        self.total_net = 0.0
        self.total_gross = 0.0
        self.positions: list[dict] = []
        self.product_stats: dict[str, dict] = {}
        self.reentry_blocks: dict[str, int] = {}
        self.created_at = utc_now_iso()
        self.updated_at = utc_now_iso()
        self.poll_count = 0
        self.tick_count = 0
        
        # For each product, track entry/exit simulation
        for pid in products:
            self.product_stats[pid] = {
                "attempts": 0,
                "entries": 0,
                "exits": 0,
                "wins": 0,
                "losses": 0,
                "avg_entry_spread_bps": 0.0,
                "avg_exit_spread_bps": 0.0,
            }
    
    def to_dict(self) -> dict:
        return {
            "lane": "coinbase_maker_shadow",
            "products": self.products,
            "max_quote_usd": self.max_quote_usd,
            "fee_config": {
                "maker_fee_bps": MAKER_FEE_BPS,
                "taker_fee_bps": TAKER_FEE_BPS,
                "round_trip_bps": ROUND_TRIP_BPS,
            },
            "cash": round(self.cash, 6),
            "initial_cash": self.initial_cash,
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "net_pct": round(self.net_pct, 4),
            "gross_pct": round(self.gross_pct, 4),
            "ghost_marks": self.ghost_marks,
            "total_net": round(self.total_net, 6),
            "total_gross": round(self.total_gross, 6),
            "open_positions": len(self.positions),
            "positions": self.positions,
            "poll_count": self.poll_count,
            "tick_count": self.tick_count,
            "product_stats": self.product_stats,
            "reentry_blocks": self.reentry_blocks,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ShadowEngine:
    """Simulates maker orders on Coinbase with the Kraken-proven geometry."""
    
    def __init__(self, state: ShadowState, min_notionals: dict[str, float] | None = None):
        self.state = state
        
        # Maker geometry (same as Kraken shadow)
        self.min_spread_bps = 50.0  # Min spread to enter
        self.min_rent_harvest_pct = 0.10  # Min profit to harvest
        self.max_loss_pct = 3.0  # Max loss before emergency stop
        self.no_mfe_stop_pct = 0.35 # Adverse stop if no MFE seen
        self.no_mfe_stop_min_age_seconds = 90.0
        self.trail_giveback_pct = 5.0  # Trail stop giveback
        self.green_insurance_activation_pct = 0.0  # Disabled by default
        self.green_insurance_giveback_pct = 0.05
        self.reentry_cooldown_polls = 60
        self.min_quote_usd = 5.0  # Live-executability guard (Min Notional)
        self.high_yield_gate_bps = 250.0 # Bypasses the fee-ceiling
        self.min_notionals: dict[str, float] = {
            str(product_id).upper(): max(0.0, float(min_notional))
            for product_id, min_notional in (min_notionals or {}).items()
        }
        self.kraken_lead_state_path: Path | None = None
        self.kraken_active_products: set[str] = set()
        
        self.bear_veto_products: set[str] = set()
        self.maker_opportunities: dict[str, dict] = {}
        self.toxicity = ToxicityFilter(SHADOW_LOG_PATH)
        self.tracker = LossTracker(
            max_consecutive_losses=2,
            cooldown_seconds=3600,
            state_path=LOSS_TRACKER_STATE_PATH
        )
        
        self.entry_count = 0
        self.last_mode = "balanced_harvest"
    
    def update_profit_mode(self, pid: str, spread_bps: float) -> str:
        """Log what the profit-mode classifier would choose."""
        # For Coinbase Maker, we assume a standard step_ratio and round-trip for simulation
        res = classify_profit_mode(
            spread_to_step_ratio=spread_bps / 100.0, # Approximate
            spread_to_range_ratio=0.5, # Baseline
            directional_bias=0.0,
            regime="mixed"
        )
        self.last_mode = res.profit_mode
        return res.profit_mode

    def refresh_kraken_lead_state(self):
        if not self.kraken_lead_state_path or not self.kraken_lead_state_path.exists():
            return
        try:
            payload = json.loads(self.kraken_lead_state_path.read_text(encoding="utf-8"))
            state = payload.get("state", {})
            active = state.get("active_positions", {})
            self.kraken_active_products = set(active.keys())
        except Exception as e:
            print(f"[WARN] Failed to read Kraken lead state: {e}")

    def calculate_nep(self, pid: str, spread_bps: float, depth_usd: float) -> dict:
        """Calculate Net Expected Profit (NEP) for both venues."""
        # 1. KRAKEN NEP (25bps Maker, 40bps Taker Insurance)
        k_spread = spread_bps # Assume similar for now
        k_fee_rt = 50.0 # BPS
        k_dds_factor = 0.90 # Slippage safety
        k_nep = (k_spread - k_fee_rt) * k_dds_factor
        
        # 2. COINBASE NEP (60bps Maker, 120bps Taker Insurance)
        c_fee_rt = 180.0 # BPS
        c_dds_factor = 0.95 # Assume slightly better fill prob?
        c_nep = (spread_bps - c_fee_rt) * c_dds_factor
        
        return {"k_nep": k_nep, "c_nep": c_nep}

    def try_enter(self, pid: str, bid: float, ask: float, spread_bps: float, event_path: Path, imbalance: float = 0.0, bid_depth_usd: float = 0.0) -> dict | None:
        """Simulate a maker entry attempt."""
        # 1. MODE MONITORING
        mode = self.update_profit_mode(pid, spread_bps)
        
        # 2. DYNAMIC FEE-AWARE ROUTING & DEEP-ALPHA LEAD (Horizon 5.1)
        nep_data = self.calculate_nep(pid, spread_bps, bid_depth_usd)
        if nep_data["c_nep"] <= 0:
            # Coinbase 180bps hurdle is too high for this spread.
            return None
            
        # DEEP-ALPHA LEAD: Coinbase can lead if depth is massive and pulse is strong.
        # Otherwise, it must wait for the Kraken Foundry Lead.
        pulse_board = load_json(ROOT / "reports" / "coinbase_spot_pulse_board.json")
        pulse_data = {r["product_id"]: r["pulse_score"] for r in pulse_board.get("products", [])}
        pulse_score = pulse_data.get(pid, 0.0)
        
        deep_alpha_lead = bid_depth_usd > 20000.0 and pulse_score > 5.0
        
        if nep_data["k_nep"] > nep_data["c_nep"] and not deep_alpha_lead:
            # Kraken is mathematically superior and this isn't a deep-alpha lead anomaly.
            # We skip Coinbase UNLESS Kraken lead logic authorizes a secondary capture.
            if not self.kraken_active_products or pid not in self.kraken_active_products:
                return None
        
        # 3. HARDENINGS
        if spread_bps < self.min_spread_bps:
            return None
            
        # High-Yield Alpha Gate (Breaks the Fee Ceiling)
        if spread_bps < self.high_yield_gate_bps:
            return None
        
        # L2 Imbalance Veto (Breaks the Ghosting Ceiling)
        if imbalance > 0.60:
            if self.state.poll_count % 60 == 0:
                print(f"  VETO (L2 GHOSTING RISK): {pid} imb={imbalance:+.2f}")
            return None
        if imbalance < -0.60:
            if self.state.poll_count % 60 == 0:
                print(f"  VETO (L2 TOXICITY RISK): {pid} imb={imbalance:+.2f}")
            return None

        if self.toxicity.is_toxic(pid):
            print(f"  VETO (TOXICITY): {pid}")
            return None

        if self.tracker.is_blocked(pid):
            print(f"  VETO (DEATH SPIRAL): {pid}")
            return None

        if pid in self.bear_veto_products:
            print(f"  VETO (BEAR VELOCITY): {pid}")
            return None
            
        if self.state.reentry_blocks.get(pid, 0) > 0:
            return None

        # 2. POSITION SIZING / LIVE-EXECUTABILITY GATE
        # DYNAMIC DEPTH SIZING (DDS): We target 15% of the top-of-book depth to minimize competition.
        min_notional = self.min_notionals.get(pid.upper(), self.min_quote_usd)
        dds_size_usd = bid_depth_usd * 0.15 if bid_depth_usd > 0 else min_notional
        
        quote_usd = min(self.state.max_quote_usd, self.state.cash * 0.12)  # 12% deploy
        quote_usd = min(quote_usd, dds_size_usd) # Apply DDS ceiling
        
        if quote_usd < min_notional:
            if self.state.poll_count % 12 == 0:
                 print(f"  VETO (MIN NOTIONAL/DDS): {pid} ${quote_usd:.2f} < ${min_notional:.2f}")
            append_jsonl(event_path, {
                "ts_utc": utc_now_iso(),
                "action": "coinbase_entry_veto",
                "product_id": pid,
                "reason": "quote_below_min_notional",
                "quote_usd": round(quote_usd, 6),
                "bid_depth_usd": round(bid_depth_usd, 6),
                "min_notional_usd": round(min_notional, 6),
                "max_quote_usd": round(self.state.max_quote_usd, 6),
                "spread_bps": round(spread_bps, 4),
            })
            return None  # Too small (Min Notional Guard)

        # 3. PROBABILISTIC FILL MODELING (PROVEN KRAKEN GEOMETRY)
        mer = to_float(self.maker_opportunities.get(pid, {}).get("mer"), 1.0)
        fill_prob = 0.5 + (min(mer, 2.0) / 2.0) * 0.4
        fill_prob = min(0.95, fill_prob)
        
        fill_roll = random.random()
        if fill_roll > fill_prob:
            append_jsonl(event_path, {
                "ts_utc": utc_now_iso(),
                "action": "maker_entry_miss",
                "product_id": pid,
                "bid": bid,
                "ask": ask,
                "mer": round(mer, 6),
                "fill_prob": round(fill_prob, 6),
                "fill_roll": round(fill_roll, 6),
                "spread_bps": round(spread_bps, 4),
            })
            return None

        # 4. EXECUTION TYPE (POST-ONLY EVOLUTION)
        # In a real live run, we would pass 'post_only=True' to the API.
        # In shadow, we simulate a 10% 'Post-Only Reject' rate where our order 
        # is cancelled because it would have crossed the spread.
        post_only_reject_roll = random.random()
        if post_only_reject_roll < 0.10:
            append_jsonl(event_path, {
                "ts_utc": utc_now_iso(),
                "action": "post_only_reject_shadow",
                "product_id": pid,
                "reason": "maker_order_would_cross_spread",
                "bid": bid,
                "ask": ask,
            })
            return None

        quantity = quote_usd / ask
        entry_fee = quote_usd * (MAKER_FEE_BPS / 10000.0)
        cost_usd = quote_usd + entry_fee
        
        pos = {
            "product_id": pid,
            "entry_type": "maker_fill",
            "entry_price": bid,  # Maker entry at bid
            "quantity": quantity,
            "cost_usd": cost_usd,
            "entry_fee": entry_fee,
            "entered_at": utc_now_iso(),
            "highest_bid": bid,
            "max_net_pct": 0.0,
            "min_net_pct": 0.0,
            "entry_spread_bps": spread_bps,
            "entry_mer": mer,
        }
        
        self.state.positions.append(pos)
        self.state.cash -= cost_usd
        self.state.product_stats[pid]["entries"] += 1
        self.state.product_stats[pid]["attempts"] += 1
        self.entry_count += 1
        
        return pos
    
    def check_exit(self, pos: dict, bid: float, ask: float, event_path: Path) -> dict | None:
        """Check if a position should exit."""
        # Maker exit at ask
        spread_bps = compute_spread_bps(bid, ask)
        
        pos["highest_bid"] = max(pos.get("highest_bid", bid), bid)
        
        proceeds = pos["quantity"] * ask
        exit_fee = proceeds * (MAKER_FEE_BPS / 10000.0)
        net = proceeds - exit_fee - pos["cost_usd"]
        net_pct = (net / pos["cost_usd"]) * 100.0
        
        pos["max_net_pct"] = max(pos.get("max_net_pct", 0.0), net_pct)
        pos["min_net_pct"] = min(pos.get("min_net_pct", 0.0), net_pct)
        
        age_seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(pos["entered_at"])).total_seconds()
        no_fee_paid_mfe = pos["max_net_pct"] < self.min_rent_harvest_pct
        
        # EXIT CHECKS
        exit_reason = None

        # Fee-Aware Insurance Gate (Prevents friction-bleed on flat tapes)
        fee_hurdle_pct = (MAKER_FEE_BPS + TAKER_FEE_BPS) / 100.0
        insurance_unlocked = pos["max_net_pct"] > fee_hurdle_pct

        # Dynamic Emergency Stop (Matched Kraken)

        avg_win = (self.state.total_net / self.state.wins) if self.state.wins > 0 else 0.0
        avg_win_pct = (avg_win / 8.0 * 100.0) if avg_win > 0 else 0.02
        dynamic_stop_pct = min(self.max_loss_pct, 3.0 * avg_win_pct)
        dynamic_stop_pct = max(dynamic_stop_pct, (MAKER_FEE_BPS * 2 / 100.0) + 0.05)

        # 1. Dynamic Stop
        if insurance_unlocked and bid < pos["entry_price"] * (1.0 - (dynamic_stop_pct / 100.0)):
            exit_reason = "emergency_stop"
        # 2. No-MFE Adverse Stop
        elif (
            insurance_unlocked
            and self.no_mfe_stop_pct > 0.0
            and no_fee_paid_mfe
            and age_seconds >= self.no_mfe_stop_min_age_seconds
            and net_pct <= -abs(self.no_mfe_stop_pct)
        ):
            exit_reason = "no_mfe_adverse_stop"
        # 3. Max loss (Hard - Always Active)
        elif net_pct <= -self.max_loss_pct:
            exit_reason = "max_loss"
        # 4. Trail stop
        elif insurance_unlocked and bid < pos["highest_bid"] * (1 - self.trail_giveback_pct / 100):
            exit_reason = "trail_stop"
        # 5. Rent harvest (captured 50%+ of spread)
        elif net_pct >= (spread_bps / 100.0 * 0.5):
            exit_reason = "rent_harvest"
        # 6. Min profit harvest
        elif net_pct >= self.min_rent_harvest_pct:
            exit_reason = "min_profit_harvest"
        
        if not exit_reason:
            return None

        # PROBABILISTIC FILL MODELING FOR EXIT
        is_taker_exit = exit_reason in {"emergency_stop", "max_loss", "no_mfe_adverse_stop", "trail_stop"}
        if is_taker_exit:
            fill_prob = 1.0
            fill_roll = 0.0
        else:
            mer = to_float(self.maker_opportunities.get(pos["product_id"], {}).get("mer"), 1.0)
            fill_prob = 0.5 + (min(mer, 2.0) / 2.0) * 0.4 + 0.20 # +20% exit boost
            fill_prob = min(0.98, fill_prob)
            fill_roll = random.random()

        if fill_roll > fill_prob:
            append_jsonl(event_path, {
                "ts_utc": utc_now_iso(),
                "action": "maker_exit_miss",
                "product_id": pos["product_id"],
                "reason": exit_reason,
                "fill_prob": round(fill_prob, 6),
                "fill_roll": round(fill_roll, 6),
                "net_pct": round(net_pct, 4),
            })
            return None
            
        return {
            "reason": exit_reason,
            "net": net,
            "net_pct": net_pct,
            "exit_spread_bps": spread_bps,
            "exit_price": bid if is_taker_exit else ask, # Taker exit at Bid
            "proceeds": proceeds if not is_taker_exit else (pos["quantity"] * bid),
            "exit_fee": exit_fee if not is_taker_exit else (pos["quantity"] * bid * TAKER_FEE_BPS / 10000.0),
            "is_taker": is_taker_exit,
        }
    
    def close_position(self, pos: dict, exit_info: dict, event_path: Path) -> None:
        """Close a position and record the result."""
        pid = pos["product_id"]
        net = exit_info["net"]
        net_pct = exit_info["net_pct"]
        
        # Telemetry: maker_fill vs taker_insurance
        exit_type = "maker_fill"
        if exit_info.get("is_taker"):
            exit_type = "taker_insurance"
        
        self.state.cash += exit_info["proceeds"] - exit_info["exit_fee"]
        self.state.positions.remove(pos)
        self.state.closes += 1
        
        is_win = net > 0
        if is_win:
            self.state.wins += 1
            self.state.product_stats[pid]["wins"] += 1
        else:
            self.state.losses += 1
            self.state.product_stats[pid]["losses"] += 1
        
        self.state.total_net += net
        self.state.total_gross += abs(net)
        self.state.net_pct = (self.state.total_net / self.state.initial_cash) * 100
        self.state.product_stats[pid]["exits"] += 1
        
        # Reentry cooldown
        self.state.reentry_blocks[pid] = self.reentry_cooldown_polls
        
        # Update avg spreads
        stats = self.state.product_stats[pid]
        stats["avg_exit_spread_bps"] = (
            (stats["avg_exit_spread_bps"] * (stats["exits"] - 1) + exit_info["exit_spread_bps"])
            / stats["exits"]
        )
        
        # Update Fleet-Wide Loss Tracker
        self.tracker.record_close(pid, won=(net > 0))
        self.tracker.save()

        # Record event
        append_jsonl(event_path, {
            "ts_utc": utc_now_iso(),
            "action": "close_coinbase_shadow",
            "product_id": pid,
            "exit_type": exit_type,
            "reason": exit_info["reason"],
            "net": round(net, 6),
            "net_pct": round(net_pct, 4),
            "entry_price": round(pos["entry_price"], 12),
            "exit_price": round(exit_info["exit_price"], 12),
            "cost_usd": round(pos["cost_usd"], 6),
            "entry_fee": round(pos["entry_fee"], 6),
            "exit_fee": round(exit_info["exit_fee"], 6),
            "maker_fee_bps": MAKER_FEE_BPS,
            "spread_bps": round(exit_info["exit_spread_bps"], 4),
            "entry_spread_bps": round(pos["entry_spread_bps"], 4),
            "max_net_pct": round(pos["max_net_pct"], 4),
            "min_net_pct": round(pos["min_net_pct"], 4),
        })
        
        direction = "WIN" if is_win else "LOSS"
        print(f"[{utc_now_iso()}] {direction}: {pid} net={net_pct:.3f}% "
              f"spread={exit_info['exit_spread_bps']:.1f}bps reason={exit_info['reason']}")

    def tick(self):
        """Tick cooldowns."""
        self.state.reentry_blocks = {
            p: polls - 1 for p, polls in self.state.reentry_blocks.items() if polls > 1
        }
        self.tracker.tick()
        self.toxicity.refresh()


def run_shadow(client: CoinbaseAdvancedClient, state_path: Path, event_path: Path,
              products: list[str], max_quote_usd: float, poll_interval: float = 5.0,
              min_notionals: dict[str, float] | None = None, kraken_lead_state_path: str = "") -> None:
    """Run the Coinbase maker shadow loop."""
    print("=" * 80)
    print("COINBASE SPOT MAKER SHADOW — Starting")
    print(f"Products: {products}")
    print(f"Max quote: ${max_quote_usd}")
    print(f"Fees: {MAKER_FEE_BPS}bps maker + {TAKER_FEE_BPS}bps taker = {ROUND_TRIP_BPS}bps RT")
    if kraken_lead_state_path:
        print(f"Foundry Lead: Active ({kraken_lead_state_path})")
    print("=" * 80)
    
    # Load or create state
    existing = load_json(state_path)
    if existing:
        print(f"[INFO] Resuming from existing state: {existing.get('closes', 0)} closes")
        state = ShadowState(products, max_quote_usd)
        state.cash = existing.get("cash", 100.0)
        state.closes = existing.get("closes", 0)
        state.wins = existing.get("wins", 0)
        state.losses = existing.get("losses", 0)
        state.total_net = existing.get("total_net", 0.0)
        state.net_pct = existing.get("net_pct", 0.0)
        state.positions = existing.get("positions", []) or existing.get("open_positions_list", []) # Support both schemas
        if not state.positions and existing.get("positions"): # Fix for my own schema
             state.positions = existing.get("positions")
        
        # SCHEMA FIX: Ensure positions is a list
        if isinstance(existing.get("positions"), list):
            state.positions = existing.get("positions")
        
        state.reentry_blocks = existing.get("reentry_blocks", {})
        state.product_stats = existing.get("product_stats", state.product_stats)
        
        # Ensure all current products are in stats
        for pid in products:
            if pid not in state.product_stats:
                state.product_stats[pid] = {
                    "attempts": 0,
                    "entries": 0,
                    "exits": 0,
                    "wins": 0,
                    "losses": 0,
                    "avg_entry_spread_bps": 0.0,
                    "avg_exit_spread_bps": 0.0,
                }
    else:
        state = ShadowState(products, max_quote_usd)
        save_json(state_path, state.to_dict())
    
    engine = ShadowEngine(state, min_notionals=min_notionals)
    
    print(f"\nPolling {products} every {poll_interval}s...")
    print("Press Ctrl+C to stop.\n")
    
    try:
        while True:
            state.poll_count += 1
            
            # 1. LOAD BOARDS & FOUNDRY LEAD
            engine.refresh_kraken_lead_state()
            bear_payload = load_json(BEAR_VELOCITY_PATH)
            if bear_payload:
                engine.bear_veto_products = {
                    str(r.get("product_id") or (r.get("base_currency", "") + "-" + r.get("quote_currency", ""))) 
                    for r in bear_payload.get("direct_dump_rows", [])
                }
                engine.bear_veto_products.discard("-")
            
            opps_payload = load_json(MAKER_OPPORTUNITY_PATH)
            if opps_payload:
                engine.maker_opportunities = {r["product_id"]: r for r in opps_payload.get("rows", [])}

            # 2. FETCH MIN NOTIONALS (EVERY 100 POLLS)
            if state.poll_count % 100 == 1:
                for pid in products:
                    try:
                        p_info = client.get_product(pid)
                        if p_info and "min_market_funds" in p_info:
                            engine.min_notionals[pid] = to_float(p_info["min_market_funds"], 1.0)
                        elif p_info and "quote_min_size" in p_info: # Try alternate field
                            engine.min_notionals[pid] = to_float(p_info["quote_min_size"], 1.0)
                    except Exception as e:
                        print(f"[WARN] Failed to fetch min-notional for {pid}: {e}")

            ticks = fetch_ticks(client, products)
            
            if not ticks:
                time.sleep(poll_interval)
                continue
            
            state.tick_count += 1
            
            # Check existing positions for exits
            for pos in list(state.positions):
                pid = pos["product_id"]
                if pid in ticks:
                    tick = ticks[pid]
                    exit_info = engine.check_exit(pos, tick["bid"], tick["ask"], event_path)
                    if exit_info:
                        engine.close_position(pos, exit_info, event_path)
                        save_json(state_path, state.to_dict())
            
            # Try new entries (one position per product max)
            active_pids = {p["product_id"] for p in state.positions}
            for pid in products:
                if pid in active_pids:
                    continue
                if pid not in ticks:
                    continue
                
                tick = ticks[pid]
                spread = compute_spread_bps(tick["bid"], tick["ask"])
                state.product_stats[pid]["attempts"] += 1
                
                # Update and Print Mode (Verbosity for Brain-Racking)
                mode = engine.update_profit_mode(pid, spread)
                l2_data = fetch_l2_data(client, pid)
                imbalance = l2_data["imb"]
                bid_depth = l2_data["bid_depth_usd"]
                
                if state.poll_count % 12 == 1: # Once a minute-ish
                     print(f"  [MODE] {pid}: {mode} (spread={spread:.1f}bps, imb={imbalance:+.2f}, depth=${bid_depth:.1f})")
                
                pos = engine.try_enter(pid, tick["bid"], tick["ask"], spread, event_path, imbalance=imbalance, bid_depth_usd=bid_depth)
                if pos:
                    print(f"[{utc_now_iso()}] ENTRY: {pid} bid={tick['bid']:.8f} "
                          f"ask={tick['ask']:.8f} spread={spread:.1f}bps "
                          f"cost=${pos['cost_usd']:.2f} mode={engine.last_mode}")
                    save_json(state_path, state.to_dict())
            
            engine.tick()

            # Print status every 60 polls
            if state.poll_count % 12 == 0:  # Every ~60s at 5s interval
                print(f"[{utc_now_iso()}] Poll #{state.poll_count} | "
                      f"Closes: {state.closes} | W/L: {state.wins}/{state.losses} | "
                      f"Net: {state.net_pct:.2f}% | Cash: ${state.cash:.2f} | "
                      f"Open: {len(state.positions)}")
                save_json(state_path, state.to_dict())
            
            time.sleep(poll_interval)
    
    except KeyboardInterrupt:
        print(f"\n[INFO] Shadow stopped. Final state saved to {state_path}")
        save_json(state_path, state.to_dict())


def parse_min_notional_overrides(value: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in str(value or "").split(","):
        item = part.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Invalid min-notional override {item!r}; expected PRODUCT-USD=USD")
        product_id, raw_amount = item.split("=", 1)
        product_id = product_id.strip().upper()
        if not product_id:
            raise ValueError(f"Invalid min-notional override {item!r}; missing product id")
        amount = to_float(raw_amount, -1.0)
        if amount <= 0.0:
            raise ValueError(f"Invalid min-notional override {item!r}; amount must be positive")
        out[product_id] = amount
    return out


def main() -> int:
    import argparse
    
    parser = argparse.ArgumentParser(description="Coinbase Spot Maker Shadow Runner")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE))
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS))
    parser.add_argument("--max-quote-usd", type=float, default=8.0)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--products", nargs="*", default=DEFAULT_PRODUCTS)
    parser.add_argument(
        "--min-notional-overrides",
        default="",
        help="Comma-separated Coinbase product minimums, e.g. SPX-USD=1,FLOCK-USD=1.",
    )
    parser.add_argument("--kraken-lead-state-path", type=str, default="", help="Path to Kraken live_exec state file for Foundry Lead logic.")
    args = parser.parse_args()
    
    state_path = Path(args.state_path)
    event_path = Path(args.events_path)
    
    # Initialize client
    try:
        client = CoinbaseAdvancedClient()
        if not client.has_auth():
            print("[ERROR] Coinbase API credentials not found. Set COINBASE_API_KEY and COINBASE_API_SECRET.")
            return 1
    except Exception as e:
        print(f"[ERROR] Failed to initialize Coinbase client: {e}")
        return 1
    
    min_notionals = parse_min_notional_overrides(args.min_notional_overrides)
    run_shadow(
        client,
        state_path,
        event_path,
        args.products,
        args.max_quote_usd,
        args.poll_interval,
        min_notionals=min_notionals,
        kraken_lead_state_path=args.kraken_lead_state_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
