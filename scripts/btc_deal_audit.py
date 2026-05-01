#!/usr/bin/env python3
"""Pull BTC deal history from MT5 to verify broker fills directly."""
import MetaTrader5 as mt5
import os, json
from pathlib import Path
from datetime import datetime, timezone

current_dir = os.path.dirname(os.path.abspath(__file__))
root = Path(current_dir).parent

# Load credentials
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

print(f"Connected to MT5 account {LOGIN}")

# Get all deals for the BTC lane magic number
BTC_MAGIC = 941779
from_dt = datetime(2026, 4, 10, 21, 0, 0, tzinfo=timezone.utc)
to_dt = datetime(2026, 4, 11, 3, 0, 0, tzinfo=timezone.utc)

deals = mt5.history_deals_get(from_dt, to_dt)
if deals:
    btc_deals = [d for d in deals if getattr(d, "magic", 0) == BTC_MAGIC]
    print(f"Total deals in window: {len(deals)}, BTC deals (magic={BTC_MAGIC}): {len(btc_deals)}")
    print()
    
    total_profit = 0.0
    total_commission = 0.0
    total_swap = 0.0
    
    for d in sorted(btc_deals, key=lambda x: x.time):
        profit = getattr(d, "profit", 0)
        commission = getattr(d, "commission", 0)
        swap = getattr(d, "swap", 0)
        net = profit + commission + swap
        total_profit += profit
        total_commission += commission
        total_swap += swap
        
        action = "BUY" if d.type == 0 else "SELL"
        entry = "OPEN" if d.entry == 0 else "CLOSE"
        print(f"  Deal {d.ticket:10d} {d.time} {entry:5s} {action:4s} "
              f"vol={d.volume} price={d.price:.2f} "
              f"profit={profit:+.2f} comm={commission:.2f} swap={swap:.2f} net={net:+.2f}")
    
    print(f"\nTotal: profit={total_profit:+.2f} commission={total_commission:+.2f} "
          f"swap={total_swap:+.2f} net={total_profit+total_commission+total_swap:+.2f}")
else:
    print("No deals found in time window")

mt5.shutdown()
