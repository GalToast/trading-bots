#!/usr/bin/env python3
"""Compress the 709 combined-scorer trades into unique execution slots.

The 709 test trades likely include many simultaneous signals at the same timestamp.
In live trading, we'd only take the top-ranked signal per cycle.
This script counts unique execution slots and simulates one-position-per-cycle.
"""
import joblib
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TABLE_PATH = ROOT / "reports" / "coinbase_spot_fee_survival_training_table.csv"
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

def main():
    print("=" * 80)
    print("EXECUTION SLOT COMPRESSION — Combined Scorer Trades")
    print("=" * 80)

    df = pd.read_csv(TABLE_PATH)
    # Coinbase fees: 240bps round-trip
    df["net_pct_coinbase"] = df["gross_pct"] - 2.4
    # Kraken fees: 80bps round-trip (40bps taker x2)
    df["net_pct_kraken"] = df["gross_pct"] - 0.8
    # Default to Coinbase for compatibility
    df["net_pct"] = df["net_pct_coinbase"]

    split_at = int(len(df) * 0.75)
    test_df = df.iloc[split_at:].copy()
    print(f"Test set: {len(test_df):,} rows")

    tail_model = load_model(TAIL_MODEL)
    fg_model = load_model(FG_MODEL)
    
    if not tail_model or not fg_model:
        print("ERROR: Models not found")
        return

    print("\nScoring test set...")
    test_tail = score_with_model(test_df, tail_model)
    test_fg = score_with_model(test_df, fg_model)

    # Best combo: Tail≥0.95, FG≥0.90
    mask = (test_tail >= 0.95) & (test_fg >= 0.90)
    selected = test_df[mask].copy()
    selected["tail_prob"] = test_tail[mask]
    selected["fg_prob"] = test_fg[mask]

    print(f"\nRaw trades at Tail≥0.95, FG≥0.90: {len(selected):,}")
    print(f"Unique timestamps: {selected['time'].nunique():,}")
    print(f"Unique product_id × time: {(selected['product_id'] + '_' + selected['time'].astype(str)).nunique():,}")

    # Group by timestamp and take the BEST signal per cycle
    print("\n" + "=" * 80)
    print("ONE-POSITION PER CYCLE (rank by tail_prob × fg_prob)")
    print("=" * 80)

    selected["combined_score"] = selected["tail_prob"] * selected["fg_prob"]
    
    # Group by time (execution cycle)
    cycles = selected.groupby("time").apply(
        lambda g: g.loc[g["combined_score"].idxmax()]
    ).reset_index(drop=True)

    print(f"Unique execution cycles: {len(cycles):,}")
    print(f"Average signals per cycle: {len(selected)/len(cycles):.1f}")
    print(f"Avg net per cycle: {cycles['net_pct'].mean():.4f}%")
    print(f"Cumulative net: {cycles['net_pct'].sum():.2f}%")
    print(f"Win rate: {(cycles['net_pct'] > 0).mean()*100:.1f}%")
    print(f"Best cycle: {cycles['net_pct'].max():.4f}%")
    print(f"Worst cycle: {cycles['net_pct'].min():.4f}%")

    # Time span
    min_time = cycles["time"].min()
    max_time = cycles["time"].max()
    # The test set is the last 25% of data — need to estimate the time span
    # From the training table, the time values are unix timestamps
    from datetime import datetime
    min_dt = datetime.utcfromtimestamp(min_time)
    max_dt = datetime.utcfromtimestamp(max_time)
    span_days = (max_dt - min_dt).total_seconds() / 86400
    
    print(f"\nTime span: {min_dt} → {max_dt}")
    print(f"Span: {span_days:.1f} days")
    print(f"Cycles per day: {len(cycles)/max(span_days, 1):.1f}")
    print(f"Daily return: {(cycles['net_pct'].sum() / max(span_days, 1)):.2f}%")
    
    if span_days > 0:
        daily_return = cycles['net_pct'].sum() / span_days
        days_to_4x = np.log(4) / np.log(1 + daily_return / 100) if daily_return > 0 else float('inf')
        print(f"Days to 4x: {days_to_4x:.0f}")

    # Also test: one position per cycle per product
    print(f"\n{'='*80}")
    print("ONE-POSITION PER CYCLE PER PRODUCT")
    print(f"{'='*80}")
    
    selected["product_time"] = selected["product_id"] + "_" + selected["time"].astype(str)
    product_cycles = selected.groupby("product_time").apply(
        lambda g: g.loc[g["combined_score"].idxmax()]
    ).reset_index(drop=True)
    
    print(f"Unique product-cycle slots: {len(product_cycles):,}")
    print(f"Avg net per slot: {product_cycles['net_pct'].mean():.4f}%")
    print(f"Cumulative net: {product_cycles['net_pct'].sum():.2f}%")
    print(f"Win rate: {(product_cycles['net_pct'] > 0).mean()*100:.1f}%")

    # Also check: what if we use the top N signals per cycle?
    print(f"\n{'='*80}")
    print("TOP-N SIGNALS PER CYCLE")
    print(f"{'='*80}")

    for n in [1, 2, 3, 5]:
        top_n_per_cycle = selected.groupby("time").apply(
            lambda g: g.nlargest(n, "combined_score")
        ).reset_index(drop=True)

        print(f"  Top {n} per cycle: {len(top_n_per_cycle):,} signals, "
              f"avg net {top_n_per_cycle['net_pct'].mean():.4f}%, "
              f"cum {top_n_per_cycle['net_pct'].sum():.2f}%, "
              f"win {(top_n_per_cycle['net_pct']>0).mean()*100:.1f}%")

    # COINBASE vs KRAKEN COMPARISON
    print(f"\n{'='*80}")
    print(f"COINBASE (240bps) vs KRAKEN (80bps) — One Position Per Cycle")
    print(f"{'='*80}")

    cycles_kraken = selected.copy()
    cycles_kraken["net_pct"] = cycles_kraken["net_pct_kraken"]
    cycles_kraken = cycles_kraken.groupby("time").apply(
        lambda g: g.loc[g["combined_score"].idxmax()]
    ).reset_index(drop=True)

    print(f"  {'Metric':<25} {'Coinbase (240bps)':>20} {'Kraken (80bps)':>20} {'Delta':>10}")
    print(f"  {'-'*77}")
    
    cb_cum = cycles["net_pct_coinbase"].sum()
    kr_cum = cycles_kraken["net_pct_kraken"].sum()
    cb_avg = cycles["net_pct_coinbase"].mean()
    kr_avg = cycles_kraken["net_pct_kraken"].mean()
    cb_win = (cycles["net_pct_coinbase"] > 0).mean() * 100
    kr_win = (cycles_kraken["net_pct_kraken"] > 0).mean() * 100
    cb_worst = cycles["net_pct_coinbase"].min()
    kr_worst = cycles_kraken["net_pct_kraken"].min()
    
    print(f"  {'Cumulative net %':<25} {cb_cum:>20.2f} {kr_cum:>20.2f} {kr_cum - cb_cum:>10.2f}")
    print(f"  {'Avg net per cycle %':<25} {cb_avg:>20.4f} {kr_avg:>20.4f} {kr_avg - cb_avg:>10.4f}")
    print(f"  {'Win rate %':<25} {cb_win:>20.1f} {kr_win:>20.1f} {kr_win - cb_win:>10.1f}")
    print(f"  {'Worst cycle %':<25} {cb_worst:>20.4f} {kr_worst:>20.4f} {kr_worst - cb_worst:>10.4f}")
    
    if span_days > 0:
        cb_daily = cb_cum / span_days
        kr_daily = kr_cum / span_days
        cb_4x = np.log(4) / np.log(1 + cb_daily / 100) if cb_daily > 0 else float('inf')
        kr_4x = np.log(4) / np.log(1 + kr_daily / 100) if kr_daily > 0 else float('inf')
        print(f"  {'Daily return %':<25} {cb_daily:>20.2f} {kr_daily:>20.2f} {kr_daily - cb_daily:>10.2f}")
        print(f"  {'Days to 4x':<25} {cb_4x:>20.0f} {kr_4x:>20.0f} {cb_4x - kr_4x:>10.0f}")
    
    # Show which cycles flip from negative to positive with Kraken fees
    flipped = cycles_kraken[
        (cycles_kraken["net_pct_kraken"] > 0) & (cycles_kraken["net_pct_coinbase"] < 0)
    ]
    if len(flipped) > 0:
        print(f"\n  Cycles that flip from LOSS → PROFIT with Kraken fees: {len(flipped)}")
        for _, row in flipped.iterrows():
            print(f"    {row['product_id']} @ {datetime.utcfromtimestamp(row['time'])}: "
                  f"Coinbase {row['net_pct_coinbase']:.4f}% → Kraken {row['net_pct_kraken']:.4f}%")
    else:
        print(f"\n  No cycles flip from loss → profit (all already profitable at Coinbase fees)")

    print(f"\n{'='*80}")
    print("DONE")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
