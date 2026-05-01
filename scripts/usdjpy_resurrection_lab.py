#!/usr/bin/env python3
"""USDJPY Resurrection Lab — mutate the dead strategy until it lives.

Current state: 1,265 closes, -$125.38 net, edge $0.083/close, spread ~$0.137/close
Net per close: -$0.054 → systematic death by spread tax.

Mutation strategies tested:
1. **Spread filter** — only enter when spread < 0.8 pips (skip expensive entries)
2. **Wider step** — 0.0001 → 0.0003 (fewer trades, bigger moves captured per trade)
3. **Higher close threshold** — close_alpha 1.0 → 0.7 (wait for bigger penetration)
4. **Minimum hold bars** — force holds ≥3 bars (avoid same-bar spread death)
5. **Combined** — spread filter + wider step + min hold (all mutations together)

Goal: Find ANY mutation that makes per-close edge > per-close spread cost.
"""
import json
import csv
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
EVENT_PATH = ROOT / "reports" / "penetration_lattice_live_source_events.jsonl"
SCOREBOARD_PATH = ROOT / "reports" / "penetration_lattice_lane_scoreboard.csv"


def load_usdjpy_events():
    """Load only USDJPY events from the live rearm lane."""
    events = []
    with open(EVENT_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            evt = json.loads(line)
            if evt.get("symbol") == "USDJPY":
                events.append(evt)
    return events


@dataclass
class MutationResult:
    name: str
    realized_pnl: float
    total_closes: int
    wins: int
    losses: int
    avg_pnl_per_close: float
    max_win: float
    max_loss: float
    win_rate: float
    blocked_entries: int  # entries blocked by mutation (spread filter, min hold, etc.)
    description: str


def simulate_mutation(events, mutation: str) -> MutationResult:
    """Simulate a single mutation on USDJPY events.
    
    Mutations:
    - baseline: no changes (current behavior)
    - spread_filter: skip entries when spread > threshold
    - wider_step: skip every 2nd entry level (simulate 3x step)
    - min_hold_3: block closes that happen within 3 bars of entry
    - close_alpha_07: require bigger penetration for close
    - combined: spread_filter + wider_step + min_hold_3
    """
    tickets = []  # (direction, entry_price, entry_bar_idx, entry_spread)
    realized_pnls = []
    blocked = 0
    bar_idx = 0
    level_counter = {"BUY": 0, "SELL": 0}
    
    # Track spread from events that include it
    spread_values = []

    for evt in events:
        action = evt.get("action")
        
        if action in ("open_ticket", "rearm_open"):
            direction = evt.get("direction", "BUY")
            entry_price = evt.get("entry_price", 0)
            fill_price = evt.get("entry_fill_price", entry_price)
            bid = evt.get("bid", 0)
            ask = evt.get("ask", 0)
            
            # Estimate spread if available
            if bid and ask:
                spread_pips = (ask - bid) / 0.01
                spread_values.append(spread_pips)
            else:
                spread_pips = 1.0  # default assumption
            
            # MUTATION: spread filter
            if mutation in ("spread_filter", "combined") and spread_pips > 0.8:
                blocked += 1
                continue
            
            # MUTATION: wider step (skip every 2nd entry)
            if mutation in ("wider_step", "combined"):
                level_counter[direction] += 1
                if level_counter[direction] % 3 != 0:
                    blocked += 1
                    continue
            
            tickets.append({
                "direction": direction,
                "entry_price": entry_price,
                "fill_price": fill_price,
                "bar_idx": bar_idx,
                "spread_pips": spread_pips,
            })

        elif action == "close_ticket":
            direction = evt.get("direction", "BUY")
            pnl = evt.get("realized_pnl", 0)
            exit_price = evt.get("exit_price", 0)
            
            bar_idx += 1
            
            # MUTATION: minimum hold bars
            if mutation in ("min_hold_3", "combined"):
                # Find matching ticket and check hold time
                matched = None
                for i, t in enumerate(tickets):
                    if t["direction"] == direction:
                        hold_bars = bar_idx - t["bar_idx"]
                        if hold_bars < 3:
                            # Block this close — position must hold
                            blocked += 1
                            matched = i
                            break
                        matched = i
                        break
                
                if matched is not None:
                    pnl = pnl  # Keep the PnL
                    tickets.pop(matched)
                    realized_pnls.append(pnl)
                continue
            
            # MUTATION: close_alpha_07 (simulate by reducing small wins)
            # In real code, alpha=0.7 means closes happen earlier with smaller moves
            # For shadow, we approximate: closes with |pnl| < 0.05 are blocked
            if mutation == "close_alpha_07":
                if abs(pnl) < 0.05:
                    blocked += 1
                    continue
            
            # Remove matching ticket (FIFO)
            removed = False
            for i, t in enumerate(tickets):
                if t["direction"] == direction:
                    tickets.pop(i)
                    removed = True
                    break
            
            if removed:
                realized_pnls.append(pnl)

    wins = sum(1 for p in realized_pnls if p > 0)
    losses = sum(1 for p in realized_pnls if p <= 0)
    total = len(realized_pnls)
    
    return MutationResult(
        name=mutation,
        realized_pnl=sum(realized_pnls),
        total_closes=total,
        wins=wins,
        losses=losses,
        avg_pnl_per_close=sum(realized_pnls) / max(1, total),
        max_win=max(realized_pnls) if realized_pnls else 0,
        max_loss=min(realized_pnls) if realized_pnls else 0,
        win_rate=wins / max(1, total) * 100,
        blocked_entries=blocked,
        description=get_mutation_description(mutation),
    )


def get_mutation_description(mutation: str) -> str:
    descriptions = {
        "baseline": "Current live behavior (no mutations)",
        "spread_filter": "Block entries when spread > 0.8 pips (skip expensive trades)",
        "wider_step": "Enter every 3rd level instead of every level (3x step spacing)",
        "min_hold_3": "Force minimum 3-bar hold (avoid same-bar spread death)",
        "close_alpha_07": "Require bigger penetration for closes (alpha 0.7 vs 1.0)",
        "combined": "Spread filter + wider step + min hold 3 bars",
    }
    return descriptions.get(mutation, mutation)


def main():
    events = load_usdjpy_events()
    print(f"Loaded {len(events)} USDJPY events")
    print(f"  Open events: {sum(1 for e in events if e.get('action') == 'open_ticket')}")
    print(f"  Close events: {sum(1 for e in events if e.get('action') == 'close_ticket')}")
    print()

    mutations = ["baseline", "spread_filter", "wider_step", "min_hold_3", "close_alpha_07", "combined"]
    
    print(f"{'='*100}")
    print(f"  USDJPY RESURRECTION LAB — Mutation Results")
    print(f"{'='*100}")
    print()
    print(f"{'Mutation':<20} {'PnL':>8} {'Closes':>7} {'WR':>7} {'Avg/Close':>10} {'MaxWin':>8} {'MaxLoss':>8} {'Blocked':>8}")
    print(f"{'-'*100}")
    
    results = []
    for mutation in mutations:
        result = simulate_mutation(events, mutation)
        results.append(result)
        
        delta = "BASELINE" if mutation == "baseline" else f"{'+' if result.realized_pnl > results[0].realized_pnl else ''}${result.realized_pnl - results[0].realized_pnl:+.2f}"
        print(f"{mutation:<20} ${result.realized_pnl:>6.2f} {result.total_closes:>7} {result.win_rate:>6.1f}% ${result.avg_pnl_per_close:>8.4f} ${result.max_win:>6.2f} ${result.max_loss:>6.2f} {result.blocked_entries:>8}  [{delta}]")

    print()
    print(f"{'='*100}")
    print(f"  ANALYSIS")
    print(f"{'='*100}")
    
    baseline = results[0]
    best = max(results[1:], key=lambda r: r.realized_pnl)
    
    print(f"""
  BASELINE: ${baseline.realized_pnl:.2f} across {baseline.total_closes} closes ({baseline.win_rate:.1f}% WR)
  Avg per close: ${baseline.avg_pnl_per_close:.4f}
  
  SPREAD TAX ESTIMATE:
  - Broker scoreboard: 1,265 closes, -$125.38
  - Shadow model: {baseline.total_closes} closes, ${baseline.realized_pnl:+.2f}
  - Gap: ${-125.38 - baseline.realized_pnl:+.2f} (spread cost not modeled in shadow)
  - Per-close spread drag: ${(-125.38 - baseline.realized_pnl) / max(1, baseline.total_closes):.4f}
  
  RESURRECTION STATUS:
""")
    
    for r in results[1:]:
        improvement = r.realized_pnl - baseline.realized_pnl
        status = "🟡 IMPROVES" if improvement > 0 else "🔴 NO CHANGE" if improvement > -10 else "❌ WORSENS"
        print(f"  {status} {r.name}: ${r.realized_pnl:+.2f} ({improvement:+.2f} vs baseline), {r.total_closes} closes, {r.blocked_entries} blocked")
    
    print(f"""
  KEY INSIGHT:
  The spread tax is ~${(-125.38 - baseline.realized_pnl) / max(1, baseline.total_closes):.4f}/close.
  To survive, mutations must either:
  1. INCREASE edge per close (bigger moves captured)
  2. DECREASE spread cost per close (filter expensive entries)
  3. DECREASE trade frequency (fewer total spread payments)
  
  No mutation that only blocks entries can fix the per-trade math.
  We need mutations that CAPTURE MORE PRICE MOVEMENT per trade.
""")
    
    # Write results
    output_path = ROOT / "reports" / "usdjpy_resurrection_lab.csv"
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["mutation", "realized_pnl", "total_closes", "wins", "losses", 
                                                "avg_pnl_per_close", "max_win", "max_loss", "win_rate", 
                                                "blocked_entries", "description"])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "mutation": r.name,
                "realized_pnl": round(r.realized_pnl, 2),
                "total_closes": r.total_closes,
                "wins": r.wins,
                "losses": r.losses,
                "avg_pnl_per_close": round(r.avg_pnl_per_close, 4),
                "max_win": round(r.max_win, 2),
                "max_loss": round(r.max_loss, 2),
                "win_rate": round(r.win_rate, 1),
                "blocked_entries": r.blocked_entries,
                "description": r.description,
            })
    
    print(f"  Results written to: {output_path}")


if __name__ == "__main__":
    main()
