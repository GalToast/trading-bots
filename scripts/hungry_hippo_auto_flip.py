#!/usr/bin/env python3
"""Auto-Flip Mechanism for Hungry Hippo v2.

Reads regime_state_live.json (from continuous_regime_monitor.py), detects flips,
and produces config patch files that HH lanes can apply to flip their asymmetry.

This is a SEPARATE process from the regime monitor and the runner. It runs as a
sidecar that:
1. Polls regime_state_live.json every N seconds
2. When a flip is detected, writes a config patch to configs/auto_flip_<symbol>.json
3. Optionally posts alerts to switchboard (if switchboard MCP is available)
4. Logs all flip events to reports/auto_flip_log.jsonl

The HH runner does NOT need to be modified to support this — it simply reads its
config file. The auto-flip mechanism OVERWRITES the config file when a flip occurs.

Usage:
  python hungry_hippo_auto_flip.py --watch --interval 30
  python hungry_hippo_auto_flip.py --once  # check once and exit
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGIME_STATE = ROOT / "reports" / "regime_state_live.json"
WEAKNESS_STATE = ROOT / "reports" / "leading_regime_weakness.json"
FLIP_LOG = ROOT / "reports" / "auto_flip_log.jsonl"
PHASE_LOG = ROOT / "reports" / "phase_transition_log.jsonl"
CONFIG_DIR = ROOT / "configs"

# Track which flips we've already processed (avoid duplicate patches)
PROCESSED_FLIPS = {}  # symbol -> last flip regime
PROCESSED_PHASES = {}  # symbol -> last phase action


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_regime_state() -> dict | None:
    """Load the latest regime state from the monitor."""
    if not REGIME_STATE.exists():
        return None
    try:
        return json.loads(REGIME_STATE.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_weakness_state() -> dict | None:
    """Load the latest leading indicator weakness scores."""
    if not WEAKNESS_STATE.exists():
        return None
    try:
        return json.loads(WEAKNESS_STATE.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_hh_config(symbol: str) -> dict | None:
    """Load the current HH config for a symbol."""
    # Try common config path patterns
    candidates = [
        CONFIG_DIR / f"hungry_hippo_{symbol.lower()}_live.json",
        CONFIG_DIR / f"hungry_hippo_{symbol.lower()}_shadow.json",
        CONFIG_DIR / f"hungry_hippo_{symbol.lower()}_m5_live.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")), path
    return None, None


def compute_flipped_config(current_config: dict, new_asymmetry: str, atr_m15: float) -> dict:
    """Given a current config and the new target asymmetry, compute the flipped config.

    This preserves all non-geometry fields (alpha, max_positions, etc.) and only
    swaps the step geometry.
    """
    config = json.loads(json.dumps(current_config))  # deep copy

    geometry = config.get("geometry") if isinstance(config.get("geometry"), dict) else {}
    existing_step = geometry.get("step") or config.get("step") or 1.0

    if atr_m15 <= 0:
        # Fallback: use config's existing step values and just swap them.
        existing_buy = geometry.get("step_buy") or config.get("step_buy") or existing_step
        existing_sell = geometry.get("step_sell") or config.get("step_sell") or existing_step
        buy_step = existing_sell  # swap
        sell_step = existing_buy
    else:
        # Use ATR-based steps
        if new_asymmetry == "BUY-tight":
            buy_step = round(atr_m15 * 0.8, 5)
            sell_step = round(atr_m15 * 1.3, 5)
        elif new_asymmetry == "SELL-tight":
            buy_step = round(atr_m15 * 1.3, 5)
            sell_step = round(atr_m15 * 0.8, 5)
        else:  # symmetric
            buy_step = round(atr_m15 * 1.0, 5)
            sell_step = round(atr_m15 * 1.0, 5)

    if geometry:
        geometry["step_buy"] = buy_step
        geometry["step_sell"] = sell_step
        geometry["asymmetric"] = buy_step != sell_step
        geometry["asymmetry_ratio"] = round(buy_step / sell_step, 4) if sell_step else None
        config["geometry"] = geometry

    config["step_buy"] = buy_step
    config["step_sell"] = sell_step
    config["asymmetry"] = new_asymmetry
    config["_auto_flipped"] = True
    config["_flipped_at"] = utc_now_iso()

    return config


def write_flip_patch(symbol: str, regime_state: dict, config_path: Path) -> dict:
    """Write a config patch for a symbol that flipped regime."""
    symbol_data = regime_state["symbols"][symbol]
    new_asymmetry = symbol_data["recommended_asymmetry"]
    current_regime = symbol_data["regime"]
    previous_regime = symbol_data.get("previous_regime", "unknown")

    geometry = symbol_data.get("recommended_geometry", {})
    atr_m15 = geometry.get("base_step_atr_m15", 0)

    # Load current config
    current_config, config_path = load_hh_config(symbol)
    if current_config is None:
        return {
            "symbol": symbol,
            "status": "skipped",
            "reason": "no config file found",
            "regime": current_regime,
            "asymmetry": new_asymmetry,
        }

    # Compute flipped config
    flipped = compute_flipped_config(current_config, new_asymmetry, atr_m15)

    # Write the patch
    patch_path = CONFIG_DIR / f"auto_flip_{symbol.lower()}.json"
    patch_path.write_text(json.dumps(flipped, indent=2), encoding="utf-8")

    # Also overwrite the live config if it exists
    if config_path and config_path.exists():
        config_path.write_text(json.dumps(flipped, indent=2), encoding="utf-8")

    # Log the flip
    log_entry = {
        "timestamp": utc_now_iso(),
        "symbol": symbol,
        "flip": f"{previous_regime} → {current_regime}",
        "asymmetry": new_asymmetry,
        "config_path": str(patch_path),
        "atr_m15": atr_m15,
        "step_buy": flipped.get("step_buy"),
        "step_sell": flipped.get("step_sell"),
        "action": "config_written",
    }

    return log_entry


def process_flips(regime_state: dict) -> list[dict]:
    """Check for unprocessed flips and handle them."""
    symbols = regime_state.get("symbols", {})
    results = []

    for symbol, data in symbols.items():
        if not data.get("flip_detected"):
            continue

        current_regime = data["regime"]
        last_processed = PROCESSED_FLIPS.get(symbol)

        # Skip if we already processed this flip
        if last_processed == current_regime:
            continue

        # Process the flip
        _, config_path = load_hh_config(symbol)
        if config_path is None:
            results.append({
                "symbol": symbol,
                "status": "skipped",
                "reason": "no config file found",
                "regime": current_regime,
            })
            continue

        result = write_flip_patch(symbol, regime_state, config_path)
        results.append(result)

        # Mark as processed
        PROCESSED_FLIPS[symbol] = current_regime

    return results


def log_flip_results(results: list[dict]) -> None:
    """Append flip results to the log file."""
    FLIP_LOG.parent.mkdir(parents=True, exist_ok=True)
    for result in results:
        line = json.dumps(result)
        with open(FLIP_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ── Phase Transition Logic (Weakness-Score-Gated) ──────────────────────

PHASE_THRESHOLDS = [
    (96, "FLIP",              "Execute full asymmetry flip"),
    (81, "PHASE_TRANSITION",  "Widen old side, tighten new side"),
    (61, "OFFENSIVE_ESCAPE",  "Close extreme positions at breakeven"),
    (31, "MONITOR",           "Flag for monitoring"),
]


def check_phase_transitions(weakness_state: dict) -> list[dict]:
    """Check weakness scores and trigger anticipatory phase transitions.

    Unlike the binary flip mechanism, this is GRADUAL:
    - Score 31-60:  Monitor (alert only)
    - Score 61-80:  Offensive escape (close extreme positions)
    - Score 81-95:  Phase transition (widen old side, tighten new side)
    - Score 96-100: Full flip (execute asymmetry swap)
    """
    results = []

    for symbol, data in weakness_state.items():
        if "error" in data or data.get("weakness_score") is None:
            continue

        score = data["weakness_score"]
        current_action = data.get("action", "HOLD")
        last_action = PROCESSED_PHASES.get(symbol)

        # Find the highest threshold we've crossed
        triggered_phase = None
        for threshold, phase_name, _ in PHASE_THRESHOLDS:
            if score >= threshold:
                triggered_phase = (phase_name, threshold)
                break  # thresholds are sorted descending

        if triggered_phase is None:
            # Score dropped below 31 — reset to HOLD
            if last_action and last_action != "HOLD":
                PROCESSED_PHASES[symbol] = "HOLD"
            continue

        phase_name, threshold = triggered_phase

        # Skip if already processed this phase for this symbol
        if last_action == phase_name:
            continue

        # Log the phase transition
        phase_result = {
            "timestamp": utc_now_iso(),
            "symbol": symbol,
            "weakness_score": score,
            "phase": phase_name,
            "threshold": threshold,
            "previous_phase": last_action or "HOLD",
            "recommendation": _phase_action(phase_name, symbol, data),
        }
        results.append(phase_result)

        # Log to file
        PHASE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(PHASE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(phase_result) + "\n")

        PROCESSED_PHASES[symbol] = phase_name

    return results


def _phase_action(phase_name: str, symbol: str, data: dict) -> dict:
    """Compute the specific action for a phase transition."""
    action = {"phase": phase_name, "symbol": symbol}

    if phase_name == "FLIP":
        action["steps"] = [
            "Close all positions on old side at best available price",
            "Swap asymmetry: step_buy <-> step_sell",
            "Rearm on new side only",
        ]

    elif phase_name == "PHASE_TRANSITION":
        action["steps"] = [
            "Widen old-side step by 1.5x (reduce opening frequency)",
            "Tighten new-side step by 0.8x (prepare for reversal)",
            "Start rearm tokens on new side",
        ]

    elif phase_name == "OFFENSIVE_ESCAPE":
        action["steps"] = [
            "Close extreme positions (level >= max - 2) at breakeven",
            "Book small profits on extremes unlikely to be revisited",
            "Keep inner lattice positions open",
        ]

    elif phase_name == "MONITOR":
        action["steps"] = [
            "No action needed — log and watch",
            "Check again in 60 seconds",
        ]

    return action


def run_once() -> dict:
    """Run one check for flips and phase transitions."""
    result = {"flips": [], "phases": []}

    state = load_regime_state()
    if state is None:
        print(f"[{utc_now_iso()}] No regime state found at {REGIME_STATE}")
        return []

    flips = state.get("flip_summary", {})
    if not flips:
        # Check regimes anyway for first-run baseline
        symbols = state.get("symbols", {})
        for sym, data in symbols.items():
            PROCESSED_FLIPS[sym] = data["regime"]
        print(f"[{state['updated_at']}] Baseline established — {len(symbols)} symbols tracked")
        return []

    results = process_flips(state)
    log_flip_results(results)

    for r in results:
        if r.get("status") == "skipped":
            print(f"[{r.get('timestamp', '?')}] SKIP {r['symbol']}: {r.get('reason')}")
        else:
            print(f"[{r.get('timestamp', '?')}] FLIP {r['symbol']}: {r['flip']} → {r['asymmetry']} (config written)")

    return results


def run_watch(interval: int = 30) -> None:
    """Continuous watch mode."""
    print(f"Starting auto-flip monitor — polling every {interval}s")
    print(f"Regime state: {REGIME_STATE}")
    print(f"Config dir: {CONFIG_DIR}")
    print()

    poll_count = 0
    while True:
        poll_count += 1
        try:
            results = run_once()
            if results:
                print(f"  Poll #{poll_count}: {len(results)} flip(s) processed")
            else:
                state = load_regime_state()
                if state:
                    regimes = ", ".join(
                        f"{sym}={state['symbols'][sym]['regime']}"
                        for sym in list(state.get("symbols", {}))[:5]
                    )
                    print(f"  Poll #{poll_count} [{state['updated_at']}] — no flips — {regimes}")
        except Exception as e:
            print(f"  Poll #{poll_count} ERROR: {e}")

        time.sleep(interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-flip mechanism for Hungry Hippo v2")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--watch", action="store_true", help="Run continuously (default)")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.once:
        run_once()
    else:
        # Default to watch mode
        run_watch(interval=args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
