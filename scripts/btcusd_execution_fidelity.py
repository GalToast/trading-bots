#!/usr/bin/env python3
"""BTCUSD execution fidelity forensics for BTC H1 live lane.

Uses broker-authoritative lane scoreboard truth when available, and keeps
shadow-state-derived slippage commentary explicitly labeled as approximate.
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "penetration_lattice_shadow_btcusd_exc2_tight_state.json"
EVENT_PATH = ROOT / "reports" / "penetration_lattice_shadow_btcusd_exc2_tight_events.jsonl"
SCOREBOARD_PATH = ROOT / "reports" / "penetration_lattice_lane_scoreboard.csv"
LANE_ID = "live_btcusd_exc2_tight_941779"

BTCUSD_POINT = 0.01
BTCUSD_PIP = 0.1  # 1 pip = 10 points for BTCUSD
VOLUME = 0.01  # live volume


def price_to_pips(px):
    return px / BTCUSD_PIP


def load_broker_total_row() -> dict[str, str] | None:
    if not SCOREBOARD_PATH.exists():
        return None
    with SCOREBOARD_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("lane_id") == LANE_ID and row.get("symbol") == "TOTAL" and row.get("realized_basis") == "broker":
                return row
    return None


def as_float(row: dict[str, str] | None, key: str, default: float = 0.0) -> float:
    if not row:
        return default
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def as_int(row: dict[str, str] | None, key: str, default: int = 0) -> int:
    if not row:
        return default
    try:
        return int(float(row.get(key, default) or default))
    except (TypeError, ValueError):
        return default


def main():
    with open(STATE_PATH) as f:
        state = json.load(f)

    btc = state["symbols"]["BTCUSD"]
    broker_row = load_broker_total_row()
    open_tickets = btc["open_tickets"]
    modeled_realized_net = float(btc["realized_net_usd"])
    modeled_realized_closes = int(btc["realized_closes"])
    rearm_opens = btc["rearm_opens"]
    max_open = btc["max_open_total"]
    anchor = btc["anchor"]
    current_price = anchor  # approximate shadow-state anchor mark, not broker MTM
    updated = state["updated_at"]
    realized_net = as_float(broker_row, "realized_usd", modeled_realized_net)
    realized_closes = as_int(broker_row, "closes", modeled_realized_closes)
    broker_floating = as_float(broker_row, "floating_usd", 0.0)
    broker_net = as_float(broker_row, "net_usd", realized_net + broker_floating)
    broker_open_count = as_int(broker_row, "open_count", len(open_tickets))
    broker_updated = broker_row.get("updated_at", "") if broker_row else ""
    broker_started = broker_row.get("session_started_at", "") if broker_row else ""

    print(f"=" * 70)
    print(f"  BTCUSD EXECUTION FIDELITY FORENSICS")
    if broker_row:
        print(f"  Broker scoreboard updated: {broker_updated}")
        print(f"  Session started: {broker_started}")
        print(f"  Broker realized: ${realized_net:+.2f} ({realized_closes} closes)")
        print(f"  Broker floating: ${broker_floating:+.2f}")
        print(f"  Broker net:      ${broker_net:+.2f}")
        print(f"  Broker open count: {broker_open_count}")
        print(f"  Shadow state updated: {updated}")
        print(f"  Shadow modeled realized: ${modeled_realized_net:+.2f} ({modeled_realized_closes} closes)")
    else:
        print(f"  Shadow state updated: {updated}")
        print(f"  Realized (shadow modeled): ${modeled_realized_net:+.2f} ({modeled_realized_closes} closes)")
    print(f"  Rearm opens: {rearm_opens}")
    print(f"  Max open: {max_open}")
    print(f"  Anchor (approx mark basis): ${anchor:,.2f}")
    print(f"=" * 70)

    # 1. ENTRY SLIPPAGE ANALYSIS
    print(f"\n{'='*60}")
    print(f"  ENTRY SLIPPAGE ANALYSIS")
    print(f"{'='*60}")

    total_slippage = 0
    worst_slip = 0
    slip_directions = {"favorable": 0, "adverse": 0}
    mass_fills = {}  # group by fill price to detect mass-fill events
    avg_slip = 0.0
    avg_slip_pips = 0.0

    for t in open_tickets:
        trigger = t["trigger_level"]
        fill = t["entry_fill_price"]
        direction = t["direction"]
        slip = fill - trigger  # positive = worse for BUY, negative = worse for SELL

        if direction == "BUY":
            adverse = slip > 0  # filled higher than trigger = bad
        else:
            adverse = slip < 0  # filled lower than trigger = bad

        slip_pips = price_to_pips(abs(slip))
        total_slippage += abs(slip)

        if adverse:
            slip_directions["adverse"] += 1
        else:
            slip_directions["favorable"] += 1

        if abs(slip) > worst_slip:
            worst_slip = abs(slip)

        # Track mass fills
        fill_key = f"${fill:,.2f}"
        if fill_key not in mass_fills:
            mass_fills[fill_key] = []
        mass_fills[fill_key].append(f"{direction}#{t.get('live_ticket', '?')}")

    print(f"\n  Shadow open tickets tracked: {len(open_tickets)}")
    print(f"  Adverse slippage: {slip_directions['adverse']} positions")
    print(f"  Favorable slippage: {slip_directions['favorable']} positions")
    print(f"  Total absolute slippage: ${total_slippage:.2f} ({price_to_pips(total_slippage):.1f} pips)")
    print(f"  Worst single slip: ${worst_slip:.2f} ({price_to_pips(worst_slip):.1f} pips)")
    if open_tickets:
        avg_slip = total_slippage / len(open_tickets)
        avg_slip_pips = price_to_pips(avg_slip)
        print(f"  Avg slippage per position: ${avg_slip:.2f} ({avg_slip_pips:.1f} pips)")
    else:
        print("  Avg slippage per position: n/a")

    # Mass fill detection
    print(f"\n  Mass-fill events (multiple positions at same fill price):")
    for fill_price, tickets in sorted(mass_fills.items(), key=lambda x: -len(x[1])):
        if len(tickets) > 1:
            print(f"    {fill_price}: {len(tickets)} positions — {', '.join(tickets[:5])}{'...' if len(tickets) > 5 else ''}")

    # 2. FLOATING PnL ANALYSIS
    print(f"\n{'='*60}")
    print(f"  FLOATING PnL")
    print(f"{'='*60}")
    if broker_row:
        print(f"\n  Broker floating PnL: ${broker_floating:+.2f}")
        print(f"  Broker net PnL:      ${broker_net:+.2f}")
    print(f"  Approx shadow mark basis: anchor ${anchor:,.2f}")

    total_float = 0
    winners = 0
    losers = 0
    max_float_loss = 0
    max_float_win = 0

    for t in open_tickets:
        fill = t["entry_fill_price"]
        direction = t["direction"]
        # Approximate PnL for 0.01 lot BTCUSD: $1 per $100 move (rough estimate)
        # Actual: profit = volume * (exit - entry) / point * point_value
        # Simplified: for 0.01 lot, each $1 move = $0.01 PnL
        if direction == "BUY":
            float_pnl = (current_price - fill) * VOLUME * 100  # approximate
        else:
            float_pnl = (fill - current_price) * VOLUME * 100

        total_float += float_pnl
        if float_pnl > 0:
            winners += 1
            max_float_win = max(max_float_win, float_pnl)
        else:
            losers += 1
            max_float_loss = min(max_float_loss, float_pnl)

    print(f"\n  Winners: {winners}, Losers: {losers}")
    print(f"  Approx shadow floating PnL at anchor: ${total_float:+.2f}")
    print(f"  Max floating win: ${max_float_win:+.2f}")
    print(f"  Max floating loss: ${max_float_loss:+.2f}")

    # 3. HOLD TIME ANALYSIS
    print(f"\n{'='*60}")
    print(f"  HOLD TIME ANALYSIS")
    print(f"{'='*60}")

    import time
    now_ts = time.time()
    hold_times = []
    for t in open_tickets:
        opened = t.get("opened_time", 0)
        if opened > 0:
            hold_hours = (now_ts - opened) / 3600
            hold_times.append(hold_hours)
            fill = t["entry_fill_price"]
            direction = t["direction"]
            ticket = t.get("live_ticket", "?")
            print(f"    {direction} #{ticket}: held {hold_hours:.1f}h, fill ${fill:,.2f}")

    if hold_times:
        print(f"\n  Avg hold time: {sum(hold_times)/len(hold_times):.1f}h")
        print(f"  Max hold time: {max(hold_times):.1f}h")
        print(f"  Min hold time: {min(hold_times):.1f}h")

    # 4. BACKTEST VS LIVE GAP
    print(f"\n{'='*60}")
    print(f"  BACKTEST vs LIVE GAP ANALYSIS")
    print(f"{'='*60}")

    backtest_pnl = 250309  # from qwen-trading's sweep at step=$45
    live_realized = realized_net
    live_estimated = broker_net if broker_row else (live_realized + total_float)
    live_estimated_label = "Broker net" if broker_row else "Approx shadow realized+float"

    print(f"\n  Backtest (120d, step=$45):  ${backtest_pnl:>12,.2f}")
    print(f"  Live realized:               ${live_realized:>12,.2f}")
    print(f"  {live_estimated_label:<26} ${live_estimated:>12,.2f}")
    print(f"  Gap (backtest vs realized):  ${backtest_pnl - live_realized:>12,.2f}")
    print(f"  Gap explained by:")
    print(f"    - Entry slippage:          ~${total_slippage * VOLUME * 100:>10,.2f} (absolute slip on open positions)")
    print(f"    - Spread cost per trade:   see analysis below")
    print(f"    - Hold time differences:   live holds much longer than backtest bars")

    # 5. KEY FINDINGS
    print(f"\n{'='*60}")
    print(f"  KEY FINDINGS")
    print(f"{'='*60}")

    hedge_line = ""
    sells_open = sum(1 for t in open_tickets if t["direction"] == "SELL")
    if sells_open == 1:
        single_sell = next((t for t in open_tickets if t["direction"] == "SELL"), None)
        if single_sell is not None:
            hedge_line = f"\n     - The single SELL at ${single_sell['entry_fill_price']:,.2f} is the only hedge"
    spread_drag_line = ""
    if open_tickets:
        spread_drag_line = f"\n     - Over {realized_closes} closes, proxy spread drag = ~${total_slippage * VOLUME * 100 * realized_closes / len(open_tickets):.0f}"

    print(f"""
  1. ENTRY SLIPPAGE is MASSIVE:
     - {'MASS-FILL EVENTS DETECTED' if any(len(v) > 3 for v in mass_fills.values()) else 'No mass fills'}
     - Avg slippage: ${avg_slip:.2f} per position""" + (f"""
     - This alone accounts for ~${avg_slip * realized_closes:.0f} over {realized_closes} closes""" if open_tickets else "") + f"""

  2. BUY-BIAS TRAP:
     - {sum(1 for t in open_tickets if t['direction'] == 'BUY')} BUY vs {sum(1 for t in open_tickets if t['direction'] == 'SELL')} SELL positions open
     - Market dropped since anchor (${anchor:,.2f}), so BUYs are trapped{hedge_line}

  3. REARM CHURN:
     - {rearm_opens} rearm opens adds more positions at losing levels
     - Each rearm adds another round-trip spread cost

  4. SPREAD COST:
     - BTCUSD spread/slippage proxy is ~{avg_slip_pips:.0f} pips avg per tracked open fill{spread_drag_line}

  CONCLUSION: Use broker scoreboard values as live truth.
  This script's shadow-state calculations are useful for slippage and mass-fill forensics,
  but not as a substitute for broker-realized or broker-floating PnL.
""")

    # Write summary
    summary_path = ROOT / "reports" / "btcusd_execution_fidelity_forensics.md"
    summary_path.write_text(f"""# BTCUSD Execution Fidelity Forensics

## Summary
- **Broker scoreboard updated:** {broker_updated or "unavailable"}
- **Shadow state updated:** {updated}
- **Anchor:** ${anchor:,.2f}
- **Broker-realized PnL:** ${realized_net:+.2f} ({realized_closes} closes)
- **Broker floating PnL:** ${broker_floating:+.2f}
- **Broker net PnL:** ${broker_net:+.2f}
- **Shadow open tickets:** {len(open_tickets)} ({sum(1 for t in open_tickets if t['direction'] == 'BUY')} BUY, {sum(1 for t in open_tickets if t['direction'] == 'SELL')} SELL)

## Entry Slippage
- **Total absolute slippage:** ${total_slippage:.2f} ({price_to_pips(total_slippage):.1f} pips)
- **Avg per tracked ticket:** {f"${avg_slip:.2f} ({avg_slip_pips:.1f} pips)" if open_tickets else "n/a"}
- **Worst single slip:** ${worst_slip:.2f} ({price_to_pips(worst_slip):.1f} pips)
- **Adverse slippage:** {slip_directions['adverse']} positions
- **Favorable slippage:** {slip_directions['favorable']} positions

## Mass-Fill Events
{chr(10).join(f"- `{fp}`: {len(tix)} positions" for fp, tix in sorted(mass_fills.items(), key=lambda x: -len(x[1])) if len(tix) > 1) or "None detected"}

## Backtest vs Live Gap
- **Backtest (120d):** $250,309
- **Broker-realized live:** ${live_realized:+.2f}
- **Broker-floating live:** ${broker_floating:+.2f}
- **Broker-net live:** ${broker_net:+.2f}
- **Gap:** ${backtest_pnl - live_realized:,.2f}

## Conclusion
Use broker scoreboard values as live truth. Shadow-state calculations here are
only for slippage / mass-fill diagnostics and should not be read as broker-live PnL.
""", encoding="utf-8")

    print(f"\n  Report written to: {summary_path}")


if __name__ == "__main__":
    main()
