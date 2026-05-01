#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_CACHE_PATH = REPORTS / "cache" / "kraken_spot_live_radar_ticks.json"
DEFAULT_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_guarded_frontier_lab.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_guarded_frontier_lab.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_guarded_frontier_lab.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def parse_horizons(value: str) -> list[int]:
    return sorted({int(float(item.strip())) for item in str(value or "").split(",") if item.strip() and int(float(item.strip())) > 0})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay Kraken radar cache across guarded entry shapes.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--radar-path", default=str(DEFAULT_RADAR_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--taker-round-trip-bps", type=float, default=80.0)
    parser.add_argument("--profit-buffer-bps", type=float, default=50.0)
    parser.add_argument("--horizons-seconds", default="60,180,300,600")
    parser.add_argument("--min-entry-gap-seconds", type=float, default=60.0)
    parser.add_argument("--max-events-per-strategy", type=int, default=5000)
    return parser.parse_args()


@dataclass(frozen=True)
class Strategy:
    name: str
    min_edge_bps: float
    max_spread_bps: float
    max_chase_bps: float
    windows: tuple[str, ...]
    signal_states: tuple[str, ...]
    mode: str = "momentum"
    min_rebound_bps: float = 0.0
    min_dump_5m_bps: float = 0.0
    max_abs_5m_bps: float = 999999.0
    min_last_bps: float = -999999.0
    min_30s_bps: float = -999999.0
    min_60s_bps: float = -999999.0
    min_5m_bps: float = -999999.0
    min_positive_short_windows: int = 0
    max_last_dominance_ratio: float = 999999.0
    max_spread_to_short_ratio: float = 999999.0
    min_spread_compression_60s_bps: float = -999999.0


STRATEGIES = [
    Strategy("strict_current", 50.0, 100.0, 450.0, ("last", "30s", "60s", "5m"), ("live_hot",)),
    Strategy("impulse_no_5m", 50.0, 100.0, 450.0, ("last", "30s", "60s"), ("live_hot",)),
    Strategy("edge100_no_5m", 100.0, 100.0, 450.0, ("last", "30s", "60s"), ("live_hot",)),
    Strategy("edge150_no_5m", 150.0, 100.0, 450.0, ("last", "30s", "60s"), ("live_hot",)),
    Strategy("tight_spread_edge100", 100.0, 50.0, 450.0, ("last", "30s", "60s", "5m"), ("live_hot",)),
    Strategy("no_chase300", 50.0, 100.0, 300.0, ("last", "30s", "60s", "5m"), ("live_hot",)),
    Strategy("micro_impulse_edge75", 75.0, 75.0, 250.0, ("last", "30s"), ("live_hot",)),
    Strategy("five_min_breakout_tight", 100.0, 50.0, 350.0, ("5m",), ("live_hot",)),
    Strategy("dump_reclaim_tight", -999.0, 50.0, 200.0, ("last", "30s", "60s"), ("live_hot", "building"), "dump_reclaim", 25.0, 75.0),
    Strategy("hard_dump_reclaim", -999.0, 75.0, 300.0, ("last", "30s", "60s"), ("live_hot", "building"), "dump_reclaim", 50.0, 150.0),
    Strategy("micro_compression_pop", -999.0, 30.0, 200.0, ("last", "30s", "60s"), ("live_hot", "building"), "compression_pop", 25.0, 0.0, 50.0),
    Strategy(
        "confirmed_30_60_edge50",
        50.0,
        75.0,
        300.0,
        ("last", "30s", "60s"),
        ("live_hot",),
        min_last_bps=2.5,
        min_30s_bps=15.0,
        min_60s_bps=25.0,
        min_positive_short_windows=3,
        max_last_dominance_ratio=3.0,
        max_spread_to_short_ratio=0.5,
    ),
    Strategy(
        "confirmed_30_60_edge100_tight",
        100.0,
        50.0,
        275.0,
        ("last", "30s", "60s"),
        ("live_hot",),
        min_last_bps=2.5,
        min_30s_bps=20.0,
        min_60s_bps=35.0,
        min_positive_short_windows=3,
        max_last_dominance_ratio=2.5,
        max_spread_to_short_ratio=0.35,
    ),
    Strategy(
        "no_last_only_edge100",
        100.0,
        75.0,
        300.0,
        ("30s", "60s"),
        ("live_hot",),
        min_30s_bps=25.0,
        min_60s_bps=25.0,
        min_positive_short_windows=2,
        max_last_dominance_ratio=2.0,
        max_spread_to_short_ratio=0.45,
    ),
    Strategy(
        "compression_confirmed_pop",
        25.0,
        35.0,
        225.0,
        ("last", "30s", "60s"),
        ("live_hot", "building"),
        "compression_pop",
        30.0,
        0.0,
        75.0,
        min_last_bps=1.0,
        min_30s_bps=10.0,
        min_positive_short_windows=2,
        max_last_dominance_ratio=3.0,
        max_spread_to_short_ratio=0.4,
        min_spread_compression_60s_bps=1.0,
    ),
]


def sample_at_or_before(samples: list[dict[str, Any]], target_ts: float) -> dict[str, Any] | None:
    candidate = None
    for sample in samples:
        if to_float(sample.get("ts")) <= target_ts:
            candidate = sample
        else:
            break
    return candidate


def sample_at_or_after(samples: list[dict[str, Any]], target_ts: float) -> dict[str, Any] | None:
    for sample in samples:
        if to_float(sample.get("ts")) >= target_ts:
            return sample
    return None


def bps_change(now_bid: float, old_bid: float) -> float:
    if now_bid <= 0.0 or old_bid <= 0.0:
        return 0.0
    return ((now_bid - old_bid) / old_bid) * 10000.0


def feature_row(samples: list[dict[str, Any]], idx: int, *, hot_bps: float = 25.0, building_bps: float = 10.0) -> dict[str, Any]:
    current = samples[idx]
    now_ts = to_float(current.get("ts"))
    bid = to_float(current.get("bid"))
    ask = to_float(current.get("ask"))
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        return {}
    previous = samples[idx - 1] if idx > 0 else None
    moves = {
        "last": bps_change(bid, to_float(previous.get("bid"))) if previous else 0.0,
        "30s": 0.0,
        "60s": 0.0,
        "5m": 0.0,
        "15m": 0.0,
    }
    for label, seconds in (("30s", 30.0), ("60s", 60.0), ("5m", 300.0), ("15m", 900.0)):
        prior = sample_at_or_before(samples, now_ts - seconds)
        moves[label] = bps_change(bid, to_float(prior.get("bid"))) if prior else 0.0
    prior_60s = sample_at_or_before(samples, now_ts - 60.0)
    best_window, best_move = max(moves.items(), key=lambda item: item[1])
    best_short = max(moves["last"], moves["30s"], moves["60s"])
    spread_bps = ((ask - bid) / bid) * 10000.0
    spread_compression_60s_bps = 0.0
    if prior_60s:
        prior_bid = to_float(prior_60s.get("bid"))
        prior_ask = to_float(prior_60s.get("ask"))
        if prior_bid > 0.0 and prior_ask >= prior_bid:
            prior_spread_bps = ((prior_ask - prior_bid) / prior_bid) * 10000.0
            spread_compression_60s_bps = prior_spread_bps - spread_bps
    if best_short >= hot_bps:
        signal_state = "live_hot"
    elif best_short >= building_bps or moves["5m"] >= hot_bps:
        signal_state = "building"
    elif max(best_short, moves["5m"], moves["15m"]) <= -building_bps:
        signal_state = "dumping"
    else:
        signal_state = "stale_or_flat"
    return {
        "ts": now_ts,
        "bid": bid,
        "ask": ask,
        "spread_bps": spread_bps,
        "signal_state": signal_state,
        "best_move_window": best_window,
        "best_move_bps": best_move,
        "spread_compression_60s_bps": spread_compression_60s_bps,
        "moves": moves,
    }


def strategy_allows(row: dict[str, Any], strategy: Strategy, *, hurdle_bps: float) -> tuple[bool, float]:
    signal_state = str(row.get("signal_state") or "")
    if signal_state not in strategy.signal_states:
        return False, 0.0
    best_window = str(row.get("best_move_window") or "")
    if best_window not in strategy.windows:
        return False, 0.0
    spread_bps = to_float(row.get("spread_bps"))
    if spread_bps > strategy.max_spread_bps:
        return False, 0.0
    best_move_bps = to_float(row.get("best_move_bps"))
    if best_move_bps > strategy.max_chase_bps:
        return False, 0.0
    moves = row.get("moves") if isinstance(row.get("moves"), dict) else {}
    best_short = max(to_float(moves.get("last")), to_float(moves.get("30s")), to_float(moves.get("60s")))
    if to_float(moves.get("last")) < strategy.min_last_bps:
        return False, 0.0
    if to_float(moves.get("30s")) < strategy.min_30s_bps:
        return False, 0.0
    if to_float(moves.get("60s")) < strategy.min_60s_bps:
        return False, 0.0
    if to_float(moves.get("5m")) < strategy.min_5m_bps:
        return False, 0.0
    positive_short_windows = sum(1 for label in ("last", "30s", "60s") if to_float(moves.get(label)) > 0.0)
    if positive_short_windows < strategy.min_positive_short_windows:
        return False, 0.0
    longer_short = max(to_float(moves.get("30s")), to_float(moves.get("60s")))
    if strategy.max_last_dominance_ratio < 999999.0:
        if longer_short <= 0.0:
            return False, 0.0
        if to_float(moves.get("last")) > longer_short * strategy.max_last_dominance_ratio:
            return False, 0.0
    if strategy.max_spread_to_short_ratio < 999999.0:
        if best_short <= 0.0:
            return False, 0.0
        if spread_bps > best_short * strategy.max_spread_to_short_ratio:
            return False, 0.0
    if to_float(row.get("spread_compression_60s_bps")) < strategy.min_spread_compression_60s_bps:
        return False, 0.0
    if strategy.mode == "dump_reclaim":
        if to_float(moves.get("5m")) > -abs(strategy.min_dump_5m_bps):
            return False, 0.0
        if best_short < strategy.min_rebound_bps:
            return False, 0.0
        best_move_bps = best_short
    elif strategy.mode == "compression_pop":
        if abs(to_float(moves.get("5m"))) > strategy.max_abs_5m_bps:
            return False, 0.0
        if best_short < strategy.min_rebound_bps:
            return False, 0.0
        best_move_bps = best_short
    edge_bps = best_move_bps - (hurdle_bps + spread_bps)
    if edge_bps < strategy.min_edge_bps:
        return False, edge_bps
    return True, edge_bps


def evaluate_entry(
    samples: list[dict[str, Any]],
    idx: int,
    *,
    deploy_usd: float,
    taker_fee_bps: float,
    horizons: list[int],
) -> dict[str, dict[str, float]]:
    current = samples[idx]
    ask = to_float(current.get("ask"))
    entry_fee = deploy_usd * taker_fee_bps / 10000.0
    quantity = (deploy_usd - entry_fee) / ask if ask > 0 else 0.0
    marks: dict[str, dict[str, float]] = {}
    for horizon in horizons:
        end_ts = to_float(current.get("ts")) + float(horizon)
        path_marks = []
        for sample in samples[idx:]:
            sample_ts = to_float(sample.get("ts"))
            if sample_ts > end_ts:
                break
            exit_bid_path = to_float(sample.get("bid"))
            gross_exit_path = exit_bid_path * quantity
            exit_fee_path = gross_exit_path * taker_fee_bps / 10000.0
            net_exit_path = gross_exit_path - exit_fee_path
            net_pnl_path = net_exit_path - deploy_usd
            path_marks.append((sample_ts, exit_bid_path, net_pnl_path))
        forward = sample_at_or_after(samples, to_float(current.get("ts")) + float(horizon))
        if not forward:
            continue
        exit_bid = to_float(forward.get("bid"))
        gross_exit = exit_bid * quantity
        exit_fee = gross_exit * taker_fee_bps / 10000.0
        net_exit = gross_exit - exit_fee
        net_pnl = net_exit - deploy_usd
        max_path = max(path_marks, key=lambda item: item[2]) if path_marks else (0.0, 0.0, 0.0)
        min_path = min(path_marks, key=lambda item: item[2]) if path_marks else (0.0, 0.0, 0.0)
        marks[str(horizon)] = {
            "net_pnl": round(net_pnl, 6),
            "net_pct": round((net_pnl / deploy_usd) * 100.0, 6) if deploy_usd else 0.0,
            "exit_bid": exit_bid,
            "exit_ts": to_float(forward.get("ts")),
            "mfe_net_pnl": round(max_path[2], 6),
            "mfe_net_pct": round((max_path[2] / deploy_usd) * 100.0, 6) if deploy_usd else 0.0,
            "mfe_bid": max_path[1],
            "mfe_ts": max_path[0],
            "mae_net_pnl": round(min_path[2], 6),
            "mae_net_pct": round((min_path[2] / deploy_usd) * 100.0, 6) if deploy_usd else 0.0,
        }
    return marks


def product_map(radar: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in radar.get("rows") or []:
        rest_pair = str(row.get("rest_pair") or "")
        if rest_pair:
            out[rest_pair] = row
    return out


def summarize_strategy(events: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    summary: dict[str, Any] = {"entries": len(events), "horizons": {}, "products": len({event.get("product_id") for event in events})}
    for horizon in horizons:
        values = []
        for event in events:
            mark = (event.get("marks") or {}).get(str(horizon))
            if isinstance(mark, dict):
                values.append(mark)
        pnls = [to_float(mark.get("net_pnl")) for mark in values]
        pcts = [to_float(mark.get("net_pct")) for mark in values]
        summary["horizons"][str(horizon)] = {
            "marked": len(values),
            "win_rate_pct": round((sum(1 for pnl in pnls if pnl > 0) / len(pnls)) * 100.0, 6) if pnls else 0.0,
            "avg_net_pnl": round(sum(pnls) / len(pnls), 6) if pnls else 0.0,
            "sum_net_pnl": round(sum(pnls), 6),
            "avg_net_pct": round(sum(pcts) / len(pcts), 6) if pcts else 0.0,
            "mfe_positive_rate_pct": round((sum(1 for mark in values if to_float(mark.get("mfe_net_pnl")) > 0) / len(values)) * 100.0, 6) if values else 0.0,
            "avg_mfe_net_pct": round(sum(to_float(mark.get("mfe_net_pct")) for mark in values) / len(values), 6) if values else 0.0,
            "max_mfe_net_pct": round(max((to_float(mark.get("mfe_net_pct")) for mark in values), default=0.0), 6),
            "avg_mae_net_pct": round(sum(to_float(mark.get("mae_net_pct")) for mark in values) / len(values), 6) if values else 0.0,
        }
    return summary


def strategy_rank_value(summary: dict[str, Any], horizon: str) -> float:
    stats = (summary.get("horizons") or {}).get(horizon, {})
    if int(stats.get("marked") or 0) <= 0:
        return -999999.0
    return to_float(stats.get("avg_net_pnl"))


def build(args: argparse.Namespace) -> dict[str, Any]:
    cache = load_json(Path(str(args.cache_path)))
    radar = load_json(Path(str(args.radar_path)))
    samples_by_pair = cache.get("samples") if isinstance(cache, dict) else {}
    if not isinstance(samples_by_pair, dict):
        samples_by_pair = {}
    radar_by_pair = product_map(radar if isinstance(radar, dict) else {})
    horizons = parse_horizons(str(args.horizons_seconds))
    deploy_usd = float(args.starting_cash) * float(args.deploy_pct)
    taker_fee_bps = float(args.taker_round_trip_bps) / 2.0
    hurdle_bps = float(args.taker_round_trip_bps) + float(args.profit_buffer_bps)
    events_by_strategy: dict[str, list[dict[str, Any]]] = {strategy.name: [] for strategy in STRATEGIES}
    last_entry_ts: dict[tuple[str, str], float] = {}
    for rest_pair, raw_samples in samples_by_pair.items():
        if not isinstance(raw_samples, list) or len(raw_samples) < 4:
            continue
        samples = sorted(raw_samples, key=lambda sample: to_float(sample.get("ts")))
        meta = radar_by_pair.get(str(rest_pair), {})
        product_id = str(meta.get("product_id") or rest_pair)
        for idx in range(1, len(samples)):
            row = feature_row(samples, idx)
            if not row:
                continue
            for strategy in STRATEGIES:
                if len(events_by_strategy[strategy.name]) >= int(args.max_events_per_strategy):
                    continue
                allowed, edge_bps = strategy_allows(row, strategy, hurdle_bps=hurdle_bps)
                if not allowed:
                    continue
                key = (strategy.name, product_id)
                if to_float(row.get("ts")) - last_entry_ts.get(key, 0.0) < float(args.min_entry_gap_seconds):
                    continue
                marks = evaluate_entry(samples, idx, deploy_usd=deploy_usd, taker_fee_bps=taker_fee_bps, horizons=horizons)
                if not marks:
                    continue
                last_entry_ts[key] = to_float(row.get("ts"))
                events_by_strategy[strategy.name].append(
                    {
                        "strategy": strategy.name,
                        "product_id": product_id,
                        "rest_pair": rest_pair,
                        "entry_ts": round(to_float(row.get("ts")), 6),
                        "entry_bid": row.get("bid"),
                        "entry_ask": row.get("ask"),
                        "signal_state": row.get("signal_state"),
                        "best_move_window": row.get("best_move_window"),
                        "best_move_bps": round(to_float(row.get("best_move_bps")), 6),
                        "spread_bps": round(to_float(row.get("spread_bps")), 6),
                        "spread_compression_60s_bps": round(to_float(row.get("spread_compression_60s_bps")), 6),
                        "kraken_edge_bps": round(edge_bps, 6),
                        "marks": marks,
                    }
                )
    strategy_summaries = {
        name: summarize_strategy(events, horizons) for name, events in events_by_strategy.items()
    }
    ranked = sorted(
        (
            {
                "strategy": name,
                **summary,
                "rank_score": strategy_rank_value(summary, "300"),
            }
            for name, summary in strategy_summaries.items()
        ),
        key=lambda item: (
            strategy_rank_value(item, "300"),
            strategy_rank_value(item, "60"),
            item.get("entries", 0),
        ),
        reverse=True,
    )
    all_events = [event for events in events_by_strategy.values() for event in events]
    all_events.sort(key=lambda event: (event.get("strategy"), to_float(event.get("entry_ts"))))
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_guarded_frontier_lab",
        "shadow_only": True,
        "cache_generated_at": cache.get("generated_at") if isinstance(cache, dict) else None,
        "radar_generated_at": radar.get("generated_at") if isinstance(radar, dict) else None,
        "parameters": {
            "cache_path": str(args.cache_path),
            "radar_path": str(args.radar_path),
            "starting_cash": float(args.starting_cash),
            "deploy_pct": float(args.deploy_pct),
            "deploy_usd": deploy_usd,
            "taker_round_trip_bps": float(args.taker_round_trip_bps),
            "profit_buffer_bps": float(args.profit_buffer_bps),
            "horizons_seconds": horizons,
            "min_entry_gap_seconds": float(args.min_entry_gap_seconds),
        },
        "read": [
            "This is a cache replay lab, not live permission.",
            "Entries are synthetic would-enter events reconstructed from the Kraken radar tick cache using bid/ask samples and starter taker fees.",
            "Use this to choose which guarded entry shape deserves more passive forward-tape time; do not infer live profitability from one cache slice.",
        ],
        "strategy_summaries": strategy_summaries,
        "ranked_strategies": ranked,
        "events": all_events[-1000:],
    }
    write_reports(payload, Path(str(args.json_path)), Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    rows = []
    for item in payload.get("ranked_strategies") or []:
        flat = {"strategy": item.get("strategy"), "entries": item.get("entries"), "products": item.get("products")}
        for horizon, stats in (item.get("horizons") or {}).items():
            flat[f"h{horizon}_marked"] = stats.get("marked")
            flat[f"h{horizon}_win_rate_pct"] = stats.get("win_rate_pct")
            flat[f"h{horizon}_avg_net_pnl"] = stats.get("avg_net_pnl")
            flat[f"h{horizon}_sum_net_pnl"] = stats.get("sum_net_pnl")
            flat[f"h{horizon}_avg_net_pct"] = stats.get("avg_net_pct")
            flat[f"h{horizon}_mfe_positive_rate_pct"] = stats.get("mfe_positive_rate_pct")
            flat[f"h{horizon}_avg_mfe_net_pct"] = stats.get("avg_mfe_net_pct")
            flat[f"h{horizon}_max_mfe_net_pct"] = stats.get("max_mfe_net_pct")
            flat[f"h{horizon}_avg_mae_net_pct"] = stats.get("avg_mae_net_pct")
        rows.append(flat)
    columns = sorted({key for row in rows for key in row.keys()})
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    lines = [
        "# Kraken Guarded Frontier Lab",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Radar generated: `{payload.get('radar_generated_at')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("read") or []])
    lines.extend(
        [
            "",
            "## Ranked Strategies",
            "",
            "| Rank | Strategy | Entries | Products | 60s Avg $ | 60s Win % | 300s Avg $ | 300s Win % | 600s Avg $ | 600s Win % |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, item in enumerate(payload.get("ranked_strategies") or [], start=1):
        horizons = item.get("horizons") or {}
        h60 = horizons.get("60", {})
        h300 = horizons.get("300", {})
        h600 = horizons.get("600", {})
        lines.append(
            "| {idx} | {strategy} | {entries} | {products} | {h60_avg:.4f} | {h60_win:.2f} | {h300_avg:.4f} | {h300_win:.2f} | {h600_avg:.4f} | {h600_win:.2f} |".format(
                idx=idx,
                strategy=item.get("strategy"),
                entries=item.get("entries"),
                products=item.get("products"),
                h60_avg=to_float(h60.get("avg_net_pnl")),
                h60_win=to_float(h60.get("win_rate_pct")),
                h300_avg=to_float(h300.get("avg_net_pnl")),
                h300_win=to_float(h300.get("win_rate_pct")),
                h600_avg=to_float(h600.get("avg_net_pnl")),
                h600_win=to_float(h600.get("win_rate_pct")),
            )
        )
    lines.extend(["", "## MFE Upper Bound", ""])
    lines.extend(
        [
            "| Rank | Strategy | 60s Avg MFE % | 60s MFE Hit % | 300s Avg MFE % | 300s MFE Hit % | 600s Avg MFE % | 600s MFE Hit % |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, item in enumerate(payload.get("ranked_strategies") or [], start=1):
        horizons = item.get("horizons") or {}
        h60 = horizons.get("60", {})
        h300 = horizons.get("300", {})
        h600 = horizons.get("600", {})
        lines.append(
            "| {idx} | {strategy} | {h60_mfe:.4f} | {h60_hit:.2f} | {h300_mfe:.4f} | {h300_hit:.2f} | {h600_mfe:.4f} | {h600_hit:.2f} |".format(
                idx=idx,
                strategy=item.get("strategy"),
                h60_mfe=to_float(h60.get("avg_mfe_net_pct")),
                h60_hit=to_float(h60.get("mfe_positive_rate_pct")),
                h300_mfe=to_float(h300.get("avg_mfe_net_pct")),
                h300_hit=to_float(h300.get("mfe_positive_rate_pct")),
                h600_mfe=to_float(h600.get("avg_mfe_net_pct")),
                h600_hit=to_float(h600.get("mfe_positive_rate_pct")),
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    payload = build(args)
    top = (payload.get("ranked_strategies") or [{}])[0]
    print(
        json.dumps(
            {
                "json_path": str(Path(str(args.json_path)).resolve()),
                "md_path": str(Path(str(args.md_path)).resolve()),
                "top_strategy": top.get("strategy"),
                "top_entries": top.get("entries"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
