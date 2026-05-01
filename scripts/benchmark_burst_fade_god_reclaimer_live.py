#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from burst_fade_god_reclaimer_live import PRODUCTS, GodReclaimerShadowEngine, CoinbaseAdvancedClient, get_fee_rate


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON_PATH = ROOT / "reports" / "coinbase_burst_god_reclaimer_benchmark.json"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_burst_god_reclaimer_benchmark.md"


def _interval_seconds(granularity: str) -> int:
    g = str(granularity).upper()
    if g == "FIFTEEN_MINUTE":
        return 900
    if g == "ONE_HOUR":
        return 3600
    return 300


def _fetch_history_window(
    client: CoinbaseAdvancedClient,
    product_id: str,
    granularity: str,
    *,
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    try:
        response = client.market_candles(product_id, start=start, end=end, granularity=str(granularity).upper())
    except Exception:
        return []
    candles = list(response.get("candles", []))
    candles.sort(key=lambda c: int(c["start"]))
    return candles


def _load_histories(client: CoinbaseAdvancedClient, granularity: str, hours: int) -> dict[str, list[dict[str, Any]]]:
    interval = _interval_seconds(granularity)
    end = int(time.time())
    start = end - max(1, int(hours)) * 3600
    max_chunk_candles = 250
    chunk_span = interval * max_chunk_candles
    histories: dict[str, list[dict[str, Any]]] = {}
    for pid in PRODUCTS:
        merged: dict[int, dict[str, Any]] = {}
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + chunk_span)
            candles = _fetch_history_window(client, pid, granularity, start=cursor, end=chunk_end)
            for candle in candles:
                merged[int(candle["start"])] = candle
            cursor = chunk_end
        ordered = [merged[key] for key in sorted(merged.keys())]
        if ordered:
            histories[pid] = ordered
    return histories


def _replay(histories: dict[str, list[dict[str, Any]]], starting_cash: float, max_concurrent: int, reclaim_floor: float) -> tuple[GodReclaimerShadowEngine, dict[str, float]]:
    engine = GodReclaimerShadowEngine(
        starting_cash=starting_cash,
        max_concurrent=max_concurrent,
        reclaim_floor=reclaim_floor,
    )
    buckets: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    last_close: dict[str, float] = {}

    for pid, candles in histories.items():
        if not candles:
            continue
        last_close[pid] = float(candles[-1]["close"])
        first = candles[0]
        engine.last_close_by_pid[pid] = float(first["close"])
        for candle in candles[1:-1]:
            buckets[int(candle["start"])][pid] = [candle]

    for start in sorted(buckets.keys()):
        engine.process_tick(buckets[start])

    return engine, last_close


def _marked_net(engine: GodReclaimerShadowEngine, last_close: dict[str, float]) -> tuple[float, float]:
    floating = 0.0
    fee_rate = get_fee_rate(engine.total_volume)
    for pos in engine.positions:
        pid = str(pos["pid"])
        mark = float(last_close.get(pid) or 0.0)
        entry = float(pos["entry"])
        quote = float(pos["quote"])
        if mark <= 0.0 or entry <= 0.0 or quote <= 0.0:
            continue
        units = quote / entry
        exit_fee = mark * units * fee_rate
        floating += ((mark - entry) * units) - exit_fee
    return engine.realized_net + floating, floating


def _product_breakdown(histories: dict[str, list[dict[str, Any]]], starting_cash: float, max_concurrent: int, reclaim_floor: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pid, candles in histories.items():
        if len(candles) < 3:
            continue
        engine, last_close = _replay({pid: candles}, starting_cash, max_concurrent=1, reclaim_floor=reclaim_floor)
        marked_net, floating = _marked_net(engine, last_close)
        rows.append(
            {
                "product_id": pid,
                "candles": max(0, len(candles) - 2),
                "realized_net_usd": round(engine.realized_net, 4),
                "marked_net_usd": round(marked_net, 4),
                "floating_pnl_usd": round(floating, 4),
                "closes": engine.realized_closes,
                "wins": engine.realized_wins,
                "losses": max(0, engine.realized_closes - engine.realized_wins),
                "open_count": len(engine.positions),
                "fees_usd": round(engine.total_fees, 4),
            }
        )
    rows.sort(key=lambda row: (-float(row["marked_net_usd"]), -int(row["closes"]), row["product_id"]))
    return rows


def write_reports(summary: dict[str, Any], rows: list[dict[str, Any]], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "products": rows}
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# God Reclaimer Benchmark",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Hours: {summary['hours']}",
        f"- Granularity: {summary['granularity']}",
        f"- Starting cash: {summary['starting_cash']}",
        f"- Max concurrent: {summary['max_concurrent']}",
        f"- Reclaim floor: {summary['reclaim_floor']}",
        "",
        "## Summary",
        "",
        f"- Products with data: {summary['products_with_data']}",
        f"- Realized net: {summary['realized_net_usd']:+.4f}",
        f"- Floating P/L: {summary['floating_pnl_usd']:+.4f}",
        f"- Marked net: {summary['marked_net_usd']:+.4f}",
        f"- Closes: {summary['closes']}",
        f"- Wins: {summary['wins']}",
        f"- Losses: {summary['losses']}",
        f"- Win rate: {summary['win_rate']:.2f}%",
        f"- Fees: {summary['fees_usd']:.4f}",
        f"- Open positions: {summary['open_count']}",
        "",
        "## Product Breakdown",
        "",
        "| Product | Candles | Realized $ | Marked $ | Floating $ | Closes | Wins | Losses | Open | Fees $ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows[:15]:
        lines.append(
            "| {product_id} | {candles} | {realized_net_usd:+.4f} | {marked_net_usd:+.4f} | {floating_pnl_usd:+.4f} | {closes} | {wins} | {losses} | {open_count} | {fees_usd:.4f} |".format(
                **row
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark God Reclaimer on recent Coinbase candles")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--reclaim-floor", type=float, default=0.6)
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()
    histories = _load_histories(client, args.granularity, args.hours)
    engine, last_close = _replay(
        histories,
        starting_cash=args.starting_cash,
        max_concurrent=args.max_concurrent,
        reclaim_floor=args.reclaim_floor,
    )
    marked_net, floating = _marked_net(engine, last_close)
    rows = _product_breakdown(
        histories,
        starting_cash=args.starting_cash,
        max_concurrent=args.max_concurrent,
        reclaim_floor=args.reclaim_floor,
    )
    summary = {
        "hours": int(args.hours),
        "granularity": str(args.granularity).upper(),
        "starting_cash": float(args.starting_cash),
        "max_concurrent": int(args.max_concurrent),
        "reclaim_floor": float(args.reclaim_floor),
        "products_with_data": len(histories),
        "realized_net_usd": round(engine.realized_net, 4),
        "floating_pnl_usd": round(floating, 4),
        "marked_net_usd": round(marked_net, 4),
        "closes": engine.realized_closes,
        "wins": engine.realized_wins,
        "losses": max(0, engine.realized_closes - engine.realized_wins),
        "win_rate": round((engine.realized_wins / max(1, engine.realized_closes)) * 100.0, 2),
        "fees_usd": round(engine.total_fees, 4),
        "open_count": len(engine.positions),
    }
    write_reports(summary, rows, Path(args.json_path), Path(args.md_path))
    print(json.dumps({"summary": summary, "top_products": rows[:10]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
