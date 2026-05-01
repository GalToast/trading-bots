#!/usr/bin/env python3
"""Multi-Signal Cycle Optimizer — find the optimal number of signals per cycle.

The combined scorer showed 17.7 simultaneous signals per cycle.
We tested top-1 through top-5, but let's go further and find the SWEET SPOT.

Tests top-N from 1 to 20 signals per cycle, measuring:
- Cumulative net
- Avg net per signal
- Win rate
- Max drawdown
- Sharpe-like ratio
- Days to 4x

This answers: how many signals should we take per cycle to MAXIMIZE returns
without degrading quality?
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
    print("MULTI-SIGNAL CYCLE OPTIMIZER — Finding the Sweet Spot")
    print("=" * 80)

    df = pd.read_csv(TABLE_PATH)
    df["net_pct"] = df["gross_pct"] - 2.4  # Coinbase 240bps

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

    # Get signals that pass combined filter
    mask = (test_tail >= 0.95) & (test_fg >= 0.90)
    selected = test_df[mask].copy()
    selected["tail_prob"] = test_tail[mask]
    selected["fg_prob"] = test_fg[mask]
    selected["combined_score"] = selected["tail_prob"] * selected["fg_prob"]

    print(f"Raw signals: {len(selected):,}")
    print(f"Unique cycles: {selected['time'].nunique():,}")

    # Test different N values
    print(f"\n{'='*80}")
    print(f"TOP-N PER CYCLE ANALYSIS (Coinbase 240bps)")
    print(f"{'='*80}")
    print(f"{'N':>3} {'Signals':>8} {'CumNet%':>10} {'AvgNet%':>10} {'Win%':>7} {'Worst%':>8} {'Best%':>8} {'Sharpe':>8}")
    print("-" * 80)

    results = []
    for n in range(1, 21):
        # Get top N per cycle
        top_n = selected.groupby("time").apply(
            lambda g: g.nlargest(n, "combined_score")
        ).reset_index(drop=True)
        
        if len(top_n) == 0:
            continue
        
        nets = top_n["net_pct"].values
        cum_net = float(np.sum(nets))
        avg_net = float(np.mean(nets))
        win_rate = float(np.mean(nets > 0))
        worst = float(np.min(nets))
        best = float(np.max(nets))
        
        # Sharpe-like: mean/std of daily returns
        # Group by day (approximate from timestamp)
        from datetime import datetime
        days = []
        for ts in top_n["time"].values:
            dt = datetime.utcfromtimestamp(ts)
            days.append(dt.strftime("%Y-%m-%d"))
        
        top_n_copy = top_n.copy()
        top_n_copy["day"] = days
        daily_returns = top_n_copy.groupby("day")["net_pct"].sum().values
        
        sharpe = float(np.mean(daily_returns) / np.std(daily_returns)) if np.std(daily_returns) > 0 else 0.0
        
        # Max drawdown
        equity = np.cumsum(daily_returns)
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / (1 + peak)
        max_dd = float(np.min(drawdown))
        
        print(f"{n:>3} {len(top_n):>8} {cum_net:>10.2f} {avg_net:>10.4f} {win_rate:>7.1%} {worst:>8.4f} {best:>8.4f} {sharpe:>8.2f}")
        
        results.append({
            "n": n,
            "signals": len(top_n),
            "cum_net": cum_net,
            "avg_net": avg_net,
            "win_rate": win_rate,
            "worst": worst,
            "best": best,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "daily_returns": daily_returns,
        })

    # Find the sweet spot
    print(f"\n{'='*80}")
    print(f"SWEET SPOT ANALYSIS")
    print(f"{'='*80}")
    
    # Best by cumulative net
    best_cum = max(results, key=lambda x: x["cum_net"])
    print(f"\nBest by CUMULATIVE NET: Top-{best_cum['n']}")
    print(f"  {best_cum['signals']} signals, cum {best_cum['cum_net']:.2f}%, avg {best_cum['avg_net']:.4f}%")
    print(f"  Win rate: {best_cum['win_rate']:.1%}, Sharpe: {best_cum['sharpe']:.2f}, Max DD: {best_cum['max_dd']:.1%}")
    
    # Best by Sharpe
    best_sharpe = max(results, key=lambda x: x["sharpe"])
    print(f"\nBest by SHARPE: Top-{best_sharpe['n']}")
    print(f"  {best_sharpe['signals']} signals, cum {best_sharpe['cum_net']:.2f}%, avg {best_sharpe['avg_net']:.4f}%")
    print(f"  Win rate: {best_sharpe['win_rate']:.1%}, Sharpe: {best_sharpe['sharpe']:.2f}, Max DD: {best_sharpe['max_dd']:.1%}")
    
    # Best by avg net (quality)
    best_avg = max(results, key=lambda x: x["avg_net"])
    print(f"\nBest by AVG NET: Top-{best_avg['n']}")
    print(f"  {best_avg['signals']} signals, cum {best_avg['cum_net']:.2f}%, avg {best_avg['avg_net']:.4f}%")
    print(f"  Win rate: {best_avg['win_rate']:.1%}, Sharpe: {best_avg['sharpe']:.2f}, Max DD: {best_avg['max_dd']:.1%}")
    
    # Find the pareto frontier
    print(f"\n{'='*80}")
    print(f"PARETO FRONTIER (best tradeoffs)")
    print(f"{'='*80}")
    print(f"{'N':>3} {'Signals':>8} {'CumNet%':>10} {'AvgNet%':>10} {'Win%':>7} {'Sharpe':>8} {'MaxDD%':>8} {'4x Days':>8}")
    print("-" * 80)
    
    for r in results:
        days = len(set(d for d in [datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") for ts in selected.groupby("time").apply(lambda g: g.nlargest(r["n"], "combined_score").reset_index(drop=True))["time"].values]))
        daily_ret = r["cum_net"] / max(days, 1)
        days_4x = np.log(4) / np.log(1 + daily_ret / 100) if daily_ret > 0 else float('inf')
        print(f"{r['n']:>3} {r['signals']:>8} {r['cum_net']:>10.2f} {r['avg_net']:>10.4f} {r['win_rate']:>7.1%} {r['sharpe']:>8.2f} {r['max_dd']*100:>8.1f} {days_4x:>8.0f}")

    print(f"\n{'='*80}")
    print("DONE")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
