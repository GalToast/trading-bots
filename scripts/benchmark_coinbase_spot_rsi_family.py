#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_coinbase_spot_rsi import fetch_candles_72h, run_rsi_system
from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_spot_rsi_family.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_spot_rsi_family.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the validated Coinbase RSI family across a shortlist.")
    parser.add_argument(
        "--products",
        nargs="+",
        default=["ARB-USD", "COMP-USD", "SOL-USD", "AVNT-USD", "LIGHTER-USD", "ROBO-USD", "VVV-USD"],
    )
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--maker-fee-bps", type=float, default=5.0)
    parser.add_argument("--rsi-period", type=int, default=7)
    parser.add_argument("--oversold", type=float, default=30.0)
    parser.add_argument("--overbought", type=float, default=70.0)
    parser.add_argument("--profit-target-pct", type=float, default=0.02)
    parser.add_argument("--stop-loss-pct", type=float, default=0.003)
    parser.add_argument("--max-hold-bars", type=int, default=48)
    parser.add_argument("--volume-filter-mult", type=float, default=1.0)
    parser.add_argument("--deploy-pct", type=float, default=0.9)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


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
        "# Coinbase Spot RSI Family",
        "",
        f"- Config: RSI({args.rsi_period}), oversold `{args.oversold}`, overbought `{args.overbought}`, TP `{args.profit_target_pct*100:.1f}%`, SL `{args.stop_loss_pct*100:.1f}%`, fee `{args.maker_fee_bps:.1f}` bps",
        "",
        "| Product | Trades | Win % | Net $ | Avg/Trade $ | Median Hold Bars | Fees $ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {product_id} | {total_trades} | {win_rate_pct:.1f}% | {realized_net_usd:.4f} | {avg_net_per_trade:.4f} | {median_hold_bars} | {total_fees:.4f} |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()
    rows: list[dict] = []
    for product_id in args.products:
        candles = fetch_candles_72h(client, product_id, args.granularity)
        result = run_rsi_system(
            candles,
            starting_cash=args.starting_cash,
            maker_fee_bps=args.maker_fee_bps,
            rsi_period=args.rsi_period,
            oversold_threshold=args.oversold,
            overbought_threshold=args.overbought,
            profit_target_pct=args.profit_target_pct,
            stop_loss_pct=args.stop_loss_pct,
            max_hold_bars=args.max_hold_bars,
            volume_filter_mult=args.volume_filter_mult,
            deploy_pct=args.deploy_pct,
            product_id=product_id,
        )
        rows.append(
            {
                "product_id": product_id,
                "candles_used": result.get("candles_used", 0),
                "realized_net_usd": float(result.get("realized_net_usd", 0.0)),
                "total_trades": int(result.get("total_trades", 0)),
                "win_rate_pct": float(result.get("win_rate", 0.0)) * 100.0,
                "avg_net_per_trade": float(result.get("avg_net_per_trade", 0.0)),
                "median_hold_bars": int(result.get("median_hold_bars", 0)),
                "tp_exits": int(result.get("tp_exits", 0)),
                "sl_exits": int(result.get("sl_exits", 0)),
                "rsi_exits": int(result.get("rsi_exits", 0)),
                "timeout_exits": int(result.get("timeout_exits", 0)),
                "total_fees": float(result.get("total_fees", 0.0)),
                "avg_entry_rsi": float(result.get("avg_entry_rsi", 0.0)),
            }
        )
    rows.sort(key=lambda row: row["realized_net_usd"], reverse=True)
    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    write_csv(csv_path, rows)
    write_md(md_path, rows, args)
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
