"""Organism Dashboard v2.1 — One command, ALL truth.

Enhanced with:
- Shared price feeder status (heartbeat, cache freshness)
- Kelly runner state (with CFG TP proximity tracking)
- Milestone countdown (GBPUSD 50-close, CFG 48-hold timeout)
- Missed open pattern tracking (trade-firing-guard correlation)

Usage: python scripts/organism_dashboard_v2_1.py
"""

import json
import os
import sys
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORTS = REPO / "reports"
CONFIGS = REPO / "configs"


def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def time_ago(iso_str):
    if not iso_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = (now - dt).total_seconds()
        if diff < 60:
            return f"{int(diff)}s ago"
        elif diff < 3600:
            return f"{int(diff/60)}m ago"
        else:
            return f"{int(diff/3600)}h {int((diff%3600)/60)}m ago"
    except:
        return "parse error"


def file_age_seconds(path):
    try:
        return time.time() - os.path.getmtime(path)
    except:
        return -1


def get_python_count():
    try:
        result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe"],
                                capture_output=True, text=True, timeout=10)
        return len([l for l in result.stdout.split("\n") if "python.exe" in l.lower()])
    except:
        return -1


# ============================================================================
# LIVE LANES
# ============================================================================

LIVE_LANE_LABELS = {
    "live_rearm_941777": "FX Rearm (EURUSD+GBPUSD, a=0.5, gated)",
    "live_momentum_alpha50_941778": "FX Momentum (a=1.0, cd=12, gated)",
    "live_btcusd_exc2_tight_941779": "BTC exc2 Tight (H1, step=$45)",
    "live_btcusd_m15_warp_941781": "BTC M15 Warp (M15, live)",
    "live_ethusd_m15_warp_graduation_941782": "ETH M15 Warp Graduation (live)",
}


def iter_enabled_live_lanes():
    registry = load_json(CONFIGS / "penetration_lattice_runner_registry.json") or {}
    rows = list(registry.get("lanes", [])) if isinstance(registry, dict) else []
    live_rows = []
    for lane in rows:
        if not isinstance(lane, dict):
            continue
        name = lane.get("name", "")
        kind = lane.get("kind", "")
        if not str(name).startswith("live_"):
            continue
        if not str(kind).startswith("live"):
            continue
        if not lane.get("enabled", True):
            continue
        live_rows.append(lane)
    return live_rows


def show_live_lanes():
    lanes = iter_enabled_live_lanes()
    print("\n" + "="*70)
    print(f"  LIVE LANES ({len(lanes)})")
    print("="*70)

    total_net = 0
    for lane_config in lanes:
        lane_name = lane_config.get("name", "")
        desc = LIVE_LANE_LABELS.get(lane_name, lane_name)
        state_path = None
        sp = lane_config.get("state_path")
        if sp:
            state_path = REPO / sp

        state = load_json(state_path) if state_path else None

        hb = None
        net_pnl = 0
        opens = 0
        exc = 0
        gated = False

        if state:
            runner = state.get("runner", {})
            hb = runner.get("heartbeat_at")
            exc = runner.get("consecutive_exceptions", 0)
            gated = runner.get("session_gated", False)

            if "realized_closes" in state:
                net_pnl = state.get("realized_net_usd", 0)
                opens = len(state.get("open_tickets", []))
            else:
                symbols = state.get("symbols", {})
                for sym_data in symbols.values():
                    if isinstance(sym_data, dict):
                        net_pnl += sym_data.get("realized_net_usd", 0)
                        opens += len(sym_data.get("open_tickets", []))

        total_net += net_pnl

        if hb:
            age = file_age_seconds(state_path) if state_path else 999
            if age < 30 and not exc:
                status = "LIVE"
            elif gated:
                status = "GATED"
            else:
                status = f"STALE({age:.0f}s)"
        else:
            status = "NO HB"

        gated_str = " [SESSION-GATED]" if gated else ""
        print(f"\n  {desc}{gated_str}")
        print(f"    Status:    {status}")
        print(f"    Net PnL:   ${net_pnl:+.2f}")
        print(f"    Opens:     {opens}")
        if hb:
            print(f"    Last HB:   {time_ago(hb)}")

    print(f"\n  {'='*40}")
    print(f"  Combined Live Net: ${total_net:+.2f}")


# ============================================================================
# SHADOW PROOFS
# ============================================================================

def show_shadow_proofs():
    print("\n" + "="*70)
    print("  SHADOW PROOF LANES")
    print("="*70)

    proofs = [
        ("shadow_gbpusd_tick_forward_state.json", "GBPUSD Tick-Forward", 50, "closes"),
        ("shadow_fx_m15_micro_gbpusd_bar_state.json", "FX M15 Micro GBPUSD", None, None),
        ("shadow_fx_m15_micro_eurusd_bar_state.json", "FX M15 Micro EURUSD", None, None),
        ("shadow_fx_m15_micro_nzdusd_bar_state.json", "FX M15 Micro NZDUSD", None, None),
        ("penetration_lattice_shadow_fx_close_policy_mixed_state.json", "FX Mixed Close-Policy", None, None),
        ("penetration_lattice_shadow_btcusd_m15_warp_state.json", "BTC M15 Warp (shadow)", None, None),
    ]

    for sf, label, milestone, metric in proofs:
        state = load_json(REPORTS / sf)
        if not state:
            print(f"\n  {label}: MISSING")
            continue

        closes = state.get("realized_closes", 0)
        pnl = state.get("realized_net_usd", 0)
        opens = len(state.get("open_tickets", []))
        bars = state.get("bars_processed", 0)
        hb = state.get("runner", {}).get("heartbeat_at")

        age = file_age_seconds(REPORTS / sf)
        if age < 30:
            status = "LIVE"
        elif closes > 0:
            status = "OK"
        else:
            status = "FRESH"

        print(f"\n  {label} [{status}]")
        print(f"    Closes: {closes}, PnL: ${pnl:+.2f}, Opens: {opens}")
        if bars:
            print(f"    Bars: {bars}")
        if milestone and closes > 0:
            pct = closes / milestone * 100
            print(f"    Milestone: {closes}/{milestone} ({pct:.0f}%) {'DONE' if closes >= milestone else ''}")
        if hb:
            print(f"    Last HB: {time_ago(hb)}")


# ============================================================================
# SHARED PRICE FEEDER
# ============================================================================

def show_feeder():
    print("\n" + "="*70)
    print("  SHARED PRICE FEEDER")
    print("="*70)

    status_payload = load_json(REPORTS / "shared_price_feeder_status.json")
    if not status_payload:
        print("  Status: NOT RUNNING")
        return

    status = status_payload.get("status", "unknown")
    hb_age = status_payload.get("heartbeat_age_seconds")
    price_cache = status_payload.get("price_cache") or {}
    tick_cache = status_payload.get("tick_cache") or {}
    cache_sym = int(price_cache.get("symbols", 0) or 0)
    tick_total = int(tick_cache.get("total_ticks", 0) or 0)
    canary = status_payload.get("canary_group", "N/A")
    fresh_symbols = int(price_cache.get("fresh_symbols", 0) or 0)
    recent_tick_symbols = int(tick_cache.get("symbols_with_recent_ticks", 0) or 0)

    emoji = "OK" if status == "ok" else "ATTENTION"
    print(f"  Status:       {emoji}")
    if isinstance(hb_age, (int, float)):
        print(f"  Heartbeat:    {hb_age:.1f}s ago")
    else:
        print(f"  Heartbeat:    {hb_age}")
    print(f"  Symbols:      {cache_sym}")
    print(f"  Fresh Prices: {fresh_symbols}")
    print(f"  Tick Cache:   {tick_total} ticks")
    print(f"  Recent Ticks: {recent_tick_symbols}")
    print(f"  Canary Group: {canary}")


# ============================================================================
# KELLY RUNNER
# ============================================================================

def show_kelly():
    print("\n" + "="*70)
    print("  KELLY RUNNER")
    print("="*70)

    state = load_json(REPORTS / "kelly_shadow_state.json")
    if not state:
        print("  Status: DEAD (no state file)")
        return

    cycle = state.get("cycle", 0)
    ledgers = state.get("ledgers", {})

    total_equity = sum(v.get("equity", 0) for v in ledgers.values() if isinstance(v, dict))

    print(f"  Cycle:       {cycle}")
    print(f"  Total Equity: ${total_equity:.2f}")
    print()

    for coin in ["CFG-USD", "IOTX-USD", "SUP-USD", "GHST-USD", "NOM-USD", "BAL-USD"]:
        data = ledgers.get(coin)
        if not isinstance(data, dict):
            continue
        equity = data.get("equity", 0)
        pnl = data.get("pnl", 0)
        pos = data.get("position", "flat")
        hold = data.get("position_hold", 0)
        max_hold = data.get("position_max_hold", 48)
        tp = data.get("position_tp", 0)
        entry = data.get("position_entry", 0)
        ret = data.get("return_pct", 0)

        hold_bar = ""
        if pos == "active" and hold > 0:
            pct = hold / max_hold * 100
            remaining = max_hold - hold
            hold_bar = f" hold={hold}/{max_hold} ({pct:.0f}%)"
            if remaining <= 3:
                hold_bar += f" [CLOSE IN {remaining} BARS]"

        tp_info = ""
        if tp and entry:
            tp_info = f" TP=${tp:.4f}"

        print(f"  {coin:<10s}: ${equity:.2f} (+${pnl:+.2f}, {ret:.0f}%){hold_bar}{tp_info}")


# ============================================================================
# MILESTONES
# ============================================================================

def show_milestones():
    print("\n" + "="*70)
    print("  MILESTONE COUNTDOWN")
    print("="*70)

    # GBPUSD tick-forward 50 closes
    tf = load_json(REPORTS / "shadow_gbpusd_tick_forward_state.json")
    if tf:
        closes = tf.get("realized_closes", 0)
        target = 50
        remaining = max(0, target - closes)
        pct = closes / target * 100
        bar = "#" * int(pct / 2) + "." * (50 - int(pct / 2))
        print(f"\n  GBPUSD Tick-Forward: {closes}/{target} ({pct:.0f}%)")
        print(f"  [{bar}]")
        if remaining > 0:
            print(f"  {remaining} closes to go")
        else:
            print(f"  MILESTONE REACHED!")

    # CFG timeout
    kelly = load_json(REPORTS / "kelly_shadow_state.json")
    if kelly:
        cfg = kelly.get("ledgers", {}).get("CFG-USD", {})
        if isinstance(cfg, dict):
            hold = cfg.get("position_hold")
            max_hold = cfg.get("position_max_hold")
            remaining = None
            if hold is not None and max_hold is not None:
                remaining = max_hold - hold
                pct = hold / max_hold * 100
                bar = "#" * int(pct / 2) + "." * (50 - int(pct / 2))
                print(f"\n  CFG-USD Hold Timeout: {hold}/{max_hold} ({pct:.0f}%)")
                print(f"  [{bar}]")
            else:
                print(f"\n  CFG-USD: FLAT (position closed)")
            if remaining is not None and remaining > 0:
                print(f"  {remaining} bars to timeout (approx {remaining*30}s at 30s bars)")
            elif remaining is not None and remaining <= 0:
                print(f"  TIMEOUT IMMINENT!")

    # M5 Warp $200 vs $100
    comparison = load_json(REPORTS / "m5_warp_step100_vs_step200_comparison.json")
    if comparison:
        s100 = comparison.get("step100", {})
        s200 = comparison.get("step200", {})
        c100 = s100.get("closes", 0)
        c200 = s200.get("closes", 0)
        print(f"\n  M5 Warp Step Comparison:")
        print(f"    $100: {c100} closes, ${s100.get('total_pnl',0):+.2f}, ${s100.get('avg_pnl_per_close',0):.2f}/close")
        print(f"    $200: {c200} closes, ${s200.get('total_pnl',0):+.2f}, ${s200.get('avg_pnl_per_close',0):.2f}/close")
        if c200 > 0 and c100 > 0:
            ratio = s200.get("avg_pnl_per_close", 0) / s100.get("avg_pnl_per_close", 1)
            print(f"    $200 is {ratio:.1f}x more efficient per close")


# ============================================================================
# MISSED OPEN PATTERNS
# ============================================================================

def show_missed_patterns():
    print("\n" + "="*70)
    print("  MISSED OPEN PATTERN (last 30 min)")
    print("="*70)

    # Count recent trade-firing-guard alerts
    alert_file = REPORTS / "trade_firing_alerts.jsonl"
    if not alert_file.exists():
        print("  No alert log found")
        return

    alerts = []
    with open(alert_file, "r") as f:
        for line in f.readlines()[-50:]:
            line = line.strip()
            if line:
                try:
                    alerts.append(json.loads(line))
                except:
                    continue

    if not alerts:
        print("  No recent alerts")
        return

    # Count by lane
    lane_counts = {}
    for a in alerts:
        lane = a.get("lane", "unknown")
        status = a.get("status", a.get("type", "unknown"))
        key = f"{lane}:{status}"
        lane_counts[key] = lane_counts.get(key, 0) + 1

    # Count recovered vs detected
    detected = sum(1 for a in alerts if "detect" in str(a.get("status", "")).lower() or "detect" in str(a.get("type", "")).lower())
    recovered = sum(1 for a in alerts if "recover" in str(a.get("status", "")).lower() or "recover" in str(a.get("type", "")).lower())

    print(f"  Detected:  {detected}")
    print(f"  Recovered: {recovered}")
    print(f"  Lost alpha: {max(0, detected - recovered)}")

    # Group by lane
    print(f"\n  By lane:")
    for key, count in sorted(lane_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"    {key}: {count}")


# ============================================================================
# PROCESS HEALTH
# ============================================================================

def show_process_health():
    print("\n" + "="*70)
    print("  PROCESS HEALTH")
    print("="*70)

    count = get_python_count()
    print(f"  Python processes: {count}")
    if count > 50:
        print(f"  Status: BLOATED (>50)")
    elif count > 30:
        print(f"  Status: ELEVATED (>30)")
    else:
        print(f"  Status: HEALTHY")

    # Watchdog status
    for name, path in [("FX", "watchdog/fx_watchdog_loop_state.json"),
                        ("Crypto", "watchdog/crypto_watchdog_loop_state.json"),
                        ("Shadow", "watchdog/shadow_watchdog_loop_state.json")]:
        wd = load_json(REPORTS / path)
        if wd:
            hb = wd.get("heartbeat_at", "N/A")
            exc = wd.get("consecutive_exceptions", 0)
            print(f"  {name:<10s}: {time_ago(hb) if hb != 'N/A' else 'N/A':<15s} [{'OK' if not exc else f'{exc} exc'}]")
        else:
            print(f"  {name:<10s}: no state file")


# ============================================================================
# MAIN
# ============================================================================

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'#'*70}")
    print(f"#  ORGANISM DASHBOARD v2.1")
    print(f"#  {now}")
    print(f"{'#'*70}")

    show_live_lanes()
    show_shadow_proofs()
    show_feeder()
    show_kelly()
    show_milestones()
    show_missed_patterns()
    show_process_health()

    print(f"\n{'#'*70}")
    print(f"#  End of Dashboard")
    print(f"{'#'*70}\n")


if __name__ == "__main__":
    main()
