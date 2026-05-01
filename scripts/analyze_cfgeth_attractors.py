#!/usr/bin/env python3
"""
CFG/ETH Ratio Sleeve Attractor Analysis
=========================================

Analyzes whether the CFG/ETH ratio sleeve's zero-entry problem is a calibration
issue or just normal patience needed.

Checks:
1. How far above attractors is the current ratio?
2. What's the recent ratio volatility? (how often does it drop below attractors?)
3. Are the attractor levels representative of recent behavior?
4. Should we recalibrate with a shorter/more recent training window?
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient
from multi_coin_isolated_runner import fetch_candles

# Current attractor levels from state file
ATTRACTOR_LEVELS = [
    8.21262e-05,   # Level 0, density 890
    7.77646e-05,   # Level 1, density 797
    7.28577e-05,   # Level 2, density 497
    8.78509e-05,   # Level 3, density 452 (HIGHEST)
    6.57700e-05,   # Level 4, density 248
]

CURRENT_RATIO = 9.037e-05  # From latest state file

def main():
    print("=" * 60)
    print("CFG/ETH RATIO ATTRACTOR ANALYSIS")
    print("=" * 60)

    # Calculate how far above attractors the current ratio is
    max_attractor = max(ATTRACTOR_LEVELS)
    min_attractor = min(ATTRACTOR_LEVELS)
    mean_attractor = sum(ATTRACTOR_LEVELS) / len(ATTRACTOR_LEVELS)

    pct_above_max = (CURRENT_RATIO - max_attractor) / max_attractor * 100
    pct_above_mean = (CURRENT_RATIO - mean_attractor) / mean_attractor * 100

    print(f"\nCurrent ratio: {CURRENT_RATIO:.8f}")
    print(f"Highest attractor: {max_attractor:.8f}")
    print(f"Mean attractor: {mean_attractor:.8f}")
    print(f"Lowest attractor: {min_attractor:.8f}")
    print(f"\nCurrent ratio is {pct_above_max:.1f}% ABOVE highest attractor")
    print(f"Current ratio is {pct_above_mean:.1f}% ABOVE mean attractor")

    # Fetch recent ratio history to see volatility
    print(f"\nFetching 24h of CFG-USD and ETH-USD candles...")
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 24 * 60 * 60  # 24 hours ago

    cfg_candles = fetch_candles(client, "CFG-USD", start, now, "FIVE_MINUTE")
    eth_candles = fetch_candles(client, "ETH-USD", start, now, "FIVE_MINUTE")

    if not cfg_candles or not eth_candles:
        print("ERROR: Failed to fetch candles")
        return

    # Compute ratio series
    ts_cfg = {int(c["start"]): float(c["close"]) for c in cfg_candles}
    ts_eth = {int(c["start"]): float(c["close"]) for c in eth_candles}
    common_ts = sorted(set(ts_cfg.keys()) & set(ts_eth.keys()))

    ratios = []
    for ts in common_ts:
        ratio = ts_cfg[ts] / ts_eth[ts]
        ratios.append(ratio)

    if not ratios:
        print("ERROR: No common timestamps")
        return

    # Analyze ratio distribution
    ratio_min = min(ratios)
    ratio_max = max(ratios)
    ratio_mean = sum(ratios) / len(ratios)
    ratio_std = (sum((r - ratio_mean)**2 for r in ratios) / len(ratios)) ** 0.5

    # How often does ratio drop below attractors?
    below_max_count = sum(1 for r in ratios if r < max_attractor)
    below_mean_count = sum(1 for r in ratios if r < mean_attractor)
    below_any_count = sum(1 for r in ratios if r < max(ATTRACTOR_LEVELS))

    print(f"\n24h Ratio Statistics ({len(ratios)} bars):")
    print(f"  Min: {ratio_min:.8f}")
    print(f"  Max: {ratio_max:.8f}")
    print(f"  Mean: {ratio_mean:.8f}")
    print(f"  Std: {ratio_std:.8f}")

    print(f"\nRatio vs Attractors (24h):")
    print(f"  Below highest attractor: {below_max_count}/{len(ratios)} ({below_max_count/len(ratios)*100:.0f}%)")
    print(f"  Below mean attractor: {below_mean_count}/{len(ratios)} ({below_mean_count/len(ratios)*100:.0f}%)")

    # Find recent dips below attractors
    dips = []
    for i, r in enumerate(ratios):
        for level in sorted(ATTRACTOR_LEVELS, reverse=True):
            if r < level:
                dips.append((i, r, level))
                break

    print(f"\nRecent dips below attractors (last 20):")
    for idx, ratio, level in dips[-20:]:
        depth_pct = (level - ratio) / level * 100
        print(f"  Bar {idx}: ratio={ratio:.8f}, below level {level:.8f} by {depth_pct:.1f}%")

    # Recommendation
    print(f"\n{'=' * 60}")
    print("RECOMMENDATION:")
    if below_max_count > len(ratios) * 0.1:
        print(f"  Ratio drops below highest attractor {below_max_count/len(ratios)*100:.0f}% of the time.")
        print(f"  Attractor levels are REASONABLE — just waiting for a dip.")
        print(f"  VERDICT: Patience needed, not a calibration issue.")
    else:
        print(f"  Ratio RARELY drops below attractors ({below_max_count/len(ratios)*100:.0f}% of 24h).")
        print(f"  Attractor levels may be MIS-CALIBRATED for current regime.")
        print(f"  VERDICT: Consider recalibrating with more recent data.")

    print(f"\n  To trigger entry, ratio needs to drop from {CURRENT_RATIO:.8f}")
    print(f"  to below {max_attractor:.8f} (a {pct_above_max:.1f}% drop).")
    print(f"  Recent 24h range: {ratio_min:.8f} to {ratio_max:.8f}")
    print(f"  A {pct_above_max:.1f}% drop {'HAS' if ratio_min < max_attractor else 'HAS NOT'} occurred in the last 24h.")


if __name__ == "__main__":
    main()
