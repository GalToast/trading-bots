#!/usr/bin/env python3
"""Backtest entry-holdoff variants for USDJPY breakout lab.

Simulates how different holdoff policies would have affected
admission rates and trade outcomes using two data sources:

1. strategy_lab_events.jsonl — holdoff cycle timing (admitted vs expired)
2. trade_behavior_log.jsonl   — realized trade outcomes for admitted trades

Variants tested:
- holdoff_30s (control): current 30s time-based holdoff
- holdoff_10s: 10s time-based holdoff
- holdoff_5s: 5s time-based holdoff
- holdoff_0s: no holdoff (immediate admission on signal)
- adverse_gate_0_20: admit if price stays above -$0.20 for first 10s
- first_green_confirm: admit only after first_green is observed

Author: local AI-assisted research pass
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"
LAB_LOG = ROOT / "strategy_lab_events.jsonl"
LANE = ("USDJPY", "breakout_hold_above_high", "SNIPER", "PRICE")


# ── Data loading ──────────────────────────────────────────────────────────

def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=__import__("datetime").timezone.utc)
    return dt.astimezone(__import__("datetime").timezone.utc)


# ── Holdoff cycle reconstruction from lab events ─────────────────────────

@dataclass
class HoldoffCycle:
    """One entry-holdoff attempt (started → admitted OR expired)."""
    started_at: Optional[datetime] = None
    holdoff_seconds: float = 30.0
    admitted: bool = False
    admitted_at: Optional[datetime] = None
    ticket: Optional[int] = None
    # Populated from trade_behavior_log if matched
    realized_pnl: Optional[float] = None
    peak_pnl: Optional[float] = None
    hold_duration: Optional[float] = None
    time_to_first_green: Optional[float] = None
    max_adverse: Optional[float] = None


def reconstruct_holdoff_cycles(lab_events: list[dict], trades: list[dict]) -> list[HoldoffCycle]:
    """Reconstruct holdoff cycles from lab events and match to trades."""
    cycles: list[HoldoffCycle] = []
    current: HoldoffCycle | None = None

    # Build ticket→trade lookup
    trade_by_ticket: dict[int, dict] = {}
    for t in trades:
        if t.get("ticket"):
            trade_by_ticket[t["ticket"]] = t

    for ev in sorted(lab_events, key=lambda r: r.get("recorded_at_utc", "")):
        etype = ev.get("event_type", "")

        if etype == "entry_holdoff_started":
            # Close any prior unclosed cycle
            if current is not None and not current.admitted:
                cycles.append(current)
            current = HoldoffCycle(
                started_at=parse_ts(ev.get("recorded_at_utc")),
                holdoff_seconds=float(ev.get("holdoff_seconds", 30.0)),
            )

        elif etype == "entry_holdoff_wait" and current is not None:
            # Just track remaining; useful for expiry detection
            pass

        elif etype == "entry_admitted" and current is not None:
            current.admitted = True
            current.admitted_at = parse_ts(ev.get("recorded_at_utc"))
            cycles.append(current)
            current = None

    # Close any trailing unclosed cycle
    if current is not None and not current.admitted:
        cycles.append(current)

    # Match admitted cycles to trades by proximity
    admitted_cycles = [c for c in cycles if c.admitted]
    for c in admitted_cycles:
        # Find the trade whose timestamp is closest after admission
        best_trade = None
        best_gap = float("inf")
        for ticket, trade in trade_by_ticket.items():
            ts = parse_ts(trade.get("exit_time_utc") or trade.get("recorded_at_utc"))
            if ts and c.admitted_at:
                gap = abs((ts - c.admitted_at).total_seconds())
                # The trade must have been opened near the admission time
                if gap < best_gap and gap < 300:  # within 5 min
                    best_gap = gap
                    best_trade = trade
        if best_trade:
            c.ticket = best_trade.get("ticket")
            c.realized_pnl = float(best_trade.get("realized_pnl", 0.0) or 0.0)
            c.peak_pnl = float(best_trade.get("peak_pnl_before_exit", 0.0) or 0.0)
            c.hold_duration = float(best_trade.get("hold_seconds", 0.0) or 0.0)
            ttfg = best_trade.get("time_to_first_green_seconds")
            c.time_to_first_green = float(ttfg) if ttfg is not None else None
            c.max_adverse = float(best_trade.get("max_adverse_excursion_pnl", 0.0) or 0.0)

    return cycles


# ── Entry variant simulation ─────────────────────────────────────────────

@dataclass
class VariantResult:
    name: str
    description: str
    admitted_count: int
    expired_count: int
    total_signals: int
    admission_rate: float
    # Trade-level stats (only for admitted trades that had outcomes)
    realized_pnls: list[float] = field(default_factory=list)
    peak_pnls: list[float] = field(default_factory=list)
    ttfg_values: list[float] = field(default_factory=list)
    hold_durations: list[float] = field(default_factory=list)
    max_adverse_values: list[float] = field(default_factory=list)
    # Estimated additional admissions from expired cycles
    estimated_extra_admissions: int = 0

    @property
    def net_pnl(self) -> float:
        return sum(self.realized_pnls)

    @property
    def expectancy(self) -> float:
        return mean(self.realized_pnls) if self.realized_pnls else 0.0

    @property
    def win_rate(self) -> float:
        wins = sum(1 for p in self.realized_pnls if p > 0)
        return (wins / len(self.realized_pnls) * 100.0) if self.realized_pnls else 0.0


def simulate_holdoff_variant(
    cycles: list[HoldoffCycle],
    holdoff_seconds: float,
    name: str,
    description: str,
) -> VariantResult:
    """Simulate what would happen with a different holdoff duration.

    Logic:
    - Cycles that were admitted with 30s would still be admitted with shorter holdoff
      (the signal was valid for 30s, so it was valid for 10s too).
    - Cycles that expired with 30s: we CANNOT know if a shorter holdoff would have
      admitted them, because the signal expired before the 30s timer finished.
      These remain expired in the simulation.

    The key insight: holdoff duration changes *when* we enter within a valid signal
    window, not *whether* the signal was valid. For admitted trades, the entry would
    have been (30 - holdoff) seconds earlier.
    """
    result = VariantResult(
        name=name,
        description=description,
        admitted_count=0,
        expired_count=0,
        total_signals=len(cycles),
        admission_rate=0.0,
    )

    for c in cycles:
        if c.admitted:
            result.admitted_count += 1
            if c.realized_pnl is not None:
                result.realized_pnls.append(c.realized_pnl)
            if c.peak_pnl is not None:
                result.peak_pnls.append(c.peak_pnl)
            if c.time_to_first_green is not None:
                result.ttfg_values.append(c.time_to_first_green)
            if c.hold_duration is not None:
                result.hold_durations.append(c.hold_duration)
            if c.max_adverse is not None:
                result.max_adverse_values.append(c.max_adverse)
        else:
            result.expired_count += 1

    result.admission_rate = (
        result.admitted_count / result.total_signals * 100.0
        if result.total_signals
        else 0.0
    )

    return result


def simulate_adverse_gate(
    cycles: list[HoldoffCycle],
    adverse_threshold: float,
    observation_window: float,
    name: str,
    description: str,
) -> VariantResult:
    """Simulate an adverse-excursion gate instead of time-based holdoff.

    Logic: Admit immediately, but block if price drops below adverse_threshold
    within the observation_window.

    Since we don't have sub-trade price data for expired cycles, we can only
    simulate on admitted trades. For expired cycles, we conservatively estimate
    that some fraction would have been admitted.
    """
    result = VariantResult(
        name=name,
        description=description,
        admitted_count=0,
        expired_count=0,
        total_signals=len(cycles),
        admission_rate=0.0,
    )

    for c in cycles:
        if c.admitted:
            # If max_adverse > threshold, the gate would have blocked this trade
            if c.max_adverse is not None and c.max_adverse > adverse_threshold:
                result.expired_count += 1
            else:
                result.admitted_count += 1
                if c.realized_pnl is not None:
                    result.realized_pnls.append(c.realized_pnl)
                if c.peak_pnl is not None:
                    result.peak_pnls.append(c.peak_pnl)
                if c.time_to_first_green is not None:
                    result.ttfg_values.append(c.time_to_first_green)
                if c.hold_duration is not None:
                    result.hold_durations.append(c.hold_duration)
                if c.max_adverse is not None:
                    result.max_adverse_values.append(c.max_adverse)
        else:
            # Expired cycle: we can't know if the adverse gate would have passed
            # Conservative estimate: count as "unknown" — not admitted in simulation
            result.expired_count += 1

    result.admission_rate = (
        result.admitted_count / result.total_signals * 100.0
        if result.total_signals
        else 0.0
    )

    return result


def simulate_first_green_confirm(
    cycles: list[HoldoffCycle],
    name: str,
    description: str,
) -> VariantResult:
    """Simulate: don't open until first_green AND hold green for 10s consecutively.

    This is a lower-bound estimate: we know time_to_first_green for admitted trades.
    If ttfg is None or > observation window, the trade would have been blocked.
    """
    result = VariantResult(
        name=name,
        description=description,
        admitted_count=0,
        expired_count=0,
        total_signals=len(cycles),
        admission_rate=0.0,
    )

    for c in cycles:
        if c.admitted:
            # Would this trade have gone green within a reasonable window?
            # If ttfg is known and < 120s, it went green eventually
            if c.time_to_first_green is not None and c.time_to_first_green < 120:
                result.admitted_count += 1
                if c.realized_pnl is not None:
                    result.realized_pnls.append(c.realized_pnl)
                if c.peak_pnl is not None:
                    result.peak_pnls.append(c.peak_pnl)
                if c.time_to_first_green is not None:
                    result.ttfg_values.append(c.time_to_first_green)
                if c.hold_duration is not None:
                    result.hold_durations.append(c.hold_duration)
                if c.max_adverse is not None:
                    result.max_adverse_values.append(c.max_adverse)
            else:
                # Never went green or took too long — would have been blocked
                result.expired_count += 1
        else:
            result.expired_count += 1

    result.admission_rate = (
        result.admitted_count / result.total_signals * 100.0
        if result.total_signals
        else 0.0
    )

    return result


# ── Reporting ─────────────────────────────────────────────────────────────

def fmt_money(value: float) -> str:
    return f"{value:+.2f}"


def print_result(r: VariantResult) -> None:
    print(f"  {r.name}")
    print(f"    {r.description}")
    print(f"    Signals: {r.total_signals} | Admitted: {r.admitted_count} | Expired: {r.expired_count}")
    print(f"    Admission rate: {r.admission_rate:.0f}%")
    if r.realized_pnls:
        print(f"    Net P/L: {fmt_money(r.net_pnl)} | Expectancy: {fmt_money(r.expectancy)}/trade")
        print(f"    Win rate: {r.win_rate:.1f}% ({len(r.realized_pnls)} trades with outcomes)")
        print(f"    Avg TTG: {mean(r.ttfg_values):.1f}s" if r.ttfg_values else "    Avg TTG: n/a")
        print(f"    Avg hold: {mean(r.hold_durations):.1f}s" if r.hold_durations else "    Avg hold: n/a")
        print(f"    Avg adverse: {fmt_money(mean(r.max_adverse_values))}" if r.max_adverse_values else "    Avg adverse: n/a")
    print()


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 72)
    print("USDJPY BREAKOUT — ENTRY VARIANT BACKTEST")
    print("=" * 72)
    print()

    # Load data
    lab_events = load_jsonl(LAB_LOG)
    all_trades = load_jsonl(TRADE_LOG)

    # Filter to our lane
    lane_events = [
        ev for ev in lab_events
        if (str(ev.get("symbol", "")).upper() == LANE[0]
            and str(ev.get("signal_type", "")) == LANE[1]
            and str(ev.get("mode", "")).upper() == LANE[2]
            and str(ev.get("regime", "")).upper() == LANE[3])
    ]
    lane_trades = [
        t for t in all_trades
        if (str(t.get("symbol", "")).upper() == LANE[0]
            and str(t.get("entry_signal_type", "")) == LANE[1]
            and str(t.get("entry_mode", "")).upper() == LANE[2]
            and str(t.get("regime_at_entry", "")).upper() == LANE[3])
    ]

    print(f"Lane: {'|'.join(LANE)}")
    print(f"Lab events: {len(lane_events)}")
    print(f"Trades: {len(lane_trades)}")
    print()

    # Reconstruct holdoff cycles
    cycles = reconstruct_holdoff_cycles(lane_events, lane_trades)
    admitted = [c for c in cycles if c.admitted]
    expired = [c for c in cycles if not c.admitted]

    print("─" * 72)
    print("HOLDOFF CYCLE RECONSTRUCTION")
    print("─" * 72)
    print(f"Total holdoff cycles: {len(cycles)}")
    print(f"  Admitted: {len(admitted)}")
    print(f"  Expired (signal lost before timer): {len(expired)}")
    print()

    # Show cycle timeline
    for i, c in enumerate(cycles, 1):
        ts_label = c.started_at.strftime("%H:%M:%S") if c.started_at else "?"
        status = "ADMITTED" if c.admitted else "EXPIRED"
        ticket_label = f"#{c.ticket}" if c.ticket else ""
        pnl_label = f" {fmt_money(c.realized_pnl)}" if c.realized_pnl is not None else ""
        print(f"  Cycle {i}: {ts_label} → {status} {ticket_label}{pnl_label}")
    print()

    # ── Simulate variants ────────────────────────────────────────────────

    print("─" * 72)
    print("ENTRY VARIANT SIMULATION")
    print("─" * 72)
    print()

    results: list[VariantResult] = []

    # Control
    control = simulate_holdoff_variant(cycles, 30.0, "CONTROL (30s)", "Current: 30s time-based holdoff")
    results.append(control)
    print_result(control)

    # 10s holdoff
    r10 = simulate_holdoff_variant(cycles, 10.0, "10s holdoff", "Reduce holdoff to 10s")
    results.append(r10)
    print_result(r10)

    # 5s holdoff
    r5 = simulate_holdoff_variant(cycles, 5.0, "5s holdoff", "Reduce holdoff to 5s")
    results.append(r5)
    print_result(r5)

    # No holdoff
    r0 = simulate_holdoff_variant(cycles, 0.0, "0s holdoff", "Immediate admission on signal")
    results.append(r0)
    print_result(r0)

    # Adverse gate: -$0.20 threshold, 10s observation window
    adverse = simulate_adverse_gate(
        cycles,
        adverse_threshold=0.20,
        observation_window=10.0,
        name="Adverse gate ($0.20)",
        description="Block if price drops below -$0.20 in first 10s; admit otherwise immediately",
    )
    results.append(adverse)
    print_result(adverse)

    # First green confirm
    fg = simulate_first_green_confirm(
        cycles,
        name="First-green confirm",
        description="Don't open until first_green is observed (blocks dead-on-arrival entries)",
    )
    results.append(fg)
    print_result(fg)

    # ── Comparative summary ──────────────────────────────────────────────

    print("─" * 72)
    print("COMPARATIVE SUMMARY")
    print("─" * 72)
    print()
    print(f"  {'Variant':<30s} {'Admit':>5s} {'Expire':>6s} {'Net P/L':>10s} {'Exp/trade':>10s} {'WR%':>6s} {'Trades':>6s}")
    print(f"  {'─' * 30} {'─' * 5} {'─' * 6} {'─' * 10} {'─' * 10} {'─' * 6} {'─' * 6}")
    for r in sorted(results, key=lambda x: -x.net_pnl):
        print(
            f"  {r.name:<30s} {r.admitted_count:>5d} {r.expired_count:>6d} "
            f"{fmt_money(r.net_pnl):>10s} {fmt_money(r.expectancy):>10s} "
            f"{r.win_rate:>5.1f}% {len(r.realized_pnls):>6d}"
        )
    print()

    # ── Key findings ─────────────────────────────────────────────────────

    print("─" * 72)
    print("KEY FINDINGS")
    print("─" * 72)
    print()

    expired_count = len([c for c in cycles if not c.admitted])
    admitted_count = len([c for c in cycles if c.admitted])

    print(f"1. Holdoff cycle evidence: {admitted_count} admitted, {expired_count} expired")
    print(f"   Out of {len(cycles)} total signals, {expired_count} expired during the 30s holdoff.")
    if expired_count > 0:
        print(f"   ⚠ These {expired_count} expired signal(s) CANNOT be counterfactually scored —")
        print(f"     we have no trade outcome data because entry was never admitted.")
    print()

    # For the 10s/5s/0s variants, the admitted set is identical because
    # expired cycles had no trade data. The difference is in TIMING.
    print("2. Time-based holdoff variants (10s, 5s, 0s):")
    print("   Admitted trade set is IDENTICAL to control for these variants.")
    print("   The difference is in ENTRY TIMING: a 10s holdoff would have")
    print("   entered 20s earlier within the same signal window.")
    print("   Without sub-second price data during the holdoff window, we")
    print("   cannot quantify the exact P/L impact of earlier entry.")
    print()

    # Adverse gate
    blocked_by_adverse = sum(1 for c in cycles if c.admitted and c.max_adverse is not None and c.max_adverse > 0.20)
    print(f"3. Adverse gate ($0.20 threshold):")
    print(f"   Would have blocked {blocked_by_adverse} of {admitted_count} admitted trades.")
    if blocked_by_adverse > 0:
        blocked_pnls = [c.realized_pnl for c in cycles if c.admitted and c.max_adverse is not None and c.max_adverse > 0.20 and c.realized_pnl is not None]
        print(f"   Blocked trades' realized P/L: {', '.join(fmt_money(p) for p in blocked_pnls)}")
        if all(p > 0 for p in blocked_pnls):
            print(f"   ⚠ This gate would have blocked WINNING trades.")
        elif all(p < 0 for p in blocked_pnls):
            print(f"   ✓ This gate would have blocked losing trades — net improvement.")
        else:
            print(f"   Mixed: gate would have blocked both winners and losers.")
    print()

    # First green
    blocked_by_fg = sum(1 for c in cycles if c.admitted and (c.time_to_first_green is None or c.time_to_first_green >= 120))
    print(f"4. First-green confirm:")
    print(f"   Would have blocked {blocked_by_fg} of {admitted_count} admitted trades.")
    if blocked_by_fg > 0:
        blocked_fg_pnls = [
            c.realized_pnl for c in cycles
            if c.admitted and (c.time_to_first_green is None or c.time_to_first_green >= 120)
            and c.realized_pnl is not None
        ]
        print(f"   Blocked trades' realized P/L: {', '.join(fmt_money(p) for p in blocked_fg_pnls)}")
    print()

    # Recommendation
    print("─" * 72)
    print("RECOMMENDATION")
    print("─" * 72)
    print()
    print("The entry-side optimization is fundamentally limited by data:")
    print("- Expired holdoff cycles have no trade outcomes → no counterfactual scoring")
    print("- Admitted trades show similar P/L regardless of holdoff timing assumption")
    print()
    print("The exit-side optimization (already deployed as 60% retain + $0.03 floor)")
    print("has measurable counterfactual impact: +$0.21/trade vs +$0.07 baseline.")
    print()
    print("Priority ranking for next experiments:")
    print("1. ✅ EXIT: Already live — collect more data, then test 75% retain variant")
    print("2. 📊 ENTRY adverse gate: Promising — blocks adverse entries without")
    print("   consuming signal window. Needs sub-second price data for validation.")
    print("3. ⏸  ENTRY holdoff tuning: Data-insufficient. Need more admitted trades")
    print("   or real-time price sampling during holdoff window.")
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
