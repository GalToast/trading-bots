#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_kraken_spot_guarded_frontier_lab import (  # noqa: E402
    STRATEGIES,
    evaluate_entry,
    feature_row,
    load_json,
    parse_horizons,
    product_map,
    strategy_allows,
    to_float,
)


DEFAULT_CACHE_PATH = REPORTS / "cache" / "kraken_spot_live_radar_ticks.json"
DEFAULT_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_QUEUE_PATH = REPORTS / "kraken_spot_5000_experiment_queue.json"
DEFAULT_TAPE_PATH = REPORTS / "kraken_spot_frontier_strategy_tape.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_frontier_strategy_review.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_frontier_strategy_review.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_frontier_strategy_review.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def rebuild_tape(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    tmp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Passive forward tape for one Kraken frontier strategy.")
    parser.add_argument("--strategy-name", default="hard_dump_reclaim")
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--queue-path", default=str(DEFAULT_QUEUE_PATH))
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--radar-path", default=str(DEFAULT_RADAR_PATH))
    parser.add_argument("--tape-path", default=str(DEFAULT_TAPE_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--taker-round-trip-bps", type=float, default=80.0)
    parser.add_argument("--profit-buffer-bps", type=float, default=50.0)
    parser.add_argument("--horizons-seconds", default="60,180,300,600")
    parser.add_argument("--dedupe-seconds", type=float, default=600.0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--loop", action="store_true")
    return parser.parse_args()


def get_strategy(name: str):
    for strategy in STRATEGIES:
        if strategy.name == name:
            return strategy
    raise SystemExit(f"Unknown strategy {name!r}; choices: {', '.join(strategy.name for strategy in STRATEGIES)}")


def parse_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def get_experiment(path: Path, experiment_id: str) -> dict[str, Any] | None:
    if not experiment_id:
        return None
    payload = load_json(path)
    for row in payload.get("rows") or []:
        if row.get("experiment_id") == experiment_id:
            return row
    raise SystemExit(f"Unknown experiment id {experiment_id!r} in {path}")


def experiment_allows(row: dict[str, Any], experiment: dict[str, Any], *, hurdle_bps: float) -> tuple[bool, float]:
    if str(row.get("best_move_window") or "") not in parse_list(experiment.get("windows")):
        return False, 0.0
    if str(row.get("signal_state") or "") not in parse_list(experiment.get("signal_states")):
        return False, 0.0
    if to_float(row.get("spread_bps")) > to_float(experiment.get("max_spread_bps")):
        return False, 0.0
    if to_float(row.get("best_move_bps")) > to_float(experiment.get("max_chase_bps")):
        return False, 0.0
    moves = row.get("moves") if isinstance(row.get("moves"), dict) else {}
    best_short = max(to_float(moves.get("last")), to_float(moves.get("30s")), to_float(moves.get("60s")))
    mode = str(experiment.get("mode") or "")
    if mode in {"dump_reclaim", "anti_chase_reclaim"}:
        if to_float(moves.get("5m")) > -abs(to_float(experiment.get("min_dump_5m_bps"))):
            return False, 0.0
        if best_short < to_float(experiment.get("min_rebound_bps")):
            return False, 0.0
        effective_move = best_short
    elif mode == "compression_pop":
        if abs(to_float(moves.get("5m"))) > to_float(experiment.get("min_dump_5m_bps")):
            return False, 0.0
        if best_short < to_float(experiment.get("min_rebound_bps")):
            return False, 0.0
        effective_move = best_short
    elif mode == "pullback_after_hot":
        if to_float(moves.get("5m")) < to_float(experiment.get("min_dump_5m_bps")):
            return False, 0.0
        if best_short < to_float(experiment.get("min_rebound_bps")):
            return False, 0.0
        effective_move = best_short
    else:
        effective_move = to_float(row.get("best_move_bps"))
    edge_bps = effective_move - (hurdle_bps + to_float(row.get("spread_bps")))
    if edge_bps < to_float(experiment.get("min_edge_bps")):
        return False, edge_bps
    return True, edge_bps


def recent_duplicate(rows: list[dict[str, Any]], strategy_name: str, product_id: str, entry_ts: float, dedupe_seconds: float) -> bool:
    for row in reversed(rows):
        if row.get("strategy") != strategy_name or row.get("product_id") != product_id:
            continue
        return entry_ts - to_float(row.get("entry_ts")) < dedupe_seconds
    return False


def mark_rows(rows: list[dict[str, Any]], samples_by_pair: dict[str, list[dict[str, Any]]], args: argparse.Namespace) -> bool:
    changed = False
    horizons = parse_horizons(str(args.horizons_seconds))
    deploy_usd = float(args.starting_cash) * float(args.deploy_pct)
    taker_fee_bps = float(args.taker_round_trip_bps) / 2.0
    for row in rows:
        if row.get("status") == "complete":
            continue
        samples = samples_by_pair.get(str(row.get("rest_pair") or ""))
        if not samples:
            continue
        samples = sorted(samples, key=lambda sample: to_float(sample.get("ts")))
        fake_current = {"ts": row.get("entry_ts"), "ask": row.get("entry_ask")}
        idx = next((i for i, sample in enumerate(samples) if to_float(sample.get("ts")) >= to_float(fake_current.get("ts"))), None)
        if idx is None:
            continue
        marks = evaluate_entry(samples, idx, deploy_usd=deploy_usd, taker_fee_bps=taker_fee_bps, horizons=horizons)
        existing = row.get("marks") if isinstance(row.get("marks"), dict) else {}
        for key, mark in marks.items():
            if key not in existing:
                existing[key] = mark
                changed = True
        row["marks"] = existing
        expected = {str(horizon) for horizon in horizons}
        row["status"] = "complete" if expected and expected.issubset(set(existing.keys())) else "pending"
    return changed


def summarize(rows: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    summary: dict[str, Any] = {"entries": len(rows), "pending": sum(1 for row in rows if row.get("status") != "complete"), "horizons": {}}
    for horizon in horizons:
        marks = [(row.get("marks") or {}).get(str(horizon)) for row in rows]
        marks = [mark for mark in marks if isinstance(mark, dict)]
        pnls = [to_float(mark.get("net_pnl")) for mark in marks]
        pcts = [to_float(mark.get("net_pct")) for mark in marks]
        summary["horizons"][str(horizon)] = {
            "marked": len(marks),
            "win_rate_pct": round((sum(1 for pnl in pnls if pnl > 0) / len(pnls)) * 100.0, 6) if pnls else 0.0,
            "avg_net_pnl": round(sum(pnls) / len(pnls), 6) if pnls else 0.0,
            "sum_net_pnl": round(sum(pnls), 6),
            "avg_net_pct": round(sum(pcts) / len(pcts), 6) if pcts else 0.0,
        }
    return summary


def write_reports(payload: dict[str, Any], csv_path: Path, md_path: Path) -> None:
    rows = payload.get("rows") or []
    horizons = payload.get("parameters", {}).get("horizons_seconds") or []
    cols = ["entry_at", "strategy", "product_id", "status", "entry_signal_state", "entry_best_move_window", "entry_best_move_bps"]
    for horizon in horizons:
        cols += [f"h{horizon}_net_pnl", f"h{horizon}_net_pct"]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            out = {col: row.get(col, "") for col in cols}
            for horizon in horizons:
                mark = (row.get("marks") or {}).get(str(horizon)) or {}
                out[f"h{horizon}_net_pnl"] = mark.get("net_pnl", "")
                out[f"h{horizon}_net_pct"] = mark.get("net_pct", "")
            writer.writerow(out)
    summary = payload.get("summary") or {}
    lines = [
        "# Kraken Frontier Strategy Tape",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Strategy: `{payload.get('strategy')}`",
        f"- Passive only: `{payload.get('passive_only')}`",
        "",
        "## Summary",
        "",
        f"- Entries: `{summary.get('entries', 0)}`",
        f"- Pending: `{summary.get('pending', 0)}`",
        "",
        "| Horizon | Marked | Win % | Avg Net $ | Sum Net $ | Avg Net % |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for horizon, stats in (summary.get("horizons") or {}).items():
        lines.append(
            "| {horizon}s | {marked} | {win_rate_pct:.2f} | {avg_net_pnl:.4f} | {sum_net_pnl:.4f} | {avg_net_pct:.4f} |".format(
                horizon=horizon,
                **stats,
            )
        )
    lines.extend(["", "## Recent Entries", "", "| Entry | Product | Status | Window | Move bps | Last Mark | Last Net $ |", "| --- | --- | --- | --- | ---: | --- | ---: |"])
    for row in rows[-30:][::-1]:
        marks = row.get("marks") if isinstance(row.get("marks"), dict) else {}
        last_key = max((int(key) for key in marks.keys()), default=0)
        last = marks.get(str(last_key), {}) if last_key else {}
        lines.append(
            "| {entry_at} | {product_id} | {status} | {entry_best_move_window} | {move:.4f} | {last_key}s | {net:.4f} |".format(
                entry_at=row.get("entry_at"),
                product_id=row.get("product_id"),
                status=row.get("status"),
                entry_best_move_window=row.get("entry_best_move_window"),
                move=to_float(row.get("entry_best_move_bps")),
                last_key=last_key,
                net=to_float(last.get("net_pnl")) if isinstance(last, dict) else 0.0,
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_once(args: argparse.Namespace) -> dict[str, Any]:
    experiment = get_experiment(Path(str(args.queue_path)), str(args.experiment_id))
    strategy = None if experiment else get_strategy(str(args.strategy_name))
    strategy_label = str(experiment.get("experiment_id")) if experiment else strategy.name
    cache = load_json(Path(str(args.cache_path)))
    radar = load_json(Path(str(args.radar_path)))
    samples_by_pair = cache.get("samples") if isinstance(cache, dict) else {}
    if not isinstance(samples_by_pair, dict):
        samples_by_pair = {}
    meta_by_pair = product_map(radar if isinstance(radar, dict) else {})
    all_rows = load_jsonl(Path(str(args.tape_path)))
    rows = [row for row in all_rows if row.get("strategy") == strategy_label]
    changed = mark_rows(rows, samples_by_pair, args)
    horizons = parse_horizons(str(args.horizons_seconds))
    hurdle_bps = float(args.taker_round_trip_bps) + float(args.profit_buffer_bps)
    now_iso = utc_now_iso()
    new_entries = 0
    for rest_pair, raw_samples in samples_by_pair.items():
        if not isinstance(raw_samples, list) or len(raw_samples) < 2:
            continue
        samples = sorted(raw_samples, key=lambda sample: to_float(sample.get("ts")))
        idx = len(samples) - 1
        row = feature_row(samples, idx)
        if not row:
            continue
        allowed, edge_bps = (
            experiment_allows(row, experiment, hurdle_bps=hurdle_bps)
            if experiment
            else strategy_allows(row, strategy, hurdle_bps=hurdle_bps)
        )
        meta = meta_by_pair.get(str(rest_pair), {})
        product_id = str(meta.get("product_id") or rest_pair)
        dedupe_seconds = to_float((experiment or {}).get("dedupe_seconds"), float(args.dedupe_seconds))
        if not allowed or recent_duplicate(rows, strategy_label, product_id, to_float(row.get("ts")), dedupe_seconds):
            continue
        rows.append(
            {
        "event": "frontier_strategy_enter",
                "strategy": strategy_label,
                "experiment_id": (experiment or {}).get("experiment_id", ""),
                "entry_at": now_iso,
                "entry_ts": row.get("ts"),
                "rest_pair": rest_pair,
                "product_id": product_id,
                "status": "pending",
                "entry_bid": row.get("bid"),
                "entry_ask": row.get("ask"),
                "entry_signal_state": row.get("signal_state"),
                "entry_best_move_window": row.get("best_move_window"),
                "entry_best_move_bps": round(to_float(row.get("best_move_bps")), 6),
                "entry_spread_bps": round(to_float(row.get("spread_bps")), 6),
                "entry_kraken_edge_bps": round(edge_bps, 6),
                "marks": {},
            }
        )
        changed = True
        new_entries += 1
    if changed:
        other_rows = [row for row in all_rows if row.get("strategy") != strategy_label]
        rebuild_tape(Path(str(args.tape_path)), other_rows + rows)
    payload = {
        "generated_at": now_iso,
        "mode": "kraken_spot_frontier_strategy_tape",
        "strategy": strategy_label,
        "experiment_id": (experiment or {}).get("experiment_id", ""),
        "shadow_only": True,
        "passive_only": True,
        "parameters": {
            "horizons_seconds": horizons,
            "dedupe_seconds": to_float((experiment or {}).get("dedupe_seconds"), float(args.dedupe_seconds)),
            "starting_cash": float(args.starting_cash),
            "deploy_pct": float(args.deploy_pct),
            "taker_round_trip_bps": float(args.taker_round_trip_bps),
            "profit_buffer_bps": float(args.profit_buffer_bps),
        },
        "new_entries": new_entries,
        "summary": summarize(rows, horizons),
        "rows": rows[-200:],
    }
    write_json(Path(str(args.json_path)), payload)
    write_reports(payload, Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def main() -> None:
    args = parse_args()
    while True:
        payload = build_once(args)
        if not args.loop:
            print(json.dumps({"json_path": str(Path(args.json_path).resolve()), "new_entries": payload["new_entries"], "entries": payload["summary"]["entries"]}, indent=2))
            return
        time.sleep(max(1.0, float(args.poll_seconds)))


if __name__ == "__main__":
    main()
