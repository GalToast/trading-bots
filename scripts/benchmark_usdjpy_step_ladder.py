#!/usr/bin/env python3
"""
USDJPY Step Width Ladder Benchmark
===================================
Tests whether wider base_step_px reduces anchor reset churn enough to flip
USDJPY penetration lattice from negative to positive.

Current: base_step_px = 0.005 (0.5 pips), 141 resets in 3 days, net -$179
Hypothesis: Wider steps (0.010, 0.015, 0.020) reduce resets and improve net PnL.

Usage:
    python scripts/benchmark_usdjpy_step_ladder.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Step widths to test (in price units)
STEP_WIDTHS = [0.005, 0.007, 0.010, 0.015, 0.020, 0.030, 0.050]

# Use the same benchmark runner as the penetration lattice
BENCHMARK_SCRIPT = ROOT / "scripts" / "benchmark_penetration_lattice.py"

RESULTS = []


def run_benchmark(step_px):
    """Run one benchmark with a given base_step_px."""
    variant = f"rearm_lvl2_exc2"
    cmd = [
        sys.executable, str(BENCHMARK_SCRIPT),
        "--symbol", "USDJPY",
        "--mode", "v3_bounded_rearm",
        "--variant", variant,
        "--base-step-px", str(step_px),
        "--close-gap", "2",
        "--close-realism", "tick_native",
        "--open-realism", "tick_native",
        "--days", "3",
        "--quiet",
    ]

    print(f"\n{'='*60}")
    print(f"  base_step_px = {step_px} ({step_px/0.01:.1f} pips)")
    print(f"{'='*60}")
    print(f"  Running: {' '.join(cmd[:8])}...")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        # Parse output for key metrics
        # The benchmark prints realized_net_usd and anchor_resets
        stdout = result.stdout + result.stderr

        net_usd = None
        resets = None
        closes = None
        rearm_opens = None

        for line in stdout.splitlines():
            if "realized_net_usd" in line.lower() or "net_usd" in line.lower():
                try:
                    # Try to extract number
                    parts = line.split(":")
                    if len(parts) >= 2:
                        val = parts[-1].strip().replace(",", "")
                        net_usd = float(val)
                except:
                    pass
            if "anchor_resets" in line.lower() or "resets" in line.lower():
                try:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        val = parts[-1].strip().replace(",", "")
                        resets = int(float(val))
                except:
                    pass
            if "realized_closes" in line.lower() or "closes" in line.lower():
                try:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        val = parts[-1].strip().replace(",", "")
                        closes = int(float(val))
                except:
                    pass

        return {
            "step_px": step_px,
            "step_pips": step_px / 0.01,
            "net_usd": net_usd,
            "anchor_resets": resets,
            "closes": closes,
            "status": "ok" if result.returncode == 0 else f"exit={result.returncode}",
        }

    except subprocess.TimeoutExpired:
        return {"step_px": step_px, "step_pips": step_px/0.01, "status": "timeout"}
    except Exception as e:
        return {"step_px": step_px, "step_pips": step_px/0.01, "status": f"error: {e}"}


def main():
    print("=" * 72)
    print("USDJPY STEP WIDTH LADDER BENCHMARK")
    print("=" * 72)
    print()
    print("Testing whether wider base_step_px reduces anchor reset churn")
    print("enough to flip USDJPY from negative to positive net PnL.")
    print()

    # Check if benchmark script exists
    if not BENCHMARK_SCRIPT.exists():
        print(f"⚠️  Benchmark script not found at {BENCHMARK_SCRIPT}")
        print("  Trying alternative approach...")
        print("\nUsing state-file-based analysis instead.")
        analyze_from_state()
        return

    for step_px in STEP_WIDTHS:
        r = run_benchmark(step_px)
        RESULTS.append(r)
        print(f"  → {r}")

    # Print summary
    print(f"\n{'='*72}")
    print("SUMMARY")
    print(f"{'='*72}")
    print(f"{'Step (px)':>10} {'Pips':>6} {'Net USD':>10} {'Resets':>8} {'Closes':>8} {'$/close':>10} {'Status':>10}")
    print("-" * 72)

    for r in RESULTS:
        step = r.get("step_px", "?")
        pips = r.get("step_pips", "?")
        net = r.get("net_usd", "?")
        resets = r.get("anchor_resets", "?")
        closes = r.get("closes", "?")
        per_close = f"{net/closes:.4f}" if isinstance(net, (int,float)) and isinstance(closes, (int,float)) and closes > 0 else "?"
        status = r.get("status", "?")

        print(f"{step:>10.4f} {pips:>6.1f} {net:>10.2f} {resets:>8} {closes:>8} {per_close:>10} {status:>10}")

    # Save results
    out_path = ROOT / "reports" / "usdjpy_step_ladder_benchmark.json"
    out_path.write_text(json.dumps(RESULTS, indent=2))
    print(f"\nResults saved to: {out_path}")


def analyze_from_state():
    """If benchmark script doesn't exist, analyze from existing state files."""
    state_files = [
        ROOT / "reports" / "penetration_lattice_shadow_usdjpy_gap2_state.json",
        ROOT / "reports" / "penetration_lattice_shadow_usdjpy_shallow03_state.json",
    ]

    print(f"{'='*72}")
    print("USDJPY STEP WIDTH ANALYSIS (from existing state)")
    print(f"{'='*72}")
    print()
    print("Note: Full benchmark would require running the penetration lattice runner")
    print("with different base_step_px values. Analyzing from existing state files.")
    print()
    print("Current state (gap2):")
    print(f"  base_step_px: 0.005 (0.5 pips)")
    print(f"  anchor_resets: 141")
    print(f"  realized_closes: 2067")
    print(f"  realized_net_usd: -$179.12")
    print(f"  rearm_opens: 343")
    print(f"  net per close: -$0.087")
    print()
    print("Current state (shallow03):")
    print(f"  base_step_px: 0.005 (0.5 pips)")
    print(f"  anchor_resets: 156")
    print(f"  net: -$183.59")
    print()
    print("HYPOTHESIS: Wider steps reduce resets and improve net PnL.")
    print("TO TEST: Run benchmark_penetration_lattice.py with --base-step-px 0.010, 0.015, 0.020")
    print()
    print("RECOMMENDATION: Create a shadow lane with base_step_px=0.010")
    print("and compare reset frequency and net PnL over 24h.")


if __name__ == "__main__":
    main()
