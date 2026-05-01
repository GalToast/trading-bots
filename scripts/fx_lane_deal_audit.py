#!/usr/bin/env python3
"""FX lane deal audit — check if modeled-vs-broker gap affects all lanes."""
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

print(f"Connected to MT5 account {LOGIN}")

# FX lane magic numbers from the registry
FX_REARM_MAGIC = 941777
FX_MOMENTUM_MAGIC = 941778

from_dt = datetime(2026, 4, 10, 10, 0, 0, tzinfo=timezone.utc)
to_dt = datetime(2026, 4, 11, 3, 0, 0, tzinfo=timezone.utc)

deals = mt5.history_deals_get(from_dt, to_dt)
if deals:
    rearm_deals = [d for d in deals if getattr(d, "magic", 0) == FX_REARM_MAGIC]
    momentum_deals = [d for d in deals if getattr(d, "magic", 0) == FX_MOMENTUM_MAGIC]

    for magic, name, deal_list in [
        (FX_REARM_MAGIC, "FX Rearm (941777)", rearm_deals),
        (FX_MOMENTUM_MAGIC, "FX Momentum (941778)", momentum_deals),
    ]:
        closes = [d for d in deal_list if d.entry == 1]  # entry=1 means CLOSE
        total_net = sum(d.profit + d.commission + d.swap for d in closes)
        wins = [d for d in closes if d.profit > 0]
        losses = [d for d in closes if d.profit < 0]

        print(f"\n{'='*80}")
        print(f"{name}: {len(deal_list)} total deals, {len(closes)} closes")
        print(f"  Net PnL: {total_net:+.2f}")
        print(f"  Wins: {len(wins)}, Losses: {len(losses)}")
        if losses:
            avg_loss = sum(d.profit for d in losses) / len(losses)
            print(f"  Avg loss: {avg_loss:+.2f}")
        if wins:
            avg_win = sum(d.profit for d in wins) / len(wins)
            print(f"  Avg win: {avg_win:+.2f}")

        # Show last 5 closes
        print(f"  Last 5 closes:")
        for d in sorted(closes, key=lambda x: x.time)[-5:]:
            action = "BUY" if d.type == 0 else "SELL"
            print(f"    Deal {d.ticket} {d.time} {action} vol={d.volume} "
                  f"price={d.price:.5f} profit={d.profit:+.2f}")
else:
    print("No deals found")

mt5.shutdown()
