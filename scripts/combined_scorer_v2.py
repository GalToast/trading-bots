#!/usr/bin/env python3
"""Combined Scorer V2: opencode's V2 FIXED tail model + fast-green.

V2 FIXED has AUC=0.9944 (vs my v1 at 0.657) thanks to temporal features.
This should produce MUCH better intersection results.
"""
import joblib
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TABLE_PATH = ROOT / "reports" / "coinbase_spot_fee_survival_training_table_v2.csv"
TAIL_V2 = REPORTS / "models" / "coinbase_spot_high_gross_tail_predictor_v2_fixed.joblib"
FG_MODEL = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"

# Fallback to v1 table if v2 doesn't exist
if not TABLE_PATH.exists():
    TABLE_PATH = ROOT / "reports" / "coinbase_spot_fee_survival_training_table.csv"

def load_model(path):
    if not path.exists():
        return None
    return joblib.load(path)

def score_with_model(df, model):
    # Handle both model formats
    if "categorical" in model:
        cat_features = model["categorical"]
        num_features = model["numeric"]
    elif "categorical_cols" in model:
        cat_features = model["categorical_cols"]
        num_features = [c for c in model["feature_cols"] if c not in cat_features]
    else:
        raise ValueError(f"Unknown model format: {list(model.keys())}")
    
    pipe = model["model"]
    encoders = model.get("encoders", {})
    
    # Use truth from the model object itself if possible
    if hasattr(pipe, "feature_names_in_"):
        feature_cols = list(pipe.feature_names_in_)
    elif "feature_cols" in model:
        feature_cols = model["feature_cols"]
    else:
        feature_cols = model["categorical"] + model["numeric"]
    
    # Preprocess
    for col in feature_cols:
        if col in encoders:
            # Use the saved LabelEncoder
            le = encoders[col]
            # Handle unseen categories by mapping to a default (usually 0 or most frequent)
            # Or just use transform if we trust the data
            try:
                df[col] = le.transform(df[col].astype(str).fillna("unknown"))
            except:
                # Fallback: map to 0
                df[col] = 0
        elif col in model.get("categorical", []) or col in model.get("categorical_cols", []):
            df[col] = df[col].astype(str).fillna("")
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
            
    return pipe.predict_proba(df[feature_cols])[:, 1]

def main():
    print("=" * 80)
    print("COMBINED SCORER V2: opencode V2 FIXED Tail + Fast-Green")
    print("=" * 80)

    df = pd.read_csv(TABLE_PATH)
    df["net_pct"] = df["gross_pct"] - 2.4

    # Chronological split
    split_at = int(len(df) * 0.75)
    train_df = df.iloc[:split_at].copy()
    test_df = df.iloc[split_at:].copy()
    print(f"Total: {len(df):,} | Train: {len(train_df):,} | Test: {len(test_df):,}")

    tail_v2 = load_model(TAIL_V2)
    fg_model = load_model(FG_MODEL)

    if not tail_v2 or not fg_model:
        print(f"ERROR: Tail V2={tail_v2 is not None}, FG={fg_model is not None}")
        return

    print("\nScoring TRAIN with V2 FIXED tail model...")
    train_tail = score_with_model(train_df, tail_v2)
    train_fg = score_with_model(train_df, fg_model)
    print(f"  Tail mean: {train_tail.mean():.4f}, FG mean: {train_fg.mean():.4f}")

    print("Scoring TEST with V2 FIXED tail model...")
    test_tail = score_with_model(test_df, tail_v2)
    test_fg = score_with_model(test_df, fg_model)
    print(f"  Tail mean: {test_tail.mean():.4f}, FG mean: {test_fg.mean():.4f}")

    # Test threshold combinations
    tail_thresholds = [0.50, 0.70, 0.80, 0.90, 0.95, 0.98, 0.99]
    fg_thresholds = [0.50, 0.70, 0.80, 0.90, 0.95, 0.98]

    results = []
    for tt in tail_thresholds:
        for ft in fg_thresholds:
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
    print(f"TEST SET — V2 FIXED Tail + Fast-Green Intersection (out-of-sample)")
    print(f"{'='*80}")
    print(f"{'TailP':>6} {'FGP':>6} {'N':>6} {'CumNet':>10} {'AvgNet':>10} {'Win%':>7} {'Best':>8} {'Worst':>8}")
    print("-" * 80)

    for r in sorted(results, key=lambda x: x["avg_net"], reverse=True)[:20]:
        print(f"{r['tail_p']:>6.2f} {r['fg_p']:>6.2f} {r['n']:>6} "
              f"{r['cum_net']:>10.2f} {r['avg_net']:>10.4f} {r['win_rate']:>7.1%} "
              f"{r['best']:>8.4f} {r['worst']:>8.4f}")

    # Best combos with >=50 trades (practical threshold)
    viable = [r for r in results if r["n"] >= 50]
    if viable:
        best_avg = max(viable, key=lambda x: x["avg_net"])
        best_cum = max(viable, key=lambda x: x["cum_net"])

        print(f"\n{'='*80}")
        print(f"BEST BY AVG NET (>=50 trades):")
        print(f"  Tail>={best_avg['tail_p']:.2f}, FG>={best_avg['fg_p']:.2f}")
        print(f"  {best_avg['n']} trades, avg {best_avg['avg_net']:.4f}%, cum {best_avg['cum_net']:.2f}%")
        print(f"  Win rate: {best_avg['win_rate']:.1%}")
        print(f"  Est trades/day: {best_avg['n']/30:.1f}")
        print(f"  Est daily return: {best_avg['n']/30 * best_avg['avg_net']:.2f}%")
        print(f"  Days to 4x: {np.log(4) / np.log(1 + best_avg['n']/30 * best_avg['avg_net']/100):.0f}")

        print(f"\nBEST BY CUMULATIVE (>=50 trades):")
        print(f"  Tail>={best_cum['tail_p']:.2f}, FG>={best_cum['fg_p']:.2f}")
        print(f"  {best_cum['n']} trades, avg {best_cum['avg_net']:.4f}%, cum {best_cum['cum_net']:.2f}%")
        print(f"  Win rate: {best_cum['win_rate']:.1%}")

        # Also check lower trade count thresholds
        for min_trades in [10, 20, 30, 100, 200]:
            sub = [r for r in results if r["n"] >= min_trades]
            if sub:
                best = max(sub, key=lambda x: x["avg_net"])
                print(f"\nBest (>={min_trades} trades): Tail>={best['tail_p']:.2f}, FG>={best['fg_p']:.2f}")
                print(f"  {best['n']} trades, avg {best['avg_net']:.4f}%, cum {best['cum_net']:.2f}%")
                print(f"  Days to 4x: {np.log(4) / np.log(1 + best['n']/30 * best['avg_net']/100):.0f}")

    print(f"\n{'='*80}")
    print("DONE")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
