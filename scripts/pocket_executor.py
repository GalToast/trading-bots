#!/usr/bin/env python3
"""Simple Pocket Executor: Run the foundry's positive pockets live.

No ML models. No cluster filters. No dual-mode execution.
Just: when the foundry's pattern fires, take the trade. Track if it works.

This is the SIMPLE concept we've been missing.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POCKET_BOARD = ROOT / "reports" / "coinbase_spot_foundry_pocket_board.json"
STATE_PATH = ROOT / "reports" / "pocket_executor_state.json"
EVENT_PATH = ROOT / "reports" / "pocket_executor_events.jsonl"

from coinbase_advanced_client import CoinbaseAdvancedClient
from live_coinbase_spot_machinegun_shadow import fetch_coinbase_ticks
from candle_cache_service import load_candles


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def spread_bps(bid_price, ask_price):
    bid = to_float(bid_price)
    ask = to_float(ask_price)
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return ((ask - bid) / mid) * 10000.0


def load_pockets(
    *,
    min_signals=3,
    min_avg_net_pct=1.0,
    min_win_rate_pct=60.0,
    min_worst_net_pct=-2.0,
    max_spread_bps_proxy=30.0,
    min_pocket_score=0.0,
):
    """Load positive pockets from foundry board."""
    with open(POCKET_BOARD) as f:
        board = json.load(f)
    
    pockets = []
    for row in board.get("rows", []):
        # Only keep pockets with meaningful sample size
        signals = int(to_float(row.get("signals"), 0))
        win_rate = to_float(row.get("win_rate_pct"))
        avg_net = to_float(row.get("avg_net_pct"))
        worst_net = to_float(row.get("worst_net_pct"))
        spread_proxy = to_float(row.get("spread_bps_proxy"))
        pocket_score = to_float(row.get("pocket_score"))
        
        if (
            signals >= min_signals
            and avg_net >= min_avg_net_pct
            and win_rate >= min_win_rate_pct
            and worst_net >= min_worst_net_pct
            and spread_proxy <= max_spread_bps_proxy
            and pocket_score >= min_pocket_score
        ):
            pockets.append({
                "product_id": row["product_id"],
                "variant_id": row["variant_id"],
                "archetype": row["archetype"],
                "trigger": row["trigger"],
                "confirmation": row.get("confirmation", ""),
                "exit": row.get("exit", ""),
                "sizing": row.get("sizing", ""),
                "signals": signals,
                "wins": row.get("wins", 0),
                "win_rate_pct": win_rate,
                "avg_net_pct": avg_net,
                "cumulative_net_pct": row.get("cumulative_net_pct", 0),
                "worst_net_pct": worst_net,
                "spread_bps_proxy": spread_proxy,
                "pocket_score": pocket_score,
            })
    
    print(f"Loaded {len(pockets)} positive pockets from foundry board")
    return pockets


def check_pattern_fired(pocket, recent_candles, timeframe_minutes=5):
    """Check if the foundry pattern fired.
    
    This is a simplified check using 5m candles - in production, you'd run the full
    foundry pattern detection logic. For now, we check:
    1. Product matches
    2. Price movement is consistent with the trigger
    
    For 'failed_breakdown': price dropped below support then recovered
    For 'impulse_buy': strong upward momentum
    For 'dump_reclaim': sharp drop followed by recovery
    """
    trigger = pocket["trigger"]
    
    if len(recent_candles) < 3:
        return False
    
    # Get last 3 candles
    c1, c2, c3 = recent_candles[-3], recent_candles[-2], recent_candles[-1]
    p1, p2, p3 = c1["close"], c2["close"], c3["close"]
    
    # Simple trigger detection
    if trigger == "failed_breakdown":
        # Price dropped then recovered
        drop_pct = (p2 - p1) / p1 * 100
        recovery_pct = (p3 - p2) / p2 * 100
        return drop_pct < -0.5 and recovery_pct > 0.3
    
    elif trigger == "impulse_buy":
        # Strong upward move
        move_pct = (p3 - p1) / p1 * 100
        return move_pct > 1.0
    
    elif trigger == "dump_reclaim":
        # Sharp drop then recovery
        min_price = min(c1["low"], c2["low"], c3["low"])
        drop_pct = (min_price - p1) / p1 * 100
        current_recovery = (p3 - min_price) / min_price * 100
        return drop_pct < -1.0 and current_recovery > 0.5
    
    elif trigger == "compression_pop":
        # Tight range then breakout
        max_c1_c2 = max(c1["high"], c2["high"])
        min_c1_c2 = min(c1["low"], c2["low"])
        range_pct = (max_c1_c2 - min_c1_c2) / min_c1_c2 * 100
        breakout = (p3 - p2) / p2 * 100
        return range_pct < 0.3 and breakout > 0.5
    
    else:
        # Default: any significant move
        move_pct = abs(p3 - p1) / p1 * 100
        return move_pct > 0.5


class PocketExecutor:
    def __init__(
        self,
        starting_cash=100.0,
        deploy_pct=0.8,
        fee_bps=120.0,
        target_net_pct=0.5,
        hard_stop_net_pct=-3.0,
        profit_lock_giveback_pct=0.35,
        manifest_seconds=300.0,
        manifest_net_pct=0.0,
        max_hold_seconds=600.0,
        max_entry_spread_bps=25.0,
    ):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.deploy_pct = deploy_pct
        self.fee_bps = fee_bps
        self.target_net_pct = target_net_pct
        self.hard_stop_net_pct = hard_stop_net_pct
        self.profit_lock_giveback_pct = profit_lock_giveback_pct
        self.manifest_seconds = manifest_seconds
        self.manifest_net_pct = manifest_net_pct
        self.max_hold_seconds = max_hold_seconds
        self.max_entry_spread_bps = max_entry_spread_bps
        self.position = None
        self.candles = {}  # product_id -> list of candle dicts
        self.trades_executed = 0
        self.trades_won = 0
        self.total_net = 0.0
        self.total_fees = 0.0
    
    def fee_rate(self):
        return self.fee_bps / 10000.0

    def elapsed_seconds(self, opened_at):
        try:
            opened = datetime.fromisoformat(str(opened_at))
        except ValueError:
            return 0.0
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds())
    
    def update_price(self, product_id, price):
        # Time processing
        dt = datetime.now(timezone.utc)
        minute = dt.minute // 5 * 5
        dt_candle = dt.replace(minute=minute, second=0, microsecond=0)
        candle_ts = int(dt_candle.timestamp())
        
        if product_id not in self.candles:
            self.candles[product_id] = []
        
        product_candles = self.candles[product_id]
        
        if not product_candles or product_candles[-1]["time"] != candle_ts:
            # new candle
            product_candles.append({
                "time": candle_ts,
                "open": price,
                "high": price,
                "low": price,
                "close": price
            })
            if len(product_candles) > 10:
                self.candles[product_id] = product_candles[-10:]
        else:
            # update current candle
            c = product_candles[-1]
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price
    
    def check_pockets(self, pockets):
        """Check if any pocket pattern fired."""
        if self.position is not None:
            return None  # Already in a trade
        
        for pocket in pockets:
            product_id = pocket["product_id"]
            recent = self.candles.get(product_id, [])
            
            if check_pattern_fired(pocket, recent):
                return pocket
        
        return None
    
    def open_position(self, pocket, ask_price, bid_price):
        """Open a position based on a pocket signal."""
        live_spread_bps = spread_bps(bid_price, ask_price)
        if live_spread_bps is None or live_spread_bps > self.max_entry_spread_bps:
            append_jsonl(EVENT_PATH, {
                "ts_utc": utc_now_iso(),
                "action": "shadow_reject",
                "shadow_only": True,
                "reject_reason": "entry_spread_too_wide",
                "product_id": pocket["product_id"],
                "variant_id": pocket["variant_id"],
                "trigger": pocket["trigger"],
                "entry_ask": ask_price,
                "entry_bid": bid_price,
                "live_spread_bps": live_spread_bps,
                "max_entry_spread_bps": self.max_entry_spread_bps,
            })
            return

        cost = self.cash * self.deploy_pct
        entry_fee = cost * self.fee_rate()
        quantity = (cost - entry_fee) / ask_price
        if quantity <= 0:
            return
        
        self.cash -= cost
        self.total_fees += entry_fee
        self.position = {
            "product_id": pocket["product_id"],
            "variant_id": pocket["variant_id"],
            "trigger": pocket["trigger"],
            "entry_price": ask_price,
            "quantity": quantity,
            "cost": cost,
            "entry_fee": entry_fee,
            "opened_at": utc_now_iso(),
            "highest_price": bid_price,
            "max_net": -entry_fee,
            "max_net_pct": (-entry_fee / cost) * 100 if cost else 0.0,
            "live_spread_bps": live_spread_bps,
            "pocket_avg_net_pct": pocket.get("avg_net_pct"),
            "pocket_win_rate_pct": pocket.get("win_rate_pct"),
            "pocket_worst_net_pct": pocket.get("worst_net_pct"),
            "pocket_score": pocket.get("pocket_score"),
        }
        
        append_jsonl(EVENT_PATH, {
            "ts_utc": utc_now_iso(),
            "action": "shadow_open",
            "shadow_only": True,
            "product_id": pocket["product_id"],
            "variant_id": pocket["variant_id"],
            "trigger": pocket["trigger"],
            "entry_price": ask_price,
            "entry_bid": bid_price,
            "quantity": quantity,
            "cost": cost,
            "entry_fee": entry_fee,
            "fee_bps_per_side": self.fee_bps,
            "live_spread_bps": live_spread_bps,
            "target_net_pct": self.target_net_pct,
            "pocket_avg_net_pct": pocket.get("avg_net_pct"),
            "pocket_win_rate_pct": pocket.get("win_rate_pct"),
            "pocket_worst_net_pct": pocket.get("worst_net_pct"),
            "pocket_score": pocket.get("pocket_score"),
        })
        
        print(f"  SHADOW OPEN: {pocket['product_id']} {pocket['trigger']} @ ask {ask_price:.6f}")
        self.trades_executed += 1
    
    def check_exit(self, pocket, current_price):
        """Check if we should exit the position."""
        if self.position is None:
            return
        
        pos = self.position
        exit_type = pocket.get("exit", "wide_bubble_trail")
        
        # Simple exit logic - in production, use the foundry's exit logic
        proceeds = pos["quantity"] * current_price
        exit_fee = proceeds * self.fee_rate()
        net = proceeds - exit_fee - pos["cost"]
        pnl_pct = (net / pos["cost"]) * 100 if pos["cost"] else 0.0
        
        elapsed = self.elapsed_seconds(pos.get("opened_at"))
        pos["highest_price"] = max(pos["highest_price"], current_price)
        pos["max_net"] = max(pos.get("max_net", -pos["entry_fee"]), net)
        pos["max_net_pct"] = max(pos.get("max_net_pct", -100.0), pnl_pct)
        
        should_exit = False
        exit_reason = ""
        
        if pnl_pct >= self.target_net_pct:
            should_exit = True
            exit_reason = "target_net_reached"
        elif (
            pos.get("max_net_pct", -100.0) >= self.target_net_pct
            and pnl_pct <= max(self.target_net_pct, pos["max_net_pct"] - self.profit_lock_giveback_pct)
        ):
            should_exit = True
            exit_reason = "profit_lock_trail"
        elif elapsed >= self.max_hold_seconds and pnl_pct > 0.0:
            should_exit = True
            exit_reason = "time_positive_exit"
        elif elapsed >= self.manifest_seconds and pos.get("max_net_pct", -100.0) < self.manifest_net_pct:
            should_exit = True
            exit_reason = "failed_to_manifest"
        elif pnl_pct <= self.hard_stop_net_pct:
            should_exit = True
            exit_reason = "hard_stop"
        
        if should_exit:
            self.close_position(exit_reason, current_price)
    
    def close_position(self, reason, price):
        """Close the position."""
        pos = self.position
        proceeds = pos["quantity"] * price
        exit_fee = proceeds * self.fee_rate()
        net = proceeds - exit_fee - pos["cost"]
        net_pct = (net / pos["cost"]) * 100
        mfe_gross_pct = ((pos["highest_price"] - pos["entry_price"]) / pos["entry_price"]) * 100 if pos["entry_price"] else 0.0
        gross_mfe_capture_pct = (
            ((price - pos["entry_price"]) / (pos["highest_price"] - pos["entry_price"])) * 100
            if pos["highest_price"] > pos["entry_price"]
            else None
        )
        net_mfe_capture_pct = (net / pos["max_net"]) * 100 if pos.get("max_net", 0.0) > 0 else None
        
        self.cash += proceeds - exit_fee
        self.total_net += net
        self.total_fees += exit_fee
        
        if net > 0:
            self.trades_won += 1
        
        append_jsonl(EVENT_PATH, {
            "ts_utc": utc_now_iso(),
            "action": "shadow_close",
            "shadow_only": True,
            "exit_reason": reason,
            "product_id": pos["product_id"],
            "entry_price": pos["entry_price"],
            "exit_price": price,
            "highest_price": pos["highest_price"],
            "net": net,
            "net_pct": net_pct,
            "max_net": pos.get("max_net", 0.0),
            "max_net_pct": pos.get("max_net_pct"),
            "mfe_gross_pct": mfe_gross_pct,
            "gross_mfe_capture_pct": gross_mfe_capture_pct,
            "net_mfe_capture_pct": net_mfe_capture_pct,
            "entry_fee": pos["entry_fee"],
            "exit_fee": exit_fee,
            "fee_bps_per_side": self.fee_bps,
            "target_net_pct": self.target_net_pct,
            "live_spread_bps": pos.get("live_spread_bps"),
            "pocket_avg_net_pct": pos.get("pocket_avg_net_pct"),
            "pocket_win_rate_pct": pos.get("pocket_win_rate_pct"),
            "pocket_worst_net_pct": pos.get("pocket_worst_net_pct"),
            "pocket_score": pos.get("pocket_score"),
            "total_net": self.total_net,
            "cash": self.cash,
        })
        
        print(f"  CLOSE: {pos['product_id']} {reason} @ {price:.6f} | net: {net_pct:+.2f}% | total: {self.total_net:+.2f}")
        self.position = None
    
    def state(self):
        return {
            "cash": self.cash,
            "starting_cash": self.starting_cash,
            "position": self.position,
            "trades_executed": self.trades_executed,
            "trades_won": self.trades_won,
            "win_rate": self.trades_won / self.trades_executed * 100 if self.trades_executed > 0 else 0,
            "total_net": self.total_net,
            "total_net_pct": self.total_net / self.starting_cash * 100,
            "total_fees": self.total_fees,
            "shadow_only": True,
            "config": {
                "fee_bps": self.fee_bps,
                "target_net_pct": self.target_net_pct,
                "hard_stop_net_pct": self.hard_stop_net_pct,
                "profit_lock_giveback_pct": self.profit_lock_giveback_pct,
                "manifest_seconds": self.manifest_seconds,
                "manifest_net_pct": self.manifest_net_pct,
                "max_hold_seconds": self.max_hold_seconds,
                "max_entry_spread_bps": self.max_entry_spread_bps,
            },
        }


def main():
    parser = argparse.ArgumentParser(description="Simple Pocket Executor")
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--fee-bps", type=float, default=120.0)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--target-net-pct", type=float, default=0.5)
    parser.add_argument("--hard-stop-net-pct", type=float, default=-3.0)
    parser.add_argument("--profit-lock-giveback-pct", type=float, default=0.35)
    parser.add_argument("--manifest-seconds", type=float, default=300.0)
    parser.add_argument("--manifest-net-pct", type=float, default=0.0)
    parser.add_argument("--max-hold-seconds", type=float, default=600.0)
    parser.add_argument("--max-entry-spread-bps", type=float, default=25.0)
    parser.add_argument("--min-signals", type=int, default=3)
    parser.add_argument("--min-avg-net-pct", type=float, default=1.0)
    parser.add_argument("--min-win-rate-pct", type=float, default=60.0)
    parser.add_argument("--min-worst-net-pct", type=float, default=-2.0)
    parser.add_argument("--max-spread-bps-proxy", type=float, default=30.0)
    parser.add_argument("--min-pocket-score", type=float, default=0.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    
    print("=" * 80)
    print("POCKET EXECUTOR - Simple Foundry Pattern Executor")
    print("=" * 80)
    print(f"Starting cash: ${args.starting_cash:.2f}")
    print(f"Deploy pct: {args.deploy_pct*100:.0f}%")
    print(f"Fee bps: {args.fee_bps:.0f}")
    print(f"Target net pct: {args.target_net_pct:.2f}%")
    
    # Load pockets
    pockets = load_pockets(
        min_signals=args.min_signals,
        min_avg_net_pct=args.min_avg_net_pct,
        min_win_rate_pct=args.min_win_rate_pct,
        min_worst_net_pct=args.min_worst_net_pct,
        max_spread_bps_proxy=args.max_spread_bps_proxy,
        min_pocket_score=args.min_pocket_score,
    )
    if not pockets:
        print("No positive pockets found. Exiting.")
        return
    
    print(f"\nTop 5 pockets by avg net:")
    for i, p in enumerate(sorted(pockets, key=lambda x: x["avg_net_pct"], reverse=True)[:5]):
        print(f"  {i+1}. {p['product_id']} {p['trigger']} -> {p['exit']} | {p['avg_net_pct']:.2f}% avg net | {p['win_rate_pct']:.0f}% win | {p['signals']} signals")
    
    # Initialize executor
    executor = PocketExecutor(
        starting_cash=args.starting_cash,
        deploy_pct=args.deploy_pct,
        fee_bps=args.fee_bps,
        target_net_pct=args.target_net_pct,
        hard_stop_net_pct=args.hard_stop_net_pct,
        profit_lock_giveback_pct=args.profit_lock_giveback_pct,
        manifest_seconds=args.manifest_seconds,
        manifest_net_pct=args.manifest_net_pct,
        max_hold_seconds=args.max_hold_seconds,
        max_entry_spread_bps=args.max_entry_spread_bps,
    )
    
    client = CoinbaseAdvancedClient()
    
    pocket_products = list(set(p["product_id"] for p in pockets))
    print(f"\nPrefilling 5m candles for {len(pocket_products)} products...")
    for product_id in pocket_products:
        # Fetch last 1 day of 5-minute candles
        candles = load_candles(product_id, "FIVE_MINUTE", days=1, max_age_minutes=5, client=client)
        if candles:
            # Keep the last 10 candles
            executor.candles[product_id] = candles[-10:]
    
    print(f"\nMonitoring {len(pocket_products)} products for pocket patterns...")
    print(f"Poll interval: {args.poll_seconds:.0f}s")
    print()
    
    try:
        while True:
            # Fetch current prices
            ticks = fetch_coinbase_ticks(client, pocket_products) if hasattr(client, 'best_bid_ask') else {}
            
            if not ticks:
                print(f"  No ticks received. Retrying in {args.poll_seconds:.0f}s...")
                time.sleep(args.poll_seconds)
                continue
            
            # Update price history
            for product_id, tick in ticks.items():
                executor.update_price(product_id, tick["bid"])
            
            # Check for pocket signals
            if executor.position is None:
                pocket = executor.check_pockets(pockets)
                if pocket:
                    tick = ticks.get(pocket["product_id"], {})
                    ask_price = tick.get("ask", 0)
                    bid_price = tick.get("bid", 0)
                    if ask_price > 0 and bid_price > 0:
                        executor.open_position(pocket, ask_price, bid_price)
            else:
                # Check exit for current position
                pocket = next((p for p in pockets if p["product_id"] == executor.position["product_id"]), None)
                if pocket:
                    price = ticks.get(executor.position["product_id"], {}).get("bid", 0)
                    if price > 0:
                        executor.check_exit(pocket, price)
            
            # Print state
            state = executor.state()
            save_json(STATE_PATH, {"updated_at": utc_now_iso(), "state": state})
            print(f"  Cash: ${state['cash']:.2f} | Net: {state['total_net_pct']:+.2f}% | Trades: {state['trades_executed']} | Win: {state['win_rate']:.0f}% | Position: {executor.position['product_id'] if executor.position else 'None'}")
            
            if args.once:
                break
            
            time.sleep(args.poll_seconds)
    
    except KeyboardInterrupt:
        print("\nStopping pocket executor...")
    
    # Final state
    state = executor.state()
    print(f"\nFinal State:")
    print(f"  Cash: ${state['cash']:.2f} (started ${state['starting_cash']:.2f})")
    print(f"  Total net: {state['total_net_pct']:+.2f}%")
    print(f"  Trades: {state['trades_executed']} | Won: {state['trades_won']} | Win rate: {state['win_rate']:.0f}%")


if __name__ == "__main__":
    main()
