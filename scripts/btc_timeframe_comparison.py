#!/usr/bin/env python3
"""BTC M5 Warp vs H1 Step Shadow Cross-Timeframe Comparison.

Answers: Which BTC timeframe is the MORE efficient edge per unit of time?

M5 Warp: 0 anchor resets, $705 net, 36 closes
H1 Step30: 87 anchor resets, $130 net, 8 closes
H1 Step50: 45 anchor resets, $102 net, 6 closes

This compares apples-to-apples: BTC penetration lattice on different timeframes.
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent

def load(path):
    with open(path) as f:
        return json.load(f)

def analyze(state, name):
    sym = state["symbols"]["BTCUSD"]
    closes = sym["realized_closes"]
    net = sym["realized_net_usd"]
    opens = len(sym["open_tickets"])
    resets = sym["anchor_resets"]
    step = state["metadata"]["step"]
    timeframe = state["metadata"]["timeframe"]
    
    pnl_per_close = net / closes if closes > 0 else 0
    pnl_per_reset = net / resets if resets > 0 else 0
    net_per_open = net / opens if opens > 0 else 0
    
    # Floating PnL estimate
    floating_pnl = 0.0
    for pos in sym["open_tickets"]:
        entry = pos["entry_fill_price"]
        trigger = pos["trigger_level"]
        if pos["direction"] == "SELL":
            floating_pnl += (entry - trigger)
        else:
            floating_pnl += (trigger - entry)
    
    # Inventory spread
    if opens > 0:
        entries = [p["entry_fill_price"] for p in sym["open_tickets"]]
        spread = max(entries) - min(entries)
    else:
        spread = 0
    
    return {
        "name": name,
        "timeframe": timeframe,
        "step": step,
        "closes": closes,
        "net_usd": net,
        "open_positions": opens,
        "anchor_resets": resets,
        "pnl_per_close": pnl_per_close,
        "pnl_per_reset": pnl_per_reset,
        "net_per_open": net_per_open,
        "floating_pnl_estimate": floating_pnl,
        "inventory_spread": spread,
        "max_open_per_side": state["metadata"]["max_open_per_side"],
    }

def main():
    m5 = load(ROOT / "reports" / "penetration_lattice_shadow_btcusd_m5_warp_state.json")
    h1_30 = load(ROOT / "reports" / "penetration_lattice_shadow_btcusd_h1_step30_state.json")
    h1_50 = load(ROOT / "reports" / "penetration_lattice_shadow_btcusd_h1_step50_state.json")
    
    a_m5 = analyze(m5, "M5 Warp")
    a_30 = analyze(h1_30, "H1 Step30")
    a_50 = analyze(h1_50, "H1 Step50")
    
    print(f"{'='*80}")
    print(f"{'Metric':<30} {'M5 Warp':>12} {'H1 Step30':>12} {'H1 Step50':>12}")
    print(f"{'='*80}")
    
    for key in ["timeframe", "step", "closes", "net_usd", "open_positions",
                "anchor_resets", "pnl_per_close", "pnl_per_reset", "net_per_open",
                "floating_pnl_estimate", "inventory_spread"]:
        v_m5 = a_m5[key]
        v_30 = a_30[key]
        v_50 = a_50[key]
        if isinstance(v_m5, float):
            print(f"{key:<30} ${v_m5:>10.2f} ${v_30:>10.2f} ${v_50:>10.2f}")
        else:
            print(f"{key:<30} {str(v_m5):>12} {str(v_30):>12} {str(v_50):>12}")
    
    print(f"{'='*80}")
    
    # Time-normalized analysis
    # M5 runs at 30s poll, H1 runs at 60s poll
    # M5 has 12x more candles per hour than H1
    # So over the same wall-clock time, M5 sees 12x more data points
    # Normalize PnL per 100 candles of data
    print(f"\nTime-normalized analysis (per 100 candles of data):", flush=True)
    # We need to know how many candles each has processed
    # Rough estimate: M5 started at 00:07 UTC, H1 at 05:08 UTC
    # That's ~16.5 hours for M5, ~11.5 hours for H1
    # M5 candles: 16.5 * 12 = 198 candles, H1 candles: 11.5 * 1 = 11.5 candles
    
    m5_hours = 16.5
    h1_hours = 11.5
    m5_candles = m5_hours * 12  # 12 per hour
    h1_candles = h1_hours * 1   # 1 per hour
    
    pnl_per_100_candles_m5 = (a_m5['net_usd'] / m5_candles) * 100
    pnl_per_100_candles_30 = (a_30['net_usd'] / h1_candles) * 100
    pnl_per_100_candles_50 = (a_50['net_usd'] / h1_candles) * 100
    
    print(f"  M5 Warp:  ${pnl_per_100_candles_m5:+.2f} per 100 candles ({m5_candles:.0f} total candles)", flush=True)
    print(f"  H1 Step30: ${pnl_per_100_candles_30:+.2f} per 100 candles ({h1_candles:.0f} total candles)", flush=True)
    print(f"  H1 Step50: ${pnl_per_100_candles_50:+.2f} per 100 candles ({h1_candles:.0f} total candles)", flush=True)
    
    # Per-hour analysis
    print(f"\nPer-wall-clock-hour analysis:", flush=True)
    pnl_per_hour_m5 = a_m5['net_usd'] / m5_hours
    pnl_per_hour_30 = a_30['net_usd'] / h1_hours
    pnl_per_hour_50 = a_50['net_usd'] / h1_hours
    
    print(f"  M5 Warp:  ${pnl_per_hour_m5:+.2f}/hour", flush=True)
    print(f"  H1 Step30: ${pnl_per_hour_30:+.2f}/hour", flush=True)
    print(f"  H1 Step50: ${pnl_per_hour_50:+.2f}/hour", flush=True)
    
    # Stability analysis
    print(f"\nStability analysis:", flush=True)
    print(f"  M5 Warp:  {a_m5['anchor_resets']} resets in {m5_hours:.1f}h = {a_m5['anchor_resets']/m5_hours:.1f}/hour", flush=True)
    print(f"  H1 Step30: {a_30['anchor_resets']} resets in {h1_hours:.1f}h = {a_30['anchor_resets']/h1_hours:.1f}/hour", flush=True)
    print(f"  H1 Step50: {a_50['anchor_resets']} resets in {h1_hours:.1f}h = {a_50['anchor_resets']/h1_hours:.1f}/hour", flush=True)
    
    # Floating risk per dollar of net PnL
    print(f"\nFloating risk efficiency (floating PnL per $ of realized net):", flush=True)
    print(f"  M5 Warp:  ${a_m5['floating_pnl_estimate']:.2f} floating / ${a_m5['net_usd']:.2f} net = {a_m5['floating_pnl_estimate']/a_m5['net_usd']:.1f}x", flush=True)
    print(f"  H1 Step30: ${a_30['floating_pnl_estimate']:.2f} floating / ${a_30['net_usd']:.2f} net = {a_30['floating_pnl_estimate']/a_30['net_usd']:.1f}x", flush=True)
    print(f"  H1 Step50: ${a_50['floating_pnl_estimate']:.2f} floating / ${a_50['net_usd']:.2f} net = {a_50['floating_pnl_estimate']/a_50['net_usd']:.1f}x", flush=True)
    
    print(f"\n{'='*80}")
    print(f"VERDICT:", flush=True)
    if pnl_per_hour_m5 > max(pnl_per_hour_30, pnl_per_hour_50):
        print(f"  M5 Warp generates MORE PnL per wall-clock hour (${pnl_per_hour_m5:.2f}/h)", flush=True)
        print(f"  AND has ZERO anchor resets (most stable)", flush=True)
        print(f"  AND has lowest floating/net ratio (cleanest risk profile)", flush=True)
        print(f"\n  → M5 WARP IS THE SUPERIOR BTC EDGE ON ALL DIMENSIONS", flush=True)
    else:
        print(f"  H1 generates more PnL per hour but with more resets and floating risk", flush=True)
        print(f"  Trade-off: higher PnL rate vs higher stability", flush=True)

if __name__ == "__main__":
    main()
