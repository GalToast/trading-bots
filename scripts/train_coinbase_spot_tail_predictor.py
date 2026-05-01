#!/usr/bin/env python3
"""Train a Tail Predictor: classify signals that land in the 90th+ percentile of gross returns.

Unlike the fee-survival model (which predicts "survives fees"), this predicts "BIG MOVE."
Combined with the fast-green model (which predicts "FAST MOVE"), the intersection captures
moves that are both big AND fast — the geometry needed for 4x account growth.

Label: gross_pct > 90th percentile of all gross returns = tail signal
Features: same as the fee-survival model (ret_1/3/6/12_bps, range, body, volume, etc.)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.metrics import average_precision_score, precision_recall_fscore_support, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TABLE_PATH = REPORTS / "coinbase_spot_fee_survival_training_table.csv"
MODEL_PATH = REPORTS / "models" / "coinbase_spot_tail_predictor.joblib"
JSON_PATH = REPORTS / "coinbase_spot_tail_predictor_report.json"
MD_PATH = REPORTS / "coinbase_spot_tail_predictor_report.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_model(scale_pos_weight: float) -> Any:
    try:
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=700,
            learning_rate=0.025,
            num_leaves=63,
            min_child_samples=80,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_alpha=0.4,
            reg_lambda=1.2,
            scale_pos_weight=scale_pos_weight,
            random_state=42,
            verbosity=-1,
        )
    except Exception:
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            learning_rate=0.03,
            max_iter=450,
            l2_regularization=0.1,
            random_state=42,
        )


def threshold_stats(df: pd.DataFrame, probabilities: np.ndarray, thresholds: list[float]) -> list[dict[str, Any]]:
    y = df["is_tail"].astype(int).to_numpy()
    out = []
    for threshold in thresholds:
        allowed = probabilities >= threshold
        count = int(allowed.sum())
        if count == 0:
            out.append({
                "threshold": threshold,
                "allowed": 0,
                "precision": 0.0,
                "recall": 0.0,
                "cumulative_net_pct": 0.0,
                "avg_gross_pct": 0.0,
                "avg_net_pct": 0.0,
                "best_gross_pct": 0.0,
                "worst_gross_pct": 0.0,
                "survivors": 0,
            })
            continue
        precision, recall, _, _ = precision_recall_fscore_support(y, allowed.astype(int), average="binary", zero_division=0)
        selected = df.loc[allowed]
        out.append({
            "threshold": threshold,
            "allowed": count,
            "precision": round(float(precision), 6),
            "recall": round(float(recall), 6),
            "cumulative_net_pct": round(float(selected["net_pct"].sum()), 6),
            "avg_gross_pct": round(float(selected["gross_pct"].mean()), 6),
            "avg_net_pct": round(float(selected["net_pct"].mean()), 6),
            "best_gross_pct": round(float(selected["gross_pct"].max()), 6),
            "worst_gross_pct": round(float(selected["gross_pct"].min()), 6),
            "survivors": int(selected["is_tail"].sum()),
        })
    return out


def score_frame(frame: pd.DataFrame, y: pd.Series, prob: np.ndarray, net_col: str = "net_pct") -> dict[str, Any]:
    roc = roc_auc_score(y, prob) if len(set(y.astype(int))) > 1 else None
    ap = average_precision_score(y, prob) if len(set(y.astype(int))) > 1 else None
    return {
        "rows": int(len(frame)),
        "positives": int(y.sum()),
        "positive_rate_pct": round((float(y.sum()) / max(1, len(y))) * 100.0, 6),
        "all_trades_cumulative_net_pct": round(float(frame[net_col].sum()), 6),
        "all_trades_avg_net_pct": round(float(frame[net_col].mean()), 6),
        "roc_auc": round(float(roc), 6) if roc is not None else None,
        "average_precision": round(float(ap), 6) if ap is not None else None,
        "thresholds": threshold_stats(frame.reset_index(drop=True), prob, [0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.98]),
    }


def train(args: argparse.Namespace) -> dict[str, Any]:
    df = pd.read_csv(args.table_path)
    if df.empty:
        raise SystemExit(f"No rows found at {args.table_path}")

    # Label: is this signal in the tail (> 90th percentile of gross returns)?
    tail_threshold = float(args.tail_percentile)
    gross_pctl = np.percentile(df["gross_pct"], tail_threshold)
    df["is_tail"] = df["gross_pct"] > gross_pctl
    
    # Also compute net after fees for evaluation
    df["net_pct"] = df["gross_pct"] - float(args.fee_bps_per_side) * 2.0 / 100.0

    print(f"Tail threshold: gross > {gross_pctl:.4f}% ({tail_threshold}th percentile)")
    print(f"Tail rows: {df['is_tail'].sum()} / {len(df)} ({df['is_tail'].mean()*100:.1f}%)")

    df["time"] = pd.to_numeric(df["time"], errors="coerce").fillna(0).astype(int)
    df = df.sort_values(["time", "product_id", "variant_id"]).reset_index(drop=True)

    categorical = ["product_id", "archetype", "trigger", "confirmation", "exit", "sizing", "trigger_mode"]
    numeric = [
        "hour_utc", "lookback", "trigger_bps", "target_pct", "stop_pct", "hold_bars",
        "spread_bps_proxy", "fee_bps_round_trip",
        "ret_1_bps", "ret_3_bps", "ret_6_bps", "ret_12_bps",
        "range_bps", "body_bps", "close_location", "volume_mult_12",
        "volatility_12_bps", "accel_vs_median_abs_12",
        "dist_from_12_high_bps", "dist_from_12_low_bps", "position_in_12_range",
    ]
    for column in categorical:
        df[column] = df[column].astype(str).fillna("")
    for column in numeric + ["net_pct", "gross_pct"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)

    split_at = int(len(df) * (1.0 - float(args.test_size)))
    split_at = max(1, min(split_at, len(df) - 1))
    train_df = df.iloc[:split_at].copy()
    test_df = df.iloc[split_at:].copy()
    y_train = train_df["is_tail"].astype(int)
    y_test = test_df["is_tail"].astype(int)
    
    if int(y_train.sum()) == 0 or int(y_test.sum()) == 0:
        raise SystemExit("Chronological split has no positives on one side.")
    
    scale_pos_weight = max(1.0, float((len(y_train) - int(y_train.sum())) / max(1, int(y_train.sum()))))
    
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), categorical),
            ("num", StandardScaler(), numeric),
        ]
    )
    model = Pipeline([("features", preprocessor), ("model", build_model(scale_pos_weight))])
    model.fit(train_df[categorical + numeric], y_train)

    train_prob = model.predict_proba(train_df[categorical + numeric])[:, 1]
    test_prob = model.predict_proba(test_df[categorical + numeric])[:, 1]

    report = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_tail_predictor",
        "source": str(args.table_path),
        "model_path": str(args.model_path),
        "split_mode": "chronological",
        "tail_percentile": tail_threshold,
        "tail_threshold_value": round(float(gross_pctl), 6),
        "parameters": {
            "test_size": float(args.test_size),
            "scale_pos_weight": round(scale_pos_weight, 6),
            "train_time_min": int(train_df["time"].min()),
            "train_time_max": int(train_df["time"].max()),
            "test_time_min": int(test_df["time"].min()),
            "test_time_max": int(test_df["time"].max()),
        },
        "leadership_read": [
            f"This model predicts signals in the {tail_threshold}th+ percentile of gross returns (>{gross_pctl:.2f}%).",
            "Combined with fast-green model: tail predictor = SIZE, fast-green = SPEED.",
            "The intersection captures big AND fast moves — the geometry needed for account growth.",
            "Test set evaluation shows which thresholds select positive cumulative net trades.",
        ],
        "features": {"categorical": categorical, "numeric": numeric},
        "train": score_frame(train_df, y_train, train_prob),
        "test": score_frame(test_df, y_test, test_prob),
    }
    
    payload = {"model": model, "categorical": categorical, "numeric": numeric, "report": report}
    Path(args.model_path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, args.model_path)
    return report


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Coinbase Spot Tail Predictor",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Source: `{report['source']}`",
        f"- Model: `{report['model_path']}`",
        f"- Tail percentile: `{report['tail_percentile']}`",
        f"- Tail threshold: `>{report['tail_threshold_value']}%` gross",
        f"- Split: `{report['split_mode']}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in report["leadership_read"]])
    for section in ("train", "test"):
        data = report[section]
        lines.extend(
            [
                "",
                f"## {section.title()}",
                "",
                f"- Rows: `{data['rows']}`",
                f"- Positives: `{data['positives']}`",
                f"- Positive rate pct: `{data['positive_rate_pct']}`",
                f"- All-trades cumulative net pct: `{data['all_trades_cumulative_net_pct']}`",
                f"- All-trades avg net pct: `{data['all_trades_avg_net_pct']}`",
                f"- ROC AUC: `{data['roc_auc']}`",
                f"- Average precision: `{data['average_precision']}`",
                "",
                "| Threshold | Allowed | Survivors | Precision | Recall | Cum Net % | Avg Gross % | Avg Net % | Best % | Worst % |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in data["thresholds"]:
            lines.append(
                "| {threshold:.2f} | {allowed} | {survivors} | {precision:.4f} | {recall:.4f} | {cumulative_net_pct:.4f} | {avg_gross_pct:.4f} | {avg_net_pct:.4f} | {best_gross_pct:.4f} | {worst_gross_pct:.4f} |".format(**row)
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a tail predictor for Coinbase spot signals.")
    parser.add_argument("--table-path", default=str(TABLE_PATH))
    parser.add_argument("--json-path", default=str(JSON_PATH))
    parser.add_argument("--md-path", default=str(MD_PATH))
    parser.add_argument("--model-path", default=str(MODEL_PATH))
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--tail-percentile", type=float, default=90.0, help="Percentile threshold for tail label")
    parser.add_argument("--fee-bps-per-side", type=float, default=120.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print("=" * 80)
    print("TAIL PREDICTOR — Coinbase Spot")
    print("=" * 80)
    
    report = train(args)
    Path(args.json_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, Path(args.md_path))
    
    # Print key results
    test = report["test"]
    print(f"\nTest set results:")
    print(f"  Rows: {test['rows']:,}")
    print(f"  Positives: {test['positives']:,} ({test['positive_rate_pct']}%)")
    print(f"  ROC AUC: {test['roc_auc']}")
    print(f"  Average precision: {test['average_precision']}")
    print(f"  All-trades cumulative net: {test['all_trades_cumulative_net_pct']:.2f}%")
    
    print(f"\nThreshold analysis:")
    for t in test["thresholds"]:
        print(f"  p>={t['threshold']:.2f}: {t['allowed']:>5} trades, "
              f"{t['precision']:.1%} precision, {t['recall']:.1%} recall, "
              f"cum net {t['cumulative_net_pct']:.2f}%, "
              f"avg net {t['avg_net_pct']:.4f}%")
    
    print(f"\nModel saved to: {args.model_path}")
    print(f"Report saved to: {args.json_path}, {args.md_path}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
