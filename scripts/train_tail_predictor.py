#!/usr/bin/env python3
"""Train tail predictor model (predict gross > 2.5%)."""
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, average_precision_score
import joblib

print("=" * 60)
print("TRAINING TAIL PREDICTOR MODEL")
print("=" * 60)

# Load training table
df = pd.read_csv('reports/coinbase_spot_high_gross_training_table.csv')
print(f"\nLoaded {len(df):,} rows")
print(f"Positive samples: {df['target'].sum():,} ({df['target'].mean()*100:.2f}%)")

# Features
feature_cols = [
    'hour_utc', 'archetype', 'trigger', 'confirmation', 'exit', 'sizing',
    'trigger_mode', 'lookback', 'trigger_bps', 'target_pct', 'stop_pct',
    'hold_bars', 'spread_bps_proxy', 'fee_bps_round_trip',
    'ret_1_bps', 'ret_3_bps', 'ret_6_bps', 'ret_12_bps',
    'range_bps', 'body_bps', 'close_location', 'volume_mult_12',
    'volatility_12_bps', 'accel_vs_median_abs_12',
    'dist_from_12_high_bps', 'dist_from_12_low_bps', 'position_in_12_range'
]

# Encode categorical
df_encoded = df.copy()
categorical_cols = ['archetype', 'trigger', 'confirmation', 'exit', 'sizing', 'trigger_mode']
for col in categorical_cols:
    df_encoded[col] = df_encoded[col].astype('category').cat.codes

X = df_encoded[feature_cols]
y = df_encoded['target']

# Train/test split (chronological)
print("\nSplitting data chronologically...")
split_idx = int(len(df) * 0.75)
X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

print(f"Train: {len(X_train):,} rows ({y_train.mean()*100:.2f}% positive)")
print(f"Test: {len(X_test):,} rows ({y_test.mean()*100:.2f}% positive)")

# Train model (LightGBM if available, else sklearn)
print("\nTraining model...")
try:
    from lightgbm import LGBMClassifier
    scale_pos_weight = (len(y_train) - y_train.sum()) / y_train.sum()
    model = LGBMClassifier(
        n_estimators=1000,
        learning_rate=0.02,
        num_leaves=63,
        min_child_samples=50,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        verbosity=-1,
    )
except ImportError:
    from sklearn.ensemble import HistGradientBoostingClassifier
    model = HistGradientBoostingClassifier(
        learning_rate=0.03,
        max_iter=500,
        random_state=42,
    )

model.fit(X_train, y_train)

# Evaluate
print("\nEvaluating model...")
y_prob = model.predict_proba(X_test)[:, 1]
auc = roc_auc_score(y_test, y_prob)
ap = average_precision_score(y_test, y_prob)

print(f"ROC AUC: {auc:.4f}")
print(f"Average Precision: {ap:.4f}")

# Check different thresholds
print("\nPerformance at different thresholds:")
for threshold in [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]:
    allowed = y_prob >= threshold
    count = allowed.sum()
    if count > 0:
        precision = y_test[allowed].mean()
        print(f"  p>={threshold}: {count} rows, precision={precision*100:.1f}%, avg_gross={df_encoded.iloc[X_test[allowed].index]['gross_pct'].mean():.2f}%")

# Save model
print("\nSaving model...")
joblib.dump({
    'model': model,
    'feature_cols': feature_cols,
    'categorical_cols': categorical_cols,
    'test_auc': auc,
    'test_ap': ap,
}, 'reports/models/coinbase_spot_high_gross_tail_predictor.joblib')

print("Done! Model saved to: reports/models/coinbase_spot_high_gross_tail_predictor.joblib")
print("=" * 60)
