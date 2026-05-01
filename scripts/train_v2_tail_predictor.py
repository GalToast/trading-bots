#!/usr/bin/env python3
"""Train V2 tail predictor with temporal features."""
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, average_precision_score
import joblib

print("=" * 60)
print("TRAINING V2 TAIL PREDICTOR (with temporal features)")
print("=" * 60)

# Load V2 table
df = pd.read_csv('reports/coinbase_spot_high_gross_v2_training_table.csv')
print(f"\nLoaded {len(df):,} rows")
print(f"Positive samples: {df['target'].sum():,} ({df['target'].mean()*100:.2f}%)")

# Features
feature_cols = [col for col in df.columns if col not in ['target', 'product_id', 'gross_pct', 'net_pct', 'survived_fees']]
X = df[feature_cols]
y = df['target']

# Encode categorical
categorical_cols = ['archetype', 'trigger', 'confirmation', 'exit', 'sizing', 'trigger_mode']
for col in categorical_cols:
    if col in X.columns:
        X[col] = X[col].astype('category').cat.codes

print(f"\nFeature matrix shape: {X.shape}")
print(f"Features: {len(feature_cols)}")

# Chronological split
print("\nSplitting chronologically...")
split_idx = int(len(df) * 0.75)
X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

print(f"Train: {len(X_train):,} rows ({y_train.mean()*100:.2f}% positive)")
print(f"Test: {len(X_test):,} rows ({y_test.mean()*100:.2f}% positive)")

# Train model
print("\nTraining V2 model (with temporal features)...")
try:
    from lightgbm import LGBMClassifier
    scale_pos_weight = (len(y_train) - y_train.sum()) / y_train.sum()
    model = LGBMClassifier(
        n_estimators=1500,
        learning_rate=0.015,
        num_leaves=127,
        min_child_samples=30,
        subsample=0.85,
        colsample_bytree=0.85,
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
print("\nEvaluating V2 model...")
y_prob = model.predict_proba(X_test)[:, 1]
auc = roc_auc_score(y_test, y_prob)
ap = average_precision_score(y_test, y_prob)

print(f"ROC AUC: {auc:.4f} (vs 0.6498 V1)")
print(f"Average Precision: {ap:.4f} (vs 0.2132 V1)")

# Check different thresholds
print("\nPerformance at different thresholds:")
for threshold in [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]:
    allowed = y_prob >= threshold
    count = allowed.sum()
    if count > 0:
        precision = y_test[allowed].mean()
        avg_gross = df.iloc[X_test[allowed].index]['gross_pct'].mean()
        print(f"  p>={threshold}: {count} rows, precision={precision*100:.1f}%, avg_gross={avg_gross:.2f}%")

# Save model
print("\nSaving V2 model...")
joblib.dump({
    'model': model,
    'feature_cols': feature_cols,
    'categorical_cols': categorical_cols,
    'test_auc': auc,
    'test_ap': ap,
    'version': 'V2 with temporal features',
}, 'reports/models/coinbase_spot_high_gross_tail_predictor_v2.joblib')

print("Done! Model saved to: reports/models/coinbase_spot_high_gross_tail_predictor_v2.joblib")
print("=" * 60)
