#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DEFAULT_TAPE_PATH = REPORTS / "kraken_spot_guarded_candidate_forward_tape.jsonl"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_forward_tape_autopsy.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_forward_tape_autopsy.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_forward_tape_autopsy.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
    rows.sort(key=lambda row: str(row.get("entry_at") or ""))
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def parse_horizons(value: str) -> list[int]:
    return sorted({int(float(item.strip())) for item in str(value or "").split(",") if item.strip() and int(float(item.strip())) > 0})


def bucket(value: float, edges: list[float]) -> str:
    if not edges:
        return "all"
    lower = "-inf"
    for edge in edges:
        if value <= edge:
            return f"{lower}..{edge:g}"
        lower = f"{edge:g}"
    return f">{edges[-1]:g}"


def summarize_marks(rows: list[dict[str, Any]], horizons: list[int]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for horizon in horizons:
        values = []
        for row in rows:
            mark = row.get(f"h{horizon}")
            if isinstance(mark, dict):
                values.append(mark)
        pnls = [to_float(mark.get("net_pnl")) for mark in values]
        out[str(horizon)] = {
            "marked": len(pnls),
            "wins": sum(1 for pnl in pnls if pnl > 0.0),
            "win_rate_pct": round((sum(1 for pnl in pnls if pnl > 0.0) / len(pnls)) * 100.0, 6) if pnls else 0.0,
            "avg_net_pnl": round(sum(pnls) / len(pnls), 6) if pnls else 0.0,
            "sum_net_pnl": round(sum(pnls), 6),
            "best_net_pnl": round(max(pnls), 6) if pnls else 0.0,
            "worst_net_pnl": round(min(pnls), 6) if pnls else 0.0,
        }
    return out


def row_to_flat(row: dict[str, Any], horizons: list[int]) -> dict[str, Any]:
    entry_row = row.get("entry_row") if isinstance(row.get("entry_row"), dict) else {}
    marks = row.get("marks") if isinstance(row.get("marks"), dict) else {}
    flat: dict[str, Any] = {
        "entry_at": row.get("entry_at"),
        "product_id": row.get("product_id"),
        "status": row.get("status"),
        "entry_signal_state": row.get("entry_signal_state"),
        "entry_window": row.get("entry_best_move_window"),
        "entry_verdict": row.get("entry_verdict"),
        "entry_source": entry_row.get("source"),
        "entry_samples": int(to_float(entry_row.get("samples"))),
        "entry_bid": to_float(row.get("entry_bid")),
        "entry_ask": to_float(row.get("entry_ask")),
        "entry_spread_bps": to_float(row.get("entry_spread_bps")),
        "entry_move_bps": to_float(row.get("entry_best_move_bps")),
        "entry_edge_bps": to_float(row.get("entry_kraken_edge_bps")),
    }
    best_horizon = 0
    best_net = None
    first_green_horizon = 0
    for horizon in horizons:
        mark = marks.get(str(horizon)) if isinstance(marks.get(str(horizon)), dict) else None
        flat[f"h{horizon}"] = mark
        if not mark:
            flat[f"h{horizon}_net_pnl"] = ""
            flat[f"h{horizon}_bid_delta_bps"] = ""
            continue
        net_pnl = to_float(mark.get("net_pnl"))
        exit_bid = to_float(mark.get("exit_bid"))
        bid_delta_bps = ((exit_bid - flat["entry_bid"]) / flat["entry_bid"]) * 10000.0 if flat["entry_bid"] > 0.0 else 0.0
        flat[f"h{horizon}_net_pnl"] = round(net_pnl, 6)
        flat[f"h{horizon}_bid_delta_bps"] = round(bid_delta_bps, 6)
        if best_net is None or net_pnl > best_net:
            best_net = net_pnl
            best_horizon = horizon
        if net_pnl > 0.0 and first_green_horizon == 0:
            first_green_horizon = horizon
    flat["oracle_best_horizon"] = best_horizon
    flat["oracle_best_net_pnl"] = round(best_net, 6) if best_net is not None else 0.0
    flat["oracle_green"] = bool(best_net is not None and best_net > 0.0)
    flat["first_green_horizon"] = first_green_horizon
    flat["spread_bucket"] = bucket(flat["entry_spread_bps"], [15.0, 30.0, 50.0, 75.0, 100.0])
    flat["edge_bucket"] = bucket(flat["entry_edge_bps"], [75.0, 125.0, 200.0, 300.0])
    flat["move_bucket"] = bucket(flat["entry_move_bps"], [225.0, 300.0, 400.0, 600.0])
    flat["sample_bucket"] = bucket(float(flat["entry_samples"]), [10.0, 30.0, 100.0, 250.0])
    return flat


def group_summary(rows: list[dict[str, Any]], key: str, horizons: list[int]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(str(row.get(key) or ""), []).append(row)
    out = []
    for name, bucket_rows in buckets.items():
        pnls = [to_float(row.get("oracle_best_net_pnl")) for row in bucket_rows if row.get("oracle_best_horizon")]
        out.append(
            {
                "group": name,
                "entries": len(bucket_rows),
                "oracle_green_rate_pct": round((sum(1 for pnl in pnls if pnl > 0.0) / len(pnls)) * 100.0, 6) if pnls else 0.0,
                "oracle_avg_net_pnl": round(sum(pnls) / len(pnls), 6) if pnls else 0.0,
                "horizons": summarize_marks(bucket_rows, horizons),
            }
        )
    out.sort(key=lambda row: (to_float(row.get("oracle_avg_net_pnl")), to_float(row.get("oracle_green_rate_pct")), int(row.get("entries") or 0)), reverse=True)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autopsy the Kraken guarded forward tape to separate bad entries from bad exits.")
    parser.add_argument("--tape-path", default=str(DEFAULT_TAPE_PATH))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--horizons-seconds", default="60,180,300,600")
    return parser.parse_args()


def build(args: argparse.Namespace) -> dict[str, Any]:
    horizons = parse_horizons(str(args.horizons_seconds))
    tape_rows = load_jsonl(Path(str(args.tape_path)))
    rows = [row_to_flat(row, horizons) for row in tape_rows]
    marked_rows = [row for row in rows if row.get("oracle_best_horizon")]
    complete_rows = [row for row in rows if row.get("status") == "complete"]
    oracle_pnls = [to_float(row.get("oracle_best_net_pnl")) for row in marked_rows]
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_forward_tape_autopsy",
        "shadow_only": True,
        "passive_only": True,
        "parameters": {
            "tape_path": str(args.tape_path),
            "horizons_seconds": horizons,
        },
        "read": [
            "This audits passive tape outcomes only; it does not open paper or live positions.",
            "Oracle best exit uses the best available marked horizon for each entry, so it is an upper bound on what fixed-horizon exits could have captured.",
            "If oracle results are negative, the entry filter is the primary failure, not just the exit timer.",
        ],
        "summary": {
            "entries": len(rows),
            "complete": len(complete_rows),
            "marked": len(marked_rows),
            "oracle_green_entries": sum(1 for pnl in oracle_pnls if pnl > 0.0),
            "oracle_green_rate_pct": round((sum(1 for pnl in oracle_pnls if pnl > 0.0) / len(oracle_pnls)) * 100.0, 6) if oracle_pnls else 0.0,
            "oracle_avg_net_pnl": round(sum(oracle_pnls) / len(oracle_pnls), 6) if oracle_pnls else 0.0,
            "fixed_horizons": summarize_marks(rows, horizons),
        },
        "groups": {
            "entry_window": group_summary(rows, "entry_window", horizons),
            "entry_verdict": group_summary(rows, "entry_verdict", horizons),
            "entry_source": group_summary(rows, "entry_source", horizons),
            "spread_bucket": group_summary(rows, "spread_bucket", horizons),
            "edge_bucket": group_summary(rows, "edge_bucket", horizons),
            "move_bucket": group_summary(rows, "move_bucket", horizons),
            "sample_bucket": group_summary(rows, "sample_bucket", horizons),
        },
        "rows": rows,
    }
    write_json(Path(str(args.json_path)), payload)
    write_csv(payload, Path(str(args.csv_path)), horizons)
    write_md(payload, Path(str(args.md_path)), horizons)
    return payload


def write_csv(payload: dict[str, Any], path: Path, horizons: list[int]) -> None:
    columns = [
        "entry_at",
        "product_id",
        "status",
        "entry_window",
        "entry_verdict",
        "entry_source",
        "entry_samples",
        "entry_spread_bps",
        "entry_move_bps",
        "entry_edge_bps",
        "oracle_best_horizon",
        "oracle_best_net_pnl",
        "oracle_green",
        "first_green_horizon",
        "spread_bucket",
        "edge_bucket",
        "move_bucket",
        "sample_bucket",
    ]
    for horizon in horizons:
        columns.extend([f"h{horizon}_net_pnl", f"h{horizon}_bid_delta_bps"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload.get("rows") or []:
            writer.writerow({column: row.get(column, "") for column in columns})


def _group_table(lines: list[str], title: str, rows: list[dict[str, Any]], horizon: int) -> None:
    lines.extend(["", f"## {title}", "", f"| Group | Entries | Oracle Green % | Oracle Avg $ | H{horizon} Win % | H{horizon} Avg $ |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for row in rows[:20]:
        hstats = (row.get("horizons") or {}).get(str(horizon), {})
        lines.append(
            "| {group} | {entries} | {oracle_green_rate_pct:.2f} | {oracle_avg_net_pnl:.4f} | {win_rate_pct:.2f} | {avg_net_pnl:.4f} |".format(
                group=row.get("group") or "(blank)",
                entries=int(row.get("entries") or 0),
                oracle_green_rate_pct=to_float(row.get("oracle_green_rate_pct")),
                oracle_avg_net_pnl=to_float(row.get("oracle_avg_net_pnl")),
                win_rate_pct=to_float(hstats.get("win_rate_pct")),
                avg_net_pnl=to_float(hstats.get("avg_net_pnl")),
            )
        )


def write_md(payload: dict[str, Any], path: Path, horizons: list[int]) -> None:
    summary = payload.get("summary") or {}
    primary_horizon = horizons[-1] if horizons else 0
    fixed = summary.get("fixed_horizons") or {}
    lines = [
        "# Kraken Spot Forward Tape Autopsy",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Shadow only: `{payload.get('shadow_only')}`",
        f"- Passive only: `{payload.get('passive_only')}`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload.get("read") or []])
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Entries: `{summary.get('entries', 0)}`",
            f"- Complete: `{summary.get('complete', 0)}`",
            f"- Marked: `{summary.get('marked', 0)}`",
            f"- Oracle green entries: `{summary.get('oracle_green_entries', 0)}`",
            f"- Oracle green rate: `{to_float(summary.get('oracle_green_rate_pct')):.2f}%`",
            f"- Oracle average net: `${to_float(summary.get('oracle_avg_net_pnl')):.4f}`",
            "",
            "## Fixed-Horizon Results",
            "",
            "| Horizon | Marked | Wins | Win % | Avg Net $ | Sum Net $ | Best $ | Worst $ |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for horizon in horizons:
        stats = fixed.get(str(horizon), {})
        lines.append(
            "| {horizon}s | {marked} | {wins} | {win_rate_pct:.2f} | {avg_net_pnl:.4f} | {sum_net_pnl:.4f} | {best_net_pnl:.4f} | {worst_net_pnl:.4f} |".format(
                horizon=horizon,
                marked=int(stats.get("marked") or 0),
                wins=int(stats.get("wins") or 0),
                win_rate_pct=to_float(stats.get("win_rate_pct")),
                avg_net_pnl=to_float(stats.get("avg_net_pnl")),
                sum_net_pnl=to_float(stats.get("sum_net_pnl")),
                best_net_pnl=to_float(stats.get("best_net_pnl")),
                worst_net_pnl=to_float(stats.get("worst_net_pnl")),
            )
        )
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    _group_table(lines, "By Entry Window", groups.get("entry_window") or [], primary_horizon)
    _group_table(lines, "By Verdict", groups.get("entry_verdict") or [], primary_horizon)
    _group_table(lines, "By Source", groups.get("entry_source") or [], primary_horizon)
    _group_table(lines, "By Spread Bucket", groups.get("spread_bucket") or [], primary_horizon)
    _group_table(lines, "By Edge Bucket", groups.get("edge_bucket") or [], primary_horizon)
    _group_table(lines, "By Move Bucket", groups.get("move_bucket") or [], primary_horizon)
    _group_table(lines, "By Sample Bucket", groups.get("sample_bucket") or [], primary_horizon)
    lines.extend(
        [
            "",
            "## Recent Rows",
            "",
            "| Entry | Product | Window | Spread | Edge | Best Horizon | Best Net $ | First Green |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in (payload.get("rows") or [])[-30:][::-1]:
        lines.append(
            "| {entry_at} | {product_id} | {window} | {spread:.2f} | {edge:.2f} | {best_horizon} | {best_net:.4f} | {first_green} |".format(
                entry_at=row.get("entry_at"),
                product_id=row.get("product_id"),
                window=row.get("entry_window"),
                spread=to_float(row.get("entry_spread_bps")),
                edge=to_float(row.get("entry_edge_bps")),
                best_horizon=int(row.get("oracle_best_horizon") or 0),
                best_net=to_float(row.get("oracle_best_net_pnl")),
                first_green=int(row.get("first_green_horizon") or 0),
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    payload = build(args)
    print(json.dumps({"json_path": str(Path(args.json_path).resolve()), "md_path": str(Path(args.md_path).resolve()), "entries": payload["summary"]["entries"]}, indent=2))


if __name__ == "__main__":
    main()
