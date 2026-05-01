#!/usr/bin/env python3
"""Full BTC deal audit — all time, not just a window."""
import MetaTrader5 as mt5
import os
from pathlib import Path
from datetime import datetime, timezone

current_dir = os.path.dirname(os.path.abspath(__file__))
root = Path(current_dir).parent

env_path = root / ".env"
for raw_line in env_path.read_text().splitlines():
    line = raw_line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

LOGIN = int(os.environ["MT5_LOGIN"])
PASSWORD = os.environ["MT5_PASSWORD"]
SERVER = os.environ.get("MT5_SERVER", "Hugosway-Demo")

if not mt5.initialize(login=LOGIN, password=PASSWORD, server=SERVER):
    print("Failed to connect to MT5")
    exit(1)

BTC_MAGIC = 941779

# Get ALL deals for this magic (since beginning of time)
from_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
to_dt = datetime(2026, 12, 31, tzinfo=timezone.utc)

deals = mt5.history_deals_get(from_dt, to_dt)
if deals:
    btc_deals = [d for d in deals if getattr(d, "magic", 0) == BTC_MAGIC]
    print(f"ALL BTC deals (magic={BTC_MAGIC}): {len(btc_deals)}")
    
    closes = [d for d in btc_deals if d.entry == 1]
    total_profit = sum(d.profit for d in closes)
    total_commission = sum(d.commission for d in closes)
    total_swap = sum(d.swap for d in closes)
    total_net = total_profit + total_commission + total_swap
    
    wins = [d for d in closes if d.profit > 0]
    losses = [d for d in closes if d.profit < 0]
    
    print(f"Closes: {len(closes)} | Wins: {len(wins)} | Losses: {len(losses)}")
    print(f"Total profit: {total_profit:+.2f}")
    print(f"Total commission: {total_commission:+.2f}")
    print(f"Total swap: {total_swap:+.2f}")
    print(f"Total net: {total_net:+.2f}")
    
    if len(closes) > 0:
        print(f"\nPer-close summary:")
        for d in sorted(closes, key=lambda x: x.time):
            action = "BUY" if d.type == 0 else "SELL"
            print(f"  Deal {d.ticket:10d} {d.time} {action} vol={d.volume} "
                  f"price={d.price:.2f} profit={d.profit:+.2f}")
else:
    print("No deals found")

mt5.shutdown()
