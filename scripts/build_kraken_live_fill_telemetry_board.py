#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from kraken_spot_client import KrakenSpotClient, to_float  # noqa: E402


REPORTS = ROOT / "reports"
DEFAULT_EVENTS_PATH = REPORTS / "kraken_tiny_live_maker_roundtrip_events.jsonl"
DEFAULT_LATEST_PATH = REPORTS / "kraken_tiny_live_maker_roundtrip_latest.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_live_fill_telemetry_board.json"
DEFAULT_MD_PATH = REPORTS / "kraken_live_fill_telemetry_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def epoch_to_iso(value: Any) -> str:
    seconds = to_float(value)
    if seconds <= 0.0:
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def seconds_between(start: Any, end: Any) -> float | None:
    start_f = to_float(start)
    end_f = to_float(end)
    if start_f <= 0.0 or end_f <= 0.0 or end_f < start_f:
        return None
    return round(end_f - start_f, 3)


def query_orders(txids: list[str]) -> dict[str, dict[str, Any]]:
    if not txids:
        return {}
    client = KrakenSpotClient()
    result = client._request("POST", "/0/private/QueryOrders", params={"txid": ",".join(txids)}, private=True)
    if not isinstance(result, dict):
        return {}
    return {str(txid): row for txid, row in result.items() if isinstance(row, dict)}


def query_balances() -> dict[str, str]:
    client = KrakenSpotClient()
    return {str(k): str(v) for k, v in client.balance().items() if to_float(v) != 0.0}


def infer_product_for_txid(txid: str, events: list[dict[str, Any]]) -> str:
    for row in events:
        if str(row.get("txid") or "") == txid and row.get("product_id"):
            return str(row.get("product_id"))
    return ""


def txids_from_events(events: list[dict[str, Any]], latest: dict[str, Any]) -> list[str]:
    txids: list[str] = []
    for row in events:
        txid = str(row.get("txid") or "")
        if txid:
            txids.append(txid)
    for key in ("entry_txid", "exit_txid", "manual_exit_txid"):
        txid = str(latest.get(key) or "")
        if txid:
            txids.append(txid)
    return sorted(set(txids))


def last_event(events: list[dict[str, Any]], action: str, txid: str = "") -> dict[str, Any]:
    for row in reversed(events):
        if row.get("action") != action:
            continue
        if txid and str(row.get("txid") or "") != txid:
            continue
        return row
    return {}


def latest_book_snapshot(
    events: list[dict[str, Any]],
    *,
    labels: set[str],
    txid: str = "",
    product_id: str = "",
) -> dict[str, Any]:
    for row in reversed(events):
        if row.get("action") != "live_roundtrip_book_snapshot":
            continue
        if labels and str(row.get("snapshot_label") or "") not in labels:
            continue
        if txid and str(row.get("txid") or "") != txid:
            continue
        if product_id and str(row.get("product_id") or "") != product_id:
            continue
        return row
    return {}


def order_summary(txid: str, status: dict[str, Any]) -> dict[str, Any]:
    side = str((status.get("descr") or {}).get("type") or "")
    price = to_float(status.get("price"))
    cost = to_float(status.get("cost"))
    fee = to_float(status.get("fee"))
    vol = to_float(status.get("vol"))
    vol_exec = to_float(status.get("vol_exec"))
    open_tm = status.get("opentm")
    close_tm = status.get("closetm")
    filled = str(status.get("status") or "").lower() == "closed" and vol_exec > 0.0
    terminal_seconds = seconds_between(open_tm, close_tm)
    return {
        "txid": txid,
        "side": side,
        "status": str(status.get("status") or ""),
        "reason": status.get("reason"),
        "price": price,
        "cost": cost,
        "fee": fee,
        "vol": vol,
        "vol_exec": vol_exec,
        "filled": filled,
        "open_time_utc": epoch_to_iso(open_tm),
        "close_time_utc": epoch_to_iso(close_tm),
        "fill_seconds": terminal_seconds if filled else None,
        "terminal_seconds": terminal_seconds,
    }


def build_cycles(events: list[dict[str, Any]], latest: dict[str, Any], statuses: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    entry_txids = [
        str(row.get("txid") or "")
        for row in events
        if row.get("action") == "live_roundtrip_entry_submitted" and row.get("txid")
    ]
    latest_entry_txid = str(latest.get("entry_txid") or "")
    latest_entry_chain = [str(txid) for txid in (latest.get("entry_txid_chain") or []) if str(txid)]
    if latest_entry_txid and latest_entry_chain:
        entry_txids = [txid for txid in entry_txids if txid not in latest_entry_chain or txid == latest_entry_txid]
        if latest_entry_txid not in entry_txids:
            entry_txids.append(latest_entry_txid)
    if not entry_txids and latest.get("entry_txid"):
        entry_txids = [str(latest["entry_txid"])]

    manual_exit_txids = [
        str(row.get("txid") or "")
        for row in events
        if "manual" in str(row.get("action") or "") and "exit" in str(row.get("action") or "") and row.get("txid")
    ]
    target_exit_txids = [
        str(row.get("txid") or "")
        for row in events
        if row.get("action") == "live_roundtrip_exit_submitted" and row.get("txid")
    ]
    latest_exit_txid = str(latest.get("exit_txid") or "")
    latest_exit_chain = [str(txid) for txid in (latest.get("exit_txid_chain") or []) if str(txid)]
    if latest_exit_txid and latest_exit_chain:
        target_exit_txids = [
            txid for txid in target_exit_txids if txid not in latest_exit_chain or txid == latest_exit_txid
        ]
        if latest_exit_txid not in target_exit_txids:
            target_exit_txids.append(latest_exit_txid)

    cycles: list[dict[str, Any]] = []
    for index, entry_txid in enumerate(entry_txids, start=1):
        entry_status = statuses.get(entry_txid) or latest.get("entry_status") or {}
        target_exit_txid = target_exit_txids[index - 1] if index - 1 < len(target_exit_txids) else str(latest.get("exit_txid") or "")
        manual_exit_txid = manual_exit_txids[index - 1] if index - 1 < len(manual_exit_txids) else ""
        target_status = statuses.get(target_exit_txid) or latest.get("exit_status") or {}
        manual_status = (statuses.get(manual_exit_txid) or {}) if manual_exit_txid else {}

        product_id = (
            infer_product_for_txid(entry_txid, events)
            or infer_product_for_txid(target_exit_txid, events)
            or infer_product_for_txid(manual_exit_txid, events)
            or str(latest.get("product_id") or "")
        )
        entry = order_summary(entry_txid, entry_status)
        target_exit = order_summary(target_exit_txid, target_status) if target_exit_txid else {}
        manual_exit = order_summary(manual_exit_txid, manual_status) if manual_exit_txid else {}
        final_exit = manual_exit if manual_exit.get("filled") else target_exit

        entry_gross = to_float(entry.get("cost"))
        entry_fee = to_float(entry.get("fee"))
        exit_gross = to_float(final_exit.get("cost"))
        exit_fee = to_float(final_exit.get("fee"))
        net_usd = exit_gross - exit_fee - entry_gross - entry_fee if entry_gross and exit_gross else 0.0
        basis = entry_gross + entry_fee
        net_pct = (net_usd / basis * 100.0) if basis > 0.0 else 0.0
        total_hold_seconds = seconds_between(entry_status.get("closetm"), (manual_status or target_status).get("closetm"))
        target_failed = bool(target_exit_txid) and not target_exit.get("filled")
        entry_filled = bool(entry.get("filled"))

        target_attempt = last_event(events, "live_roundtrip_exit_submit_attempt")
        manual_submit = last_event(events, "live_roundtrip_manual_breakeven_exit_submitted", manual_exit_txid)
        entry_attempt = last_event(events, "live_roundtrip_entry_submit_attempt")
        entry_book = latest_book_snapshot(
            events,
            labels={"entry_order_submitted", "entry_submit_attempt"},
            txid=entry_txid,
            product_id=product_id,
        ) or latest_book_snapshot(
            events,
            labels={"entry_submit_attempt"},
            product_id=product_id,
        )
        exit_book = latest_book_snapshot(
            events,
            labels={"exit_order_submitted", "exit_terminal", "entry_filled_before_exit_submit"},
            txid=target_exit_txid,
            product_id=product_id,
        ) or latest_book_snapshot(
            events,
            labels={"entry_filled_before_exit_submit"},
            product_id=product_id,
        )

        taker_proof_margin_bps: float | None = None
        final_bid = to_float(manual_submit.get("bid") or target_attempt.get("bid"))
        final_price = to_float(final_exit.get("price"))
        if final_bid > 0.0 and final_price > 0.0:
            taker_proof_margin_bps = round((final_price - final_bid) / final_bid * 10000.0, 6)
        entry_l10_ratio = entry_attempt.get("ghost_ratio_at_entry")
        if entry_l10_ratio is None:
            entry_l10_ratio = entry_book.get("book_l10_imbalance_ratio")
        exit_book_missing = entry_filled and not exit_book and taker_proof_margin_bps is None

        cycles.append(
            {
                "product_id": product_id,
                "entry": entry,
                "target_exit": target_exit,
                "manual_or_rescue_exit": manual_exit,
                "final_exit_source": "manual_or_rescue_exit" if manual_exit.get("filled") else "target_exit",
                "target_profit_exit_attempted": bool(target_exit_txid),
                "entry_filled": entry_filled,
                "entry_missed": not entry_filled,
                "target_profit_exit_failed": target_failed,
                "total_seconds_entry_fill_to_final_exit_fill": total_hold_seconds,
                "gross_exit_minus_entry_usd": round(exit_gross - entry_gross, 8) if entry_gross and exit_gross else 0.0,
                "total_fees_usd": round(entry_fee + exit_fee, 8),
                "net_usd": round(net_usd, 8),
                "net_pct_on_entry_cost_plus_fee": round(net_pct, 6),
                "green_after_fees": net_usd > 0.0,
                "staggered_exit_fill_bps": None,
                "taker_proof_margin_bps": taker_proof_margin_bps,
                "ghost_ratio_at_entry": entry_l10_ratio,
                "entry_book_snapshot": entry_book,
                "exit_book_snapshot": exit_book,
                "observability_gaps": [
                    gap
                    for gap, missing in (
                        ("entry_book_snapshot_missing", not entry_book),
                        ("exit_book_snapshot_missing", exit_book_missing),
                        ("ghost_ratio_at_entry_missing", entry_l10_ratio is None),
                    )
                    if missing
                ],
            }
        )
    return cycles


def product_summaries(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cycle in cycles:
        grouped[str(cycle.get("product_id") or "")].append(cycle)
    rows: list[dict[str, Any]] = []
    for product_id, product_cycles in sorted(grouped.items()):
        complete = [row for row in product_cycles if to_float(row.get("net_usd")) != 0.0]
        entry_fills = [row for row in product_cycles if row.get("entry_filled")]
        entry_misses = [row for row in product_cycles if row.get("entry_missed")]
        target_fills = [row for row in product_cycles if row.get("target_exit", {}).get("filled")]
        green = [row for row in complete if row.get("green_after_fees")]
        blockers: list[str] = []
        if len(complete) < 5:
            blockers.append("live_cycle_sample_lt_5")
        if entry_misses:
            blockers.append("entry_miss_observed")
        if len(target_fills) < max(1, int(len(complete) * 0.8)):
            blockers.append("profit_target_exit_fill_rate_lt_80pct")
        if any(row.get("manual_or_rescue_exit", {}).get("filled") for row in product_cycles):
            blockers.append("uses_manual_or_rescue_exit")
        if any(row.get("observability_gaps") for row in product_cycles):
            blockers.append("missing_live_microstructure_fields")
        verdict = "promotable_for_tiny_autonomy" if not blockers else "blocked_for_autonomous_live"
        rows.append(
            {
                "product_id": product_id,
                "cycles": len(product_cycles),
                "complete_roundtrips": len(complete),
                "entry_fills": len(entry_fills),
                "entry_misses": len(entry_misses),
                "green_after_fees": len(green),
                "net_usd": round(sum(to_float(row.get("net_usd")) for row in complete), 8),
                "net_pct_sum": round(sum(to_float(row.get("net_pct_on_entry_cost_plus_fee")) for row in complete), 6),
                "target_profit_exit_fills": len(target_fills),
                "target_profit_exit_failures": sum(1 for row in product_cycles if row.get("target_profit_exit_failed")),
                "manual_or_rescue_exits": sum(1 for row in product_cycles if row.get("manual_or_rescue_exit", {}).get("filled")),
                "max_entry_fill_seconds": max(
                    [to_float(row.get("entry", {}).get("fill_seconds")) for row in product_cycles if row.get("entry_filled")] or [0.0]
                ),
                "max_total_hold_seconds": max(
                    [to_float(row.get("total_seconds_entry_fill_to_final_exit_fill")) for row in complete] or [0.0]
                ),
                "blockers": blockers,
                "verdict": verdict,
            }
        )
    return rows


def build_payload(
    *,
    events_path: Path,
    latest_path: Path,
    query_private: bool,
) -> dict[str, Any]:
    events = load_jsonl(events_path)
    latest = load_json(latest_path)
    txids = txids_from_events(events, latest)
    statuses = query_orders(txids) if query_private else {}
    balances = query_balances() if query_private else {}
    cycles = build_cycles(events, latest, statuses)
    products = product_summaries(cycles)
    blockers: list[str] = []
    complete_cycles = sum(row["complete_roundtrips"] for row in products)
    target_failures = sum(row["target_profit_exit_failures"] for row in products)
    manual_exits = sum(row["manual_or_rescue_exits"] for row in products)
    entry_misses = sum(row["entry_misses"] for row in products)
    if complete_cycles < 20:
        blockers.append("global_live_cycle_sample_lt_20")
    if target_failures > 0:
        blockers.append("profit_target_exit_miss_observed")
    if entry_misses > 0:
        blockers.append("entry_miss_observed")
    if manual_exits > 0:
        blockers.append("manual_or_rescue_exit_observed")
    if any(cycle.get("observability_gaps") for cycle in cycles):
        blockers.append("live_microstructure_observability_incomplete")
    if not query_private:
        blockers.append("private_order_status_not_queried")

    return {
        "generated_at_utc": utc_now_iso(),
        "events_path": str(events_path),
        "latest_path": str(latest_path),
        "query_private": query_private,
        "queried_txids": txids if query_private else [],
        "nonzero_balances": balances,
        "cycles": cycles,
        "products": products,
        "summary": {
            "complete_live_roundtrips": complete_cycles,
            "green_after_fees": sum(row["green_after_fees"] for row in products),
            "net_usd": round(sum(row["net_usd"] for row in products), 8),
            "target_profit_exit_failures": target_failures,
            "manual_or_rescue_exits": manual_exits,
            "entry_misses": entry_misses,
            "promotion_status": "blocked_for_autonomous_live" if blockers else "eligible_for_next_tiny_live_stage",
            "promotion_blockers": blockers,
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Kraken Live Fill Telemetry Board",
        "",
        f"- Generated: `{payload['generated_at_utc']}`",
        f"- Private order status queried: `{payload['query_private']}`",
        f"- Complete live roundtrips: `{summary['complete_live_roundtrips']}`",
        f"- Green after fees: `{summary['green_after_fees']}`",
        f"- Net USD: `{summary['net_usd']}`",
        f"- Promotion status: `{summary['promotion_status']}`",
        f"- Blockers: `{', '.join(summary['promotion_blockers']) if summary['promotion_blockers'] else 'none'}`",
        "",
        "## Products",
        "",
        "| Product | Entries | Entry misses | Complete | Green | Net USD | Target fills | Target fails | Rescue exits | Max hold sec | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["products"]:
        lines.append(
            "| {product_id} | {cycles} | {entry_misses} | {complete_roundtrips} | {green_after_fees} | {net_usd:.8f} | "
            "{target_profit_exit_fills} | {target_profit_exit_failures} | {manual_or_rescue_exits} | "
            "{max_total_hold_seconds:.3f} | {verdict} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Live Cycles",
            "",
            "| Product | Entry status | Entry fill sec | Final source | Hold sec | Net USD | Net % | Gaps |",
            "|---|---|---:|---|---:|---:|---:|---|",
        ]
    )
    for cycle in payload["cycles"]:
        entry_fill = cycle.get("entry", {}).get("fill_seconds")
        hold = cycle.get("total_seconds_entry_fill_to_final_exit_fill")
        lines.append(
            "| {product} | {entry_status} | {entry_fill} | {source} | {hold} | {net:.8f} | {pct:.6f} | {gaps} |".format(
                product=cycle.get("product_id") or "",
                entry_status=cycle.get("entry", {}).get("status") or "",
                entry_fill="" if entry_fill is None else f"{to_float(entry_fill):.3f}",
                source=cycle.get("final_exit_source") or "",
                hold="" if hold is None else f"{to_float(hold):.3f}",
                net=to_float(cycle.get("net_usd")),
                pct=to_float(cycle.get("net_pct_on_entry_cost_plus_fee")),
                gaps=", ".join(cycle.get("observability_gaps") or []),
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This board is a live-equivalence gate, not a profit claim.",
            "- One green tiny roundtrip clears order syntax and basic fee accounting.",
            "- Autonomous promotion remains blocked until profit-target exits fill repeatedly without manual rescue and the event tape records book snapshots around exit fills.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Kraken tiny-live fill telemetry and promotion gate board.")
    parser.add_argument("--events-path", default=str(DEFAULT_EVENTS_PATH))
    parser.add_argument("--latest-path", default=str(DEFAULT_LATEST_PATH))
    parser.add_argument("--output-json", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--output-md", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--query-private", action="store_true", help="Read-only QueryOrders/Balance enrichment.")
    args = parser.parse_args()

    payload = build_payload(
        events_path=Path(args.events_path),
        latest_path=Path(args.latest_path),
        query_private=args.query_private,
    )
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    output_md.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
