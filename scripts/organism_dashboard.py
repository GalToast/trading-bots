#!/usr/bin/env python3
"""
Unified Organism Dashboard — One screen to rule them all.

Reads all lane state files and generates a clean status report.
Run: python scripts/organism_dashboard.py

Output: reports/organism_dashboard.md (for browsing)
        Also prints to console
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
import json
import MetaTrader5 as mt5
from pathlib import Path
from datetime import datetime
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def age_str(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        diff = (datetime.now(timezone.utc) - dt).total_seconds()
        if diff < 60:
            return f"{diff:.0f}s ago"
        elif diff < 3600:
            return f"{diff/60:.0f}m ago"
        else:
            return f"{diff/3600:.1f}h ago"
    except:
        return "?"


def status_icon(state: dict[str, Any]) -> str:
    hb = state.get("runner", {}).get("heartbeat_at", "")
    if not hb:
        return "[?]"
    age = age_str(hb)
    if "s ago" in age and float(age.replace("s ago", "")) < 120:
        return "[OK]"
    elif "m ago" in age:
        mins = float(age.replace("m ago", ""))
        if mins < 10:
            return "[OK]"
        elif mins < 30:
            return "[WARN]"
        else:
            return "[STALE]"
    else:
        return "[STALE]"


def lane_summary(name: str, state_path: Path, symbol_key: str = "BTCUSD") -> str:
    state = load_json(state_path)
    if not state:
        return f"**{name}**: ❓ State file not found\n"
    
    runner = state.get("runner", {})
    symbol = (state.get("symbols", {}) or {}).get(symbol_key, {})
    icon = status_icon(state)
    realized = symbol.get("realized_net_usd", 0.0)
    closes = symbol.get("realized_closes", 0)
    open_count = len(symbol.get("open_tickets", []) or [])
    anchor_resets = symbol.get("anchor_resets", 0)
    res_risk = symbol.get("anchor_resets_risk", 0)
    res_flat = symbol.get("anchor_resets_flat", 0)
    hb = runner.get("heartbeat_at", "?")
    
    resets_str = f"{anchor_resets}"
    if res_risk > 0 or res_flat > 0:
        resets_str = f"{anchor_resets} (R:{res_risk}/F:{res_flat})"
    
    return f"**{name}**: {icon} +${realized:.2f} ({closes} closes) | {open_count} open | {resets_str} resets | HB: {age_str(hb)}\n"


def kelly_summary() -> str:
    state_path = REPORTS / "kelly_shadow_state.json"
    state = load_json(state_path)
    if not state:
        return "**Kelly**: ❓ State not found\n"
    
    cycle = state.get("cycle", 0)
    equity = state.get("total_equity", 0.0)
    pnl = state.get("total_pnl", 0.0)
    ledgers = state.get("ledgers", {})
    
    lines = [f"**Kelly**: Cycle {cycle}, Equity ${equity:.2f} (PnL ${pnl:+.2f})"]
    for coin, data in ledgers.items():
        closes = data.get("close_count", 0)
        pos = data.get("position", "flat")
        if closes > 0:
            icon = "[CLOSED]"
        elif pos != "flat":
            icon = "[ACTIVE]"
        else:
            icon = "[WAIT]"
        lines.append(f"  {coin}: {icon} {closes} closes | {pos}")
    
    return "\n".join(lines) + "\n"


def rotation_summary() -> str:
    state_path = ROOT / "rotation_shadow_state.json"
    state = load_json(state_path)
    if not state:
        return "**Rotation**: ❓ State not found (may be in separate dir)\n"
    
    cycle = state.get("cycle", 0)
    pairs = state.get("active_pairs", state.get("pairs", {}))
    
    lines = [f"**Rotation**: Cycle {cycle}"]
    if not pairs:
        lines.append("  No pair data available")
    for pair_name, pair_data in pairs.items():
        if isinstance(pair_data, dict):
            if pair_data.get("open"):
                status = "[OPEN]"
            else:
                status = "[WAIT]"
            rs = pair_data.get("rs", pair_data.get("rs_pct", 0.0))
            hold = pair_data.get("hold", pair_data.get("hold_count", 0))
            lines.append(f"  {pair_name}: {status} RS={rs:+.1f}% | hold {hold}")
    
    return "\n".join(lines) + "\n"


def organism_health() -> str:
    """Calculate overall organism health score."""
    lanes = [
        ("live_rearm", REPORTS / "penetration_lattice_shadow_state.json", "EURUSD"),
        ("live_momentum", REPORTS / "penetration_lattice_shadow_momentum_state.json", "EURUSD"),
        ("BTC exc2_tight", REPORTS / "penetration_lattice_shadow_btcusd_exc2_tight_state.json", "BTCUSD"),
        ("BTC M5 warp", REPORTS / "penetration_lattice_live_btcusd_m5_warp_state.json", "BTCUSD"),
        ("BTC M15 warp", REPORTS / "penetration_lattice_shadow_btcusd_m15_warp_state.json", "BTCUSD"),
    ]
    
    healthy = 0
    total = len(lanes)
    for name, path, sym in lanes:
        state = load_json(path)
        if state:
            healthy += 1
    
    if healthy == total:
        return "[ALL HEALTHY]"
    elif healthy >= total * 0.7:
        return f"[{healthy}/{total} lanes healthy]"
    else:
        return f"[{healthy}/{total} lanes healthy -- ATTENTION NEEDED]"


def generate_dashboard() -> str:
    lines = [
        "# Organism Dashboard",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Health:** {organism_health()}",
        "",
        "## Live Lanes",
        "",
    ]
    
    # Live lanes
    live_lanes = [
        ("FX Rearm α=1.0", REPORTS / "penetration_lattice_shadow_state.json", "EURUSD"),
        ("FX Momentum α50", REPORTS / "penetration_lattice_shadow_momentum_alpha50_state.json", "EURUSD"),
        ("BTC exc2_tight", REPORTS / "penetration_lattice_shadow_btcusd_exc2_tight_state.json", "BTCUSD"),
        ("BTC M5 warp", REPORTS / "penetration_lattice_shadow_btcusd_m5_warp_state.json", "BTCUSD"),
    ]
    
    for name, path, sym in live_lanes:
        lines.append(lane_summary(name, path, sym))
    
    lines.extend([
        "",
        "## Shadow Lanes",
        "",
    ])
    
    # Shadow lanes
    shadows = [
        ("BTC H1 step30", REPORTS / "penetration_lattice_shadow_btcusd_h1_step30_state.json", "BTCUSD"),
        ("BTC H1 step50", REPORTS / "penetration_lattice_shadow_btcusd_h1_step50_state.json", "BTCUSD"),
        ("BTC M15 warp", REPORTS / "penetration_lattice_shadow_btcusd_m15_warp_state.json", "BTCUSD"),
        ("GBPUSD tick-fwd", REPORTS / "shadow_gbpusd_tick_forward_state.json", "GBPUSD"),
        ("CFG/ETH synth", REPORTS / "cfg_eth_synthetic_sleeve_shadow_state.json", "CFGUSD"),
        ("FX: GBPUSD micro", REPORTS / "penetration_lattice_shadow_gbpusd_m15_fxmicro_state.json", "GBPUSD"),
        ("FX: EURUSD micro", REPORTS / "penetration_lattice_shadow_eurusd_m15_fxmicro_state.json", "EURUSD"),
        ("FX: NZDUSD micro", REPORTS / "penetration_lattice_shadow_nzdusd_m15_fxmicro_state.json", "NZDUSD"),
    ]
    
    for name, path, sym in shadows:
        lines.append(lane_summary(name, path, sym))
    
    lines.extend([
        "",
        "## Kelly Shadow",
        "",
    ])
    lines.append(kelly_summary())
    
    lines.extend([
        "",
        "## Rotation",
        "",
    ])
    lines.append(rotation_summary())
    
    lines.extend([
        "",
        "## Terminal Hygiene",
        "",
    ])
    lines.append(ghost_audit_summary())
    
    lines.extend([
        "",
        "---",
        "",
        "**Run:** `python scripts/organism_dashboard.py` to refresh.",
        "**War Room:** All switchboard messages relay to user's single terminal.",
    ])
    
    return "\n".join(lines)


def ghost_audit_summary() -> str:
    # 1. Get Direct Broker State from Execution Monitor
    monitor_path = REPORTS / "execution_monitor_report.json"
    if not monitor_path.exists():
        return "Execution monitor report not found. Waiting for first cycle."
    
    with open(monitor_path, "r", encoding="utf-8") as f:
        monitor_data = json.load(f)
    
    broker_positions = monitor_data.get("broker_positions", [])
    
    # 2. Get Active Magics from Registry
    registry_path = Path("configs/penetration_lattice_runner_registry.json")
    if not registry_path.exists():
        return "Registry not found."
    
    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    active_magics = set()
    for lane in registry.get("lanes", []):
        if lane.get("enabled", True):
            args = lane.get("restart_args", [])
            for i, arg in enumerate(args):
                if arg == "--live-magic" and i+1 < len(args):
                    active_magics.add(int(args[i+1]))
                    break
    
    if not broker_positions:
        return "[OK] No open positions detected on broker."

    ghosts = [p for p in broker_positions if p.get("magic", 0) > 0 and p.get("magic") not in active_magics]

    if not ghosts:
        return "[OK] All active trades are owned by the current registry."
    
    out = [f"**[!] ALERT: Found {len(ghosts)} GHOST POSITIONS!**", ""]
    out.append("| Ticket | Symbol | Magic | PnL | Comment |")
    out.append("| --- | --- | --- | --- | --- |")
    for g in ghosts:
        out.append(f"| {g.get('ticket')} | {g.get('symbol')} | {g.get('magic')} | ${g.get('profit', 0):.2f} | {g.get('comment')} |")
    
    return "\n".join(out)


def main() -> None:
    dashboard = generate_dashboard()
    
    # Print to console with ASCII-safe fallback for Windows CMD
    try:
        print(dashboard)
    except UnicodeEncodeError:
        print(dashboard.encode('ascii', 'replace').decode('ascii'))
    
    # Write to file (UTF-8)
    out_path = REPORTS / "organism_dashboard.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dashboard + "\n", encoding="utf-8")
    
    print(f"\n📄 Saved to: {out_path}")


if __name__ == "__main__":
    main()
