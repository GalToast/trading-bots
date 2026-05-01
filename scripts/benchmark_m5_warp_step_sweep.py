"""Benchmark BTC M5 Warp with step=$200 vs step=$100 on tick replay."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

sys.path.insert(0, str(Path(__file__).parent))
from tick_penetration_lattice_core import (
    engine_from_args,
    load_ticks_range,
)

REPO = Path(__file__).resolve().parent.parent
STATE_PATH = REPO / "reports" / "penetration_lattice_shadow_btcusd_m5_warp_state.json"
UTC = timezone.utc


def marked_floating_net(engine, last_tick):
    net = 0.0
    for t in engine.state.open_tickets:
        if last_tick is None:
            continue
        if t["direction"] == "SELL":
            price = last_tick.get("bid", last_tick.get("ask", 0))
            net += (t["fill_price"] - price) * engine.volume
        else:
            price = last_tick.get("ask", last_tick.get("bid", 0))
            net += (price - t["fill_price"]) * engine.volume
    return net


def run_benchmark():
    with open(STATE_PATH) as f:
        state = json.load(f)

    symbols_data = state.get("symbols", {})
    btc = symbols_data.get("BTCUSD", {})
    if not btc:
        print("No BTCUSD data in shadow state")
        return

    anchor = btc.get("anchor", 73037.57)
    variant = btc.get("variant", "rearm_lvl2_exc1")
    mode = btc.get("mode", "raw_stateful_rearm")
    max_open = state.get("metadata", {}).get("max_open_per_side", 60)
    close_alpha = btc.get("raw_close_alpha", 1.0)
    close_gap = btc.get("raw_close_gap", 1)
    sell_gap = state.get("metadata", {}).get("raw_sell_gap", 1)
    buy_gap = state.get("metadata", {}).get("raw_buy_gap", 1)
    momentum_gate = btc.get("momentum_gate", False)
    cooldown = btc.get("rearm_cooldown_bars", 0)

    end_utc = datetime.now(UTC)
    start_utc = end_utc - timedelta(days=7)

    print(f"M5 Warp Step Benchmark")
    print(f"  Window: {start_utc.strftime('%Y-%m-%d')} to {end_utc.strftime('%Y-%m-%d')}")
    print(f"  Anchor: {anchor}, Variant: {variant}")
    print(f"  Max open/side: {max_open}, Close alpha: {close_alpha}")
    print()

    results = []
    for step in [100.0, 150.0, 200.0, 250.0, 300.0]:
        engine = engine_from_args(
            symbol="BTCUSD",
            step=step,
            max_open_per_side=max_open,
            variant_name=variant,
            timeframe_name="M5",
            close_alpha=close_alpha,
            momentum_gate=momentum_gate,
            cooldown_bars=cooldown,
            sell_gap=sell_gap,
            buy_gap=buy_gap,
        )

        ticks = load_ticks_range("BTCUSD", start_utc, end_utc)
        if not ticks:
            print(f"  step=${step:.0f}: NO TICKS")
            continue

        last_tick = None
        for tick in ticks:
            last_tick = tick
            engine.process_tick(tick)

        realized = engine.state.realized_net_usd
        closes = engine.state.realized_closes
        opens = len(engine.state.open_tickets)
        floating = marked_floating_net(engine, last_tick)
        net = realized + floating
        resets = engine.state.anchor_resets

        per_close = realized / max(closes, 1)
        print(f"  step=${step:.0f}: ${realized:.2f}, {closes}c, {opens} open, floating=${floating:.2f}, net=${net:.2f}, {resets} resets, ${per_close:.2f}/close")
        results.append({
            "step": step,
            "realized": realized,
            "closes": closes,
            "opens": opens,
            "floating": floating,
            "net": net,
            "resets": resets,
            "per_close": per_close,
        })

    # Report
    report_path = REPO / "reports" / "m5_warp_step_benchmark.md"
    lines = [
        "# M5 Warp Step Benchmark",
        "",
        f"Window: {start_utc.strftime('%Y-%m-%d')} to {end_utc.strftime('%Y-%m-%d')} (7 days)",
        f"Anchor: {anchor}, Variant: {variant}, Close alpha: {close_alpha}",
        "",
        "| Step | Realized | Closes | Opens | Floating | Total Net | Resets | $/Close |",
        "|------|----------|--------|-------|----------|-----------|--------|---------|",
    ]
    for r in results:
        lines.append(
            f"| ${r['step']:.0f} | ${r['realized']:.2f} | {r['closes']} | {r['opens']} | ${r['floating']:.2f} | ${r['net']:.2f} | {r['resets']} | ${r['per_close']:.2f} |"
        )

    if results:
        baseline = results[0]
        lines.append(f"\nBaseline (step=$100): ${baseline['realized']:.2f}, {baseline['closes']}c, ${baseline['per_close']:.2f}/close")
        for r in results[1:]:
            delta_pct = (r['realized'] - baseline['realized']) / max(abs(baseline['realized']), 1) * 100
            lines.append(f"  step=${r['step']:.0f}: ${r['realized']:.2f} ({delta_pct:+.1f}%), {r['closes']}c, ${r['per_close']:.2f}/close")

    lines.extend([
        "",
        "## Interpretation",
        "- Higher step = fewer opens, fewer closes, but potentially higher $/close",
        "- Floating risk scales with open count; wider step reduces floating exposure",
        "- The optimal step balances replay PnL with live execution quality",
        "",
    ])

    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    if not mt5.initialize():
        print("MT5 initialize() failed")
        sys.exit(1)
    try:
        run_benchmark()
    finally:
        mt5.shutdown()
