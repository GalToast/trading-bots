#!/usr/bin/env python3
"""BTC M5 Warp Floating Risk Monitor

Polls the live M5 Warp state and current BTC price every 60s,
appends floating PnL trajectory to a log file.
Gives us honest data on the drawdown envelope.

Usage:
    python scripts/monitor_btc_m5_warp_floating.py
"""
import json
import time
import MetaTrader5 as mt5
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "penetration_lattice_live_btcusd_m5_warp_state.json"
LOG_PATH = ROOT / "reports" / "btc_m5_warp_floating_log.csv"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main():
    if not mt5.initialize():
        print("MT5 initialize failed")
        return 1

    # Write header if file doesn't exist
    if not LOG_PATH.exists():
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text(
            "timestamp,btc_bid,btc_ask,btc_mid,realized_usd,floating_usd,net_usd,"
            "sell_count,buy_count,total_open,worst_sell_pnl,best_sell_pnl,buy_pnl\n",
            encoding="utf-8",
        )

    print(f"BTC M5 Warp Floating Monitor — logging to {LOG_PATH}")
    print("Polling every 60s. Ctrl+C to stop.\n")

    try:
        while True:
            tick = mt5.symbol_info_tick("BTCUSD")
            if tick is None:
                time.sleep(10)
                continue

            if not STATE_PATH.exists():
                time.sleep(10)
                continue

            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            btc = state["symbols"]["BTCUSD"]
            tickets = btc["open_tickets"]

            mid = (tick.bid + tick.ask) / 2
            buys = [t for t in tickets if t["direction"] == "BUY"]
            sells = [t for t in tickets if t["direction"] == "SELL"]

            # Calibrate effective volume from broker scoreboard
            # At breakeven (72316.54), floating = 0. Current floating ~-3563 at ~74195.
            # distance = 1879, positions delta = 16, floating = -3563
            # effective_volume = 3563 / (16 * 1879) ≈ 0.1185
            # This accounts for contract size and broker-specific PnL calculation.
            effective_volume = 0.1185

            buy_pnl = sum((mid - t["entry_fill_price"]) * effective_volume for t in buys)
            sell_pnl = sum((t["entry_fill_price"] - mid) * effective_volume for t in sells)
            floating = buy_pnl + sell_pnl
            realized = btc["realized_net_usd"]
            net = realized + floating

            worst_sell = min(
                sells, key=lambda t: t["entry_fill_price"] - mid, default=None
            )
            best_sell = max(
                sells, key=lambda t: t["entry_fill_price"] - mid, default=None
            )

            row = (
                f"{utc_now_iso()},{tick.bid},{tick.ask},{mid:.2f},"
                f"{realized:.2f},{floating:.2f},{net:.2f},"
                f"{len(sells)},{len(buys)},{len(tickets)},"
            )
            if worst_sell:
                row += f"{worst_sell['entry_fill_price'] - mid:.2f},"
            else:
                row += ","
            if best_sell:
                row += f"{best_sell['entry_fill_price'] - mid:.2f},"
            else:
                row += ","
            row += f"{buy_pnl:.2f}\n"

            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(row)

            print(
                f"  BTC={mid:.0f} | realized=+{realized:.0f} | "
                f"floating={floating:.0f} | net={net:.0f} | "
                f"ratio={abs(floating)/realized:.1f}x | "
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
