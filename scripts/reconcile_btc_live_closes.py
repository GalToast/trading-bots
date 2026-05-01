#!/usr/bin/env python3
"""Per-trade reconciliation of BTC live lane: modeled PnL vs broker fills.

Reads the exec-event log for lane live_btcusd_exc2_tight_941779,
extracts every close attempt (both engine-initiated and manual-pause),
and compares the modeled realized_pnl against the actual broker fill
profit.  Writes a CSV and markdown report.

Usage:
    python scripts/reconcile_btc_live_closes.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXEC_LOG = ROOT / "reports" / "penetration_lattice_live_btcusd_exc2_tight_exec_events.jsonl"
OUT_CSV = ROOT / "reports" / "btc_reconcile_per_trade.csv"
OUT_MD = ROOT / "reports" / "btc_reconcile_per_trade.md"


def main() -> int:
    if not EXEC_LOG.exists():
        print(f"Missing {EXEC_LOG}")
        return 1

    trades: list[dict] = []
    total_modeled = 0.0
    total_broker = 0.0
    total_gap = 0.0
    manual_closes = 0
    reconcile_retries = 0

    with EXEC_LOG.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            action = rec.get("action", "")
            if action != "close_attempt" and action != "manual_pause_close_attempt":
                continue

            event = rec.get("event", {})
            result = rec.get("result", {})
            tracked = rec.get("tracked", {})
            ts = rec.get("ts_utc", "")

            direction = str(event.get("direction") or tracked.get("direction") or "")
            entry_price = float(event.get("entry_price") or 0.0)
            exit_price = float(event.get("exit_price") or 0.0)
            modeled_pnl = float(event.get("realized_pnl") or 0.0)
            ticket = int(event.get("ticket") or tracked.get("live_ticket") or 0)
            symbol = str(event.get("symbol") or tracked.get("symbol") or "BTCUSD")

            broker_fill = result.get("broker_fill", {})
            broker_profit = float(broker_fill.get("profit") if broker_fill else 0.0)
            broker_price = float(broker_fill.get("price") if broker_fill else 0.0)
            broker_commission = float(broker_fill.get("commission") if broker_fill else 0.0)
            broker_ticket = int(broker_fill.get("ticket") if broker_fill else 0)

            gap = modeled_pnl - broker_profit

            total_modeled += modeled_pnl
            total_broker += broker_profit
            total_gap += gap

            is_manual = action == "manual_pause_close_attempt"
            is_reconcile = "reconcile" in str(event.get("mode", ""))

            if is_manual:
                manual_closes += 1
            if is_reconcile:
                reconcile_retries += 1

            trades.append({
                "ts_utc": ts,
                "action": action,
                "ticket": ticket,
                "direction": direction,
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "broker_fill_price": broker_price,
                "modeled_pnl": modeled_pnl,
                "broker_profit": broker_profit,
                "broker_commission": broker_commission,
                "gap": gap,
                "is_manual": is_manual,
                "broker_deal_ticket": broker_ticket,
            })

    # Sort by timestamp
    trades.sort(key=lambda t: t["ts_utc"])

    # Write CSV
    fields = [
        "ts_utc", "action", "ticket", "direction", "symbol",
        "entry_price", "exit_price", "broker_fill_price",
        "modeled_pnl", "broker_profit", "broker_commission", "gap",
        "is_manual", "broker_deal_ticket",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            writer.writerow(t)

    # Write markdown
    lines = [
        "# BTC Live Per-Trade Reconciliation",
        "",
        f"Source: `{EXEC_LOG.name}`",
        f"Total trades analyzed: {len(trades)}",
        f"Manual pause closes: {manual_closes}",
        f"Reconcile retry opens: {reconcile_retries}",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"| --- | ---: |",
        f"| Modeled total | {total_modeled:+.2f} |",
        f"| Broker total | {total_broker:+.2f} |",
        f"| Total gap (modeled - broker) | {total_gap:+.2f} |",
        f"| Per-trade avg gap | {total_gap / len(trades):+.2f} |" if trades else "",
        "",
        "## Per-Trade Detail",
        "",
        "| TS UTC | Action | Ticket | Dir | Entry | Exit (modeled) | Fill (broker) | Modeled PnL | Broker PnL | Gap | Manual? |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for t in trades:
        lines.append(
            f"| {t['ts_utc'][:19]} | {t['action']} | {t['ticket']} | {t['direction']} | "
            f"{t['entry_price']:.2f} | {t['exit_price']:.2f} | {t['broker_fill_price']:.2f} | "
            f"{t['modeled_pnl']:+.2f} | {t['broker_profit']:+.2f} | {t['gap']:+.2f} | "
            f"{'Y' if t['is_manual'] else 'N'} |"
        )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Console summary
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote {OUT_MD}")
    print()
    print(f"  Modeled total:  {total_modeled:+.2f}")
    print(f"  Broker total:   {total_broker:+.2f}")
    print(f"  Total gap:      {total_gap:+.2f}")
    print(f"  Trades:         {len(trades)}")
    print(f"  Manual closes:  {manual_closes}")
    print()

    # Find the largest gaps
    if trades:
        sorted_by_gap = sorted(trades, key=lambda t: abs(t["gap"]), reverse=True)
        print("  Top 5 gaps:")
        for t in sorted_by_gap[:5]:
            print(f"    {t['ts_utc'][:19]} {t['direction']:4s} ticket={t['ticket']} "
                  f"modeled={t['modeled_pnl']:+.2f} broker={t['broker_profit']:+.2f} "
                  f"gap={t['gap']:+.2f} ({t['action']})")

    # Check if exit prices differ
    exit_price_diffs = [t for t in trades if t["exit_price"] and t["broker_fill_price"] and abs(t["exit_price"] - t["broker_fill_price"]) > 0.01]
    if exit_price_diffs:
        print(f"\n  Exit price mismatches: {len(exit_price_diffs)} of {len(trades)}")
        for t in exit_price_diffs[:5]:
            print(f"    {t['ts_utc'][:19]} modeled_exit={t['exit_price']:.2f} broker_fill={t['broker_fill_price']:.2f} "
                  f"diff={t['exit_price'] - t['broker_fill_price']:+.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
