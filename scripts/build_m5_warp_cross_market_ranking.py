#!/usr/bin/env python3
"""Build cross-market M5 Warp ranking from all state files + event summaries.

Produces a ranked table of ALL M5 Warp lanes by $/close, reset rate,
spread/step ratio, and composite score.

Usage: python scripts/build_m5_warp_cross_market_ranking.py
Output: reports/m5_warp_cross_market_ranking.md + .json
"""

import json
import glob
import os
from datetime import datetime, timezone

REPORTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")

# Lane definitions: (name_glob, symbol, asset_class, step_usd_or_pips)
LANES = [
    # Live
    ("penetration_lattice_live_btcusd_m5_warp_state.json", "BTCUSD", "crypto", "live"),
    ("penetration_lattice_live_ethusd_m5_warp_state.json", "ETHUSD", "crypto", "live"),
    ("penetration_lattice_live_solusd_m5_warp_state.json", "SOLUSD", "crypto", "live"),
    # Shadows — crypto
    ("penetration_lattice_shadow_btcusd_m5_warp_state.json", "BTCUSD", "crypto", None),
    ("penetration_lattice_shadow_ethusd_m5_warp_state.json", "ETHUSD", "crypto", None),
    ("penetration_lattice_shadow_ethusd_m5_warp_5_state.json", "ETHUSD", "crypto", None),
    ("penetration_lattice_shadow_ethusd_m5_warp_wide_state.json", "ETHUSD", "crypto", None),
    ("penetration_lattice_shadow_solusd_m5_warp_state.json", "SOLUSD", "crypto", None),
    ("penetration_lattice_shadow_xrpusd_m5_warp_state.json", "XRPUSD", "crypto", None),
    ("penetration_lattice_shadow_ltcusd_m5_warp_state.json", "LTCUSD", "crypto", None),
    ("penetration_lattice_shadow_adausd_m5_warp_state.json", "ADAUSD", "crypto", None),
    # Shadows — FX M5
    ("penetration_lattice_shadow_gbpusd_m5_warp_state.json", "GBPUSD", "fx", None),
    ("penetration_lattice_shadow_gbpusd_m5_warp_1x_state.json", "GBPUSD", "fx", None),
    ("penetration_lattice_shadow_usdjpy_m5_warp_state.json", "USDJPY", "fx", None),
    ("penetration_lattice_shadow_usdjpy_m5_warp_1x_state.json", "USDJPY", "fx", None),
    ("penetration_lattice_shadow_audusd_m5_warp_state.json", "AUDUSD", "fx", None),
    ("penetration_lattice_shadow_eurusd_m5_warp_state.json", "EURUSD", "fx", None),
    ("penetration_lattice_shadow_nzdusd_m5_warp_state.json", "NZDUSD", "fx", None),
    ("penetration_lattice_shadow_usdcad_m5_warp_state.json", "USDCAD", "fx", None),
    # Shadows — indices/commodities
    ("penetration_lattice_shadow_xauusd_m5_warp_state.json", "XAUUSD", "index", None),
    ("penetration_lattice_shadow_nas100_m5_warp_state.json", "NAS100", "index", None),
    ("penetration_lattice_shadow_us30_m5_warp_state.json", "US30", "index", None),
]


def load_state(filename):
    path = os.path.join(REPORTS, filename)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_event_summary(state_filename):
    """Load corresponding event summary file."""
    base = state_filename.replace("_state.json", "_events.summary.json")
    path = os.path.join(REPORTS, base)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def extract_metrics(state, event_summary, symbol, asset_class):
    if state is None:
        return None
    
    sym_data = state.get("symbols", {}).get(symbol, {})
    runner = state.get("runner", {})
    metadata = state.get("metadata", {})
    
    realized_closes = sym_data.get("realized_closes", 0)
    realized_net = sym_data.get("realized_net_usd", 0.0)
    resets = sym_data.get("anchor_resets", 0)
    open_tickets = len(sym_data.get("open_tickets", []))
    step = sym_data.get("base_step_px", 0) or metadata.get("step", 0)
    
    # From event summary
    evt_closes = 0
    evt_net = 0.0
    evt_wins = 0
    evt_losses = 0
    evt_resets = 0
    if event_summary:
        c = event_summary.get("closes", {})
        evt_closes = c.get("total", 0)
        evt_net = c.get("net_usd", 0.0)
        evt_wins = c.get("wins", 0)
        evt_losses = c.get("losses", 0)
        evt_resets = event_summary.get("resets", 0)
    
    # Merge state + event summary:
    # - State file has durable realized_closes and realized_net_usd (from the engine itself)
    # - Event summary may be tail-only (last 200 events) — use it ONLY for win rate
    # - For fresh restarts (0 realized_closes), fall back to event summary
    if realized_closes > 0:
        total_closes = realized_closes
        total_net = realized_net
        total_resets = resets
    elif evt_closes > 0:
        total_closes = evt_closes
        total_net = evt_net
        total_resets = evt_resets
    else:
        total_closes = 0
        total_net = 0.0
        total_resets = resets  # Resets persist in state even after restart
    
    pnl_per_close = total_net / total_closes if total_closes > 0 else 0.0
    win_rate = evt_wins / (evt_wins + evt_losses) if (evt_wins + evt_losses) > 0 else 0.0
    reset_rate = total_resets / total_closes if total_closes > 0 else 0.0
    
    # Runtime
    pid = runner.get("pid", 0)
    started = runner.get("started_at", "")
    heartbeat = runner.get("heartbeat_at", "")
    running = pid > 0
    
    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "step": step,
        "closes": total_closes,
        "net_usd": round(total_net, 2),
        "pnl_per_close": round(pnl_per_close, 2),
        "win_rate": round(win_rate, 3),
        "resets": total_resets,
        "reset_rate": round(reset_rate, 2),
        "open_tickets": open_tickets,
        "pid": pid,
        "running": running,
        "started_at": started,
        "heartbeat_at": heartbeat,
        "state_file": "",
    }


def main():
    results = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M UTC")
    
    for filename, symbol, asset_class, _ in LANES:
        state = load_state(filename)
        evt = load_event_summary(filename)
        metrics = extract_metrics(state, evt, symbol, asset_class)
        if metrics:
            metrics["state_file"] = filename
            results.append(metrics)
    
    # Sort by composite score: positive reset-adjusted PnL first, highest first
    # Then zero-close lanes
    # Then negative reset-adjusted PnL
    def sort_key(m):
        if m["closes"] == 0:
            return (1, 0)  # No data — rank last
        adjusted = m["pnl_per_close"] * max(0, 1 - m["reset_rate"])
        if adjusted > 0:
            return (0, -adjusted)  # Positive: highest first
        else:
            return (2, abs(adjusted))  # Negative: least loss first
    
    results.sort(key=sort_key)
    
    # Build markdown
    lines = [
        f"# M5 Warp Cross-Market Ranking — {now}",
        "",
        "All M5 Warp lanes ranked by reset-adjusted $/close.",
        "",
        "| Rank | Lane | Symbol | Class | Closes | Net $ | $/Close | Win% | Resets | Reset Rate | Open | Status |",
        "|------|------|--------|-------|--------|-------|---------|------|--------|------------|------|--------|",
    ]
    
    for i, m in enumerate(results, 1):
        status = "✅" if m["closes"] > 0 and m["pnl_per_close"] > 0 else \
                 "⚠️" if m["closes"] > 0 and m["pnl_per_close"] < 0 else \
                 "⏳"
        cls_label = "LIVE" if m.get("state_file", "").startswith("penetration_lattice_live") else "shadow"
        
        lines.append(
            f"| {i} | {cls_label} | {m['symbol']} | {m['asset_class']} | "
            f"{m['closes']} | ${m['net_usd']:+.2f} | ${m['pnl_per_close']:+.2f} | "
            f"{m['win_rate']*100:.0f}% | {m['resets']} | {m['reset_rate']:.2f} | "
            f"{m['open_tickets']} | {status} |"
        )
    
    lines.append("")
    lines.append("## By Asset Class")
    lines.append("")
    
    for asset in ["crypto", "fx", "index"]:
        subset = [m for m in results if m["asset_class"] == asset and m["closes"] > 0]
        if subset:
            total_c = sum(m["closes"] for m in subset)
            total_n = sum(m["net_usd"] for m in subset)
            avg_pnl = total_n / total_c if total_c > 0 else 0
            pos = sum(1 for m in subset if m["pnl_per_close"] > 0)
            lines.append(f"**{asset.upper()}:** {len(subset)} lanes with closes, {total_c} closes, "
                        f"${total_n:+.2f} net, ${avg_pnl:+.2f}/close, {pos}/{len(subset)} positive")
    
    lines.append("")
    lines.append("## Read")
    lines.append("- Reset-adjusted $/close = $/close × (1 - reset_rate). Penalizes lanes that chase anchors.")
    lines.append("- 'LIVE' = running live on broker. 'shadow' = paper trading.")
    lines.append("- Lanes with 0 closes are ranked last — need more runtime.")
    lines.append("")
    
    md_path = os.path.join(REPORTS, "m5_warp_cross_market_ranking.md")
    json_path = os.path.join(REPORTS, "m5_warp_cross_market_ranking.json")
    
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": now, "lanes": results}, f, indent=2)
    
    print(f"Ranking saved: {md_path}")
    print(f"JSON saved: {json_path}")
    print()
    # Print top 10
    for line in lines[:15]:
        print(line)


if __name__ == "__main__":
    main()
