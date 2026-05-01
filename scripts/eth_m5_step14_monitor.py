#!/usr/bin/env python3
"""
ETH M5 Step14 Control Monitor — Tracks proof progress against graduation gates.

Reads the registered step14 control state file and reports:
1. Proof progress (closes toward 25-close gate)
2. $/close trajectory
3. Reset rate
4. Max floating loss
5. Geometry normalization status

Usage:
    python scripts/eth_m5_step14_monitor.py
    python scripts/eth_m5_step14_monitor.py --output reports/eth_m5_step14_monitor_latest.md
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

STATE_PATH = REPORTS / "penetration_lattice_shadow_ethusd_m5_step14_control_state.json"

TARGET_CLOSES = 25
MAX_RESETS_PER_HOUR = 6


@dataclass
class ETHMonitorResult:
    timestamp: str
    realized_closes: int
    realized_net_usd: float
    avg_per_close: float
    open_positions: int
    anchor: float
    anchor_resets: int
    next_buy_level: float
    next_sell_level: float
    declared_step_buy: float
    declared_step_sell: float
    effective_buy_distance: float
    effective_sell_distance: float
    geometry_normalized: bool
    offensive_closure_enabled: bool
    heartbeat_age_minutes: float
    progress_pct: float
    closes_remaining: int
    verdict: str
    alerts: list[str]


def monitor() -> ETHMonitorResult:
    """Read state and compute monitor result."""
    if not STATE_PATH.exists():
        return ETHMonitorResult(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            realized_closes=0, realized_net_usd=0, avg_per_close=0,
            open_positions=0, anchor=0, anchor_resets=0,
            next_buy_level=0, next_sell_level=0,
            declared_step_buy=14, declared_step_sell=14,
            effective_buy_distance=0, effective_sell_distance=0,
            geometry_normalized=False, offensive_closure_enabled=False,
            heartbeat_age_minutes=999, progress_pct=0, closes_remaining=TARGET_CLOSES,
            verdict="LANE_NOT_RUNNING",
            alerts=[f"State file not found: {STATE_PATH.name}"]
        )

    with open(STATE_PATH) as f:
        state = json.load(f)

    meta = state.get("metadata", {})
    runner = state.get("runner", {})
    sym = state.get("symbols", {}).get("ETHUSD", {})

    declared_step_buy = meta.get("step_buy", meta.get("step", 14))
    declared_step_sell = meta.get("step_sell", meta.get("step", 14))

    anchor = sym.get("anchor", 0)
    next_buy = sym.get("next_buy_level", 0)
    next_sell = sym.get("next_sell_level", 0)

    eff_buy_dist = anchor - next_buy if anchor > 0 and next_buy > 0 else 0
    eff_sell_dist = next_sell - anchor if anchor > 0 and next_sell > 0 else 0

    # Geometry is normalized if both distances are within 20% of declared
    geo_norm = False
    if declared_step_buy > 0 and declared_step_sell > 0 and eff_buy_dist > 0 and eff_sell_dist > 0:
        buy_ratio = eff_buy_dist / declared_step_buy
        sell_ratio = eff_sell_dist / declared_step_sell
        geo_norm = (0.8 <= buy_ratio <= 1.2) and (0.8 <= sell_ratio <= 1.2)

    realized_closes = sym.get("realized_closes", 0)
    realized_net = sym.get("realized_net_usd", 0)
    avg = realized_net / realized_closes if realized_closes > 0 else 0
    open_n = len(sym.get("open_tickets", []))
    anchor_resets = sym.get("anchor_resets", 0)

    # Heartbeat age
    hb_str = runner.get("heartbeat_at") or runner.get("last_successful_run_at")
    if hb_str:
        try:
            hb = datetime.fromisoformat(hb_str)
            now = datetime.now(timezone.utc)
            age_min = (now - hb).total_seconds() / 60
        except Exception:
            age_min = 999
    else:
        age_min = 999

    closes_remaining = max(0, TARGET_CLOSES - realized_closes)
    progress = min(100, (realized_closes / TARGET_CLOSES) * 100) if TARGET_CLOSES > 0 else 0

    # Verdict
    if age_min > 10:
        verdict = "HEARTBEAT_STALE"
    elif realized_closes == 0:
        verdict = "WAITING_FOR_FIRST_CLOSE"
    elif realized_closes < TARGET_CLOSES and realized_net > 0:
        verdict = "PROOF_ACCUMULATING_POSITIVE"
    elif realized_closes < TARGET_CLOSES and realized_net < 0:
        verdict = "PROOF_ACCUMULATING_NEGATIVE"
    elif realized_closes >= TARGET_CLOSES and realized_net > 0:
        verdict = "VALIDATED_SHADOW_PASSED"
    elif realized_closes >= TARGET_CLOSES and realized_net < 0:
        verdict = "VALIDATED_SHADOW_FAILED"
    else:
        verdict = "UNKNOWN"

    # Alerts
    alerts = []
    if not geo_norm:
        alerts.append(
            f"Geometry NOT normalized: buy_dist={eff_buy_dist:.2f} (target {declared_step_buy}), "
            f"sell_dist={eff_sell_dist:.2f} (target {declared_step_sell})"
        )
    if realized_net < -15 and realized_closes <= 1:
        alerts.append("First close hit max floating loss (-$15). Step=14 may be too tight for current ETH volatility.")
    if age_min > 5:
        alerts.append(f"Heartbeat stale: {age_min:.1f} minutes old")
    if open_n > 12:
        alerts.append(f"High open positions: {open_n}")

    return ETHMonitorResult(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        realized_closes=realized_closes,
        realized_net_usd=round(realized_net, 2),
        avg_per_close=round(avg, 2),
        open_positions=open_n,
        anchor=round(anchor, 2),
        anchor_resets=anchor_resets,
        next_buy_level=round(next_buy, 2),
        next_sell_level=round(next_sell, 2),
        declared_step_buy=declared_step_buy,
        declared_step_sell=declared_step_sell,
        effective_buy_distance=round(eff_buy_dist, 2),
        effective_sell_distance=round(eff_sell_dist, 2),
        geometry_normalized=geo_norm,
        offensive_closure_enabled=meta.get("offensive_closure_enabled", False),
        heartbeat_age_minutes=round(age_min, 1),
        progress_pct=round(progress, 1),
        closes_remaining=closes_remaining,
        verdict=verdict,
        alerts=alerts
    )


def format_report(r: ETHMonitorResult) -> str:
    """Format as markdown."""
    lines = []
    lines.append("# ETH M5 Step14 Control Monitor")
    lines.append(f"- Generated at: `{r.timestamp}`")
    lines.append("")
    lines.append(f"## Verdict: `{r.verdict}`")
    lines.append("")

    lines.append("## Proof Progress")
    lines.append(f"- Realized closes: `{r.realized_closes}` / `{TARGET_CLOSES}` ({r.progress_pct}%)")
    lines.append(f"- Closes remaining: `{r.closes_remaining}`")
    lines.append(f"- Realized net: `${r.realized_net_usd:+.2f}`")
    lines.append(f"- Average per close: `${r.avg_per_close:+.2f}`")
    lines.append("")

    lines.append("## Geometry Status")
    lines.append(f"- Declared step: buy={r.declared_step_buy}, sell={r.declared_step_sell}")
    lines.append(f"- Effective distance: buy={r.effective_buy_distance:.2f}, sell={r.effective_sell_distance:.2f}")
    lines.append(f"- Geometry normalized: `{'YES' if r.geometry_normalized else 'NO'}`")
    lines.append(f"- Anchor: {r.anchor:.2f}")
    lines.append(f"- Next BUY: {r.next_buy_level:.2f}, Next SELL: {r.next_sell_level:.2f}")
    lines.append("")

    lines.append("## Runtime Health")
    lines.append(f"- Heartbeat age: `{r.heartbeat_age_minutes:.1f}` minutes")
    lines.append(f"- Open positions: `{r.open_positions}`")
    lines.append(f"- Anchor resets: `{r.anchor_resets}`")
    lines.append(f"- Offensive closure enabled: `{r.offensive_closure_enabled}`")
    lines.append("")

    if r.alerts:
        lines.append("## Alerts")
        lines.append("")
        for a in r.alerts:
            lines.append(f"- WARNING: {a}")
        lines.append("")

    lines.append("## Graduation Gate")
    lines.append("")
    if r.verdict == "VALIDATED_SHADOW_PASSED" and r.geometry_normalized:
        lines.append("[PASS] All gates passed. Ready for offensive-closure A/B variant launch.")
    elif r.verdict == "VALIDATED_SHADOW_PASSED" and not r.geometry_normalized:
        lines.append("[WARN] Closes passed but geometry NOT normalized. Variant A/B would be contaminated.")
    elif r.verdict == "PROOF_ACCUMULATING_POSITIVE":
        lines.append(f"[POSITIVE] Positive trajectory. Need {r.closes_remaining} more closes to validate.")
    elif r.verdict == "PROOF_ACCUMULATING_NEGATIVE":
        lines.append(f"[NEGATIVE] Negative trajectory. If this persists through {TARGET_CLOSES} closes, the theory fails.")
    else:
        lines.append(f"[WAITING] {r.verdict}")
    lines.append("")

    return "\n".join(lines)


def main():
    result = monitor()
    report = format_report(result)
    print(report)

    # Write to file (use UTF-8 encoding for emoji compatibility)
    output_path = REPORTS / "eth_m5_step14_monitor_latest.md"
    output_path.write_text(report, encoding="utf-8")
    print(f"\nReport also written to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
