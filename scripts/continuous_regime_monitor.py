#!/usr/bin/env python3
"""Continuous Regime Monitor — persistent process that polls MTF regime detection,
detects trend flips, and writes a shared state file for all HH lanes to consume.

Output: reports/regime_state_live.json

Fields per symbol:
  - regime: current MTF regime classification
  - flip_detected: bool — true when regime changed since last poll
  - flip_at: ISO timestamp of flip detection
  - previous_regime: what it was before the flip
  - recommended_asymmetry: BUY-tight, SELL-tight, or symmetric
  - mtf_detail: raw MTF detector output for reference
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add parent dir so we can import mtf_regime_detector
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from mtf_regime_detector import detect_regime

OUTPUT_JSON = ROOT / "reports" / "regime_state_live.json"
STATE_CACHE = {}  # symbol -> last regime string

# Default symbols to monitor
DEFAULT_SYMBOLS = ["NAS100", "US30", "EURUSD", "GBPUSD", "ETHUSD", "BTCUSD", "XAUUSD", "NZDUSD", "USDJPY"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def regime_to_asymmetry(regime: str, bounce_confirmed: bool = False, reversal_signal: str = "") -> str:
    """Map MTF regime to recommended HH asymmetry.

    BUY-tight = catch every pullback (uptrend)
    SELL-tight = catch every rally (downtrend)
    symmetric = no directional preference (ranging/transition)
    """
    regime_upper = regime.upper() if regime else ""

    # Bounce/breakout overrides
    if bounce_confirmed:
        if reversal_signal and "UP" in reversal_signal:
            return "BUY-tight"
        elif reversal_signal and "DOWN" in reversal_signal:
            return "SELL-tight"

    if regime_upper in ("STRONG_UPTREND", "UPTREND"):
        return "BUY-tight"
    elif regime_upper in ("STRONG_DOWNTREND", "DOWNTREND"):
        return "SELL-tight"
    elif regime_upper in ("AT_EXTREME_HIGH",):
        # At high expecting reversal down
        return "SELL-tight"
    elif regime_upper in ("AT_EXTREME_LOW",):
        # At low expecting reversal up
        return "BUY-tight"
    elif regime_upper in ("TRANSITION", "RANGING"):
        return "symmetric"
    else:
        return "symmetric"


def detect_flips(symbol_results: dict) -> dict:
    """Compare current regimes to cached previous regimes, detect flips."""
    flips = {}

    for symbol, data in symbol_results.items():
        mtf = data.get("mtf", {})
        current_regime = mtf.get("regime", "UNKNOWN")
        previous_regime = STATE_CACHE.get(symbol, {}).get("regime")

        flip_detected = False
        if previous_regime is not None and current_regime != previous_regime:
            flip_detected = True

        flips[symbol] = {
            "regime": current_regime,
            "flip_detected": flip_detected,
            "flip_at": utc_now_iso() if flip_detected else None,
            "previous_regime": previous_regime,
            "recommended_asymmetry": regime_to_asymmetry(
                current_regime,
                bounce_confirmed=mtf.get("bounce_confirmed", False),
                reversal_signal=mtf.get("reversal_signal", ""),
            ),
            "mtf_detail": {
                "confluence": mtf.get("confluence"),
                "up_count": mtf.get("up_count"),
                "down_count": mtf.get("down_count"),
                "bounce_confirmed": mtf.get("bounce_confirmed"),
                "breakout_confirmed": mtf.get("breakout_confirmed"),
                "reversal_signal": mtf.get("reversal_signal"),
                "m15_trend": mtf.get("m15_trend"),
                "m5_trend": mtf.get("m5_trend"),
            },
            "recommended_geometry": data.get("recommended_geometry", {}),
        }

        # Update cache
        STATE_CACHE[symbol] = {
            "regime": current_regime,
            "updated_at": utc_now_iso(),
        }

    return flips


def run_monitor(symbols: list[str]) -> dict:
    """Run one poll of the regime monitor across all symbols."""
    symbol_results = {}
    for sym in symbols:
        try:
            result = detect_regime(sym)
            symbol_results[sym] = result
        except Exception as e:
            symbol_results[sym] = {"error": str(e), "mtf": {"regime": "ERROR"}}

    flips = detect_flips(symbol_results)

    payload = {
        "updated_at": utc_now_iso(),
        "poll_count": len(symbol_results),
        "symbols": flips,
        "flip_summary": {
            sym: data["flip_detected"]
            for sym, data in flips.items()
            if data.get("flip_detected")
        },
    }

    return payload


def write_output(payload: dict) -> None:
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_once(symbols: list[str]) -> None:
    payload = run_monitor(symbols)
    write_output(payload)

    # Print flip alerts
    flips = payload.get("flip_summary", {})
    if flips:
        for sym in flips:
            data = payload["symbols"][sym]
            print(f"🚨 FLIP: {sym} {data['previous_regime']} → {data['regime']} (asymmetry: {data['recommended_asymmetry']})")
    else:
        print(f"[{payload['updated_at']}] No flips detected — {payload['poll_count']} symbols polled")


def run_watch(symbols: list[str], interval: int = 60) -> None:
    """Continuous watch mode — poll every N seconds."""
    print(f"Starting continuous regime monitor — polling every {interval}s for {len(symbols)} symbols")
    print(f"Output: {OUTPUT_JSON}")
    print(f"Symbols: {', '.join(symbols)}")
    print()

    poll_count = 0
    while True:
        poll_count += 1
        try:
            payload = run_monitor(symbols)
            write_output(payload)

            flips = payload.get("flip_summary", {})
            timestamp = payload["updated_at"]

            if flips:
                for sym in flips:
                    data = payload["symbols"][sym]
                    print(f"[{timestamp}] 🚨 FLIP #{poll_count}: {sym} {data['previous_regime']} → {data['regime']} → asymmetry: {data['recommended_asymmetry']}")
            else:
                regimes_summary = ", ".join(
                    f"{sym}={payload['symbols'][sym]['regime']}"
                    for sym in symbols[:5]
                )
                print(f"[{timestamp}] Poll #{poll_count} — no flips — {regimes_summary}...")

        except Exception as e:
            print(f"[{utc_now_iso()}] ERROR on poll #{poll_count}: {e}")

        time.sleep(interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuous regime monitor for Hungry Hippo fleet")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="Symbols to monitor")
    parser.add_argument("--once", action="store_true", help="Run once and exit (default: watch mode)")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds (watch mode only)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.once:
        run_once(args.symbols)
    else:
        run_watch(args.symbols, interval=args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
