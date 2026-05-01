#!/usr/bin/env python3
"""
ATR Formula Synthesis — Which Multiplier Actually Wins?
========================================================
Compares the 3 competing ATR multiplier hypotheses against live performance data:
- Hypothesis A: step = ATR x 0.5 (original optimizer recommendation)
- Hypothesis B: step = ATR x 0.9-1.0 (qwen-main's M5 sweet spot)
- Hypothesis C: step = ATR x 1.55 (qwen-2's universal formula)

Uses actual live M5 Warp performance to determine which multiplier maximizes $/hour
while keeping resets under control.

Usage:
    python scripts/atr_formula_synthesis.py
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parent.parent

# Live performance data from the tracker's latest snapshot
# (These are the ACTUAL results from running lanes)
LANES = {
    "BTC M5": {
        "step": 100.0,
        "atr_m5": 64.50,
        "price": 74000,
        "closes": 41,
        "realized_usd": 878.44,
        "opens": 18,
        "resets": 0,
        "start_epoch": None,  # will compute from state file
        "state_path": REPO / "reports" / "penetration_lattice_live_btcusd_m5_warp_state.json",
    },
    "ETH M5": {
        "step": 3.0,
        "atr_m5": 3.24,
        "price": 2244,
        "closes": 0,
        "realized_usd": 0,
        "opens": 12,
        "resets": 0,
        "start_epoch": None,
        "state_path": REPO / "reports" / "penetration_lattice_shadow_ethusd_m5_warp_state.json",
    },
    "SOL M5": {
        "step": 0.12,
        "atr_m5": 0.124,
        "price": 86,
        "closes": 1,
        "realized_usd": 1.70,
        "opens": 2,
        "resets": 0,
        "start_epoch": None,
        "state_path": REPO / "reports" / "penetration_lattice_shadow_solusd_m5_warp_state.json",
    },
    "XRP M5": {
        "step": 0.0016,
        "atr_m5": 0.0016,
        "price": 1.37,
        "closes": 0,
        "realized_usd": 0,
        "opens": 3,
        "resets": 0,
        "start_epoch": None,
        "state_path": REPO / "reports" / "penetration_lattice_shadow_xrpusd_m5_warp_state.json",
    },
    "LTC M5": {
        "step": 0.10,
        "atr_m5": 0.059,
        "price": 55,
        "closes": 0,
        "realized_usd": 0,
        "opens": 0,
        "resets": 0,
        "start_epoch": None,
        "state_path": REPO / "reports" / "penetration_lattice_shadow_ltcusd_m5_warp_state.json",
    },
    "ADA M5": {
        "step": 0.0008,
        "atr_m5": 0.00035,
        "price": 0.25,
        "closes": 0,
        "realized_usd": 0,
        "opens": 1,
        "resets": 0,
        "start_epoch": None,
        "state_path": REPO / "reports" / "penetration_lattice_shadow_adausd_m5_warp_state.json",
    },
}


def get_start_epoch(lane):
    """Get lane start time from state file."""
    path = lane["state_path"]
    if not path.exists():
        return path.stat().st_mtime
    try:
        state = json.loads(path.read_text())
        sym = None
        for key in state.get("symbols", {}):
            sym = state["symbols"][key]
            break
        if sym:
            st = sym.get("lattice_started_time", sym.get("start_time", 0))
            if st and st > 0:
                if isinstance(st, str):
                    try:
                        return datetime.fromisoformat(st.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        pass
                return st
        # Fallback: use file mtime, but adjust for closes
        closes = lane["closes"]
        if closes > 0:
            return path.stat().st_mtime - closes * 180
        return path.stat().st_mtime
    except Exception:
        return path.stat().st_mtime


def main():
    print("=" * 80)
    print("ATR FORMULA SYNTHESIS — Live Performance vs Competing Hypotheses")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 80)
    print()

    # Compute ATR multiples and rates for each lane
    results = []
    for name, lane in LANES.items():
        lane["start_epoch"] = get_start_epoch(lane)
        atr_mult = lane["step"] / lane["atr_m5"] if lane["atr_m5"] > 0 else 0
        step_pct = lane["step"] / lane["price"] * 100

        now = time.time()
        runtime_h = (now - lane["start_epoch"]) / 3600 if lane["start_epoch"] > 0 else 0
        # Minimum runtime: if closes > 0, assume at least closes * 3 min
        if lane["closes"] > 0:
            min_runtime = lane["closes"] * 180 / 3600
            runtime_h = max(runtime_h, min_runtime)

        dollar_per_close = lane["realized_usd"] / lane["closes"] if lane["closes"] > 0 else 0
        dollar_per_hour = lane["realized_usd"] / runtime_h if runtime_h > 0 else 0
        closes_per_hour = lane["closes"] / runtime_h if runtime_h > 0 else 0

        results.append({
            "name": name,
            "step": lane["step"],
            "atr_m5": lane["atr_m5"],
            "atr_mult": atr_mult,
            "step_pct": step_pct,
            "closes": lane["closes"],
            "realized_usd": lane["realized_usd"],
            "opens": lane["opens"],
            "resets": lane["resets"],
            "runtime_h": runtime_h,
            "dollar_per_close": dollar_per_close,
            "dollar_per_hour": dollar_per_hour,
            "closes_per_hour": closes_per_hour,
        })

    # Print live performance table
    print("LIVE PERFORMANCE DATA:")
    print("-" * 80)
    print(f"{'Lane':<10} {'Step':>8} {'ATR×':>6} {'step%':>7} {'Closes':>7} {'$/close':>8} {'$/hr':>8} {'c/hr':>6} {'Resets':>7}")
    print("-" * 80)
    for r in results:
        print(f"{r['name']:<10} ${r['step']:>7.4f} {r['atr_mult']:>5.2f}x {r['step_pct']:>6.3f}% "
              f"{r['closes']:>7} ${r['dollar_per_close']:>7.2f} ${r['dollar_per_hour']:>7.2f} "
              f"{r['closes_per_hour']:>5.1f} {r['resets']:>7}")
    print()

    # Hypothesis comparison
    print("HYPOTHESIS COMPARISON:")
    print("-" * 80)
    print()
    print("  Hypothesis A (original):  step = ATR x 0.5")
    print("  Hypothesis B (qwen-main): step = ATR x 0.9-1.0 (M5 sweet spot)")
    print("  Hypothesis C (qwen-2):    step = ATR x 1.55 (universal formula)")
    print()

    # Only BTC and SOL have closes to compare
    btc = results[0]
    sol = results[2]

    print("  BTC M5 — Champion data (only lane with significant closes):")
    print(f"    ATR× = {btc['atr_mult']:.2f}x  →  ${btc['dollar_per_close']:.2f}/close, ${btc['dollar_per_hour']:.2f}/hr, {btc['resets']} resets")
    print()

    # Compute what each hypothesis would suggest for BTC
    btc_atr = btc["atr_m5"]
    print(f"    If step = 0.5 x ATR (${btc_atr*0.5:.2f}): would get MORE closes but smaller $/close")
    print(f"    If step = 1.0 x ATR (${btc_atr*1.0:.2f}): balanced frequency/size")
    print(f"    If step = 1.55 x ATR (${btc_atr*1.55:.2f}): actual champion — ${btc['dollar_per_close']:.2f}/close")
    print()

    # Key insight
    print("KEY INSIGHT:")
    print("-" * 80)
    print()
    print("  The ONLY lane with statistically significant data is BTC M5 at 1.55x ATR.")
    print("  SOL M5 (0.97x ATR) has 1 close — too early to judge.")
    print("  All other lanes have 0 closes — no performance data yet.")
    print()
    print("  To settle the debate, we need:")
    print("  1. SOL to hit 10+ closes at 0.97x ATR → compare $/hr to BTC's 1.55x")
    print("  2. XRP to hit 10+ closes at 1.00x ATR → compare $/hr to BTC")
    print("  3. BTC shadow at 0.5x ATR ($32 step) → direct comparison to champion")
    print()

    # Recommendation
    print("RECOMMENDATION:")
    print("-" * 80)
    print()
    print("  1. LAUNCH BTC M5 shadow at $32 (0.5x ATR) — direct A/B test vs champion")
    print("  2. WAIT for SOL/XRP/LTC/ADA to accumulate 10+ closes each")
    print("  3. Compare $/hr across all ATR multiples → find the optimal k")
    print("  4. The formula that maximizes $/hr with resets < 3/hr wins")
    print()

    # Save as JSON for programmatic use
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lanes": results,
        "hypotheses": {
            "A_0.5x_atr": {"formula": "ATR x 0.5", "proponent": "original optimizer"},
            "B_0.9_1.0x_atr": {"formula": "ATR x 0.9-1.0", "proponent": "qwen-main"},
            "C_1.55x_atr": {"formula": "ATR x 1.55", "proponent": "qwen-2"},
        },
        "conclusion": "Need 10+ closes per lane to settle debate. BTC at 1.55x is the only data point.",
    }
    out_path = REPO / "reports" / "atr_formula_synthesis.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
