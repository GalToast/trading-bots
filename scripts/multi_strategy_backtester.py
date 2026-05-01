#!/usr/bin/env python3
"""Titan 10.3 The Strategy Factory: Procedural Multi-Strategy Backtester.

This engine generates thousands of *structurally distinct* trading strategies 
by combining different Entry Triggers, Execution Styles, and Exit Rules.
It evaluates them against historical tick data to find true structural edge,
not just over-fit parameters of a single logic branch.
"""
import argparse
import itertools
import json
import statistics
from pathlib import Path
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

# --- 1. ENTRY TRIGGERS ---
def trigger_vulture_dump(history: List[Dict], current: Dict, params: Dict) -> bool:
    """Detects a sudden idiosyncratic dump (price drops rapidly)."""
    if len(history) < params["lookback_ticks"]: return False
    past = history[-params["lookback_ticks"]]
    drop_bps = (float(past["bid"]) - float(current["bid"])) / float(past["bid"]) * 10000
    return drop_bps > params["threshold_bps"]

def trigger_obi_scalp(history: List[Dict], current: Dict, params: Dict) -> bool:
    """Detects extreme order book imbalance using depth fields."""
    bid_depth = float(current.get("bid_depth_usd", 0))
    ask_depth = float(current.get("ask_depth_usd", 0))
    total = bid_depth + ask_depth
    if total == 0: return False
    imb = bid_depth / total
    return imb > params["threshold_pct"] if params["side"] == "BUY" else imb < (1 - params["threshold_pct"])

def trigger_momentum_taker(history: List[Dict], current: Dict, params: Dict) -> bool:
    """Detects rapid movement in the ask (buy) or bid (sell)."""
    move_bps = float(current.get("ask_down_bps", 0)) if params["side"] == "BUY" else float(current.get("bid_up_bps", 0))
    return move_bps > params["threshold_bps"]

ENTRY_MODULES = {
    "VultureDump": (trigger_vulture_dump, [{"lookback_ticks": 5, "threshold_bps": 20}, {"lookback_ticks": 10, "threshold_bps": 40}]),
    "OBI_Scalp": (trigger_obi_scalp, [{"threshold_pct": 0.85, "side": "BUY"}, {"threshold_pct": 0.85, "side": "SELL"}]),
    "MomTaker": (trigger_momentum_taker, [{"threshold_bps": 20, "side": "BUY"}, {"threshold_bps": 20, "side": "SELL"}]),
}

# --- 2. EXECUTION MODELS ---
def exec_maker_entry(current: Dict) -> float:
    # Enter at bid + 1 tick (simplified offset)
    return float(current["bid"]) * 1.0001

def exec_taker_entry(current: Dict) -> float:
    # Enter at ask (pay the spread)
    return float(current["ask"])

def exec_maker_exit(current: Dict) -> float:
    # Exit at ask - 1 tick
    return float(current["ask"]) * 0.9999

def exec_taker_exit(current: Dict) -> float:
    # Exit at bid (pay the spread)
    return float(current["bid"])

EXECUTION_STYLES = {
    "Maker-Maker": {"entry": exec_maker_entry, "exit": exec_maker_exit, "fee_bps": 50}, # 25+25
    "Maker-Taker": {"entry": exec_maker_entry, "exit": exec_taker_exit, "fee_bps": 85}, # 25+60
    "Taker-Maker": {"entry": exec_taker_entry, "exit": exec_maker_exit, "fee_bps": 85}, # 60+25
    "Taker-Taker": {"entry": exec_taker_entry, "exit": exec_taker_exit, "fee_bps": 120},# 60+60
}

# --- 3. EXIT RULES ---
def exit_fixed_ttl(ticks_held: int, current_price: float, params: Dict) -> bool:
    return ticks_held >= params["max_ticks"]

def exit_profit_target(entry_price: float, current_price: float, params: Dict) -> bool:
    pnl_bps = (current_price - entry_price) / entry_price * 10000
    return pnl_bps > params["target_bps"]

def exit_stop_loss(entry_price: float, current_price: float, params: Dict) -> bool:
    pnl_bps = (current_price - entry_price) / entry_price * 10000
    return pnl_bps < -params["stop_bps"]

EXIT_MODULES = {
    "TTL_10s": (exit_fixed_ttl, {"max_ticks": 10}),
    "TTL_30s": (exit_fixed_ttl, {"max_ticks": 30}),
    "TakeProfit_50bps": (exit_profit_target, {"target_bps": 50}),
    "TakeProfit_100bps": (exit_profit_target, {"target_bps": 100}),
    "StopLoss_50bps": (exit_stop_loss, {"stop_bps": 50}),
}

def generate_strategies() -> List[Dict]:
    """Procedurally generates thousands of unique strategy combinations."""
    strategies = []
    idx = 0
    for entry_name, (entry_func, entry_param_list) in ENTRY_MODULES.items():
        for entry_params in entry_param_list:
            p_val = entry_params.get('lookback_ticks') or entry_params.get('threshold_pct') or entry_params.get('threshold_bps') or entry_params.get('multiplier')
            for exec_name, exec_funcs in EXECUTION_STYLES.items():
                for exit_name, (exit_func, exit_params) in EXIT_MODULES.items():
                    # Generate combinations of Exit rules (e.g. TTL OR TakeProfit)
                    strategies.append({
                        "id": f"STRAT_{idx}",
                        "name": f"{entry_name}({p_val}) | {exec_name} | {exit_name}",
                        "entry_func": entry_func,
                        "entry_params": entry_params,
                        "exec_style": exec_funcs,
                        "exit_func": exit_func,
                        "exit_params": exit_params
                    })
                    idx += 1
    return strategies

def run_backtest(strategies: List[Dict], ticks_by_product: Dict[str, List[Dict]]):
    print(f"Running backtest for {len(strategies)} strategies across {len(ticks_by_product)} products...")
    
    results = {s["id"]: {"name": s["name"], "trades": 0, "wins": 0, "net_bps": 0.0} for s in strategies}
    
    for pid, ticks in ticks_by_product.items():
        if len(ticks) < 20: continue
        
        for s in strategies:
            in_position = False
            entry_price = 0.0
            ticks_held = 0
            pending_entry = False
            pending_entry_ticks = 0
            
            for i in range(10, len(ticks)):
                current = ticks[i]
                history = ticks[:i]
                
                if not in_position:
                    # Check Entry
                    if not pending_entry and s["entry_func"](history, current, s["entry_params"]):
                        if "Maker" in s["name"].split(" | ")[1].split("-")[0]:
                            pending_entry = True
                            pending_entry_ticks = 0
                            # Intended entry price
                            entry_price = s["exec_style"]["entry"](current)
                        else:
                            entry_price = s["exec_style"]["entry"](current)
                            in_position = True
                            ticks_held = 0
                    elif pending_entry:
                        # Queue penalty for Maker: wait 3 ticks without price running away
                        if float(current["bid"]) < entry_price: # Price fell through our bid, we got filled!
                            in_position = True
                            ticks_held = 0
                            pending_entry = False
                        elif float(current["bid"]) > entry_price * 1.005: # Price ran away by 50bps
                            pending_entry = False # Missed the fill
                        else:
                            pending_entry_ticks += 1
                            if pending_entry_ticks >= 3:
                                in_position = True # Queued and filled
                                ticks_held = 0
                                pending_entry = False
                else:
                    ticks_held += 1
                    current_exit_price = s["exec_style"]["exit"](current)
                    
                    # Check Exit
                    is_last_tick = (i == len(ticks) - 1)
                    
                    if is_last_tick or s["exit_func"](ticks_held if "ticks" in s["name"] else entry_price, 
                                      current_exit_price if "Profit" in s["name"] or "Stop" in s["name"] else s["exit_params"], 
                                      s["exit_params"]):
                        
                        # Force exit at current market if last tick
                        if is_last_tick:
                             current_exit_price = float(current["bid"]) # Taker sell to force close

                        gross_bps = (current_exit_price - entry_price) / entry_price * 10000
                            
                        net_bps = gross_bps - s["exec_style"]["fee_bps"]
                        
                        results[s["id"]]["trades"] += 1
                        if net_bps > 0:
                            results[s["id"]]["wins"] += 1
                        results[s["id"]]["net_bps"] += net_bps
                        
                        in_position = False
                        pending_entry = False
    return results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-path", default="reports/cache/kraken_spot_live_radar_ticks.json")
    args = parser.parse_args()
    
    cache_file = ROOT / args.cache_path
    if not cache_file.exists():
        print(f"Cache file not found: {cache_file}")
        return
        
    print(f"Loading tick cache: {cache_file.name} ...")
    ticks_by_product = defaultdict(list)
    
    # Simple parse of jsonl or list of dicts
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "samples" in data:
                # Radar Cache Format
                for pid, ticks in data["samples"].items():
                    ticks_by_product[pid] = ticks
            elif "ticks" in data:
                # 1s Tape Format
                pid = data.get("product", "UNKNOWN")
                ticks_by_product[pid] = data["ticks"]
            else:
                print("Unknown cache format.")
                return
    except Exception as e:
         print(f"Error loading ticks: {e}")
         return
         
    strategies = generate_strategies()
    results = run_backtest(strategies, ticks_by_product)
    
    # Sort and display top 10
    sorted_res = sorted(results.values(), key=lambda x: x["net_bps"], reverse=True)
    
    print("\n--- TOP 10 STRATEGIES (Net BPS) ---")
    for r in sorted_res[:10]:
        win_rate = (r["wins"] / r["trades"] * 100) if r["trades"] > 0 else 0
        print(f"{r['name'][:50]:<50} | Trades: {r['trades']:<4} | Win%: {win_rate:5.1f}% | Net: {r['net_bps']:8.1f} bps")

if __name__ == "__main__":
    main()
