#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_kraken_spot_guarded_frontier_lab import bps_change, load_json, sample_at_or_after, sample_at_or_before, to_float  # noqa: E402


DEFAULT_QUEUE_PATH = REPORTS / "kraken_spot_5000_experiment_queue.json"
DEFAULT_CACHE_PATH = REPORTS / "cache" / "kraken_spot_live_radar_ticks.json"
DEFAULT_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_5000_experiment_batch.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_5000_experiment_batch.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_5000_experiment_batch.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the Kraken 5,000-experiment queue against the bid/ask cache.")
    parser.add_argument("--queue-path", default=str(DEFAULT_QUEUE_PATH))
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--radar-path", default=str(DEFAULT_RADAR_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--taker-round-trip-bps", type=float, default=80.0)
    parser.add_argument("--profit-buffer-bps", type=float, default=50.0)
    parser.add_argument("--min-events-for-rank", type=int, default=3)
    parser.add_argument("--min-products-for-rank", type=int, default=1)
    parser.add_argument("--min-win-rate-for-rank", type=float, default=0.0)
    parser.add_argument("--top-n", type=int, default=200)
    return parser.parse_args()


def product_map(radar: dict[str, Any]) -> dict[str, str]:
    return {str(row.get("rest_pair") or ""): str(row.get("product_id") or row.get("rest_pair") or "") for row in radar.get("rows") or []}


def build_feature_frame(
    cache: dict[str, Any],
    radar: dict[str, Any],
    *,
    deploy_usd: float,
    taker_fee_bps: float,
    horizons: list[int],
) -> pd.DataFrame:
    samples_by_pair = cache.get("samples") if isinstance(cache, dict) else {}
    if not isinstance(samples_by_pair, dict):
        samples_by_pair = {}
    products = product_map(radar if isinstance(radar, dict) else {})
    rows: list[dict[str, Any]] = []
    for rest_pair, raw_samples in samples_by_pair.items():
        if not isinstance(raw_samples, list) or len(raw_samples) < 3:
            continue
        samples = sorted(raw_samples, key=lambda sample: to_float(sample.get("ts")))
        product_id = products.get(str(rest_pair), str(rest_pair))
        for idx in range(1, len(samples)):
            current = samples[idx]
            bid = to_float(current.get("bid"))
            ask = to_float(current.get("ask"))
            ts = to_float(current.get("ts"))
            if bid <= 0.0 or ask <= 0.0 or ask < bid:
                continue
            previous = samples[idx - 1]
            move_last = bps_change(bid, to_float(previous.get("bid")))
            ret_30 = bps_change(bid, to_float((sample_at_or_before(samples, ts - 30.0) or {}).get("bid")))
            ret_60 = bps_change(bid, to_float((sample_at_or_before(samples, ts - 60.0) or {}).get("bid")))
            ret_5m = bps_change(bid, to_float((sample_at_or_before(samples, ts - 300.0) or {}).get("bid")))
            ret_15m = bps_change(bid, to_float((sample_at_or_before(samples, ts - 900.0) or {}).get("bid")))
            moves = {"last": move_last, "30s": ret_30, "60s": ret_60, "5m": ret_5m, "15m": ret_15m}
            best_window, best_move = max(moves.items(), key=lambda item: item[1])
            best_short = max(move_last, ret_30, ret_60)
            spread_bps = ((ask - bid) / bid) * 10000.0
            signal_state = "live_hot" if best_short >= 25.0 else "building" if best_short >= 10.0 or ret_5m >= 25.0 else "dumping" if max(best_short, ret_5m, ret_15m) <= -10.0 else "stale_or_flat"
            base = {
                "rest_pair": str(rest_pair),
                "product_id": product_id,
                "entry_ts": ts,
                "sample_index": idx + 1,
                "sample_count": len(samples),
                "entry_bid": bid,
                "entry_ask": ask,
                "spread_bps": spread_bps,
                "signal_state": signal_state,
                "best_move_window": best_window,
                "best_move_bps": best_move,
                "best_short_bps": best_short,
                "move_last_bps": move_last,
                "ret_30s_bps": ret_30,
                "ret_60s_bps": ret_60,
                "ret_5m_bps": ret_5m,
                "ret_15m_bps": ret_15m,
            }
            entry_fee = deploy_usd * taker_fee_bps / 10000.0
            quantity = (deploy_usd - entry_fee) / ask
            for horizon in horizons:
                forward = sample_at_or_after(samples, ts + float(horizon))
                if not forward:
                    base[f"h{horizon}_net_pnl"] = None
                    base[f"h{horizon}_net_pct"] = None
                    continue
                exit_bid = to_float(forward.get("bid"))
                gross_exit = exit_bid * quantity
                exit_fee = gross_exit * taker_fee_bps / 10000.0
                net_exit = gross_exit - exit_fee
                net_pnl = net_exit - deploy_usd
                base[f"h{horizon}_net_pnl"] = net_pnl
                base[f"h{horizon}_net_pct"] = (net_pnl / deploy_usd) * 100.0
            rows.append(base)
    return pd.DataFrame(rows)


def parse_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


def experiment_mask(features: pd.DataFrame, experiment: dict[str, Any], *, hurdle_bps: float) -> pd.Series:
    mode = str(experiment.get("mode") or "")
    windows = parse_list(experiment.get("windows"))
    states = parse_list(experiment.get("signal_states"))
    mask = (
        features["best_move_window"].isin(windows)
        & features["signal_state"].isin(states)
        & (features["spread_bps"] <= to_float(experiment.get("max_spread_bps")))
        & (features["best_move_bps"] <= to_float(experiment.get("max_chase_bps")))
    )
    if mode in {"dump_reclaim", "anti_chase_reclaim"}:
        effective_move = features["best_short_bps"]
        mask = mask & (features["ret_5m_bps"] <= -abs(to_float(experiment.get("min_dump_5m_bps")))) & (
            features["best_short_bps"] >= to_float(experiment.get("min_rebound_bps"))
        )
    elif mode in {"compression_pop", "prebreak_compression", "first_lift_after_flat"}:
        effective_move = features["best_short_bps"]
        max_abs_5m = to_float(experiment.get("max_abs_5m_bps"), to_float(experiment.get("min_dump_5m_bps")))
        max_abs_15m = to_float(experiment.get("max_abs_15m_bps"), 999999.0)
        mask = mask & (features["ret_5m_bps"].abs() <= max_abs_5m) & (features["ret_15m_bps"].abs() <= max_abs_15m) & (
            features["best_short_bps"] >= to_float(experiment.get("min_rebound_bps"))
        )
    elif mode == "pullback_after_hot":
        effective_move = features["best_short_bps"]
        mask = mask & (features["ret_5m_bps"] >= to_float(experiment.get("min_dump_5m_bps"))) & (
            features["best_short_bps"] >= to_float(experiment.get("min_rebound_bps"))
        )
    else:
        effective_move = features["best_move_bps"]
    if "min_samples" in experiment and "sample_count" in features:
        mask = mask & (features["sample_count"] >= to_float(experiment.get("min_samples")))
    if "min_sample_index" in experiment and "sample_index" in features:
        mask = mask & (features["sample_index"] >= to_float(experiment.get("min_sample_index")))
    edge = effective_move - (hurdle_bps + features["spread_bps"])
    return mask & (edge >= to_float(experiment.get("min_edge_bps")))


def dedupe(df: pd.DataFrame, seconds: float) -> pd.DataFrame:
    if df.empty:
        return df
    rows = []
    last_by_product: dict[str, float] = {}
    for row in df.sort_values("entry_ts").itertuples(index=False):
        product_id = str(getattr(row, "product_id"))
        ts = float(getattr(row, "entry_ts"))
        if ts - last_by_product.get(product_id, 0.0) < seconds:
            continue
        last_by_product[product_id] = ts
        rows.append(row._asdict())
    return pd.DataFrame(rows)


def score_experiment(features: pd.DataFrame, experiment: dict[str, Any], *, hurdle_bps: float) -> dict[str, Any]:
    horizon = int(to_float(experiment.get("hold_horizon_seconds")))
    pnl_col = f"h{horizon}_net_pnl"
    pct_col = f"h{horizon}_net_pct"
    if pnl_col not in features:
        selected = features.iloc[0:0]
    else:
        mask = experiment_mask(features, experiment, hurdle_bps=hurdle_bps) & features[pnl_col].notna()
        selected = dedupe(features[mask], to_float(experiment.get("dedupe_seconds")))
    pnls = selected[pnl_col] if not selected.empty else pd.Series(dtype=float)
    pcts = selected[pct_col] if not selected.empty else pd.Series(dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gross_win = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses.sum())) if not losses.empty else 0.0
    return {
        "experiment_id": experiment.get("experiment_id"),
        "mode": experiment.get("mode"),
        "entry_window_set": experiment.get("entry_window_set"),
        "hold_horizon_seconds": horizon,
        "min_edge_bps": experiment.get("min_edge_bps"),
        "max_spread_bps": experiment.get("max_spread_bps"),
        "max_chase_bps": experiment.get("max_chase_bps"),
        "min_rebound_bps": experiment.get("min_rebound_bps"),
        "min_dump_5m_bps": experiment.get("min_dump_5m_bps"),
        "max_abs_5m_bps": experiment.get("max_abs_5m_bps"),
        "max_abs_15m_bps": experiment.get("max_abs_15m_bps"),
        "min_samples": experiment.get("min_samples"),
        "min_sample_index": experiment.get("min_sample_index"),
        "dedupe_seconds": experiment.get("dedupe_seconds"),
        "entries": int(len(selected)),
        "products": int(selected["product_id"].nunique()) if not selected.empty else 0,
        "avg_net_pnl": round(float(pnls.mean()), 6) if not pnls.empty else 0.0,
        "sum_net_pnl": round(float(pnls.sum()), 6) if not pnls.empty else 0.0,
        "avg_net_pct": round(float(pcts.mean()), 6) if not pcts.empty else 0.0,
        "win_rate_pct": round(float((pnls > 0).mean() * 100.0), 6) if not pnls.empty else 0.0,
        "worst_net_pnl": round(float(pnls.min()), 6) if not pnls.empty else 0.0,
        "best_net_pnl": round(float(pnls.max()), 6) if not pnls.empty else 0.0,
        "profit_factor": round(gross_win / gross_loss, 6) if gross_loss > 0 else round(gross_win, 6),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    queue_payload = load_json(Path(str(args.queue_path)))
    experiments = queue_payload.get("rows") if isinstance(queue_payload, dict) else []
    cache = load_json(Path(str(args.cache_path)))
    radar = load_json(Path(str(args.radar_path)))
    horizons = sorted({int(to_float(row.get("hold_horizon_seconds"))) for row in experiments if to_float(row.get("hold_horizon_seconds")) > 0})
    deploy_usd = float(args.starting_cash) * float(args.deploy_pct)
    features = build_feature_frame(
        cache if isinstance(cache, dict) else {},
        radar if isinstance(radar, dict) else {},
        deploy_usd=deploy_usd,
        taker_fee_bps=float(args.taker_round_trip_bps) / 2.0,
        horizons=horizons,
    )
    hurdle_bps = float(args.taker_round_trip_bps) + float(args.profit_buffer_bps)
    results = [score_experiment(features, row, hurdle_bps=hurdle_bps) for row in experiments]
    min_events = int(args.min_events_for_rank)
    min_products = int(args.min_products_for_rank)
    min_win_rate = float(args.min_win_rate_for_rank)
    for row in results:
        gate_penalty = 0.0
        if int(row.get("entries") or 0) < min_events:
            gate_penalty += 100.0
        if int(row.get("products") or 0) < min_products:
            gate_penalty += 50.0
        if to_float(row.get("win_rate_pct")) < min_win_rate:
            gate_penalty += 25.0
        row["rank_score"] = to_float(row.get("avg_net_pnl")) - gate_penalty
    results.sort(
        key=lambda row: (
            to_float(row.get("rank_score")),
            to_float(row.get("win_rate_pct")),
            int(row.get("products") or 0),
            int(row.get("entries") or 0),
        ),
        reverse=True,
    )
    top = results[: int(args.top_n)]
    by_mode: dict[str, dict[str, Any]] = {}
    for row in results:
        mode = str(row.get("mode"))
        bucket = by_mode.setdefault(mode, {"experiments": 0, "positive": 0, "best_avg_net_pnl": -999.0, "best_experiment_id": ""})
        bucket["experiments"] += 1
        if to_float(row.get("avg_net_pnl")) > 0 and int(row.get("entries") or 0) >= min_events:
            bucket["positive"] += 1
        if int(row.get("entries") or 0) >= min_events and to_float(row.get("avg_net_pnl")) > to_float(bucket.get("best_avg_net_pnl")):
            bucket["best_avg_net_pnl"] = row.get("avg_net_pnl")
            bucket["best_experiment_id"] = row.get("experiment_id")
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_5000_experiment_batch",
        "shadow_only": True,
        "parameters": {
            "queue_path": str(args.queue_path),
            "cache_path": str(args.cache_path),
            "radar_path": str(args.radar_path),
            "experiments": len(experiments),
            "feature_rows": int(len(features)),
            "starting_cash": float(args.starting_cash),
            "deploy_pct": float(args.deploy_pct),
            "deploy_usd": deploy_usd,
            "taker_round_trip_bps": float(args.taker_round_trip_bps),
            "profit_buffer_bps": float(args.profit_buffer_bps),
            "min_events_for_rank": min_events,
            "min_products_for_rank": min_products,
            "min_win_rate_for_rank": min_win_rate,
        },
        "read": [
            "This evaluates the explicit 5,000-experiment Kraken queue on the current full-universe bid/ask cache.",
            "All results model ask entry, bid exit, and Kraken starter taker fees on both sides.",
            "Positive rows are candidates for passive forward tape only; they are not live or paper-trading permission.",
        ],
        "mode_summary": by_mode,
        "top_results": top,
    }
    write_reports(payload, Path(str(args.json_path)), Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    rows = payload.get("top_results") or []
    columns = [
        "experiment_id",
        "mode",
        "entry_window_set",
        "hold_horizon_seconds",
        "entries",
        "products",
        "avg_net_pnl",
        "sum_net_pnl",
        "avg_net_pct",
        "win_rate_pct",
        "worst_net_pnl",
        "best_net_pnl",
        "profit_factor",
        "min_edge_bps",
        "max_spread_bps",
        "max_chase_bps",
        "min_rebound_bps",
        "min_dump_5m_bps",
        "max_abs_5m_bps",
        "max_abs_15m_bps",
        "min_samples",
        "min_sample_index",
        "dedupe_seconds",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    params = payload.get("parameters") or {}
    lines = [
        "# Kraken Spot 5,000 Experiment Batch",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Experiments: `{params.get('experiments')}`",
        f"- Feature rows: `{params.get('feature_rows')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("read") or []])
    lines.extend(["", "## Mode Summary", "", "| Mode | Experiments | Positive | Best Avg $ | Best ID |", "| --- | ---: | ---: | ---: | --- |"])
    for mode, stats in sorted((payload.get("mode_summary") or {}).items()):
        lines.append(
            "| {mode} | {experiments} | {positive} | {best_avg_net_pnl:.4f} | {best_experiment_id} |".format(
                mode=mode,
                experiments=stats.get("experiments", 0),
                positive=stats.get("positive", 0),
                best_avg_net_pnl=to_float(stats.get("best_avg_net_pnl")),
                best_experiment_id=stats.get("best_experiment_id", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Top Experiments",
            "",
            "| Rank | ID | Mode | Horizon | Entries | Products | Avg $ | Win % | Worst $ | Spread | Chase | Rebound | Dump5m |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, row in enumerate(rows[:50], start=1):
        lines.append(
            "| {idx} | {experiment_id} | {mode} | {hold_horizon_seconds} | {entries} | {products} | {avg_net_pnl:.4f} | {win_rate_pct:.2f} | {worst_net_pnl:.4f} | {max_spread_bps} | {max_chase_bps} | {min_rebound_bps} | {min_dump_5m_bps} |".format(
                idx=idx,
                **row,
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    payload = build(args)
    top = (payload.get("top_results") or [{}])[0]
    print(
        json.dumps(
            {
                "json_path": str(Path(str(args.json_path)).resolve()),
                "md_path": str(Path(str(args.md_path)).resolve()),
                "top_experiment": top.get("experiment_id"),
                "top_mode": top.get("mode"),
                "top_avg_net_pnl": top.get("avg_net_pnl"),
                "feature_rows": payload["parameters"]["feature_rows"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
