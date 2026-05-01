#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from run_coinbase_spot_gpu_foundry import (
    candle_files,
    build_variant_rows,
    load_spreads,
    load_theories,
    to_float,
)


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
MATRIX_PATH = REPORTS / "coinbase_spot_gpu_foundry_product_matrix.csv"
CSV_PATH = REPORTS / "coinbase_spot_fee_survival_training_table.csv"
JSON_PATH = REPORTS / "coinbase_spot_fee_survival_training_table.json"
MD_PATH = REPORTS / "coinbase_spot_fee_survival_training_table.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def parse_candles_with_time(path: Path) -> dict[str, Any] | None:
    payload = load_json(path)
    rows = payload.get("candles") if isinstance(payload, dict) else []
    candles = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        open_ = to_float(row.get("open"))
        high = to_float(row.get("high"))
        low = to_float(row.get("low"))
        close = to_float(row.get("close"))
        volume = to_float(row.get("volume"))
        timestamp = int(to_float(row.get("time")))
        if timestamp <= 0 or open_ <= 0.0 or high <= 0.0 or low <= 0.0 or close <= 0.0 or high < low:
            continue
        candles.append((timestamp, open_, high, low, close, volume))
    if len(candles) < 80:
        return None
    return {"product_id": str(payload.get("product_id") or path.stem.replace("_", "-")), "candles": candles}


def rolling_mean(values: np.ndarray, idx: int, window: int) -> float:
    start = max(0, idx - window + 1)
    segment = values[start : idx + 1]
    if segment.size == 0:
        return 0.0
    return float(np.mean(segment))


def rolling_std(values: np.ndarray, idx: int, window: int) -> float:
    start = max(0, idx - window + 1)
    segment = values[start : idx + 1]
    if segment.size < 2:
        return 0.0
    return float(np.std(segment))


def rolling_max(values: np.ndarray, idx: int, window: int) -> float:
    start = max(0, idx - window + 1)
    segment = values[start : idx + 1]
    return float(np.max(segment)) if segment.size else 0.0


def rolling_min(values: np.ndarray, idx: int, window: int) -> float:
    start = max(0, idx - window + 1)
    segment = values[start : idx + 1]
    return float(np.min(segment)) if segment.size else 0.0


def pct_change(close: np.ndarray, idx: int, bars: int) -> float:
    if idx < bars or close[idx - bars] <= 0.0:
        return 0.0
    return float((close[idx] / close[idx - bars]) - 1.0)


def build_signal_mask(variant: dict[str, Any], open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, valid_t: int) -> np.ndarray:
    idx = np.arange(valid_t)
    lookback = int(variant["lookback"])
    trigger_bps = float(variant["trigger_bps"]) / 10_000.0
    min_location = float(variant["min_close_location"])
    min_volume_mult = float(variant["min_volume_mult"])
    close_now = close[:valid_t]
    high_now = high[:valid_t]
    low_now = low[:valid_t]
    open_now = open_[:valid_t]
    volume_now = volume[:valid_t]
    range_now = np.maximum(high_now - low_now, 1e-12)
    close_location = np.clip((close_now - low_now) / range_now, 0.0, 1.0)
    valid = idx >= lookback
    prior_close = np.roll(close_now, lookback)
    base = valid & (close_location >= min_location)
    if min_volume_mult > 0.0:
        vol_base = np.maximum(np.roll(volume_now, lookback), 1e-12)
        base = base & (volume_now >= vol_base * min_volume_mult)

    mode = str(variant["trigger_mode"])
    if mode == "impulse":
        signal = base & (((close_now / np.maximum(prior_close, 1e-12)) - 1.0) >= trigger_bps)
    elif mode == "dump_reclaim":
        flush = ((low_now / np.maximum(prior_close, 1e-12)) - 1.0) <= -trigger_bps
        reclaim = ((close_now / np.maximum(low_now, 1e-12)) - 1.0) >= trigger_bps * 0.5
        signal = base & flush & reclaim
    elif mode == "compression_expansion":
        prev_range = np.roll((high_now / np.maximum(low_now, 1e-12)) - 1.0, 1)
        cur_range = (high_now / np.maximum(low_now, 1e-12)) - 1.0
        body = (close_now / np.maximum(open_now, 1e-12)) - 1.0
        signal = base & (prev_range <= 0.006) & (cur_range >= prev_range * 1.8) & (body >= trigger_bps)
    elif mode == "failed_breakdown":
        prior_low = np.roll(low_now, lookback)
        signal = base & (low_now < prior_low * (1.0 - trigger_bps)) & (close_now > prior_low)
    elif mode == "inside_bar_break":
        prev_high = np.roll(high_now, 1)
        prev_low = np.roll(low_now, 1)
        prior_high = np.roll(high_now, 2)
        prior_low = np.roll(low_now, 2)
        inside = (prev_high <= prior_high) & (prev_low >= prior_low)
        signal = base & inside & (close_now > prev_high)
    elif mode == "range_close_high":
        cur_range = (high_now / np.maximum(low_now, 1e-12)) - 1.0
        signal = base & (cur_range >= trigger_bps)
    else:
        signal = base

    kind = str(variant["confirmation_kind"])
    bars = int(variant["confirmation_bars"])
    if kind == "positive_sequence" and bars > 1:
        for shift in range(bars):
            signal = signal & (((np.roll(close_now, shift) / np.maximum(np.roll(close_now, shift + 1), 1e-12)) - 1.0) > 0.0)
        signal = signal & (idx >= bars)
    elif kind == "higher_low":
        signal = signal & (low_now > np.roll(low_now, 1)) & (idx >= 1)
    elif kind == "midpoint_hold":
        midpoint = (high_now + low_now) * 0.5
        signal = signal & (close_now >= midpoint)
    elif kind == "above_mean":
        mean_ref = np.roll(close_now, bars)
        signal = signal & (close_now >= mean_ref) & (idx >= bars)
    elif kind == "range_not_extreme":
        cur_range = (high_now / np.maximum(low_now, 1e-12)) - 1.0
        prior_range = np.roll(cur_range, max(1, bars))
        signal = signal & (cur_range <= np.maximum(prior_range * 3.0, 0.003)) & (idx >= bars)
    signal[: max(lookback, bars, 2)] = False
    return signal


def select_pairs(args: argparse.Namespace) -> pd.DataFrame:
    matrix = pd.read_csv(args.matrix_path)
    matrix["signals"] = pd.to_numeric(matrix["signals"], errors="coerce").fillna(0).astype(int)
    matrix["cumulative_net_pct"] = pd.to_numeric(matrix["cumulative_net_pct"], errors="coerce").fillna(0.0)
    matrix["avg_net_pct"] = pd.to_numeric(matrix["avg_net_pct"], errors="coerce").fillna(0.0)
    positives = matrix[
        (matrix["signals"] >= int(args.min_pair_signals))
        & (matrix["cumulative_net_pct"] > 0.0)
        & (matrix["avg_net_pct"] > float(args.min_pair_avg_net_pct))
    ].copy()
    products = set(positives["product_id"].astype(str))
    hard_negatives = matrix[
        (matrix["product_id"].astype(str).isin(products))
        & (matrix["signals"] >= int(args.min_pair_signals))
        & (matrix["cumulative_net_pct"] < 0.0)
    ].copy()
    hard_negatives["loss_pressure"] = hard_negatives["cumulative_net_pct"].abs() * hard_negatives["signals"]
    hard_negatives = hard_negatives.sort_values("loss_pressure", ascending=False).head(max(0, len(positives) * int(args.negative_pair_multiplier)))
    pairs = pd.concat([positives, hard_negatives], ignore_index=True)
    pairs = pairs.drop_duplicates(["product_id", "variant_id"])
    if int(args.max_pairs) > 0:
        pairs = pairs.head(int(args.max_pairs))
    return pairs


def build_table(args: argparse.Namespace) -> dict[str, Any]:
    theories = load_theories(int(args.max_variants))
    variants = {int(row.get("id") or 0): row for row in build_variant_rows(theories)}
    pairs = select_pairs(args)
    spreads = load_spreads()
    needed: dict[str, set[int]] = {}
    for row in pairs.to_dict("records"):
        variant_id = int(row["variant_id"])
        if variant_id in variants:
            needed.setdefault(str(row["product_id"]), set()).add(variant_id)

    rows: list[dict[str, Any]] = []
    skipped_products = []
    files = candle_files(granularity=str(args.granularity), days=int(args.days), max_products=int(args.max_products))
    file_by_product = {}
    for path in files:
        product = path.name.split("_USD_", 1)[0].replace("_", "-") + "-USD"
        file_by_product[product] = path

    for product_id, variant_ids in sorted(needed.items()):
        path = file_by_product.get(product_id)
        if not path:
            skipped_products.append(product_id)
            continue
        parsed = parse_candles_with_time(path)
        if not parsed:
            skipped_products.append(product_id)
            continue
        candles = np.asarray(parsed["candles"], dtype=float)
        timestamps = candles[:, 0].astype(int)
        open_ = candles[:, 1]
        high = candles[:, 2]
        low = candles[:, 3]
        close = candles[:, 4]
        volume = candles[:, 5]
        spread_bps = min(max(0.0, spreads.get(product_id, float(args.default_spread_bps))), float(args.max_spread_bps))
        fee_pct = (2.0 * float(args.fee_bps_per_side) / 10_000.0) + (spread_bps / 10_000.0)
        abs_ret_1 = np.abs(np.r_[0.0, np.diff(close) / np.maximum(close[:-1], 1e-12)])

        for variant_id in sorted(variant_ids):
            variant = variants.get(variant_id)
            if not variant:
                continue
            hold = int(variant["hold_bars"])
            valid_t = len(close) - hold
            if valid_t <= 80:
                continue
            signal = build_signal_mask(variant, open_, high, low, close, volume, valid_t)
            signal_indices = np.flatnonzero(signal)
            if int(args.max_rows) > 0 and len(rows) >= int(args.max_rows):
                break
            for idx in signal_indices:
                entry = close[idx]
                future_close = close[idx + hold]
                future_high = float(np.max(high[idx + 1 : idx + hold + 1]))
                future_low = float(np.min(low[idx + 1 : idx + hold + 1]))
                target = float(variant["target_pct"])
                stop = float(variant["stop_pct"])
                gross = (future_close / entry) - 1.0
                if future_high >= entry * (1.0 + target):
                    gross = target
                if future_low <= entry * (1.0 - stop):
                    gross = -stop
                net = gross - fee_pct
                high_12 = rolling_max(high, idx, 12)
                low_12 = rolling_min(low, idx, 12)
                range_12 = max(high_12 - low_12, 1e-12)
                median_abs_12 = float(np.median(abs_ret_1[max(0, idx - 11) : idx + 1])) if idx > 0 else 0.0
                rows.append(
                    {
                        "product_id": product_id,
                        "variant_id": variant_id,
                        "time": int(timestamps[idx]),
                        "hour_utc": int(datetime.fromtimestamp(int(timestamps[idx]), tz=timezone.utc).hour),
                        "archetype": str(variant.get("archetype") or ""),
                        "trigger": str(variant.get("trigger") or ""),
                        "confirmation": str(variant.get("confirmation") or ""),
                        "exit": str(variant.get("exit") or ""),
                        "sizing": str(variant.get("sizing") or ""),
                        "trigger_mode": str(variant.get("trigger_mode") or ""),
                        "lookback": int(variant["lookback"]),
                        "trigger_bps": float(variant["trigger_bps"]),
                        "target_pct": round(target * 100.0, 4),
                        "stop_pct": round(stop * 100.0, 4),
                        "hold_bars": hold,
                        "spread_bps_proxy": round(spread_bps, 4),
                        "fee_bps_round_trip": round(float(args.fee_bps_per_side) * 2.0 + spread_bps, 4),
                        "ret_1_bps": round(pct_change(close, idx, 1) * 10_000.0, 6),
                        "ret_3_bps": round(pct_change(close, idx, 3) * 10_000.0, 6),
                        "ret_6_bps": round(pct_change(close, idx, 6) * 10_000.0, 6),
                        "ret_12_bps": round(pct_change(close, idx, 12) * 10_000.0, 6),
                        "range_bps": round(((high[idx] / max(low[idx], 1e-12)) - 1.0) * 10_000.0, 6),
                        "body_bps": round(((close[idx] / max(open_[idx], 1e-12)) - 1.0) * 10_000.0, 6),
                        "close_location": round(float(np.clip((close[idx] - low[idx]) / max(high[idx] - low[idx], 1e-12), 0.0, 1.0)), 6),
                        "volume_mult_12": round(volume[idx] / max(rolling_mean(volume, idx, 12), 1e-12), 6),
                        "volatility_12_bps": round(rolling_std(abs_ret_1, idx, 12) * 10_000.0, 6),
                        "accel_vs_median_abs_12": round(abs(pct_change(close, idx, 1)) / max(median_abs_12, 1e-12), 6),
                        "dist_from_12_high_bps": round(((close[idx] / max(high_12, 1e-12)) - 1.0) * 10_000.0, 6),
                        "dist_from_12_low_bps": round(((close[idx] / max(low_12, 1e-12)) - 1.0) * 10_000.0, 6),
                        "position_in_12_range": round(float(np.clip((close[idx] - low_12) / range_12, 0.0, 1.0)), 6),
                        "future_mfe_pct": round(((future_high / entry) - 1.0) * 100.0, 6),
                        "future_mae_pct": round(((future_low / entry) - 1.0) * 100.0, 6),
                        "gross_pct": round(gross * 100.0, 6),
                        "net_pct": round(net * 100.0, 6),
                        "survived_fees": net > 0.0,
                    }
                )
                if int(args.max_rows) > 0 and len(rows) >= int(args.max_rows):
                    break
            if int(args.max_rows) > 0 and len(rows) >= int(args.max_rows):
                break

    rows.sort(key=lambda row: (row["product_id"], row["time"], row["variant_id"]))
    summary = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_fee_survival_training_table",
        "source_matrix": str(args.matrix_path),
        "rows": len(rows),
        "selected_pairs": len(pairs),
        "selected_products": len(needed),
        "skipped_products": skipped_products[:50],
        "fee_bps_per_side": float(args.fee_bps_per_side),
        "leadership_read": [
            "This is a per-signal table built from historical candle cache and fee/spread assumptions.",
            "Features stop at entry time; gross/net/survival columns are labels for model training and audit.",
            "It is meant to train a live gate after chronological validation, not to authorize live orders.",
        ],
    }
    return {"summary": summary, "rows": rows}


def write_outputs(payload: dict[str, Any]) -> None:
    rows = payload["rows"]
    JSON_PATH.write_text(json.dumps(payload["summary"], indent=2), encoding="utf-8")
    if rows:
        with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        CSV_PATH.write_text("", encoding="utf-8")
    summary = payload["summary"]
    positive = sum(1 for row in rows if row["survived_fees"])
    avg_net = float(np.mean([row["net_pct"] for row in rows])) if rows else 0.0
    lines = [
        "# Coinbase Spot Fee Survival Training Table",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Rows: `{summary['rows']}`",
        f"- Selected product/setup pairs: `{summary['selected_pairs']}`",
        f"- Selected products: `{summary['selected_products']}`",
        f"- Fee bps per side: `{summary['fee_bps_per_side']}`",
        f"- Survived-fee rows: `{positive}`",
        f"- Survival rate pct: `{round((positive / max(1, len(rows))) * 100.0, 6)}`",
        f"- Average net pct: `{round(avg_net, 6)}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in summary["leadership_read"]])
    lines.extend(["", "## Top Net Rows", "", "| Rank | Product | Variant | Time | Setup | Net % | Gross % | MFE % | MAE % |", "| ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |"])
    for rank, row in enumerate(sorted(rows, key=lambda item: item["net_pct"], reverse=True)[:30], start=1):
        setup = f"{row['trigger']} / {row['confirmation']} / {row['exit']}"
        lines.append(f"| {rank} | {row['product_id']} | {row['variant_id']} | {row['time']} | {setup} | {row['net_pct']:.4f} | {row['gross_pct']:.4f} | {row['future_mfe_pct']:.4f} | {row['future_mae_pct']:.4f} |")
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build per-signal Coinbase spot fee-survival ML training rows.")
    parser.add_argument("--matrix-path", default=str(MATRIX_PATH))
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-products", type=int, default=0)
    parser.add_argument("--max-variants", type=int, default=5000)
    parser.add_argument("--min-pair-signals", type=int, default=2)
    parser.add_argument("--min-pair-avg-net-pct", type=float, default=0.0)
    parser.add_argument("--negative-pair-multiplier", type=int, default=4)
    parser.add_argument("--max-pairs", type=int, default=0)
    parser.add_argument("--max-rows", type=int, default=250000)
    parser.add_argument("--fee-bps-per-side", type=float, default=120.0)
    parser.add_argument("--default-spread-bps", type=float, default=25.0)
    parser.add_argument("--max-spread-bps", type=float, default=150.0)
    return parser.parse_args()


def main() -> int:
    payload = build_table(parse_args())
    write_outputs(payload)
    print(json.dumps({"csv_path": str(CSV_PATH), "json_path": str(JSON_PATH), "md_path": str(MD_PATH), **payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
