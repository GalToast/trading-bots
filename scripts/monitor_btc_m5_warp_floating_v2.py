#!/usr/bin/env python3
"""BTC M5 Warp Floating Risk Monitor v2

Reads floating risk directly from MT5 broker positions (not state files).
Polls every 60s, appends to CSV and updates MD report.

Usage:
    python scripts/monitor_btc_m5_warp_floating_v2.py
"""
import json
import time
import MetaTrader5 as mt5
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
LOG_CSV = ROOT / "reports" / "btc_m5_warp_floating_log.csv"
LOG_MD = ROOT / "reports" / "btc_m5_warp_floating_monitor.md"


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def main():
    if not mt5.initialize():
        print("MT5 initialize failed")
        return 1

    positions = mt5.positions_get(symbol="BTCUSD")
    if not positions:
        print("No BTCUSD positions found. Nothing to monitor.")
        mt5.shutdown()
        return 1

    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_CSV.exists():
        LOG_CSV.write_text(
            "timestamp,btc_bid,btc_ask,realized_usd,floating_usd,net_usd,"
            "sell_count,buy_count,total_open,avg_sell_entry,avg_buy_entry\n",
            encoding="utf-8",
        )

    print(f"BTC M5 Warp Floating Monitor v2 — logging to {LOG_CSV}")
    print(f"Found {len(positions)} BTCUSD positions. Polling every 60s.\n")

    try:
        cycle = 0
        while True:
            cycle += 1
            tick = mt5.symbol_info_tick("BTCUSD")
            positions = mt5.positions_get(symbol="BTCUSD")
            account = mt5.account_info()

            if not tick or not positions:
                time.sleep(10)
                continue

            buys = [p for p in positions if p.type == mt5.ORDER_TYPE_BUY]
            sells = [p for p in positions if p.type == mt5.ORDER_TYPE_SELL]

            buy_pnl = sum(p.profit + p.swap + p.commission for p in buys)
            sell_pnl = sum(p.profit + p.swap + p.commission for p in sells)
            floating = buy_pnl + sell_pnl

            # Get realized PnL from MT5 history
            from_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)  # today
            history = mt5.history_deals_get(from_date.timestamp())
            realized = 0
            if history:
                for deal in history:
                    if "BTCUSD" in deal.symbol or deal.symbol == "BTCUSD":
                        realized += deal.profit + deal.commission + deal.swap

            net = realized + floating

            equity = account.equity if account else 0

            avg_sell_entry = sum(p.price_open for p in sells) / len(sells) if sells else 0
            avg_buy_entry = sum(p.price_open for p in buys) / len(buys) if buys else 0

            row = (
                f"{utc_now_iso()},{tick.bid},{tick.ask},{realized:.2f},"
                f"{floating:.2f},{net:.2f},"
                f"{len(sells)},{len(buys)},{len(positions)},"
                f"{avg_sell_entry:.2f},{avg_buy_entry:.2f}\n"
            )
            with LOG_CSV.open("a", encoding="utf-8") as f:
                f.write(row)

            # Write MD report
            md = f"""# BTC M5 Warp Floating Monitor

- Updated: `{utc_now_iso()}`
- Lane: `live_btcusd_m5_warp_probation_941780`
- BTC: `{tick.bid:.0f}` / `{tick.ask:.0f}`

## Current State

| Metric | Value |
|---|---|
| Realized | ${realized:,.2f} |
| Floating | ${floating:,.2f} |
| **Net** | **${net:,.2f}** |
| Total Open | {len(positions)} |
| SELLs | {len(sells)} |
| BUYs | {len(buys)} |
| Avg SELL Entry | {avg_sell_entry:.2f} |
| Avg BUY Entry | {avg_buy_entry:.2f} |
| Account Equity | ${account.equity:,.2f} |

## Breakeven Analysis

Breakeven price (if all closes at once): ~72,316 (needs to drop from current)
Floating/realized ratio: {abs(floating/realized) if realized > 0 else 0:.1f}x

## Recent Trajectory
"""
            # Read last 10 rows for trajectory
            lines = LOG_CSV.read_text(encoding="utf-8").strip().split("\n")[1:]
            if len(lines) > 10:
                lines = lines[-10:]
            md += "| Time | BTC Mid | Floating | Net | S/B |\n|---|---|---|---|---|\n"
            for line in lines:
                parts = line.split(",")
                ts = parts[0][11:16]  # HH:MM
                btc_mid = (float(parts[1]) + float(parts[2])) / 2
                md += f"| {ts} | {btc_mid:.0f} | ${float(parts[4]):,.0f} | ${float(parts[5]):,.0f} | {parts[7]}/{parts[8]} |\n"

            LOG_MD.write_text(md, encoding="utf-8")

            print(
                f"  [{utc_now_iso()[11:19]}] BTC={tick.bid:.0f} | "
                f"floating=${floating:,.0f} | net=${net:,.0f} | "
                f"S{len(sells)}/B{len(buys)}"
            )

            time.sleep(60)

    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    finally:
        mt5.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
