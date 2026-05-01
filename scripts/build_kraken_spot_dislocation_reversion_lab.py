#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import kraken_config as cfg  # noqa: E402
from build_kraken_spot_guarded_frontier_lab import (  # noqa: E402
    bps_change,
    load_json,
    sample_at_or_after,
    sample_at_or_before,
    to_float,
    write_json,
)


DEFAULT_CACHE_PATH = REPORTS / "cache" / "kraken_spot_live_radar_ticks.json"
DEFAULT_RADAR_PATH = REPORTS / "kraken_spot_live_radar.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_dislocation_reversion_lab.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_dislocation_reversion_lab.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_dislocation_reversion_lab.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_horizons(value: str) -> list[int]:
    return sorted({int(float(item.strip())) for item in str(value or "").split(",") if item.strip() and int(float(item.strip())) > 0})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay Kraken bid/ask cache for long-only dislocation snapback candidates.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--radar-path", default=str(DEFAULT_RADAR_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--allowed-quotes", default="", help="Optional comma-separated quote filter, e.g. USD,USDT,USDC.")
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--deploy-pct", type=float, default=0.8)
    parser.add_argument("--execution-model", choices=("taker", "maker-entry-taker-exit", "maker-upper"), default="taker")
    parser.add_argument("--maker-fee-bps", type=float, default=cfg.DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--taker-fee-bps", type=float, default=cfg.DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--profit-buffer-bps", type=float, default=25.0)
    parser.add_argument("--horizons-seconds", default="30,60,180,300,600")
    parser.add_argument("--min-entry-gap-seconds", type=float, default=60.0)
    parser.add_argument("--max-events-per-setup", type=int, default=5000)
    return parser.parse_args()


@dataclass(frozen=True)
class Setup:
    name: str
    lookback_seconds: int
    min_dislocation_bps: float
    max_spread_bps: float
    min_ask_discount_bps: float
    min_spread_expansion_bps: float = -999999.0
    max_prior_context_abs_bps: float = 999999.0


SETUPS = [
    Setup("micro_snapback_20_30s", 30, 20.0, 125.0, 5.0),
    Setup("micro_snapback_20_60s", 60, 20.0, 125.0, 5.0),
    Setup("snapback_35_60s", 60, 35.0, 175.0, 10.0),
    Setup("snapback_50_60s", 60, 50.0, 225.0, 20.0),
    Setup("deep_washout_100_60s", 60, 100.0, 300.0, 45.0),
    Setup("spread_washout_30_60s", 60, 30.0, 300.0, 10.0, min_spread_expansion_bps=25.0),
    Setup("spread_washout_50_180s", 180, 50.0, 350.0, 20.0, min_spread_expansion_bps=40.0),
    Setup("calm_context_snapback_25_60s", 60, 25.0, 150.0, 10.0, max_prior_context_abs_bps=50.0),
    Setup("calm_context_snapback_50_180s", 180, 50.0, 250.0, 20.0, max_prior_context_abs_bps=100.0),
]


def mid_price(sample: dict[str, Any]) -> float:
    bid = to_float(sample.get("bid"))
    ask = to_float(sample.get("ask"))
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        return 0.0
    return (bid + ask) / 2.0


def spread_bps(sample: dict[str, Any]) -> float:
    bid = to_float(sample.get("bid"))
    ask = to_float(sample.get("ask"))
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        return 0.0
    return ((ask - bid) / bid) * 10000.0


def product_map(radar: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in radar.get("rows") or []:
        rest_pair = str(row.get("rest_pair") or "")
        if rest_pair:
            out[rest_pair] = row
    return out


def parse_set(value: str) -> set[str]:
    return {item.strip().upper() for item in str(value or "").split(",") if item.strip()}


def feature_row(samples: list[dict[str, Any]], idx: int, setup: Setup) -> dict[str, Any]:
    current = samples[idx]
    now_ts = to_float(current.get("ts"))
    prior = sample_at_or_before(samples, now_ts - float(setup.lookback_seconds))
    context_prior = sample_at_or_before(samples, now_ts - float(setup.lookback_seconds * 2))
    if not prior:
        return {}
    current_mid = mid_price(current)
    prior_mid = mid_price(prior)
    if current_mid <= 0.0 or prior_mid <= 0.0:
        return {}
    current_ask = to_float(current.get("ask"))
    move_mid_bps = bps_change(current_mid, prior_mid)
    dislocation_bps = max(-move_mid_bps, 0.0)
    ask_discount_bps = ((prior_mid - current_ask) / prior_mid) * 10000.0 if prior_mid > 0 else 0.0
    current_spread_bps = spread_bps(current)
    prior_spread_bps = spread_bps(prior)
    context_move_bps = 0.0
    if context_prior:
        context_mid = mid_price(context_prior)
        context_move_bps = bps_change(prior_mid, context_mid) if context_mid > 0 else 0.0
    return {
        "ts": now_ts,
        "bid": to_float(current.get("bid")),
        "ask": current_ask,
        "mid": current_mid,
        "prior_mid": prior_mid,
        "lookback_seconds": setup.lookback_seconds,
        "move_mid_bps": move_mid_bps,
        "dislocation_bps": dislocation_bps,
        "ask_discount_bps": ask_discount_bps,
        "spread_bps": current_spread_bps,
        "prior_spread_bps": prior_spread_bps,
        "spread_expansion_bps": current_spread_bps - prior_spread_bps,
        "context_move_bps": context_move_bps,
    }


def setup_allows(row: dict[str, Any], setup: Setup) -> tuple[bool, float]:
    if to_float(row.get("dislocation_bps")) < setup.min_dislocation_bps:
        return False, 0.0
    if to_float(row.get("spread_bps")) > setup.max_spread_bps:
        return False, 0.0
    if to_float(row.get("ask_discount_bps")) < setup.min_ask_discount_bps:
        return False, 0.0
    if to_float(row.get("spread_expansion_bps")) < setup.min_spread_expansion_bps:
        return False, 0.0
    if abs(to_float(row.get("context_move_bps"))) > setup.max_prior_context_abs_bps:
        return False, 0.0
    opportunity_bps = to_float(row.get("ask_discount_bps")) - to_float(row.get("spread_bps"))
    return True, opportunity_bps


def evaluate_long_entry(
    samples: list[dict[str, Any]],
    idx: int,
    *,
    deploy_usd: float,
    execution_model: str,
    maker_fee_bps: float,
    taker_fee_bps: float,
    profit_buffer_bps: float,
    horizons: list[int],
) -> dict[str, dict[str, float]]:
    current = samples[idx]
    entry_price = to_float(current.get("bid")) if execution_model in {"maker-entry-taker-exit", "maker-upper"} else to_float(current.get("ask"))
    entry_ts = to_float(current.get("ts"))
    entry_fee_bps = maker_fee_bps if execution_model in {"maker-entry-taker-exit", "maker-upper"} else taker_fee_bps
    exit_fee_bps = maker_fee_bps if execution_model == "maker-upper" else taker_fee_bps
    exit_field = "ask" if execution_model == "maker-upper" else "bid"
    entry_fee = deploy_usd * entry_fee_bps / 10000.0
    quantity = (deploy_usd - entry_fee) / entry_price if entry_price > 0 else 0.0
    exit_fee_mult = 1.0 - (exit_fee_bps / 10000.0)
    target_net_exit = deploy_usd * (1.0 + (profit_buffer_bps / 10000.0))
    target_bid = target_net_exit / (quantity * exit_fee_mult) if quantity > 0 and exit_fee_mult > 0 else 0.0
    marks: dict[str, dict[str, float]] = {}
    for horizon in horizons:
        end_ts = entry_ts + float(horizon)
        path_marks: list[tuple[float, float, float]] = []
        target_hit_ts = 0.0
        for sample in samples[idx:]:
            sample_ts = to_float(sample.get("ts"))
            if sample_ts > end_ts:
                break
            exit_bid_path = to_float(sample.get(exit_field))
            gross_exit_path = exit_bid_path * quantity
            exit_fee_path = gross_exit_path * exit_fee_bps / 10000.0
            net_exit_path = gross_exit_path - exit_fee_path
            net_pnl_path = net_exit_path - deploy_usd
            path_marks.append((sample_ts, exit_bid_path, net_pnl_path))
            if not target_hit_ts and target_bid > 0.0 and exit_bid_path >= target_bid:
                target_hit_ts = sample_ts
        forward = sample_at_or_after(samples, end_ts)
        if not forward:
            continue
        exit_bid = to_float(forward.get(exit_field))
        gross_exit = exit_bid * quantity
        exit_fee = gross_exit * exit_fee_bps / 10000.0
        net_exit = gross_exit - exit_fee
        net_pnl = net_exit - deploy_usd
        max_path = max(path_marks, key=lambda item: item[2]) if path_marks else (0.0, 0.0, 0.0)
        min_path = min(path_marks, key=lambda item: item[2]) if path_marks else (0.0, 0.0, 0.0)
        marks[str(horizon)] = {
            "net_pnl": round(net_pnl, 6),
            "net_pct": round((net_pnl / deploy_usd) * 100.0, 6) if deploy_usd else 0.0,
            "exit_bid": exit_bid,
            "exit_ts": to_float(forward.get("ts")),
            "entry_price": entry_price,
            "exit_price_field": exit_field,
            "entry_fee_bps": entry_fee_bps,
            "exit_fee_bps": exit_fee_bps,
            "mfe_net_pnl": round(max_path[2], 6),
            "mfe_net_pct": round((max_path[2] / deploy_usd) * 100.0, 6) if deploy_usd else 0.0,
            "mae_net_pnl": round(min_path[2], 6),
            "mae_net_pct": round((min_path[2] / deploy_usd) * 100.0, 6) if deploy_usd else 0.0,
            "target_bid": round(target_bid, 12),
            "target_hit": bool(target_hit_ts),
            "target_hit_seconds": round(target_hit_ts - entry_ts, 6) if target_hit_ts else 0.0,
        }
    return marks


def summarize_setup(events: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    summary: dict[str, Any] = {"entries": len(events), "products": len({event.get("product_id") for event in events}), "horizons": {}}
    for horizon in horizons:
        marks = [(event.get("marks") or {}).get(str(horizon)) for event in events]
        values = [mark for mark in marks if isinstance(mark, dict)]
        pnls = [to_float(mark.get("net_pnl")) for mark in values]
        pcts = [to_float(mark.get("net_pct")) for mark in values]
        mfes = [to_float(mark.get("mfe_net_pct")) for mark in values]
        summary["horizons"][str(horizon)] = {
            "marked": len(values),
            "win_rate_pct": round((sum(1 for pnl in pnls if pnl > 0) / len(pnls)) * 100.0, 6) if pnls else 0.0,
            "avg_net_pnl": round(sum(pnls) / len(pnls), 6) if pnls else 0.0,
            "sum_net_pnl": round(sum(pnls), 6),
            "avg_net_pct": round(sum(pcts) / len(pcts), 6) if pcts else 0.0,
            "mfe_positive_rate_pct": round((sum(1 for mfe in mfes if mfe > 0) / len(mfes)) * 100.0, 6) if mfes else 0.0,
            "avg_mfe_net_pct": round(sum(mfes) / len(mfes), 6) if mfes else 0.0,
            "target_hit_rate_pct": round((sum(1 for mark in values if bool(mark.get("target_hit"))) / len(values)) * 100.0, 6) if values else 0.0,
        }
    return summary


def rank_value(summary: dict[str, Any], horizon: str) -> float:
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
    allowed_quotes = parse_set(str(args.allowed_quotes))
    horizons = parse_horizons(str(args.horizons_seconds))
    deploy_usd = float(args.starting_cash) * float(args.deploy_pct)
    events_by_setup: dict[str, list[dict[str, Any]]] = {setup.name: [] for setup in SETUPS}
    last_entry_ts: dict[tuple[str, str], float] = {}
    for rest_pair, raw_samples in samples_by_pair.items():
        if not isinstance(raw_samples, list) or len(raw_samples) < 4:
            continue
        samples = sorted(raw_samples, key=lambda sample: to_float(sample.get("ts")))
        meta = radar_by_pair.get(str(rest_pair), {})
        product_id = str(meta.get("product_id") or rest_pair)
        if allowed_quotes and str(meta.get("quote_currency") or "").upper() not in allowed_quotes:
            continue
        if meta and not bool(meta.get("can_trade_starting_cash", True)):
            continue
        for idx in range(1, len(samples)):
            for setup in SETUPS:
                if len(events_by_setup[setup.name]) >= int(args.max_events_per_setup):
                    continue
                row = feature_row(samples, idx, setup)
                if not row:
                    continue
                allowed, opportunity_bps = setup_allows(row, setup)
                if not allowed:
                    continue
                key = (setup.name, product_id)
                if to_float(row.get("ts")) - last_entry_ts.get(key, 0.0) < float(args.min_entry_gap_seconds):
                    continue
                marks = evaluate_long_entry(
                    samples,
                    idx,
                    deploy_usd=deploy_usd,
                    execution_model=str(args.execution_model),
                    maker_fee_bps=float(args.maker_fee_bps),
                    taker_fee_bps=float(args.taker_fee_bps),
                    profit_buffer_bps=float(args.profit_buffer_bps),
                    horizons=horizons,
                )
                if not marks:
                    continue
                last_entry_ts[key] = to_float(row.get("ts"))
                events_by_setup[setup.name].append(
                    {
                        "setup": setup.name,
                        "product_id": product_id,
                        "rest_pair": rest_pair,
                        "entry_ts": round(to_float(row.get("ts")), 6),
                        "entry_bid": row.get("bid"),
                        "entry_ask": row.get("ask"),
                        "spread_bps": round(to_float(row.get("spread_bps")), 6),
                        "lookback_seconds": row.get("lookback_seconds"),
                        "move_mid_bps": round(to_float(row.get("move_mid_bps")), 6),
                        "dislocation_bps": round(to_float(row.get("dislocation_bps")), 6),
                        "ask_discount_bps": round(to_float(row.get("ask_discount_bps")), 6),
                        "spread_expansion_bps": round(to_float(row.get("spread_expansion_bps")), 6),
                        "context_move_bps": round(to_float(row.get("context_move_bps")), 6),
                        "opportunity_bps": round(opportunity_bps, 6),
                        "marks": marks,
                    }
                )
    setup_summaries = {name: summarize_setup(events, horizons) for name, events in events_by_setup.items()}
    ranked = sorted(
        ({"setup": name, **summary, "rank_score": rank_value(summary, "300")} for name, summary in setup_summaries.items()),
        key=lambda item: (rank_value(item, "300"), rank_value(item, "60"), item.get("entries", 0)),
        reverse=True,
    )
    all_events = [event for events in events_by_setup.values() for event in events]
    all_events.sort(key=lambda event: (event.get("setup"), to_float(event.get("entry_ts"))))
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_dislocation_reversion_lab",
        "shadow_only": True,
        "cache_generated_at": cache.get("generated_at") if isinstance(cache, dict) else None,
        "radar_generated_at": radar.get("generated_at") if isinstance(radar, dict) else None,
        "parameters": {
            "cache_path": str(args.cache_path),
            "radar_path": str(args.radar_path),
            "allowed_quotes": sorted(allowed_quotes),
            "starting_cash": float(args.starting_cash),
            "deploy_pct": float(args.deploy_pct),
            "deploy_usd": deploy_usd,
            "execution_model": str(args.execution_model),
            "maker_fee_bps": float(args.maker_fee_bps),
            "taker_fee_bps": float(args.taker_fee_bps),
            "profit_buffer_bps": float(args.profit_buffer_bps),
            "horizons_seconds": horizons,
            "min_entry_gap_seconds": float(args.min_entry_gap_seconds),
        },
        "read": [
            "This is a cache replay lab, not live permission.",
            "It tests long-only spot snapback after downward bid/ask dislocation. Execution model is declared in parameters.",
            "Positive rows would still need public forward tape plus maker/microfill calibration before live use.",
        ],
        "setup_summaries": setup_summaries,
        "ranked_setups": ranked,
        "events": all_events[-1000:],
    }
    write_reports(payload, Path(str(args.json_path)), Path(str(args.csv_path)), Path(str(args.md_path)))
    return payload


def write_reports(payload: dict[str, Any], json_path: Path, csv_path: Path, md_path: Path) -> None:
    write_json(json_path, payload)
    rows = []
    for item in payload.get("ranked_setups") or []:
        flat = {"setup": item.get("setup"), "entries": item.get("entries"), "products": item.get("products")}
        for horizon, stats in (item.get("horizons") or {}).items():
            flat[f"h{horizon}_marked"] = stats.get("marked")
            flat[f"h{horizon}_win_rate_pct"] = stats.get("win_rate_pct")
            flat[f"h{horizon}_avg_net_pnl"] = stats.get("avg_net_pnl")
            flat[f"h{horizon}_sum_net_pnl"] = stats.get("sum_net_pnl")
            flat[f"h{horizon}_avg_net_pct"] = stats.get("avg_net_pct")
            flat[f"h{horizon}_mfe_positive_rate_pct"] = stats.get("mfe_positive_rate_pct")
            flat[f"h{horizon}_avg_mfe_net_pct"] = stats.get("avg_mfe_net_pct")
            flat[f"h{horizon}_target_hit_rate_pct"] = stats.get("target_hit_rate_pct")
        rows.append(flat)
    columns = sorted({key for row in rows for key in row.keys()})
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    lines = [
        "# Kraken Spot Dislocation Reversion Lab",
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
            "## Ranked Setups",
            "",
            "| Rank | Setup | Entries | Products | 60s Avg $ | 60s Win % | 300s Avg $ | 300s Win % | 600s Avg $ | 600s Win % |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, item in enumerate(payload.get("ranked_setups") or [], start=1):
        horizons = item.get("horizons") or {}
        h60 = horizons.get("60", {})
        h300 = horizons.get("300", {})
        h600 = horizons.get("600", {})
        lines.append(
            "| {idx} | {setup} | {entries} | {products} | {h60_avg:.4f} | {h60_win:.2f} | {h300_avg:.4f} | {h300_win:.2f} | {h600_avg:.4f} | {h600_win:.2f} |".format(
                idx=idx,
                setup=item.get("setup"),
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
    lines.extend(["", "## MFE/Target Upper Bound", ""])
    lines.extend(
        [
            "| Rank | Setup | 60s Avg MFE % | 60s MFE Hit % | 300s Avg MFE % | 300s Target Hit % | 600s Avg MFE % | 600s Target Hit % |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for idx, item in enumerate(payload.get("ranked_setups") or [], start=1):
        horizons = item.get("horizons") or {}
        h60 = horizons.get("60", {})
        h300 = horizons.get("300", {})
        h600 = horizons.get("600", {})
        lines.append(
            "| {idx} | {setup} | {h60_mfe:.4f} | {h60_hit:.2f} | {h300_mfe:.4f} | {h300_target:.2f} | {h600_mfe:.4f} | {h600_target:.2f} |".format(
                idx=idx,
                setup=item.get("setup"),
                h60_mfe=to_float(h60.get("avg_mfe_net_pct")),
                h60_hit=to_float(h60.get("mfe_positive_rate_pct")),
                h300_mfe=to_float(h300.get("avg_mfe_net_pct")),
                h300_target=to_float(h300.get("target_hit_rate_pct")),
                h600_mfe=to_float(h600.get("avg_mfe_net_pct")),
                h600_target=to_float(h600.get("target_hit_rate_pct")),
            )
        )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    payload = build(args)
    top = (payload.get("ranked_setups") or [{}])[0]
    print(
        json.dumps(
            {
                "json_path": str(Path(str(args.json_path)).resolve()),
                "md_path": str(Path(str(args.md_path)).resolve()),
                "top_setup": top.get("setup"),
                "top_entries": top.get("entries"),
                "top_rank_score": top.get("rank_score"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
