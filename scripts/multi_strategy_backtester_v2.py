#!/usr/bin/env python3
"""Titan 10.5 The Strategy Factory v2.1: Multi-Exchange Procedural Backtester.

Generates and backtests thousands of structural strategies across multiple 
exchanges (Kraken and Coinbase) using synced tick tapes.
"""
import argparse
import json
import random
from pathlib import Path
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

# --- 1. ENTRY TRIGGERS (MULTI-EXCHANGE) ---
def trigger_bridge_arb(history: List[Dict], current: Dict, params: Dict) -> bool:
    """Detects price delta between exchanges exceeding a threshold."""
    product = params["product"]
    krk_pid = product
    cb_pid = product.replace("/", "-")
    
    krk = current["exchanges"]["kraken"].get(krk_pid)
    cb = current["exchanges"]["coinbase"].get(cb_pid)
    
    if not krk or not cb: return False
    
    # Buy cheap Kraken, Sell expensive Coinbase
    # Delta = (Coinbase Bid / Kraken Ask) - 1
    delta_bps = (cb["bid"] / krk["ask"] - 1.0) * 10000.0
    return delta_bps > params["threshold_bps"]

def trigger_vulture_dump(history: List[Dict], current: Dict, params: Dict) -> bool:
    """Detects a sudden idiosyncratic dump on Kraken."""
    product = params["product"]
    if len(history) < params["lookback_ticks"]: return False
    past = history[-params["lookback_ticks"]]
    
    curr_krk = current["exchanges"]["kraken"].get(product)
    past_krk = past["exchanges"]["kraken"].get(product)
    
    if not curr_krk or not past_krk: return False
    
    drop_bps = (past_krk["bid"] - curr_krk["bid"]) / past_krk["bid"] * 10000
    return drop_bps > params["threshold_bps"]

def trigger_price_convergence(history: List[Dict], current: Dict, params: Dict) -> bool:
    """Detects when Kraken/Coinbase prices have diverged and are starting to converge."""
    product = params["product"]
    krk = current["exchanges"]["kraken"].get(product)
    cb = current["exchanges"]["coinbase"].get(product.replace("/", "-"))
    
    if not krk or not cb or not history: return False
    
    past = history[-1]
    past_krk = past["exchanges"]["kraken"].get(product)
    past_cb = past["exchanges"]["coinbase"].get(product.replace("/", "-"))
    
    if not past_krk or not past_cb: return False
    
    curr_delta = (krk["bid"] + krk["ask"]) / 2.0 - (cb["bid"] + cb["ask"]) / 2.0
    past_delta = (past_krk["bid"] + past_krk["ask"]) / 2.0 - (past_cb["bid"] + past_cb["ask"]) / 2.0
    
    # If delta was large and is now shrinking, it's converging
    return abs(past_delta) > params["min_delta_bps"] and abs(curr_delta) < abs(past_delta)

def trigger_toxic_sniffer(history: List[Dict], current: Dict, params: Dict) -> bool:
    """Detects when Coinbase price moves violently but Kraken is lagging."""
    product = params["product"]
    krk = current["exchanges"]["kraken"].get(product)
    cb = current["exchanges"]["coinbase"].get(product.replace("/", "-"))
    
    if not krk or not cb or len(history) < params["lookback_ticks"]: return False
    
    past = history[-params["lookback_ticks"]]
    past_cb = past["exchanges"]["coinbase"].get(product.replace("/", "-"))
    past_krk = past["exchanges"]["kraken"].get(product)
    
    if not past_cb or not past_krk: return False
    
    cb_move_bps = (cb["bid"] / past_cb["bid"] - 1.0) * 10000.0
    krk_move_bps = (krk["bid"] / past_krk["bid"] - 1.0) * 10000.0
    
    # If Coinbase moves 50bps but Kraken only moves 10bps, it's lagging
    return cb_move_bps > params["cb_move_bps"] and krk_move_bps < (cb_move_bps * 0.2)

ENTRY_MODULES = {
    "BridgeArb": (trigger_bridge_arb, [{"threshold_bps": 30}, {"threshold_bps": 50}]),
    "VultureDump": (trigger_vulture_dump, [{"lookback_ticks": 5, "threshold_bps": 40}]),
    "PriceConv": (trigger_price_convergence, [{"min_delta_bps": 20}]),
    "ToxicSniffer": (trigger_toxic_sniffer, [{"lookback_ticks": 5, "cb_move_bps": 50}, {"lookback_ticks": 10, "cb_move_bps": 100}]),
}

# --- 2. EXECUTION MODELS (ADVERSARIAL) ---
# Fees: Kraken (25bps Maker, 60bps Taker) + Coinbase (60-80bps Taker)
# Bridge Total Friction (Taker-Taker): ~140bps

def exec_maker_entry(tick: Dict, product: str) -> float:
    return tick["exchanges"]["kraken"][product]["bid"]

def exec_taker_entry(tick: Dict, product: str) -> float:
    return tick["exchanges"]["kraken"][product]["ask"]

def exec_taker_hedge(tick: Dict, product: str) -> float:
    cb_pid = product.replace("/", "-")
    return tick["exchanges"]["coinbase"][cb_pid]["bid"]

def exec_maker_entry_cb(tick: Dict, product: str) -> float:
    return tick["exchanges"]["coinbase"][product.replace("/", "-")]["bid"]

def exec_taker_hedge_krk(tick: Dict, product: str) -> float:
    return tick["exchanges"]["kraken"][product]["bid"]

EXECUTION_STYLES = {
    "Maker-Bridge": {"entry": exec_maker_entry, "hedge": exec_taker_hedge, "fee_bps": 100}, # 25(K) + 75(C)
    "Taker-Bridge": {"entry": exec_taker_entry, "hedge": exec_taker_hedge, "fee_bps": 140}, # 60(K) + 80(C)
    "Inverted-Bridge": {"entry": exec_maker_entry_cb, "hedge": exec_taker_hedge_krk, "fee_bps": 100}, # 25(C) + 75(K)
}

# --- 3. EXIT RULES ---
def exit_fixed_ttl(ticks_held: int, current_tick: Dict, params: Dict) -> bool:
    return ticks_held >= params["max_ticks"]

EXIT_MODULES = {
    "TTL_30s": (exit_fixed_ttl, {"max_ticks": 30}),
    "TTL_60s": (exit_fixed_ttl, {"max_ticks": 60}),
}

def generate_strategies(product: str) -> List[Dict]:
    strategies = []
    idx = 0
    for entry_name, (entry_func, entry_param_list) in ENTRY_MODULES.items():
        for entry_params in entry_param_list:
            entry_params["product"] = product
            for exec_name, exec_funcs in EXECUTION_STYLES.items():
                for exit_name, (exit_func, exit_params) in EXIT_MODULES.items():
                    strategies.append({
                        "id": f"STRAT_{idx}",
                        "name": f"{entry_name} | {exec_name} | {exit_name}",
                        "entry_func": entry_func,
                        "entry_params": entry_params,
                        "exec_style": exec_funcs,
                        "exit_func": exit_func,
                        "exit_params": exit_params
                    })
                    idx += 1
    return strategies

def run_backtest(strategies: List[Dict], tape: List[Dict], product: str):
    results = {s["id"]: {"name": s["name"], "trades": 0, "wins": 0, "net_bps": 0.0, "token_gain_bps": 0.0} for s in strategies}
    
    for s in strategies:
        in_position = False
        entry_krk = 0.0
        entry_cb = 0.0
        ticks_held = 0
        
        for i in range(10, len(tape)):
            current = tape[i]
            history = tape[:i]
            
            if not in_position:
                if s["entry_func"](history, current, s["entry_params"]):
                    entry_krk = s["exec_style"]["entry"](current, product)
                    entry_cb = s["exec_style"]["hedge"](current, product)
                    in_position = True
                    ticks_held = 0
            else:
                ticks_held += 1
                if s["exit_func"](ticks_held, current, s["exit_params"]) or i == len(tape)-1:
                    # Closing both legs
                    exit_krk = current["exchanges"]["kraken"][product]["bid"] # Taker Sell on Kraken
                    exit_cb = current["exchanges"]["coinbase"][product.replace("/","-")]["ask"] # Taker Buy on Coinbase
                    
                    # USD Profit = (Kraken PnL) + (Coinbase Hedge PnL) - Fees
                    krk_pnl = (exit_krk - entry_krk) / entry_krk
                    cb_pnl = (entry_cb - exit_cb) / entry_cb # Short leg
                    total_net_bps = (krk_pnl + cb_pnl) * 10000.0 - s["exec_style"]["fee_bps"]
                    
                    # TOKEN Gain (The 'Ratchet'): How many more units of Token do we have?
                    # If we buy Kraken floor and sell Coinbase high, we've increased our 'purchasing power' in Token units.
                    token_gain_bps = (entry_cb / exit_krk - 1.0) * 10000.0
                    
                    results[s["id"]]["trades"] += 1
                    if total_net_bps > 0:
                        results[s["id"]]["wins"] += 1
                    results[s["id"]]["net_bps"] += total_net_bps
                    results[s["id"]]["token_gain_bps"] += token_gain_bps
                    in_position = False
                    
    return results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape-path", default="reports/cache/dual_exchange_synced_tape.jsonl")
    args = parser.parse_args()
    
    tape_file = ROOT / args.tape_path
    if not tape_file.exists():
        print(f"Tape not found: {tape_file}")
        return
        
    print(f"Loading synced tape: {tape_file.name}...")
    tape = []
    with open(tape_file, "r") as f:
        for line in f:
            try: tape.append(json.loads(line))
            except: pass
            
    if not tape:
        print("Empty tape.")
        return

    # Backtest for each product in tape
    products = list(tape[0]["exchanges"]["kraken"].keys())
    
    for product in products:
        print(f"\n--- Results for {product} ---")
        strategies = generate_strategies(product)
        results = run_backtest(strategies, tape, product)
        
        sorted_res = sorted(results.values(), key=lambda x: x["net_bps"], reverse=True)
        for r in sorted_res[:5]:
            win_rate = (r["wins"] / r["trades"] * 100) if r["trades"] > 0 else 0
            print(f"{r['name']:<50} | Trades: {r['trades']:<4} | Win%: {win_rate:5.1f}% | Net: {r['net_bps']:8.1f} bps | UnitGain: {r['token_gain_bps']:8.1f} bps")

if __name__ == "__main__":
    main()
