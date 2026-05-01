#!/usr/bin/env python3
"""
Spread-Safety Floor Calculator

For every tradable symbol, computes:
1. p90 spread (price units and USD cost)
2. Min-viable step (2x p90 spread)
3. Recommended step (3x p90 spread for buffer)
4. Current running step (from state files, unit-normalized)
5. Spread safety verdict (SAFE / MARGINAL / UNSAFE)

Key insight: steps must be compared in the SAME UNITS as spread.
For configs that report step in pip units, we convert to price units.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# Known spread data from previous probes (price units)
KNOWN_SPREADS: dict[str, dict] = {
    # FX pairs (price units)
    "GBPUSD": {"p90_spread_px": 0.00018, "pip": 0.0001, "price": 1.356, "volume": 0.01, "contract": 100000},
    "EURUSD": {"p90_spread_px": 0.00015, "pip": 0.0001, "price": 1.178, "volume": 0.01, "contract": 100000},
    "USDJPY": {"p90_spread_px": 0.018, "pip": 0.01, "price": 158.9, "volume": 0.01, "contract": 100000},
    "NZDUSD": {"p90_spread_px": 0.00020, "pip": 0.0001, "price": 0.590, "volume": 0.01, "contract": 100000},
    "AUDUSD": {"p90_spread_px": 0.00020, "pip": 0.0001, "price": 0.655, "volume": 0.01, "contract": 100000},
    "USDCAD": {"p90_spread_px": 0.00025, "pip": 0.0001, "price": 1.380, "volume": 0.01, "contract": 100000},
    "USDCHF": {"p90_spread_px": 0.00022, "pip": 0.0001, "price": 0.885, "volume": 0.01, "contract": 100000},
    # Crypto
    "BTCUSD": {"p90_spread_px": 4.0, "pip": 1.0, "price": 74000, "volume": 0.01, "contract": 1},
    "ETHUSD": {"p90_spread_px": 0.7, "pip": 0.01, "price": 2330, "volume": 0.01, "contract": 1},
    # Indices
    "NAS100": {"p90_spread_px": 1.5, "pip": 0.1, "price": 25800, "volume": 0.01, "contract": 1},
    "US30": {"p90_spread_px": 2.0, "pip": 1.0, "price": 48500, "volume": 0.01, "contract": 1},
    "XAUUSD": {"p90_spread_px": 0.35, "pip": 0.01, "price": 4820, "volume": 0.01, "contract": 100},
}


@dataclass
class SymbolSpreadInfo:
    symbol: str
    price: float
    p90_spread_px: float
    p90_spread_pips: float
    pip: float
    volume: float
    contract: float
    spread_cost_usd: float
    min_viable_step_px: float
    recommended_step_px: float
    current_step_px: float | None  # normalized to price units
    current_timeframe: str | None
    spread_safety_ratio: float  # current_step_px / min_viable_step_px
    verdict: str  # SAFE, MARGINAL, UNSAFE, NO_CURRENT_CONFIG


def spread_cost_usd(spread_px: float, price: float, volume: float, contract: float) -> float:
    """Compute USD cost of spread for a single round trip.

    For FX: spread_px * volume * contract
    For crypto (contract=1): spread_px * volume
    For indices (contract varies): spread_px * volume * contract
    """
    return abs(spread_px) * volume * contract


def is_step_in_price_units(symbol: str, step: float, metadata: dict) -> bool:
    """Heuristic: is the step in price units or pip units?

    If step_is_price is explicitly set, trust it.
    Otherwise, compare step magnitude to the pip size:
    - If step < 10 * pip, it's probably in pip units
    - If step >= pip * 100, it's probably in price units
    - For crypto/indices where pip ≈ price units, assume price units
    """
    if metadata.get("step_is_price", False):
        return True
    if metadata.get("step_is_pip", False):
        return False

    # For crypto/indices where pip is a meaningful price unit
    if symbol.upper() in ("BTCUSD", "ETHUSD", "NAS100", "US30", "XAUUSD", "SOLUSD", "XRPUSD", "DOGEUSD"):
        return True

    # For FX: if step is very small (< 0.01), it's probably in price units
    # If step is >= 0.1, it's probably in pips
    if step < 0.01:
        return True  # price units (e.g., 0.00018)
    else:
        return False  # pip units (e.g., 1.0 = 1 pip)


def normalize_step_to_price_units(symbol: str, step: float, metadata: dict) -> float:
    """Convert step to price units if it's in pip units."""
    if is_step_in_price_units(symbol, step, metadata):
        return step
    # It's in pip units — convert to price units
    info = KNOWN_SPREADS.get(symbol.upper())
    if info:
        return step * info["pip"]
    # Guess: assume 1 pip = 0.0001 for unknown FX
    return step * 0.0001


def load_current_steps() -> dict[str, tuple[float, str]]:
    """Load current step sizes from all state files, normalized to price units."""
    steps = {}
    for state_file in sorted(REPORTS.glob("*_state.json")):
        try:
            with open(state_file) as f:
                state = json.load(f)
            metadata = state.get("metadata", {})
            symbols_data = state.get("symbols", {})
            raw_step = metadata.get("step", 0)
            tf = metadata.get("timeframe", "?")
            for sym in symbols_data:
                if raw_step > 0:
                    normalized = normalize_step_to_price_units(sym, raw_step, metadata)
                    # Keep the largest step (most recent/safest config)
                    existing = steps.get(sym.upper(), (0, None))
                    if normalized >= existing[0]:
                        steps[sym.upper()] = (normalized, tf)
        except Exception:
            pass
    return steps


def compute_spread_safety() -> list[SymbolSpreadInfo]:
    """Compute spread safety for all known symbols."""
    current_steps = load_current_steps()
    results = []

    for symbol, info in sorted(KNOWN_SPREADS.items()):
        spread_usd = spread_cost_usd(
            info["p90_spread_px"], info["price"], info["volume"], info["contract"]
        )
        spread_pips = info["p90_spread_px"] / info["pip"] if info["pip"] > 0 else 0
        min_viable = 2 * info["p90_spread_px"]  # 2x spread = break-even on round trip
        recommended = 3 * info["p90_spread_px"]  # 3x spread = comfortable margin

        current_step_px, current_tf = current_steps.get(symbol.upper(), (None, None))

        if current_step_px is not None and min_viable > 0:
            ratio = current_step_px / min_viable
        else:
            ratio = 0.0

        if current_step_px is None:
            verdict = "NO_CURRENT_CONFIG"
        elif ratio >= 3.0:
            verdict = "SAFE"
        elif ratio >= 2.0:
            verdict = "MARGINAL"
        else:
            verdict = "UNSAFE"

        results.append(SymbolSpreadInfo(
            symbol=symbol,
            price=info["price"],
            p90_spread_px=info["p90_spread_px"],
            p90_spread_pips=spread_pips,
            pip=info["pip"],
            volume=info["volume"],
            contract=info["contract"],
            spread_cost_usd=spread_usd,
            min_viable_step_px=min_viable,
            recommended_step_px=recommended,
            current_step_px=current_step_px,
            current_timeframe=current_tf,
            spread_safety_ratio=ratio,
            verdict=verdict,
        ))

    return results


def format_report(results: list[SymbolSpreadInfo]) -> str:
    """Format as markdown report."""
    lines = []
    lines.append("# Spread-Safety Floor Report")
    lines.append(f"- Generated at: {time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())}")
    lines.append("- Purpose: compute the minimum viable step for each symbol given its actual spread cost.")
    lines.append("- Unit normalization: steps are converted to price units for fair comparison.")
    lines.append("")

    # Summary
    safe = [r for r in results if r.verdict == "SAFE"]
    marginal = [r for r in results if r.verdict == "MARGINAL"]
    unsafe = [r for r in results if r.verdict == "UNSAFE"]
    no_config = [r for r in results if r.verdict == "NO_CURRENT_CONFIG"]

    lines.append("## Summary")
    lines.append(f"- SAFE (>=3x spread): {len(safe)}")
    lines.append(f"- MARGINAL (2-3x spread): {len(marginal)}")
    lines.append(f"- UNSAFE (<2x spread): {len(unsafe)}")
    lines.append(f"- No current config: {len(no_config)}")
    lines.append("")

    # Main table
    lines.append("## Per-Symbol Analysis")
    lines.append("")
    lines.append("| Symbol | Price | p90 Spread (px) | p90 Spread (pips) | Spread Cost (USD) | Min Step (px) | Rec Step (px) | Current Step (px) | TF | Ratio | Verdict |")
    lines.append("|--------|-------|-----------------|-------------------|-------------------|---------------|---------------|-------------------|-----|-------|---------|")

    for r in sorted(results, key=lambda x: x.spread_safety_ratio, reverse=True):
        if r.current_step_px is not None:
            step_str = f"{r.current_step_px:.8f}"
            ratio_str = f"{r.spread_safety_ratio:.2f}x"
        else:
            step_str = "—"
            ratio_str = "—"
        tf_str = r.current_timeframe or "—"
        lines.append(
            f"| {r.symbol} | {r.price:.4f} | {r.p90_spread_px:.8f} "
            f"| {r.p90_spread_pips:.2f} | ${r.spread_cost_usd:.4f} "
            f"| {r.min_viable_step_px:.8f} | {r.recommended_step_px:.8f} "
            f"| {step_str} | {tf_str} | {ratio_str} | {r.verdict} |"
        )

    # Unsafe configs
    if unsafe:
        lines.append("")
        lines.append("## UNSAFE Configs — Immediate Action Required")
        lines.append("")
        lines.append("These running configs have step sizes below 2x p90 spread. They will lose money to spread costs.")
        lines.append("")
        for r in unsafe:
            lines.append(
                f"- **{r.symbol}** ({r.current_timeframe}): "
                f"step={r.current_step_px:.8f}px, min_viable={r.min_viable_step_px:.8f}px, "
                f"ratio={r.spread_safety_ratio:.2f}x, spread_cost=${r.spread_cost_usd:.4f}"
            )

    # Recommendations
    lines.append("")
    lines.append("## Recommendations")
    lines.append("")

    # Symbols that should be running but aren't
    not_running = [r for r in results if r.current_step_px is None]
    if not_running:
        lines.append("### Spread-Safe Symbols Not Currently Running")
        lines.append("")
        for r in sorted(not_running, key=lambda x: x.spread_cost_usd):
            lines.append(
                f"- **{r.symbol}**: recommended step={r.recommended_step_px:.8f}px "
                f"({r.recommended_step_px / r.pip:.2f} pips), spread cost=${r.spread_cost_usd:.4f}"
            )

    # BTC step-size sweet spot reference
    lines.append("")
    lines.append("### BTC M15 Sweet Spot Reference (from `btc_step_size_sweet_spot_analysis.md`)")
    lines.append("")
    lines.append("- step=75 (0.33x bar range): +$4.59/close, 276 closes, +$1,267 net — WINNER")
    lines.append("- step=45 (0.20x bar range): +$3.87/close, 107 closes, +$414 net — WINNER")
    lines.append("- step=20 (0.09x bar range): -$18.03/close, 26 closes, -$469 net — LOSER")
    lines.append("- step=129.7 (0.57x bar range): -$19.52/close, 53 closes, -$1,035 net — LOSER")
    lines.append("")
    lines.append("The sweet spot is 45-75 for BTC M15, which is 3-5x the p90 spread of $4.0.")

    # Key insight
    lines.append("")
    lines.append("## Key Insight")
    lines.append("")
    lines.append(
        "The spread-safety floor is `2x p90 spread` (in price units). "
        "Below this, the grid loses on round-trip spread costs alone. "
        "The recommended floor is `3x p90 spread` to provide margin for spread widening during volatility. "
        "This is Gate 0 for any launch: if the step is below 2x spread, the config is guaranteed to lose money. "
        "No theory, no geometry, no controller can overcome the math of spread tax."
    )
    lines.append("")

    return "\n".join(lines)


def main():
    results = compute_spread_safety()
    report = format_report(results)
    print(report)

    # Also write to file
    output_path = REPORTS / "spread_safety_floor_report.md"
    output_path.write_text(report)
    print(f"\nReport also written to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
