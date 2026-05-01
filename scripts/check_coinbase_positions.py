"""Check for orphaned Coinbase positions and report current state."""
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

try:
    from coinbase_advanced_client import CoinbaseAdvancedClient
except ImportError:
    print("ERROR: Cannot import coinbase_advanced_client")
    print("The client may not be available or dependencies missing.")
    sys.exit(1)

try:
    client = CoinbaseAdvancedClient()
except Exception as e:
    print(f"ERROR: Failed to initialize Coinbase client: {e}")
    sys.exit(1)

print(f"[{datetime.now(timezone.utc).isoformat()}] Checking Coinbase positions...\n")

# Check open orders
try:
    open_orders = client.list_orders(order_status="OPEN", limit=100)
    orders = open_orders.get("orders", [])
    print(f"OPEN ORDERS: {len(orders)}")
    if orders:
        for o in orders:
            print(f"  - {o.get('product_id', '?')}: side={o.get('side', '?')}, "
                  f"size={o.get('size', '?')}, price={o.get('price', '?')}, "
                  f"id={o.get('order_id', '?')}")
    else:
        print("  (none)")
except Exception as e:
    print(f"ERROR fetching open orders: {e}")

print()

# Check account balances
try:
    accounts = client.accounts()
    accts = accounts.get("accounts", [])
    print(f"ACCOUNTS: {len(accts)}")
    # Filter to non-zero balances
    active = [a for a in accts if float(a.get("available_balance", {}).get("value", 0)) > 0 or
              float(a.get("balance", {}).get("value", 0)) > 0]
    print(f"Active (non-zero): {len(active)}")
    for a in sorted(active, key=lambda x: float(x.get("balance", {}).get("value", 0)), reverse=True):
        bal = a.get("balance", {})
        avail = a.get("available_balance", {})
        hold = a.get("hold", {})
        currency = a.get("currency", "?")
        print(f"  {currency}: balance={bal.get('value', '0')}, "
              f"available={avail.get('value', '0')}, hold={hold.get('value', '0')}")
except Exception as e:
    print(f"ERROR fetching accounts: {e}")

# Check runner state file
print("\n--- Runner State File ---")
state_path = os.path.join(os.path.dirname(__file__), "..", "reports", "multi_coin_isolated_state.json")
try:
    import json
    with open(state_path) as f:
        state = json.load(f)
    print(f"Cycle: {state.get('cycle', '?')}")
    print(f"Equity: ${state.get('total_equity', '?')}")
    print(f"Last updated: {state.get('last_updated', '?')}")
    positions = state.get("ledgers", {})
    active_positions = {k: v for k, v in positions.items() if v.get("position", {}).get("ep")}
    print(f"Active positions: {len(active_positions)}")
    for coin, ledger in active_positions.items():
        pos = ledger["position"]
        strat = ledger.get("strategy", "?")
        print(f"  {coin} ({strat}): entry=${pos.get('ep')}, "
              f"SL=${pos.get('sl')}, TP=${pos.get('tp')}, "
              f"units={pos.get('units')}, hold={pos.get('hold')}")
except FileNotFoundError:
    print(f"State file not found: {state_path}")
except Exception as e:
    print(f"ERROR reading state file: {e}")

print("\n--- Heartbeat Check ---")
hb_path = os.path.join(os.path.dirname(__file__), "..", "reports", "multi_coin_isolated_heartbeat.json")
try:
    import json
    with open(hb_path) as f:
        hb = json.load(f)
    last_hb = hb.get("last_heartbeat_ts", 0)
    import time
    age = time.time() - last_hb
    print(f"Heartbeat age: {age:.0f}s ({age/60:.1f} min)")
    print(f"PID: {hb.get('pid', '?')}, Cycle: {hb.get('cycle', '?')}")
    if age > 300:
        print("⚠️  RUNNER IS STALE (heartbeat > 5 min old)")
    elif age > 120:
        print("⚠️  RUNNER MAY BE STALE (heartbeat > 2 min old)")
    else:
        print("✅ Runner appears healthy")
except FileNotFoundError:
    print("Heartbeat file not found (watchdog may not be deployed yet)")
except Exception as e:
    print(f"ERROR reading heartbeat: {e}")
