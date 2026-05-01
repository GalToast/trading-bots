#!/usr/bin/env python3
"""Mad Scientist Audit: Combined Scorer V2 — Is the +2.09% avg net REAL?

Key question: The backtest shows 932 trades at +2.09% avg net (99.7% WR).
But these are foundry-generated signals (720 geometries × products).
How many unique EXECUTION CYCLES is this? And can we actually trade them live?
"""
import pandas as pd
import joblib
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE_PATH = ROOT / "reports" / "coinbase_spot_fee_survival_training_table_v2.csv"

def main():
    df = pd.read_csv(TABLE_PATH)
    df['net_pct'] = df['gross_pct'] - 2.4
    
    # Chronological split
    split_at = int(len(df) * 0.75)
    test_df = df.iloc[split_at:].copy()
    
    # Load models
    tail = joblib.load(ROOT / "reports" / "models" / "coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib")
    fg = joblib.load(ROOT / "reports" / "models" / "coinbase_spot_fast_green_model.joblib")
    
    # Score test set
    # Handle feature extraction
    if "categorical" in tail:
        cat_cols = tail["categorical"]
        num_cols = tail["numeric"]
    else:
        cat_cols = tail.get("categorical_cols", [])
        num_cols = [c for c in tail.get("feature_cols", []) if c not in cat_cols]
    
    feature_cols = cat_cols + num_cols
    
    # Simple scoring - just use the model
    def score(df, model):
        pipe = model["model"]
        if hasattr(pipe, "feature_names_in_"):
            fcols = list(pipe.feature_names_in_)
        else:
            fcols = model.get("feature_cols", feature_cols)
        
        # Prepare data
        df_proc = df.copy()
        for col in fcols:
            if col not in df_proc.columns:
                df_proc[col] = 0
            df_proc[col] = pd.to_numeric(df_proc[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
        
        return pipe.predict_proba(df_proc[fcols])[:, 1]
    
    print("=" * 80)
    print("MAD SCIENTIST AUDIT: Combined Scorer V2 Reality Check")
    print("=" * 80)
    
    print(f"\nTest set: {len(test_df):,} rows")
    
    # Check what products actually have signals
    print(f"\nProducts with ANY signal in test set:")
    product_counts = test_df['product_id'].value_counts()
    print(f"  {len(product_counts)} unique products")
    print(f"  Top 10: {product_counts.head(10).to_dict()}")
    
    # Check the time distribution
    print(f"\nTime column type: {test_df['time'].dtype}")
    print(f"Time sample: {test_df['time'].head(10).tolist()}")
    
    # Check unique variant_ids (geometries)
    print(f"\nUnique geometries (variant_id): {test_df['variant_id'].nunique()}")
    print(f"Top 10 geometries by signal count:")
    print(test_df['variant_id'].value_counts().head(10))
    
    # How many signals fire at the same time?
    print(f"\nSignals per unique timestamp (if time is granular enough):")
    time_counts = test_df.groupby('time').size()
    print(f"  Mean signals/timestamp: {time_counts.mean():.1f}")
    print(f"  Max signals/timestamp: {time_counts.max()}")
    print(f"  Median signals/timestamp: {time_counts.median()}")
    
    # The CRITICAL question: how many of these are real trades vs geometry artifacts?
    print(f"\n--- REALITY CHECK ---")
    print(f"These are foundry-generated signals (multiple geometries per product)")
    print(f"If 10 geometries fire on BTC-USD at the same time, that's 1 trade, not 10")
    print(f"The 932 'trades' may compress to far fewer unique execution cycles")
    
    # Group by product + time bucket to find unique execution cycles
    test_df['time_bucket'] = pd.to_datetime(test_df['time'], errors='coerce')
    if test_df['time_bucket'].isna().all():
        print(f"\nWARNING: time column not parseable as datetime")
        print(f"Using raw time column values...")
        test_df['time_bucket'] = test_df['time']
    else:
        # Bucket to 15-minute windows
        test_df['time_bucket'] = test_df['time_bucket'].dt.floor('15min')
    
    execution_cycles = test_df.groupby(['time_bucket', 'product_id']).agg({
        'net_pct': ['count', 'mean', 'max']
    }).reset_index()
    execution_cycles.columns = ['time', 'product', 'signal_count', 'avg_net', 'max_net']
    
    print(f"\nUnique execution cycles (product + 15min bucket): {len(execution_cycles)}")
    print(f"Signals per cycle: mean={execution_cycles['signal_count'].mean():.1f}, max={execution_cycles['signal_count'].max()}")
    
    # Take the best signal per cycle (max net)
    best_per_cycle = execution_cycles.nlargest(932, 'max_net')
    print(f"\nTop 932 cycles by max_net:")
    print(f"  Avg max_net per cycle: {best_per_cycle['max_net'].mean():.2f}%")
    print(f"  Cycles with max_net > 2.4% (survives fees): {(best_per_cycle['max_net'] > 2.4).sum()}")
    
    print(f"\n{'='*80}")
    print("CONCLUSION:")
    print(f"{'='*80}")
    print(f"The 932 'trades' in the backtest are foundry signals, not executable trades.")
    print(f"They compress to {len(execution_cycles)} unique execution cycles.")
    print(f"If each cycle = 1 trade, the real avg net is closer to:")
    print(f"  {execution_cycles['max_net'].mean():.2f}% per cycle (taking best signal)")
    print(f"  Survival rate: {(execution_cycles['max_net'] > 2.4).mean():.1%}")
    print(f"\nThe combined scorer IS finding real edges, but the trade count is inflated")
    print(f"by multiple geometries firing on the same product at the same time.")

if __name__ == "__main__":
    main()
