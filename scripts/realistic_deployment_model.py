#!/usr/bin/env python3
"""Realistic Deployment Model: Capital-aware simulation of combined scorer signals.

Takes the 709 test signals and simulates realistic trading with:
1. Cycle compression: simultaneous signals → top-N positions per cycle
2. Kelly-optimal sizing with correlation penalty  
3. MFE capture discount (live fills capture close, not high)
4. Realistic fee model baked into the net_pct already
5. Compounding with drawdown tracking
"""
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TABLE_PATH = REPORTS / "coinbase_spot_fee_survival_training_table.csv"

# Constants
MFE_CAPTURE_DISCOUNT = 0.6  # Live captures ~60% of MFE (close vs high)
STARTING_CAPITAL = 100.0  # USD
MAX_POSITIONS_PER_CYCLE = 3  # Top-N per cycle
KELLY_FRACTION = 0.25  # Conservative Kelly (quarter-Kelly)


def main():
    print("=" * 80)
    print("REALISTIC DEPLOYMENT MODEL: Combined Scorer")
    print("=" * 80)
    print(f"\nAssumptions:")
    print(f"  MFE capture discount: {MFE_CAPTURE_DISCOUNT:.0%} (live captures close, not high)")
    print(f"  Kelly fraction: {KELLY_FRACTION:.0%} (quarter-Kelly)")
    print(f"  Max positions per cycle: {MAX_POSITIONS_PER_CYCLE}")
    print(f"  Starting capital: ${STARTING_CAPITAL:.2f}")
    print(f"  Fee already baked into net_pct: 2.4% (120bps × 2)")

    # Load the test set results from the combined scorer output
    # Instead of re-scoring, just work with the known 709 signals
    # We need to reconstruct them from the training table
    
    import joblib
    TAIL_MODEL = REPORTS / "models" / "coinbase_spot_tail_predictor.joblib"
    FG_MODEL = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"
    
    df = pd.read_csv(TABLE_PATH)
    split_at = int(len(df) * 0.75)
    test_df = df.iloc[split_at:].copy()
    
    # Find timestamp column
    ts_col = None
    for c in ["timestamp", "ts", "time", "datetime", "candle_time"]:
        if c in test_df.columns:
            ts_col = c
            break
    if ts_col is None:
        ts_col = test_df.columns[0]
    print(f"\nTimestamp column: '{ts_col}'")
    print(f"Available columns: {list(test_df.columns[:15])}")
    
    # Score with models
    tail_model = joblib.load(TAIL_MODEL)
    fg_model = joblib.load(FG_MODEL)
    
    tail_cat = tail_model["categorical"]
    tail_num = tail_model["numeric"]
    for col in tail_cat:
        test_df[col] = test_df[col].astype(str).fillna("")
    for col in tail_num:
        test_df[col] = pd.to_numeric(test_df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    test_df["tail_prob"] = tail_model["model"].predict_proba(test_df[tail_cat + tail_num])[:, 1]
    
    fg_cat = fg_model["categorical"]
    fg_num = fg_model["numeric"]
    for col in fg_cat:
        test_df[col] = test_df[col].astype(str).fillna("")
    for col in fg_num:
        test_df[col] = pd.to_numeric(test_df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    test_df["fg_prob"] = fg_model["model"].predict_proba(test_df[fg_cat + fg_num])[:, 1]
    
    # Apply combined filter
    mask = (test_df["tail_prob"] >= 0.95) & (test_df["fg_prob"] >= 0.90)
    signals = test_df[mask].copy()
    signals["net_realistic"] = signals["net_pct"] * MFE_CAPTURE_DISCOUNT
    
    print(f"Total test rows: {len(test_df):,}")
    print(f"Signals passing filter (tail≥0.95, fg≥0.90): {len(signals):,}")
    
    # Group by timestamp → execution cycles
    cycles = signals.groupby(ts_col).agg(
        n_signals=("net_pct", "count"),
        nets=("net_realistic", lambda x: list(x)),
    ).reset_index()
    cycles = cycles.sort_values(ts_col).reset_index(drop=True)
    
    print(f"\nExecution cycles: {len(cycles)}")
    print(f"Signals per cycle: mean={cycles['n_signals'].mean():.1f}, max={cycles['n_signals'].max()}, min={cycles['n_signals'].min()}")
    
    # Simulate deployment
    cash = STARTING_CAPITAL
    equity_curve = [STARTING_CAPITAL]
    trade_log = []
    
    # Correlation penalty estimate
    product_counts = signals.groupby("product_id").size()
    avg_per_product = product_counts.mean()
    correlation_penalty = min(0.5, avg_per_product / 100)
    print(f"\nCorrelation penalty: {correlation_penalty:.0%} (avg {avg_per_product:.0f} signals per product)")
    
    for idx, cycle in cycles.iterrows():
        n = int(cycle["n_signals"])
        nets = cycle["nets"]
        
        # Sort nets descending, take top-N
        nets_sorted = sorted(nets, reverse=True)
        if n <= MAX_POSITIONS_PER_CYCLE:
            cycle_net = sum(nets_sorted)
        else:
            cycle_net = sum(nets_sorted[:MAX_POSITIONS_PER_CYCLE])
        
        # Kelly sizing
        # Simple Kelly: fraction = edge / odds
        # For this cycle: edge = avg net, odds = variance
        avg_net_cycle = np.mean(nets)
        std_net_cycle = np.std(nets) if len(nets) > 1 else 1.0
        
        if std_net_cycle > 0:
            kelly_raw = avg_net_cycle / std_net_cycle  # Simplified Kelly
            kelly = max(0, min(kelly_raw, 0.5))  # Cap at 50%
        else:
            kelly = 0.1
        
        effective_kelly = kelly * KELLY_FRACTION * (1 - correlation_penalty)
        position_size = cash * effective_kelly
        
        # PnL
        cycle_pnl = position_size * (cycle_net / 100)
        cash += cycle_pnl
        cash = max(cash, 0)
        
        equity_curve.append(cash)
        trade_log.append({
            "cycle": idx,
            "n_signals": n,
            "kelly_raw": kelly,
            "effective_kelly": effective_kelly,
            "position_size": position_size,
            "cycle_net_pct": cycle_net,
            "cycle_pnl_usd": cycle_pnl,
            "equity": cash,
        })
    
    trade_df = pd.DataFrame(trade_log)
    
    # Results
    print(f"\n{'='*80}")
    print(f"DEPLOYMENT SIMULATION RESULTS")
    print(f"{'='*80}")
    
    final_equity = equity_curve[-1]
    total_return = (final_equity / STARTING_CAPITAL - 1) * 100
    n_cycles = len(trade_df)
    profitable_cycles = int((trade_df["cycle_pnl_usd"] > 0).sum())
    cycle_win_rate = profitable_cycles / n_cycles if n_cycles > 0 else 0
    
    max_equity = max(equity_curve)
    min_equity = min(equity_curve)
    max_drawdown = (max_equity - min_equity) / max_equity * 100 if max_equity > 0 else 0
    
    print(f"\nStarting capital: ${STARTING_CAPITAL:.2f}")
    print(f"Final equity: ${final_equity:.2f}")
    print(f"Total return: {total_return:.1f}%")
    print(f"Execution cycles: {n_cycles}")
    print(f"Profitable cycles: {profitable_cycles}/{n_cycles} ({cycle_win_rate:.1%})")
    print(f"Max drawdown: {max_drawdown:.1f}%")
    print(f"Max equity: ${max_equity:.2f}")
    print(f"Min equity: ${min_equity:.2f}")
    
    avg_pnl = trade_df["cycle_pnl_usd"].mean()
    std_pnl = trade_df["cycle_pnl_usd"].std()
    best_cycle = trade_df["cycle_pnl_usd"].max()
    worst_cycle = trade_df["cycle_pnl_usd"].min()
    
    print(f"\nPer-cycle PnL:")
    print(f"  Mean: ${avg_pnl:.2f}")
    print(f"  Std: ${std_pnl:.2f}")
    print(f"  Best: ${best_cycle:.2f}")
    print(f"  Worst: ${worst_cycle:.2f}")
    
    if std_pnl > 0:
        sharpe = avg_pnl / std_pnl
        print(f"  Sharpe (per cycle): {sharpe:.2f}")
    
    # Capital projections
    print(f"\n{'='*80}")
    print(f"CAPITAL PROJECTIONS (assuming same signal rate)")
    print(f"{'='*80}")
    daily_return_pct = total_return / 5.2  # 5.2 days of test data
    for starting in [100, 500, 1000, 5000, 10000]:
        scale = starting / STARTING_CAPITAL
        projected = final_equity * scale
        print(f"  ${starting:>6,} → ${projected:>10,.2f}  ({(projected/starting-1)*100:+.1f}%)")
    
    print(f"\n  Daily return rate (from test): {daily_return_pct:+.1f}%")
    print(f"  Days to 2x at this rate: {np.log(2) / np.log(1 + daily_return_pct/100):.0f}")
    print(f"  Days to 10x at this rate: {np.log(10) / np.log(1 + daily_return_pct/100):.0f}")
    
    # Honest comparison
    print(f"\n{'='*80}")
    print(f"HONEST ADJUSTMENT vs CLAIMED")
    print(f"{'='*80}")
    print(f"  CLAIMED: 99.9% win rate, +2.07% avg net, 709 signals, +1,470% cumulative")
    print(f"  ADJUSTED: {cycle_win_rate:.0%} cycle win rate, ${avg_pnl:.2f} avg PnL, {n_cycles} cycles, {total_return:.0f}% cumulative")
    print(f"\n  Key adjustments:")
    print(f"    1. MFE capture discount (60%): signals predict reachability, not realized")
    print(f"    2. Cycle compression: 709 signals → {n_cycles} allocation decisions")
    print(f"    3. Kelly sizing + correlation penalty: can't deploy 100% on every signal")
    print(f"    4. Fee model: 2.4% used (may understate by 0.25%)")
    print(f"\n  The combined scorer may still be positive. But shadow trading")
    print(f"  is the ONLY honest validation. Historical numbers are upper bounds.")
    print(f"{'='*80}")
    
    # Save
    output_path = REPORTS / "deployment_simulation_results.csv"
    trade_df.to_csv(output_path, index=False)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
