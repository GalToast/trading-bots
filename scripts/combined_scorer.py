#!/usr/bin/env python3
"""Combined Scorer: Intersection of Tail Predictor + Fast-Green models.

CRITICAL: Reports results on the CHRONOLOGICAL TEST SET ONLY (last 25%).
The full-table results are shown for context but are overfit.
"""
import json
import joblib
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TABLE_PATH = REPORTS / "coinbase_spot_fee_survival_training_table.csv"
TAIL_MODEL = REPORTS / "models" / "coinbase_spot_tail_predictor.joblib"
FAST_GREEN_MODEL = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"

def load_model(path):
    if not path.exists():
        return None
    return joblib.load(path)

def score_with_model(df, model):
    cat_features = model["categorical"]
    num_features = model["numeric"]
    pipe = model["model"]
    for col in cat_features:
        df[col] = df[col].astype(str).fillna("")
    for col in num_features:
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return pipe.predict_proba(df[cat_features + num_features])[:, 1]

def main():
    print("=" * 80)
    print("COMBINED SCORER: Tail Predictor + Fast-Green Intersection")
    print("=" * 80)
    
    df = pd.read_csv(TABLE_PATH)
    df["net_pct"] = df["gross_pct"] - 2.4
    
    # Chronological split (same as model training: 75/25)
    split_at = int(len(df) * 0.75)
    train_df = df.iloc[:split_at].copy()
    test_df = df.iloc[split_at:].copy()
    print(f"Total: {len(df):,} | Train: {len(train_df):,} | Test: {len(test_df):,}")
    
    tail_model = load_model(TAIL_MODEL)
    fg_model = load_model(FAST_GREEN_MODEL)
    
    if not tail_model or not fg_model:
        print("ERROR: One or both models not found.")
        return
    
    # Score train and test separately
    print("\nScoring TRAIN set...")
    train_tail = score_with_model(train_df, tail_model)
    train_fg = score_with_model(train_df, fg_model)
    print(f"  Tail mean: {train_tail.mean():.4f}, FG mean: {train_fg.mean():.4f}")
    
    print("Scoring TEST set...")
    test_tail = score_with_model(test_df, tail_model)
    test_fg = score_with_model(test_df, fg_model)
    print(f"  Tail mean: {test_tail.mean():.4f}, FG mean: {test_fg.mean():.4f}")
    
    # Test all threshold combinations on TEST SET
    thresholds = [0.50, 0.70, 0.80, 0.90, 0.95, 0.98]
    results = []
    
    for tt in thresholds:
        for ft in thresholds:
            mask = (test_tail >= tt) & (test_fg >= ft)
            n = int(mask.sum())
            if n == 0:
                continue
            sel = test_df[mask]
            results.append({
                "tail_p": tt, "fg_p": ft, "n": n,
                "cum_net": float(sel["net_pct"].sum()),
                "avg_net": float(sel["net_pct"].mean()),
                "avg_gross": float(sel["gross_pct"].mean()),
                "win_rate": float((sel["net_pct"] > 0).mean()),
                "best": float(sel["net_pct"].max()),
                "worst": float(sel["net_pct"].min()),
            })
    
    print(f"\n{'='*80}")
    print(f"TEST SET ONLY (out-of-sample, {len(test_df):,} rows)")
    print(f"{'='*80}")
    print(f"{'TailP':>6} {'FGP':>6} {'N':>6} {'CumNet':>10} {'AvgNet':>10} {'Win%':>7} {'Best':>8} {'Worst':>8}")
    print("-" * 80)
    
    for r in sorted(results, key=lambda x: x["avg_net"], reverse=True):
        print(f"{r['tail_p']:>6.2f} {r['fg_p']:>6.2f} {r['n']:>6} "
              f"{r['cum_net']:>10.2f} {r['avg_net']:>10.4f} {r['win_rate']:>7.1%} "
              f"{r['best']:>8.4f} {r['worst']:>8.4f}")
    
    # Best combos
    viable = [r for r in results if r["n"] >= 5]
    if viable:
        best_avg = max(viable, key=lambda x: x["avg_net"])
        best_cum = max(viable, key=lambda x: x["cum_net"])
        
        print(f"\n{'='*80}")
        print(f"BEST BY AVG NET (≥5 trades):")
        print(f"  Tail≥{best_avg['tail_p']:.2f}, FG≥{best_avg['fg_p']:.2f}")
        print(f"  {best_avg['n']} trades, avg {best_avg['avg_net']:.4f}%, cum {best_avg['cum_net']:.2f}%")
        print(f"  Win rate: {best_avg['win_rate']:.1%}, ~{best_avg['n']/30:.1f}/day")
        
        print(f"\nBEST BY CUMULATIVE (≥5 trades):")
        print(f"  Tail≥{best_cum['tail_p']:.2f}, FG≥{best_cum['fg_p']:.2f}")
        print(f"  {best_cum['n']} trades, avg {best_cum['avg_net']:.4f}%, cum {best_cum['cum_net']:.2f}%")
        print(f"  Win rate: {best_cum['win_rate']:.1%}")
    
    # Compare train vs test for the best combo (overfit check)
    if viable:
        ref = best_avg
        train_mask = (train_tail >= ref["tail_p"]) & (train_fg >= ref["fg_p"])
        test_mask = (test_tail >= ref["tail_p"]) & (test_fg >= ref["fg_p"])
        train_sel = train_df[train_mask]
        test_sel = test_df[test_mask]
        
        print(f"\n{'='*80}")
        print(f"OVERFIT CHECK (Tail≥{ref['tail_p']:.2f}, FG≥{ref['fg_p']:.2f}):")
        print(f"  Train: {len(train_sel)} trades, avg {train_sel['net_pct'].mean():.4f}%, cum {train_sel['net_pct'].sum():.2f}%")
        print(f"  Test:  {len(test_sel)} trades, avg {test_sel['net_pct'].mean():.4f}%, cum {test_sel['net_pct'].sum():.2f}%")
        ratio = test_sel["net_pct"].mean() / train_sel["net_pct"].mean() if train_sel["net_pct"].mean() != 0 else 0
        print(f"  Retention: {ratio:.1%} (1.0 = no overfit, <0.5 = overfit)")
    
    print(f"\n{'='*80}")
    print("DONE")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
