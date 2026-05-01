#!/usr/bin/env python3
"""Lattice Perfection Lab - Multivariate Parameter Sweep (Step vs Alpha)
Uses 30 days of M1 history to find the 'Perfect' configuration for each symbol.
"""
import MetaTrader5 as mt5
import json
import csv
import time
import pandas as pd
import argparse
from pathlib import Path
from dataclasses import asdict
from datetime import datetime, timezone

# Ensure the core is importable
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "scripts"))

from tick_penetration_lattice_core import TickStatefulRearmEngine, RawConfig, VOLUME
from live_penetration_lattice_shadow import REARM_VARIANTS

# CONFIGURATION
VARIANT = REARM_VARIANTS["rearm_lvl2_exc2"]

REPORT_DIR = ROOT / "reports" / "perfection_lab"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

class MockActionSink:
    def __init__(self, symbol):
        self.symbol = symbol
        self.total_pnl = 0.0

    def __call__(self, request):
        if request["kind"] == "open":
            return {"ok": True, "fill_price": request["fill_price"]}
        elif request["kind"] == "close":
            return {"ok": True, "fill_price": request["fill_price"]}
        return {"ok": False}

def get_atr(bars, period=14):
    df = pd.DataFrame(bars)
    df['h_l'] = df['high'] - df['low']
    df['h_pc'] = abs(df['high'] - df['close'].shift(1))
    df['l_pc'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['h_l', 'h_pc', 'l_pc']].max(axis=1)
    return df['tr'].rolling(window=period).mean().iloc[-1]

def run_sim(symbol, bars, step_atr_mult, alpha):
    info = mt5.symbol_info(symbol)
    if info is None: return None
    
    atr = get_atr(bars)
    step_pips = (atr * step_atr_mult) / (info.point * 10) # rough pips
    if symbol in ["US30", "NAS100", "XAUUSD"]: # Price units
        step_val = atr * step_atr_mult
        cfg = RawConfig(step_pips=step_val, max_open_per_side=25, close_mode="two_level", step_is_price_units=True)
    else: # FX Pips
        cfg = RawConfig(step_pips=step_pips, max_open_per_side=25, close_mode="two_level")

    engine = TickStatefulRearmEngine(
        symbol=symbol,
        cfg=cfg,
        symbol_info=info,
        timeframe_name="M5",
        variant=VARIANT,
        close_alpha=alpha,
        max_floating_loss_usd=-50.0 
    )
    
    sink = MockActionSink(symbol)
    
    for bar in bars:
        price_seq = [bar['open'], bar['high'], bar['low'], bar['close']] 
        for p in price_seq:
            tick = {
                "time": bar['time'],
                "time_msc": bar['time'] * 1000,
                "bid": p,
                "ask": p + (info.spread * info.point),
                "last": p
            }
            engine.process_tick(tick, action_sink=sink, emit=False)
            
    res = engine.state
    stability = 1.0 / (res.anchor_resets_risk + 1)
    score = (res.realized_net_usd / 50.0) * stability 
    
    return {
        "symbol": symbol,
        "step_mult": step_atr_mult,
        "alpha": alpha,
        "pnl": round(res.realized_net_usd, 2),
        "closes": res.realized_closes,
        "resets_flat": res.anchor_resets_flat,
        "resets_risk": res.anchor_resets_risk,
        "score": round(score, 4)
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str, default="US30,NAS100,XAUUSD,GBPUSD,USDJPY", help="Comma-separated symbols")
    ap.add_argument("--steps", type=str, default="0.8,1.0,1.25,1.55,1.8,2.1", help="Comma-separated step coefficients")
    ap.add_argument("--alphas", type=str, default="0.5,0.8,1.0,1.2,1.5", help="Comma-separated close alphas")
    ap.add_argument("--days", type=int, default=7, help="Days of history to test")
    ap.add_argument("--label", type=str, default="sweep", help="Label for output")
    args = ap.parse_args()

    symbols_list = args.symbols.split(",")
    steps = [float(s) for s in args.steps.split(",")]
    alphas = [float(a) for a in args.alphas.split(",")]
    lookback_bars = args.days * 1440

    if not mt5.initialize():
        print("MT5 Init Failed")
        return

    results = []
    for sym in symbols_list:
        print(f"--- Fetching data for {sym} ---")
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, lookback_bars)
        if rates is None or len(rates) == 0:
            print(f"Skipping {sym}: No data")
            continue
        
        bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rates]
        
        total_sims = len(steps) * len(alphas)
        sim_count = 0
        for step_mult in steps:
            for alpha in alphas:
                sim_count += 1
                t0 = time.time()
                res = run_sim(sym, bars, step_mult, alpha)
                if res:
                    results.append(res)
                    print(f"[{sym}] ({sim_count}/{total_sims}) Step={step_mult} Alpha={alpha} -> PnL=${res['pnl']} RiskResets={res['resets_risk']} Score={res['score']} ({time.time()-t0:.2f}s)", flush=True)

    # Save Results
    df = pd.DataFrame(results)
    matrix_csv = REPORT_DIR / f"{args.label}_matrix.csv"
    df.to_csv(matrix_csv, index=False)
    
    # Generate Summary Report
    report_md = REPORT_DIR / f"{args.label}_report.md"
    with open(report_md, "w") as f:
        f.write(f"# Lattice Perfection Report: {args.label}\n\nGenerated: {datetime.now(timezone.utc).isoformat()}\n\n")
        f.write("## Perfection Champions per Symbol\n\n")
        f.write("| Symbol | Step Multiplier | Close Alpha | Net PnL | Risk Resets | Perfection Score |\n")
        f.write("| --- | --- | --- | --- | --- | --- |\n")
        
        for sym in symbols_list:
            sym_df = df[df['symbol'] == sym]
            if sym_df.empty: continue
            champion = sym_df.loc[sym_df['score'].idxmax()]
            f.write(f"| {sym} | {champion['step_mult']} | {champion['alpha']} | ${champion['pnl']} | {champion['resets_risk']} | **{champion['score']}** |\n")
        
        f.write("\n\n*Quality Score = (Net / Max Risk) * (1 / (Risk_Resets + 1))*\n")

    print(f"\nDone! Report saved to {report_md}")
    mt5.shutdown()

if __name__ == "__main__":
    main()
