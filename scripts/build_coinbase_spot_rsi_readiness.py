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


CSV_PATH = ROOT / "reports" / "coinbase_spot_rsi_readiness.csv"
MD_PATH = ROOT / "reports" / "coinbase_spot_rsi_readiness.md"

BASELINE_CFG = {
    "rsi_period": 7,
    "oversold_threshold": 30.0,
    "overbought_threshold": 70.0,
    "profit_target_pct": 0.02,
    "stop_loss_pct": 0.003,
    "max_hold_bars": 48,
    "volume_filter_mult": 1.0,
}

PRODUCTS = ["ARB-USD", "LIGHTER-USD", "VVV-USD", "CHECK-USD", "COMP-USD", "SOL-USD"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Coinbase spot RSI readiness report")
    parser.add_argument("--products", nargs="+", default=PRODUCTS)
    parser.add_argument("--csv-path", default=str(CSV_PATH))
    parser.add_argument("--md-path", default=str(MD_PATH))
    parser.add_argument("--walkforward-paths", nargs="*", default=None)
    return parser.parse_args()


def load_walkforward(paths: list[Path]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("results") or []:
            if row.get("label") == "baseline":
                out[str(row.get("product_id") or "")] = row
    return out


def classify(*, candles_used: int, full_net: float, split_train: float, split_test: float, walkforward_positive: int | None, walkforward_windows: int | None) -> tuple[str, str]:
    if full_net <= 0 or split_test <= 0:
        return "reject", "baseline edge not positive enough"
    if candles_used < 500:
        return "monitor_only", "positive but shallow history"
    if split_train <= 0:
        return "monitor_only", "positive recent slice but weak train history"
    if walkforward_windows is None:
        return "monitor_only", "positive baseline but no walk-forward read"
    if walkforward_positive >= 2:
        return "probationary", "enough depth and repeated positive walk-forward windows"
    return "monitor_only", "positive baseline but inconsistent walk-forward"


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()
    walkforward_paths = (
        [Path(p) for p in args.walkforward_paths]
        if args.walkforward_paths
        else [
            ROOT / "reports" / "coinbase_spot_rsi_walkforward.json",
            ROOT / "reports" / "coinbase_spot_rsi_walkforward_candidates.json",
        ]
    )
    walkforward = load_walkforward(walkforward_paths)
    rows: list[dict] = []
    bars_per_hour = 12
    needed = 72 * bars_per_hour
    test_bars = 24 * bars_per_hour

    for product_id in args.products:
        candles = fetch_candles_72h(client, product_id, "FIVE_MINUTE")
        full = run_rsi_system(
            candles,
            starting_cash=48.0,
            maker_fee_bps=5.0,
            deploy_pct=0.9,
            product_id=product_id,
            **BASELINE_CFG,
        )
        recent = candles[-needed:] if len(candles) > needed else candles[:]
        train = recent[:-test_bars] if len(recent) > test_bars else recent[:]
        test = recent[-test_bars:] if len(recent) > test_bars else []
        train_res = run_rsi_system(
            train,
            starting_cash=48.0,
            maker_fee_bps=5.0,
            deploy_pct=0.9,
            product_id=product_id,
            **BASELINE_CFG,
        ) if train else {"realized_net_usd": 0.0, "total_trades": 0}
        test_res = run_rsi_system(
            test,
            starting_cash=48.0,
            maker_fee_bps=5.0,
            deploy_pct=0.9,
            product_id=product_id,
            **BASELINE_CFG,
        ) if test else {"realized_net_usd": 0.0, "total_trades": 0}
        wf = walkforward.get(product_id)
        positive_windows = int(wf.get("positive_test_windows")) if wf else None
        windows_count = int(wf.get("windows_count")) if wf else None
        verdict, note = classify(
            candles_used=int(full.get("candles_used", 0)),
            full_net=float(full.get("realized_net_usd", 0.0)),
            split_train=float(train_res.get("realized_net_usd", 0.0)),
            split_test=float(test_res.get("realized_net_usd", 0.0)),
            walkforward_positive=positive_windows,
            walkforward_windows=windows_count,
        )
        rows.append(
            {
                "product_id": product_id,
                "candles_used": int(full.get("candles_used", 0)),
                "approx_hours": round(int(full.get("candles_used", 0)) / bars_per_hour, 1),
                "full_net_usd": round(float(full.get("realized_net_usd", 0.0)), 4),
                "full_trades": int(full.get("total_trades", 0)),
                "split_train_net_usd": round(float(train_res.get("realized_net_usd", 0.0)), 4),
                "split_train_trades": int(train_res.get("total_trades", 0)),
                "split_test_net_usd": round(float(test_res.get("realized_net_usd", 0.0)), 4),
                "split_test_trades": int(test_res.get("total_trades", 0)),
                "walkforward_positive_windows": positive_windows if positive_windows is not None else "",
                "walkforward_windows": windows_count if windows_count is not None else "",
                "verdict": verdict,
                "note": note,
            }
        )

    verdict_rank = {"probationary": 0, "monitor_only": 1, "reject": 2}
    rows.sort(key=lambda r: (verdict_rank.get(str(r["verdict"]), 9), -float(r["full_net_usd"])))

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Coinbase Spot RSI Readiness",
        "",
        "| Product | Verdict | Candles | Hours | Full Net $ | Split Train $ | Split Test $ | WF Pos | Note |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        wf_text = "-"
        if row["walkforward_windows"] != "":
            wf_text = f"{row['walkforward_positive_windows']}/{row['walkforward_windows']}"
        lines.append(
            f"| {row['product_id']} | {row['verdict']} | {row['candles_used']} | {row['approx_hours']:.1f} | {row['full_net_usd']:.4f} | {row['split_train_net_usd']:.4f} | {row['split_test_net_usd']:.4f} | {wf_text} | {row['note']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
