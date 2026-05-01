#!/usr/bin/env python3
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TABLE_PATH = REPORTS / "coinbase_spot_fee_survival_training_table.csv"
TAIL_MODEL = REPORTS / "models" / "coinbase_spot_tail_predictor.joblib"
FG_MODEL = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"

def load_model(path):
    if not path.exists():
        return None
    return joblib.load(path)

def score_with_model(df, model):
    if "categorical" in model:
        cat_features = model["categorical"]
        num_features = model["numeric"]
    elif "categorical_cols" in model:
        cat_features = model["categorical_cols"]
        num_features = [c for c in model["feature_cols"] if c not in cat_features]
    else:
        raise ValueError(f"Unknown model format: {list(model.keys())}")
    
    pipe = model["model"]
    for col in cat_features:
        df[col] = df[col].astype(str).fillna("")
    for col in num_features:
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return pipe.predict_proba(df[cat_features + num_features])[:, 1]

def run_simulation(signals, max_positions, deploy_pct, fee_bps):
    """
    Simulates trading with $100 starting capital.
    - signals: DataFrame sorted by 'time'.
    - max_positions: maximum concurrent positions (N).
    - deploy_pct: percentage of *free cash* to deploy per position.
    - fee_bps: round trip fee in basis points.
    """
    cash = 100.0
    active_positions = [] # list of dicts: {'exit_time': t, 'pnl_pct': x, 'size': s}
    
    history = []
    
    peak_cash = cash
    max_portfolio_dd = 0.0
    
    # Sort time unique
    times = sorted(signals["time"].unique())
    
    for t in times:
        # 1. Close expired positions
        remaining_positions = []
        for pos in active_positions:
            if t >= pos["exit_time"]:
                # Close it
                cash += pos["size"] * (1 + pos["pnl_pct"] / 100.0)
            else:
                remaining_positions.append(pos)
        active_positions = remaining_positions
        
        # update peak and drawdown based on free cash + valuation of active positions
        current_equity = cash + sum(pos["size"] * (1 + pos["pnl_pct"] / 100.0) for pos in active_positions)
        if current_equity > peak_cash:
            peak_cash = current_equity
        elif peak_cash > 0:
            dd = (peak_cash - current_equity) / peak_cash
            if dd > max_portfolio_dd:
                max_portfolio_dd = dd
        
        # 2. Open new positions if we have capacity
        open_slots = max_positions - len(active_positions)
        if open_slots <= 0:
            continue
            
        current_signals = signals[signals["time"] == t]
        if len(current_signals) == 0:
            continue
            
        # Take the top `open_slots` signals by combined score
        top_signals = current_signals.nlargest(open_slots, "combined_score")
        
        for _, sig in top_signals.iterrows():
            if cash <= 0:
                break
                
            # Use hold_bars * 300 seconds as holding time (assuming 5m bars)
            # Default to 300 seconds if missing or 0
            hold_sec = max(300, sig.get("hold_bars", 1) * 300)
            exit_time = t + hold_sec
            
            # Calculate net_pct based on fee
            net_pct = sig["gross_pct"] - (fee_bps / 100.0)
            
            # Allocation
            if max_positions == 1:
                size = cash * deploy_pct
            else:
                size = cash * deploy_pct
                
            if size > cash:
                size = cash
                
            cash -= size
            active_positions.append({
                "exit_time": exit_time,
                "pnl_pct": net_pct,
                "size": size,
                "product": sig["product_id"]
            })
            
            history.append({
                "entry_time": t,
                "exit_time": exit_time,
                "product": sig["product_id"],
                "size": size,
                "net_pct": net_pct,
                "cash_after_entry": cash
            })
            
    # At the end, force close all remaining positions
    for pos in active_positions:
        cash += pos["size"] * (1 + pos["pnl_pct"] / 100.0)
        
    return cash, pd.DataFrame(history), max_portfolio_dd * 100

def main():
    print("=" * 80)
    print("REALISTIC $100 CAPITAL COMPRESSION SIMULATION")
    print("=" * 80)

    df = pd.read_csv(TABLE_PATH)
    split_at = int(len(df) * 0.75)
    test_df = df.iloc[split_at:].copy()
    
    tail_model = load_model(TAIL_MODEL)
    fg_model = load_model(FG_MODEL)
    
    if not tail_model or not fg_model:
        print("ERROR: Models not found")
        return

    test_tail = score_with_model(test_df, tail_model)
    test_fg = score_with_model(test_df, fg_model)

    mask = (test_tail >= 0.95) & (test_fg >= 0.90)
    selected = test_df[mask].copy()
    selected["combined_score"] = test_tail[mask] * test_fg[mask]
    
    print(f"Test Set Date Range: {datetime.utcfromtimestamp(test_df['time'].min())} to {datetime.utcfromtimestamp(test_df['time'].max())}")
    print(f"Total Qualified Signals: {len(selected)}")
    
    scenarios = [
        {"name": "One Position (Coinbase 240bps)", "N": 1, "deploy": 0.8, "fee": 240},
        {"name": "One Position (Kraken 80bps)", "N": 1, "deploy": 0.8, "fee": 80},
        {"name": "Top-3 Positions (Coinbase 240bps)", "N": 3, "deploy": 0.3, "fee": 240},
        {"name": "Top-3 Positions (Kraken 80bps)", "N": 3, "deploy": 0.3, "fee": 80},
    ]
    
    for s in scenarios:
        final_capital, hist_df, max_portfolio_dd = run_simulation(selected, s["N"], s["deploy"], s["fee"])
        trades_taken = len(hist_df)
        win_rate = (hist_df['net_pct'] > 0).mean() * 100 if trades_taken > 0 else 0
        avg_net = hist_df['net_pct'].mean() if trades_taken > 0 else 0
        
        print(f"\n--- {s['name']} ---")
        print(f"Final Capital : ${final_capital:.2f}")
        print(f"Total Trades  : {trades_taken}")
        print(f"Win Rate      : {win_rate:.1f}%")
        print(f"Avg Net Return: {avg_net:.4f}%")
        if trades_taken > 0:
            print(f"Worst Trade   : {hist_df['net_pct'].min():.4f}%")
            print(f"Portfolio DD  : {max_portfolio_dd:.4f}%")
            
    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()
