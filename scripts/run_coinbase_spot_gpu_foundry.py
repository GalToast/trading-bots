#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CANDLE_DIR = REPORTS / "candle_cache"
THEORY_PATH = REPORTS / "coinbase_spot_money_velocity_theory_map.json"
DISSONANCE_PATH = REPORTS / "coinbase_spot_dissonance_board.json"
LIVE_RADAR_PATH = REPORTS / "coinbase_spot_live_radar.json"
JSON_PATH = REPORTS / "coinbase_spot_gpu_foundry_results.json"
CSV_PATH = REPORTS / "coinbase_spot_gpu_foundry_results.csv"
MD_PATH = REPORTS / "coinbase_spot_gpu_foundry_results.md"
PRODUCT_MATRIX_CSV_PATH = REPORTS / "coinbase_spot_gpu_foundry_product_matrix.csv"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def candle_files(*, granularity: str, days: int, max_products: int) -> list[Path]:
    suffix = f"_{granularity.upper()}_{int(days)}d.json"
    files = sorted(CANDLE_DIR.glob(f"*{suffix}"))
    priority = {
        "RAVE",
        "IOTX",
        "BAL",
        "BLUR",
        "ALEPH",
        "KAT",
        "MOG",
        "NOM",
        "GHST",
        "TRU",
        "SUP",
        "A8",
        "PRL",
        "FARTCOIN",
        "TROLL",
        "CFG",
    }

    def sort_key(path: Path) -> tuple[int, str]:
        base = path.name.split("_USD_", 1)[0]
        return (0 if base in priority else 1, path.name)

    files.sort(key=sort_key)
    return files[:max_products] if max_products > 0 else files


def parse_candles(path: Path) -> dict[str, Any] | None:
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
        if open_ <= 0.0 or high <= 0.0 or low <= 0.0 or close <= 0.0 or high < low:
            continue
        candles.append((open_, high, low, close, volume))
    if len(candles) < 60:
        return None
    return {
        "product_id": str(payload.get("product_id") or path.stem.replace("_", "-")),
        "candles": candles,
    }


def load_spreads() -> dict[str, float]:
    radar = load_json(LIVE_RADAR_PATH)
    spreads = {}
    for row in radar.get("rows") or []:
        product_id = str(row.get("product_id") or "")
        if product_id:
            spreads[product_id] = max(0.0, to_float(row.get("spread_bps")))
    return spreads


def load_dissonance_blocks() -> set[str]:
    payload = load_json(DISSONANCE_PATH)
    return {
        str(row.get("product_id") or "")
        for row in payload.get("rows") or []
        if str(row.get("action") or "") in {"avoid_toxic_spread", "avoid_broad_dump_wave", "wait_for_alignment", "rebound_watch_only"}
    }


def load_theories(limit: int) -> list[dict[str, Any]]:
    payload = load_json(THEORY_PATH)
    rows = [row for row in payload.get("rows") or [] if str(row.get("feasibility") or "") == "testable_now"]
    return rows[:limit] if limit > 0 else rows


def trigger_params(trigger: str) -> dict[str, float | str]:
    table: dict[str, dict[str, float | str]] = {
        "live_bid_burst": {"mode": "impulse", "lookback": 1, "bps": 25.0, "loc": 0.60, "vol": 0.0},
        "five_min_ignition": {"mode": "impulse", "lookback": 1, "bps": 50.0, "loc": 0.70, "vol": 0.0},
        "dump_reclaim": {"mode": "dump_reclaim", "lookback": 2, "bps": 75.0, "loc": 0.65, "vol": 0.0},
        "spread_compression": {"mode": "compression_expansion", "lookback": 10, "bps": 35.0, "loc": 0.70, "vol": 0.0},
        "volume_climax": {"mode": "impulse", "lookback": 2, "bps": 25.0, "loc": 0.55, "vol": 2.0},
        "failed_breakdown": {"mode": "failed_breakdown", "lookback": 5, "bps": 50.0, "loc": 0.60, "vol": 0.0},
        "inside_bar_break": {"mode": "inside_bar_break", "lookback": 3, "bps": 20.0, "loc": 0.65, "vol": 0.0},
        "range_close_high": {"mode": "range_close_high", "lookback": 3, "bps": 60.0, "loc": 0.80, "vol": 0.0},
        "relative_quote_slip": {"mode": "impulse", "lookback": 3, "bps": 75.0, "loc": 0.65, "vol": 0.0},
        "leader_followthrough": {"mode": "impulse", "lookback": 2, "bps": 100.0, "loc": 0.70, "vol": 1.5},
    }
    return dict(table.get(trigger, table["live_bid_burst"]))


def confirmation_params(confirmation: str) -> dict[str, float | str]:
    table: dict[str, dict[str, float | str]] = {
        "one_poll_hot": {"kind": "none", "bars": 1},
        "two_poll_hold": {"kind": "positive_sequence", "bars": 2},
        "three_poll_hold": {"kind": "positive_sequence", "bars": 3},
        "higher_low": {"kind": "higher_low", "bars": 2},
        "midpoint_hold": {"kind": "midpoint_hold", "bars": 1},
        "cross_quote_vote": {"kind": "above_mean", "bars": 6},
        "spread_not_widening": {"kind": "range_not_extreme", "bars": 8},
        "ghost_winner_bias": {"kind": "none", "bars": 1},
        "dissonance_clear": {"kind": "dissonance_clear", "bars": 1},
    }
    return dict(table.get(confirmation, table["one_poll_hot"]))


def exit_params(exit_name: str) -> dict[str, float]:
    table = {
        "tight_fee_paid_trail": {"target": 0.035, "stop": 0.012, "hold": 6},
        "wide_bubble_trail": {"target": 0.07, "stop": 0.025, "hold": 12},
        "failed_reclaim_cut": {"target": 0.035, "stop": 0.01, "hold": 4},
        "broad_rollover_exit": {"target": 0.04, "stop": 0.015, "hold": 5},
        "opportunity_rotation": {"target": 0.05, "stop": 0.018, "hold": 6},
        "time_decay_exit": {"target": 0.045, "stop": 0.018, "hold": 3},
        "spread_toxic_exit": {"target": 0.035, "stop": 0.01, "hold": 3},
        "profit_bond_stop": {"target": 0.06, "stop": 0.015, "hold": 10},
        "cash_shelter_exit": {"target": 0.03, "stop": 0.01, "hold": 2},
        "maker_profit_rachet": {"target": 0.055, "stop": 0.018, "hold": 8},
    }
    return dict(table.get(exit_name, table["tight_fee_paid_trail"]))


def build_variant_rows(theories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for theory in theories:
        trigger = trigger_params(str(theory.get("trigger") or ""))
        confirmation = confirmation_params(str(theory.get("confirmation") or ""))
        exit_rule = exit_params(str(theory.get("exit") or ""))
        rows.append(
            {
                **theory,
                "trigger_mode": trigger["mode"],
                "lookback": int(trigger["lookback"]),
                "trigger_bps": float(trigger["bps"]),
                "min_close_location": float(trigger["loc"]),
                "min_volume_mult": float(trigger["vol"]),
                "confirmation_kind": confirmation["kind"],
                "confirmation_bars": int(confirmation["bars"]),
                "target_pct": float(exit_rule["target"]),
                "stop_pct": float(exit_rule["stop"]),
                "hold_bars": int(exit_rule["hold"]),
            }
        )
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (
            row["trigger_mode"],
            row["lookback"],
            row["trigger_bps"],
            row["min_close_location"],
            row["min_volume_mult"],
            row["confirmation_kind"],
            row["confirmation_bars"],
            row["target_pct"],
            row["stop_pct"],
            row["hold_bars"],
        )
        existing = grouped.get(key)
        if existing is None:
            copy = dict(row)
            copy["duplicate_theory_count"] = 1
            copy["duplicate_archetypes"] = str(row.get("archetype") or "")
            grouped[key] = copy
            continue
        existing["duplicate_theory_count"] = int(existing.get("duplicate_theory_count") or 1) + 1
        archetype = str(row.get("archetype") or "")
        current_archetypes = {
            part.strip()
            for part in str(existing.get("duplicate_archetypes") or "").split(",")
            if part.strip()
        }
        if archetype and len(current_archetypes) < 8 and archetype not in current_archetypes:
            current_archetypes.add(archetype)
            existing["duplicate_archetypes"] = ", ".join(sorted(current_archetypes))
    return list(grouped.values())


def run_foundry(args: argparse.Namespace) -> dict[str, Any]:
    import torch

    device = torch.device("cuda" if bool(args.use_gpu) and torch.cuda.is_available() else "cpu")
    theories = load_theories(int(args.max_variants))
    variants = build_variant_rows(theories)
    if not variants:
        raise SystemExit("No testable variants found. Run build_coinbase_spot_money_velocity_theory_map.py first.")

    spreads = load_spreads()
    dissonance_blocks = load_dissonance_blocks()
    product_files = candle_files(granularity=str(args.granularity), days=int(args.days), max_products=int(args.max_products))

    v = len(variants)
    signals = torch.zeros(v, device=device)
    wins = torch.zeros(v, device=device)
    net_sum = torch.zeros(v, device=device)
    gross_sum = torch.zeros(v, device=device)
    worst_net = torch.full((v,), float("inf"), device=device)
    positive_products = torch.zeros(v, device=device)
    top_product_net: list[tuple[float, str, int]] = [(-1e18, "", 0) for _ in range(v)]
    product_matrix_rows: list[dict[str, Any]] = []

    lookback = torch.tensor([row["lookback"] for row in variants], device=device, dtype=torch.long)
    trigger_bps = torch.tensor([row["trigger_bps"] for row in variants], device=device) / 10_000.0
    min_loc = torch.tensor([row["min_close_location"] for row in variants], device=device)
    min_vol = torch.tensor([row["min_volume_mult"] for row in variants], device=device)
    target = torch.tensor([row["target_pct"] for row in variants], device=device)
    stop = torch.tensor([row["stop_pct"] for row in variants], device=device)
    hold = torch.tensor([row["hold_bars"] for row in variants], device=device, dtype=torch.long)
    max_hold = int(torch.max(hold).item())

    trigger_modes = [str(row["trigger_mode"]) for row in variants]
    confirmation_kinds = [str(row["confirmation_kind"]) for row in variants]
    confirmation_bars = torch.tensor([row["confirmation_bars"] for row in variants], device=device, dtype=torch.long)

    tested_products = 0
    skipped_products = 0
    bad_cache_files = []
    for path in product_files:
        parsed = parse_candles(path)
        if not parsed:
            skipped_products += 1
            bad_cache_files.append(path.name)
            continue
        product_id = str(parsed["product_id"])
        if bool(args.skip_dissonant_products) and product_id in dissonance_blocks:
            skipped_products += 1
            continue
        candles = parsed["candles"]
        if len(candles) <= max_hold + int(torch.max(lookback).item()) + 5:
            skipped_products += 1
            continue
        tested_products += 1
        data = torch.tensor(candles, device=device, dtype=torch.float32)
        open_ = data[:, 0]
        high = data[:, 1]
        low = data[:, 2]
        close = data[:, 3]
        volume = data[:, 4]
        n = close.numel()
        valid_t = n - max_hold
        time_idx = torch.arange(valid_t, device=device)
        close_now = close[:valid_t]
        high_now = high[:valid_t]
        low_now = low[:valid_t]
        open_now = open_[:valid_t]
        volume_now = volume[:valid_t]
        close_location = torch.clamp((close_now - low_now) / torch.clamp(high_now - low_now, min=1e-12), 0.0, 1.0)

        entry_signals = torch.zeros((v, valid_t), device=device, dtype=torch.bool)
        for idx, mode in enumerate(trigger_modes):
            lb = int(lookback[idx].item())
            valid = time_idx >= lb
            prior_close = torch.roll(close_now, shifts=lb)
            base = valid & (close_location >= min_loc[idx])
            if float(min_vol[idx].item()) > 0.0:
                vol_base = torch.roll(volume_now, shifts=lb).clamp(min=1e-12)
                base = base & (volume_now >= vol_base * min_vol[idx])
            if mode == "impulse":
                signal = base & (((close_now / prior_close.clamp(min=1e-12)) - 1.0) >= trigger_bps[idx])
            elif mode == "dump_reclaim":
                flush = ((low_now / prior_close.clamp(min=1e-12)) - 1.0) <= -trigger_bps[idx]
                reclaim = ((close_now / low_now.clamp(min=1e-12)) - 1.0) >= trigger_bps[idx] * 0.5
                signal = base & flush & reclaim
            elif mode == "compression_expansion":
                prev_range = torch.roll((high_now / low_now.clamp(min=1e-12)) - 1.0, shifts=1)
                cur_range = (high_now / low_now.clamp(min=1e-12)) - 1.0
                body = (close_now / open_now.clamp(min=1e-12)) - 1.0
                signal = base & (prev_range <= 0.006) & (cur_range >= prev_range * 1.8) & (body >= trigger_bps[idx])
            elif mode == "failed_breakdown":
                prior_low = torch.roll(low_now, shifts=lb)
                signal = base & (low_now < prior_low * (1.0 - trigger_bps[idx])) & (close_now > prior_low)
            elif mode == "inside_bar_break":
                prev_high = torch.roll(high_now, shifts=1)
                prev_low = torch.roll(low_now, shifts=1)
                prior_high = torch.roll(high_now, shifts=2)
                prior_low = torch.roll(low_now, shifts=2)
                inside = (prev_high <= prior_high) & (prev_low >= prior_low)
                signal = base & inside & (close_now > prev_high)
            elif mode == "range_close_high":
                cur_range = (high_now / low_now.clamp(min=1e-12)) - 1.0
                signal = base & (cur_range >= trigger_bps[idx])
            else:
                signal = base

            kind = confirmation_kinds[idx]
            bars = int(confirmation_bars[idx].item())
            if kind == "positive_sequence" and bars > 1:
                for shift in range(bars):
                    signal = signal & (((torch.roll(close_now, shifts=shift) / torch.roll(close_now, shifts=shift + 1).clamp(min=1e-12)) - 1.0) > 0.0)
                signal = signal & (time_idx >= bars)
            elif kind == "higher_low":
                signal = signal & (low_now > torch.roll(low_now, shifts=1)) & (time_idx >= 1)
            elif kind == "midpoint_hold":
                midpoint = (high_now + low_now) * 0.5
                signal = signal & (close_now >= midpoint)
            elif kind == "above_mean":
                mean_ref = torch.roll(close_now, shifts=bars)
                signal = signal & (close_now >= mean_ref) & (time_idx >= bars)
            elif kind == "range_not_extreme":
                cur_range = (high_now / low_now.clamp(min=1e-12)) - 1.0
                prior_range = torch.roll(cur_range, shifts=max(1, bars))
                signal = signal & (cur_range <= torch.clamp(prior_range * 3.0, min=0.003)) & (time_idx >= bars)
            elif kind == "dissonance_clear":
                signal = signal & (product_id not in dissonance_blocks)
            entry_signals[idx] = signal

        future_close = torch.stack([close[torch.arange(valid_t, device=device) + int(h.item())] for h in hold], dim=0)
        future_high = []
        future_low = []
        for h in hold.tolist():
            h_int = int(h)
            high_window = torch.stack([high[torch.arange(valid_t, device=device) + step] for step in range(1, h_int + 1)], dim=0)
            low_window = torch.stack([low[torch.arange(valid_t, device=device) + step] for step in range(1, h_int + 1)], dim=0)
            future_high.append(torch.max(high_window, dim=0).values)
            future_low.append(torch.min(low_window, dim=0).values)
        max_high = torch.stack(future_high, dim=0)
        min_low = torch.stack(future_low, dim=0)
        entry = close_now.unsqueeze(0)
        target_hit = max_high >= entry * (1.0 + target.unsqueeze(1))
        stop_hit = min_low <= entry * (1.0 - stop.unsqueeze(1))
        gross = (future_close / entry) - 1.0
        gross = torch.where(target_hit, target.unsqueeze(1), gross)
        gross = torch.where(stop_hit, -stop.unsqueeze(1), gross)
        spread_pct = min(max(0.0, spreads.get(product_id, float(args.default_spread_bps))), float(args.max_spread_bps)) / 10_000.0
        fee_pct = (2.0 * float(args.fee_bps_per_side) / 10_000.0) + spread_pct
        net = gross - fee_pct
        masked_net = torch.where(entry_signals, net, torch.zeros_like(net))
        masked_gross = torch.where(entry_signals, gross, torch.zeros_like(gross))
        product_signal_counts = entry_signals.sum(dim=1).float()
        product_net = masked_net.sum(dim=1)
        product_wins = ((net > 0.0) & entry_signals).sum(dim=1).float()
        product_gross = masked_gross.sum(dim=1)
        signals += product_signal_counts
        wins += product_wins
        net_sum += product_net
        gross_sum += product_gross
        any_signal = product_signal_counts > 0
        positive_products += (product_net > 0.0).float()
        worst_candidate = torch.where(entry_signals, net, torch.full_like(net, float("inf"))).min(dim=1).values
        worst_net = torch.minimum(worst_net, worst_candidate)
        if bool(args.write_product_matrix):
            product_signal_counts_cpu = product_signal_counts.detach().cpu().tolist()
            product_wins_cpu = product_wins.detach().cpu().tolist()
            product_net_cpu = product_net.detach().cpu().tolist()
            product_gross_cpu = product_gross.detach().cpu().tolist()
            product_worst_cpu = worst_candidate.detach().cpu().tolist()
            for idx in torch.nonzero(any_signal, as_tuple=False).flatten().tolist():
                signal_count = int(product_signal_counts_cpu[idx])
                if signal_count <= 0:
                    continue
                variant = variants[idx]
                cumulative_net_pct = product_net_cpu[idx] * 100.0
                product_matrix_rows.append(
                    {
                        "product_id": product_id,
                        "variant_id": int(variant.get("id") or 0),
                        "archetype": str(variant.get("archetype") or ""),
                        "duplicate_theory_count": int(variant.get("duplicate_theory_count") or 1),
                        "trigger": str(variant.get("trigger") or ""),
                        "confirmation": str(variant.get("confirmation") or ""),
                        "exit": str(variant.get("exit") or ""),
                        "sizing": str(variant.get("sizing") or ""),
                        "trigger_mode": str(variant.get("trigger_mode") or ""),
                        "lookback": int(variant.get("lookback") or 0),
                        "trigger_bps": float(variant.get("trigger_bps") or 0.0),
                        "target_pct": round(float(variant.get("target_pct") or 0.0) * 100.0, 4),
                        "stop_pct": round(float(variant.get("stop_pct") or 0.0) * 100.0, 4),
                        "hold_bars": int(variant.get("hold_bars") or 0),
                        "signals": signal_count,
                        "wins": int(product_wins_cpu[idx]),
                        "win_rate_pct": round((product_wins_cpu[idx] / max(1, signal_count)) * 100.0, 4),
                        "avg_net_pct": round((product_net_cpu[idx] / max(1, signal_count)) * 100.0, 6),
                        "cumulative_net_pct": round(cumulative_net_pct, 6),
                        "avg_gross_pct": round((product_gross_cpu[idx] / max(1, signal_count)) * 100.0, 6),
                        "worst_net_pct": round((product_worst_cpu[idx] if math.isfinite(product_worst_cpu[idx]) else 0.0) * 100.0, 6),
                        "spread_bps_proxy": round(min(max(0.0, spreads.get(product_id, float(args.default_spread_bps))), float(args.max_spread_bps)), 4),
                        "survived_fees": cumulative_net_pct > 0.0,
                    }
                )
        for idx in torch.nonzero(any_signal, as_tuple=False).flatten().tolist():
            product_net_value = float(product_net[idx].detach().cpu().item()) * 100.0
            if product_net_value > top_product_net[idx][0]:
                top_product_net[idx] = (product_net_value, product_id, int(product_signal_counts[idx].detach().cpu().item()))

    rows = []
    hours = max(0.001, (int(args.days) * 24.0))
    sig_cpu = signals.detach().cpu().tolist()
    wins_cpu = wins.detach().cpu().tolist()
    net_cpu = net_sum.detach().cpu().tolist()
    gross_cpu = gross_sum.detach().cpu().tolist()
    worst_cpu = worst_net.detach().cpu().tolist()
    pos_products_cpu = positive_products.detach().cpu().tolist()
    for idx, variant in enumerate(variants):
        signal_count = int(sig_cpu[idx])
        if signal_count < int(args.min_signals):
            continue
        cumulative_net_pct = net_cpu[idx] * 100.0
        avg_net_pct = (net_cpu[idx] / max(1, signal_count)) * 100.0
        rows.append(
            {
                "rank": 0,
                "id": int(variant.get("id") or 0),
                "archetype": str(variant.get("archetype") or ""),
                "trigger": str(variant.get("trigger") or ""),
                "confirmation": str(variant.get("confirmation") or ""),
                "exit": str(variant.get("exit") or ""),
                "sizing": str(variant.get("sizing") or ""),
                "duplicate_theory_count": int(variant.get("duplicate_theory_count") or 1),
                "duplicate_archetypes": str(variant.get("duplicate_archetypes") or ""),
                "signals": signal_count,
                "wins": int(wins_cpu[idx]),
                "win_rate_pct": round((wins_cpu[idx] / max(1, signal_count)) * 100.0, 4),
                "avg_net_pct": round(avg_net_pct, 6),
                "cumulative_net_pct": round(cumulative_net_pct, 6),
                "net_pct_per_hour": round(cumulative_net_pct / hours, 6),
                "avg_gross_pct": round((gross_cpu[idx] / max(1, signal_count)) * 100.0, 6),
                "worst_net_pct": round((worst_cpu[idx] if math.isfinite(worst_cpu[idx]) else 0.0) * 100.0, 6),
                "positive_products": int(pos_products_cpu[idx]),
                "top_product": top_product_net[idx][1],
                "top_product_net_pct": round(top_product_net[idx][0], 6) if top_product_net[idx][1] else 0.0,
                "top_product_signals": top_product_net[idx][2],
                "trigger_mode": str(variant.get("trigger_mode") or ""),
                "lookback": int(variant.get("lookback") or 0),
                "trigger_bps": float(variant.get("trigger_bps") or 0.0),
                "target_pct": round(float(variant.get("target_pct") or 0.0) * 100.0, 4),
                "stop_pct": round(float(variant.get("stop_pct") or 0.0) * 100.0, 4),
                "hold_bars": int(variant.get("hold_bars") or 0),
            }
        )
    rows.sort(
        key=lambda row: (
            row["net_pct_per_hour"],
            row["avg_net_pct"],
            row["positive_products"],
            row["signals"],
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_gpu_foundry_stage1",
        "device": str(device),
        "parameters": {
            "granularity": str(args.granularity),
            "days": int(args.days),
            "max_products": int(args.max_products),
            "products_tested": tested_products,
            "products_skipped": skipped_products,
            "bad_or_short_cache_files": bad_cache_files[:50],
            "variants_loaded": len(variants),
            "theories_loaded": len(theories),
            "min_signals": int(args.min_signals),
            "fee_bps_per_side": float(args.fee_bps_per_side),
            "skip_dissonant_products": bool(args.skip_dissonant_products),
        },
        "leadership_read": [
            "This is a GPU/torch Stage-1 screen over cached candles, not live permission.",
            "It tests all loaded testable-now theory variants with conservative stop-first handling and current fee/spread proxy.",
            "Positive rows must graduate to a slower candle-path replay and then shadow proof before any live use.",
        ],
        "rows": rows,
        "product_matrix_rows": product_matrix_rows,
    }


def write_outputs(
    payload: dict[str, Any],
    *,
    json_path: Path,
    csv_path: Path,
    md_path: Path,
    product_matrix_csv_path: Path,
) -> None:
    json_payload = dict(payload)
    product_matrix_rows = json_payload.pop("product_matrix_rows", [])
    json_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")
    columns = [
        "rank",
        "id",
        "archetype",
        "trigger",
        "confirmation",
        "exit",
        "sizing",
        "duplicate_theory_count",
        "duplicate_archetypes",
        "signals",
        "wins",
        "win_rate_pct",
        "avg_net_pct",
        "cumulative_net_pct",
        "net_pct_per_hour",
        "avg_gross_pct",
        "worst_net_pct",
        "positive_products",
        "top_product",
        "top_product_net_pct",
        "top_product_signals",
        "trigger_mode",
        "lookback",
        "trigger_bps",
        "target_pct",
        "stop_pct",
        "hold_bars",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({column: row.get(column, "") for column in columns})
    if product_matrix_rows:
        matrix_columns = [
            "product_id",
            "variant_id",
            "archetype",
            "duplicate_theory_count",
            "trigger",
            "confirmation",
            "exit",
            "sizing",
            "trigger_mode",
            "lookback",
            "trigger_bps",
            "target_pct",
            "stop_pct",
            "hold_bars",
            "signals",
            "wins",
            "win_rate_pct",
            "avg_net_pct",
            "cumulative_net_pct",
            "avg_gross_pct",
            "worst_net_pct",
            "spread_bps_proxy",
            "survived_fees",
        ]
        with product_matrix_csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=matrix_columns)
            writer.writeheader()
            for row in product_matrix_rows:
                writer.writerow({column: row.get(column, "") for column in matrix_columns})
    lines = [
        "# Coinbase Spot GPU Foundry Results",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Device: `{payload['device']}`",
        f"- Products tested: `{payload['parameters']['products_tested']}`",
        f"- Variants loaded: `{payload['parameters']['variants_loaded']}`",
        f"- Product matrix rows: `{len(product_matrix_rows)}`",
        f"- Fee bps per side: `{payload['parameters']['fee_bps_per_side']}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload["leadership_read"]])
    lines.extend(
        [
            "",
            "## Top Rows",
            "",
            "| Rank | Theory | Dups | Archetype | Trigger | Confirm | Exit | Signals | Win % | Avg Net % | Net %/h | Pos Products | Top Product |",
            "| ---: | ---: | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["rows"][:50]:
        lines.append(
            "| {rank} | {id} | {duplicate_theory_count} | {archetype} | {trigger} | {confirmation} | {exit} | {signals} | {win_rate_pct:.2f} | {avg_net_pct:.4f} | {net_pct_per_hour:.4f} | {positive_products} | {top_product}:{top_product_net_pct:.2f}% |".format(
                **row
            )
        )
    if not payload["rows"]:
        lines.append("|  |  |  |  |  |  | 0 | 0.00 | 0.0000 | 0.0000 | 0 |  |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GPU/torch Stage-1 foundry for Coinbase spot theory variants.")
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-products", type=int, default=80)
    parser.add_argument("--max-variants", type=int, default=5000)
    parser.add_argument("--min-signals", type=int, default=5)
    parser.add_argument("--fee-bps-per-side", type=float, default=120.0)
    parser.add_argument("--default-spread-bps", type=float, default=25.0)
    parser.add_argument("--max-spread-bps", type=float, default=150.0)
    parser.add_argument("--skip-dissonant-products", action="store_true", default=True)
    parser.add_argument("--write-product-matrix", action="store_true", default=True)
    parser.add_argument("--cpu", dest="use_gpu", action="store_false")
    parser.set_defaults(use_gpu=True)
    parser.add_argument("--json-path", default=str(JSON_PATH))
    parser.add_argument("--csv-path", default=str(CSV_PATH))
    parser.add_argument("--md-path", default=str(MD_PATH))
    parser.add_argument("--product-matrix-csv-path", default=str(PRODUCT_MATRIX_CSV_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = run_foundry(args)
    product_matrix_rows = len(payload.get("product_matrix_rows") or [])
    write_outputs(
        payload,
        json_path=Path(args.json_path),
        csv_path=Path(args.csv_path),
        md_path=Path(args.md_path),
        product_matrix_csv_path=Path(args.product_matrix_csv_path),
    )
    print(
        json.dumps(
            {
                "json_path": args.json_path,
                "csv_path": args.csv_path,
                "md_path": args.md_path,
                "product_matrix_csv_path": args.product_matrix_csv_path,
                "product_matrix_rows": product_matrix_rows,
                "device": payload["device"],
                "top_rows": payload["rows"][:5],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
