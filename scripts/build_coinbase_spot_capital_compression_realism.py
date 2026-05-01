#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_TABLE_PATH = REPORTS / "coinbase_spot_fee_survival_training_table.csv"
DEFAULT_TAIL_MODEL = REPORTS / "models" / "coinbase_spot_tail_predictor.joblib"
DEFAULT_FAST_GREEN_MODEL = REPORTS / "models" / "coinbase_spot_fast_green_model.joblib"
DEFAULT_JSON_PATH = REPORTS / "coinbase_spot_capital_compression_realism.json"
DEFAULT_CSV_PATH = REPORTS / "coinbase_spot_capital_compression_realism.csv"
DEFAULT_MD_PATH = REPORTS / "coinbase_spot_capital_compression_realism.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_model(path: Path) -> Any:
    return joblib.load(path)


def score_with_model(df: pd.DataFrame, model: Any) -> np.ndarray:
    if "categorical" in model:
        categorical = list(model["categorical"])
        numeric = list(model["numeric"])
    elif "categorical_cols" in model:
        categorical = list(model["categorical_cols"])
        numeric = [column for column in model["feature_cols"] if column not in categorical]
    else:
        raise ValueError(f"Unknown model format: {list(model.keys())}")
    work = df.copy()
    for column in categorical:
        work[column] = work[column].astype(str).fillna("")
    for column in numeric:
        work[column] = pd.to_numeric(work[column], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return model["model"].predict_proba(work[categorical + numeric])[:, 1]


def net_pct_for_model(row: pd.Series, outcome_model: str, fee_bps: float, capture_rate: float) -> float:
    if outcome_model == "table_net_pct":
        if abs(fee_bps - to_float(row.get("fee_bps_round_trip"))) < 1e-9:
            return to_float(row.get("net_pct"))
        return to_float(row.get("gross_pct")) - (fee_bps / 100.0)
    if outcome_model == "gross_minus_fee":
        return to_float(row.get("gross_pct")) - (fee_bps / 100.0)
    if outcome_model == "mfe_capture":
        spread_pct = to_float(row.get("spread_bps_proxy")) / 100.0
        return (to_float(row.get("future_mfe_pct")) * capture_rate) - (fee_bps / 100.0) - spread_pct
    raise ValueError(f"unknown outcome model: {outcome_model}")


def simulate(signals: pd.DataFrame, *, max_positions: int, deploy_pct: float, fee_bps: float, outcome_model: str, capture_rate: float) -> dict[str, Any]:
    cash = 100.0
    active: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    peak_equity = cash
    max_drawdown_pct = 0.0
    for current_time in sorted(signals["time"].unique()):
        remaining: list[dict[str, Any]] = []
        for position in active:
            if current_time >= position["exit_time"]:
                cash += position["size"] * (1.0 + position["net_pct"] / 100.0)
            else:
                remaining.append(position)
        active = remaining
        equity = cash + sum(position["size"] * (1.0 + position["net_pct"] / 100.0) for position in active)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0.0:
            max_drawdown_pct = max(max_drawdown_pct, ((peak_equity - equity) / peak_equity) * 100.0)
        slots = max(0, int(max_positions) - len(active))
        if slots <= 0:
            continue
        current = signals[signals["time"] == current_time]
        for _, signal in current.nlargest(slots, "combined_score").iterrows():
            if cash <= 0.0:
                break
            size = min(cash, cash * float(deploy_pct))
            if size <= 0.0:
                continue
            hold_seconds = max(300.0, to_float(signal.get("hold_bars"), 1.0) * 300.0)
            net_pct = net_pct_for_model(signal, outcome_model, fee_bps, capture_rate)
            cash -= size
            trade = {
                "entry_time": to_float(signal.get("time")),
                "exit_time": to_float(signal.get("time")) + hold_seconds,
                "product_id": signal.get("product_id"),
                "variant_id": signal.get("variant_id"),
                "size": size,
                "net_pct": net_pct,
            }
            active.append(trade)
            trades.append(trade)
    for position in active:
        cash += position["size"] * (1.0 + position["net_pct"] / 100.0)
    net_pcts = [to_float(trade.get("net_pct")) for trade in trades]
    return {
        "final_capital": round(cash, 6),
        "profit_pct": round(((cash / 100.0) - 1.0) * 100.0, 6),
        "trades": len(trades),
        "win_rate_pct": round((sum(1 for value in net_pcts if value > 0.0) / len(net_pcts)) * 100.0, 6) if net_pcts else 0.0,
        "avg_net_pct": round(sum(net_pcts) / len(net_pcts), 6) if net_pcts else 0.0,
        "worst_trade_pct": round(min(net_pcts), 6) if net_pcts else 0.0,
        "best_trade_pct": round(max(net_pcts), 6) if net_pcts else 0.0,
        "max_drawdown_pct": round(max_drawdown_pct, 6),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a conservative capital-compression realism report for Coinbase spot ML signals.")
    parser.add_argument("--table-path", default=str(DEFAULT_TABLE_PATH))
    parser.add_argument("--tail-model", default=str(DEFAULT_TAIL_MODEL))
    parser.add_argument("--fast-green-model", default=str(DEFAULT_FAST_GREEN_MODEL))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--tail-threshold", type=float, default=0.95)
    parser.add_argument("--fast-green-threshold", type=float, default=0.90)
    return parser.parse_args()


def build(args: argparse.Namespace) -> dict[str, Any]:
    df = pd.read_csv(Path(str(args.table_path)))
    split_at = int(len(df) * 0.75)
    test_df = df.iloc[split_at:].copy()
    tail = score_with_model(test_df, load_model(Path(str(args.tail_model))))
    fast_green = score_with_model(test_df, load_model(Path(str(args.fast_green_model))))
    mask = (tail >= float(args.tail_threshold)) & (fast_green >= float(args.fast_green_threshold))
    selected = test_df[mask].copy()
    selected["tail_prob"] = tail[mask]
    selected["fast_green_prob"] = fast_green[mask]
    selected["combined_score"] = selected["tail_prob"] * selected["fast_green_prob"]
    scenarios = []
    for outcome_model, capture_rate in [
        ("gross_minus_fee", 1.0),
        ("table_net_pct", 1.0),
        ("mfe_capture", 0.75),
        ("mfe_capture", 0.60),
        ("mfe_capture", 0.50),
        ("mfe_capture", 0.40),
        ("mfe_capture", 0.30),
        ("mfe_capture", 0.25),
        ("mfe_capture", 0.20),
        ("mfe_capture", 0.10),
    ]:
        for venue, fee_bps in [("coinbase", 240.0), ("kraken", 80.0)]:
            for label, max_positions, deploy_pct in [("one_position", 1, 0.8), ("top3", 3, 0.3)]:
                row = {
                    "venue": venue,
                    "fee_bps": fee_bps,
                    "deployment": label,
                    "max_positions": max_positions,
                    "deploy_pct": deploy_pct,
                    "outcome_model": outcome_model,
                    "capture_rate": capture_rate,
                }
                row.update(simulate(selected, max_positions=max_positions, deploy_pct=deploy_pct, fee_bps=fee_bps, outcome_model=outcome_model, capture_rate=capture_rate))
                scenarios.append(row)
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_capital_compression_realism",
        "shadow_only": True,
        "parameters": {
            "table_path": str(args.table_path),
            "tail_model": str(args.tail_model),
            "fast_green_model": str(args.fast_green_model),
            "tail_threshold": float(args.tail_threshold),
            "fast_green_threshold": float(args.fast_green_threshold),
            "test_rows": int(len(test_df)),
            "selected_rows": int(len(selected)),
            "test_start": datetime.utcfromtimestamp(float(test_df["time"].min())).isoformat() if len(test_df) else "",
            "test_end": datetime.utcfromtimestamp(float(test_df["time"].max())).isoformat() if len(test_df) else "",
        },
        "read": [
            "This is a capital-compression audit for historical Coinbase candle/ML signals, not live permission.",
            "gross_minus_fee reproduces the optimistic label-style simulation.",
            "mfe_capture scenarios discount future reachable MFE; these are closer to the live trailing-capture question but still require shadow proof.",
        ],
        "scenarios": scenarios,
    }
    write_reports(payload, Path(str(args.json_path)), Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    rows = payload.get("scenarios") or []
    columns = [
        "venue",
        "deployment",
        "outcome_model",
        "capture_rate",
        "fee_bps",
        "max_positions",
        "deploy_pct",
        "final_capital",
        "profit_pct",
        "trades",
        "win_rate_pct",
        "avg_net_pct",
        "worst_trade_pct",
        "best_trade_pct",
        "max_drawdown_pct",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    params = payload.get("parameters") or {}
    lines = [
        "# Coinbase Spot Capital Compression Realism",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Test rows: `{params.get('test_rows')}`",
        f"- Selected rows: `{params.get('selected_rows')}`",
        f"- Test window: `{params.get('test_start')}` to `{params.get('test_end')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("read") or []])
    lines.extend(
        [
            "",
            "## Scenarios",
            "",
            "| Venue | Deployment | Outcome | Capture | Final $ | Profit % | Trades | Win % | Avg Net % | Worst % | DD % |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        lines.append(
            "| {venue} | {deployment} | {outcome_model} | {capture_rate:.2f} | {final_capital:.2f} | {profit_pct:.2f} | {trades} | {win_rate_pct:.2f} | {avg_net_pct:.4f} | {worst_trade_pct:.4f} | {max_drawdown_pct:.2f} |".format(
                venue=row.get("venue"),
                deployment=row.get("deployment"),
                outcome_model=row.get("outcome_model"),
                capture_rate=to_float(row.get("capture_rate")),
                final_capital=to_float(row.get("final_capital")),
                profit_pct=to_float(row.get("profit_pct")),
                trades=int(row.get("trades") or 0),
                win_rate_pct=to_float(row.get("win_rate_pct")),
                avg_net_pct=to_float(row.get("avg_net_pct")),
                worst_trade_pct=to_float(row.get("worst_trade_pct")),
                max_drawdown_pct=to_float(row.get("max_drawdown_pct")),
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build(parse_args())
    print(json.dumps({"json_path": str(DEFAULT_JSON_PATH.resolve()), "md_path": str(DEFAULT_MD_PATH.resolve()), "selected_rows": payload["parameters"]["selected_rows"]}, indent=2))


if __name__ == "__main__":
    main()
