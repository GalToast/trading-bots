#!/usr/bin/env python3
"""BTC $510 Gap Reconciliation — modeled vs broker per-trade comparison."""
import json
from pathlib import Path

REPORTS = Path(__file__).parent.parent / "reports"
EXEC_EVENTS = REPORTS / "penetration_lattice_live_btcusd_exc2_tight_exec_events.jsonl"

def main():
    events = [json.loads(l) for l in EXEC_EVENTS.read_text().splitlines() if l.strip()]
    closes = [e for e in events if "close" in e.get("action", "")]

    modeled_total = 0.0
    broker_total_known = 0.0
    broker_pnls_known = []

    print("Per-trade reconciliation:")
    print("=" * 100)
    for i, c in enumerate(closes):
        action = c.get("action", "")
        pnl = c.get("event", {}).get("realized_pnl", 0)
        modeled_total += pnl
        direction = c.get("event", {}).get("direction", "?")
        entry = c.get("event", {}).get("entry_price", "?")
        exit_p = c.get("event", {}).get("exit_price", "?")
        ts = c.get("event", {}).get("ts_utc", c.get("ts_utc", "?"))

        # Try to find broker_fill.profit
        result = c.get("result", {})
        broker_fill = result.get("broker_fill")
        if broker_fill:
            broker_profit = broker_fill.get("profit")
            broker_commission = broker_fill.get("commission", 0)
            broker_swap = broker_fill.get("swap", 0)
            if broker_profit is not None:
                net = broker_profit + broker_commission + broker_swap
                broker_total_known += net
                broker_pnls_known.append(net)
                broker_str = f"{net:+.2f}"
            else:
                broker_str = "N/A"
        else:
            broker_str = "NO_FILL"

        label = f"#{i+1:02d} {action}"
        print(f"  {label:45s} dir={direction} modeled={pnl:+.2f} broker={broker_str}")

    print()
    print("=" * 100)
    print(f"Total modeled PnL (all {len(closes)} closes):    {modeled_total:+.2f}")
    print(f"Broker PnL from {len(broker_pnls_known)} fills with data: {broker_total_known:+.2f}")
    print(f"Broker scoreboard total:                        -248.79")
    print(f"Modeled vs scoreboard gap:                      {modeled_total - (-248.79):+.2f}")
    print()
    print(f"Gap breakdown:")
    print(f"  Trades WITHOUT broker_fill data: {sum(1 for c in closes if not c.get('result',{}).get('broker_fill'))}")
    print(f"  Trades WITH broker_fill data:    {len(broker_pnls_known)}")
    print(f"    Their modeled PnL:             {sum(c.get('event',{}).get('realized_pnl',0) for c in closes if c.get('result',{}).get('broker_fill')):+.2f}")
    print(f"    Their actual broker PnL:       {broker_total_known:+.2f}")
    if len(broker_pnls_known) > 0:
        missing_modeled = modeled_total - sum(c.get('event',{}).get('realized_pnl',0) for c in closes if c.get('result',{}).get('broker_fill'))
        print(f"  Trades MISSING broker_fill:")
        print(f"    Their modeled PnL:             {missing_modeled:+.2f}")
        implied_broker = -248.79 - broker_total_known
        print(f"    Implied broker PnL:            {implied_broker:+.2f}")
        if missing_modeled != 0:
            print(f"    Drift factor:                {implied_broker / missing_modeled:.2f}x")

if __name__ == "__main__":
    main()
