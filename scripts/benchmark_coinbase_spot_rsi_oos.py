#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from itertools import product
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_coinbase_spot_rsi import fetch_candles_72h, run_rsi_system
from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_spot_rsi_oos.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_spot_rsi_oos.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Out-of-sample Coinbase RSI sweep")
    parser.add_argument("--products", nargs="+", default=["ARB-USD", "LIGHTER-USD", "VVV-USD"])
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--maker-fee-bps", type=float, default=5.0)
    parser.add_argument("--train-hours", type=int, default=48)
    parser.add_argument("--test-hours", type=int, default=24)
    parser.add_argument("--top-per-product", type=int, default=5)
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def config_grid() -> list[dict[str, float | int]]:
    configs = []
    for rsi_period, oversold, overbought, pt, sl, hold, vf in product(
        [5, 7, 9, 14],
        [20, 25, 30, 35],
        [60, 65, 70, 75],
        [0.015, 0.02, 0.025],
        [0.003, 0.005],
        [24, 48, 72],
        [1.0, 1.25],
    ):
        if oversold >= overbought:
            continue
        configs.append(
            {
                "rsi_period": rsi_period,
                "oversold": float(oversold),
                "overbought": float(overbought),
                "profit_target_pct": float(pt),
                "stop_loss_pct": float(sl),
                "max_hold_bars": int(hold),
                "volume_filter_mult": float(vf),
            }
        )
    return configs


def split_candles(candles: list[dict], train_hours: int, test_hours: int) -> tuple[list[dict], list[dict]]:
    total_hours = train_hours + test_hours
    bars_per_hour = 12
    needed = total_hours * bars_per_hour
    recent = candles[-needed:] if len(candles) > needed else candles[:]
    test_bars = test_hours * bars_per_hour
    if len(recent) <= test_bars:
        return recent, []
    return recent[:-test_bars], recent[-test_bars:]


def run_config(
    candles: list[dict],
    product_id: str,
    args: argparse.Namespace,
    cfg: dict[str, float | int],
) -> dict:
    return run_rsi_system(
        candles,
        starting_cash=args.starting_cash,
        maker_fee_bps=args.maker_fee_bps,
        rsi_period=int(cfg["rsi_period"]),
        oversold_threshold=float(cfg["oversold"]),
        overbought_threshold=float(cfg["overbought"]),
        profit_target_pct=float(cfg["profit_target_pct"]),
        stop_loss_pct=float(cfg["stop_loss_pct"]),
        max_hold_bars=int(cfg["max_hold_bars"]),
        volume_filter_mult=float(cfg["volume_filter_mult"]),
        deploy_pct=0.9,
        product_id=product_id,
    )


def score_row(row: dict) -> tuple:
    return (
        float(row["test_realized_net_usd"]),
        float(row["train_realized_net_usd"]),
        float(row["test_avg_net_per_trade"]),
        int(row["test_total_trades"]),
    )


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_md(path: Path, rows: list[dict], args: argparse.Namespace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot RSI OOS Sweep",
        "",
        f"- Split: train `{args.train_hours}h`, test `{args.test_hours}h`",
        f"- Fee: `{args.maker_fee_bps:.1f}` bps",
        "",
        "| Product | Config | Train $ | Test $ | Train Trades | Test Trades | Test Avg/Trade $ |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['product_id']} | {row['config_name']} | {row['train_realized_net_usd']:.4f} | {row['test_realized_net_usd']:.4f} | {row['train_total_trades']} | {row['test_total_trades']} | {row['test_avg_net_per_trade']:.4f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()
    rows: list[dict] = []
    all_cfgs = config_grid()

    for product_id in args.products:
        candles = fetch_candles_72h(client, product_id, args.granularity)
        train, test = split_candles(candles, args.train_hours, args.test_hours)
        product_rows: list[dict] = []
        for cfg in all_cfgs:
            train_res = run_config(train, product_id, args, cfg)
            test_res = run_config(test, product_id, args, cfg) if test else {"realized_net_usd": 0.0, "total_trades": 0, "avg_net_per_trade": 0.0}
            row = {
                "product_id": product_id,
                "config_name": (
                    f"rsi{cfg['rsi_period']}_os{int(cfg['oversold'])}_ob{int(cfg['overbought'])}"
                    f"_pt{float(cfg['profit_target_pct'])*100:.1f}_sl{float(cfg['stop_loss_pct'])*100:.1f}"
                    f"_hold{cfg['max_hold_bars']}_vf{float(cfg['volume_filter_mult']):.2f}"
                ),
                "rsi_period": int(cfg["rsi_period"]),
                "oversold": float(cfg["oversold"]),
                "overbought": float(cfg["overbought"]),
                "profit_target_pct": float(cfg["profit_target_pct"]),
                "stop_loss_pct": float(cfg["stop_loss_pct"]),
                "max_hold_bars": int(cfg["max_hold_bars"]),
                "volume_filter_mult": float(cfg["volume_filter_mult"]),
                "train_realized_net_usd": float(train_res.get("realized_net_usd", 0.0)),
                "train_total_trades": int(train_res.get("total_trades", 0)),
                "train_win_rate_pct": float(train_res.get("win_rate", 0.0)) * 100.0,
                "test_realized_net_usd": float(test_res.get("realized_net_usd", 0.0)),
                "test_total_trades": int(test_res.get("total_trades", 0)),
                "test_win_rate_pct": float(test_res.get("win_rate", 0.0)) * 100.0,
                "test_avg_net_per_trade": float(test_res.get("avg_net_per_trade", 0.0)),
            }
            if row["train_total_trades"] >= 2:
                product_rows.append(row)

        product_rows.sort(key=score_row, reverse=True)
        rows.extend(product_rows[: args.top_per_product])

    rows.sort(key=score_row, reverse=True)
    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    write_csv(csv_path, rows)
    write_md(md_path, rows, args)
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
