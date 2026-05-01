#!/usr/bin/env python3
"""Chunked staged-anchor competition runner.

Runs one (anchor × close × handoff) combo at a time, saving results
incrementally to a shared CSV so progress survives interruptions.

Usage:
  python chunked_staged_anchor.py --symbols BTCUSD --days 14 --lane-names live_btcusd_m15_warp_941781
  python chunked_staged_anchor.py --days 7  # all registry lanes
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import MetaTrader5 as mt5
from backtest_staged_anchor_competition import (
    build_markdown,
    lane_case_label,
    load_bars,
    load_step_ladder_configs,
    simulate_contract,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--lane-names", nargs="*")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--include-disabled", action="store_true")
    p.add_argument("--output-csv")
    p.add_argument("--output-md")
    p.add_argument("--chunk-delay", type=float, default=0.5, help="seconds between chunks")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    output_csv = Path(args.output_csv) if args.output_csv else ROOT / "reports" / "chunked_staged_anchor.csv"
    output_md = Path(args.output_md) if args.output_md else ROOT / "reports" / "chunked_staged_anchor.md"
    progress_path = output_csv.with_suffix(".progress.json")

    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

    # Load configs
    symbol_filter = {s.upper() for s in (args.symbols or [])} or None
    lane_filter = {s for s in (args.lane_names or [])} or None
    configs = load_step_ladder_configs(
        symbol_filter=symbol_filter,
        lane_name_filter=lane_filter,
        kind_filter=None,
        include_disabled=bool(args.include_disabled),
    )
    if not configs:
        print("No configs loaded")
        mt5.shutdown()
        return

    # Load existing progress
    completed: set[str] = set()
    existing_rows: list[dict] = []
    if output_csv.exists():
        with output_csv.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_rows.append(row)
                key = f"{row['symbol']}:{row['anchor_mode']}:{row['close_mode']}:{row['handoff_steps']}"
                completed.add(key)
        print(f"Resuming: {len(completed)} combos already done")

    if progress_path.exists():
        saved = json.loads(progress_path.read_text())
        completed.update(set(saved.get("completed", [])))

    # Define sweep parameters
    anchor_modes = ["stable_price", "self_last_fill", "vwap20"]
    close_modes = ["handoff", "trail_75", "handoff_then_trail_75"]
    handoff_steps_list = [0.0, 0.5, 1.0, 2.0, 3.0]

    fieldnames = None
    total = 0
    done = 0
    start_t = time.time()

    for cfg in configs:
        info = mt5.symbol_info(cfg.symbol)
        if info is None:
            print(f"  SKIP {cfg.symbol}: no symbol info")
            continue

        bars = load_bars(cfg.symbol, cfg.timeframe, args.days)
        if not bars:
            print(f"  SKIP {cfg.symbol}:{cfg.timeframe}: no bars")
            continue

        case_label = lane_case_label(cfg)
        print(f"\n{'='*60}")
        print(f"CASE: {case_label} ({len(bars)} bars, {args.days}d)")
        print(f"{'='*60}")

        for anchor in anchor_modes:
            for close_mode in close_modes:
                # handoff values only matter for handoff modes
                if close_mode == "handoff" or close_mode.startswith("handoff_then_"):
                    handoff_values = handoff_steps_list
                else:
                    handoff_values = [0.0]

                for hs in handoff_values:
                    key = f"{cfg.symbol}:{anchor}:{close_mode}:{hs}"
                    if key in completed:
                        done += 1
                        continue
                    total += 1

                    row = simulate_contract(
                        cfg=cfg,
                        bars=bars,
                        spread_px=0.0,
                        usd_per_price_unit=float(info.trade_contract_size or 1.0),
                        step_scale=1.0,
                        entry_start_steps=1.0,
                        entry_shape="uniform",
                        anchor_mode=anchor,
                        close_mode=close_mode,
                        handoff_steps=hs,
                        split_depth=1,
                        trail_activation_steps=1.0,
                        trail_floor_steps=0.25,
                    )
                    if row:
                        if fieldnames is None:
                            fieldnames = list(row.keys())
                        existing_rows.append(row)
                        # Append immediately
                        write_mode = "a" if output_csv.exists() else "w"
                        with output_csv.open(write_mode, newline="", encoding="utf-8") as f:
                            writer = csv.DictWriter(f, fieldnames=fieldnames)
                            if write_mode == "w":
                                writer.writeheader()
                            writer.writerow(row)

                    completed.add(key)
                    done += 1

                    # Save progress every 5 combos
                    if done % 5 == 0:
                        progress_path.write_text(
                            json.dumps({"completed": list(completed), "done": done, "total": total + done}),
                            encoding="utf-8",
                        )

                    elapsed = time.time() - start_t
                    rate = done / max(elapsed, 0.1)
                    remaining = max(0, (total + done - done) / max(rate, 0.01))
                    print(f"  [{done}/{done+total}] {key} → ${row.get('realized_net_usd', 0):.2f} | {rate:.1f} combos/s | ~{remaining:.0f}s left")

                    if args.chunk_delay > 0:
                        time.sleep(args.chunk_delay)

    # Final progress save
    progress_path.write_text(
        json.dumps({"completed": list(completed), "done": done + total, "total": done + total}),
        encoding="utf-8",
    )

    # Build markdown
    if existing_rows:
        md = build_markdown(existing_rows, days=args.days, tested_cases=[lane_case_label(c) for c in configs])
        output_md.write_text(md, encoding="utf-8")

    elapsed = time.time() - start_t
    print(f"\nDone! {len(existing_rows)} rows in {elapsed:.0f}s")
    print(f"Output: {output_csv}")
    print(f"Report: {output_md}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
