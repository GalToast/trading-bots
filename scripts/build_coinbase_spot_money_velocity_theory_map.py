#!/usr/bin/env python3
from __future__ import annotations

import json
import csv
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
JSON_PATH = REPORTS / "coinbase_spot_money_velocity_theory_map.json"
CSV_PATH = REPORTS / "coinbase_spot_money_velocity_theory_map.csv"
MD_PATH = REPORTS / "coinbase_spot_money_velocity_theory_map.md"


THEORIES = [
    ("live_fee_cleared_breakout", "spot_native", "Enter only when live bid movement has already paid fee + spread + buffer.", "implemented_partial"),
    ("bubble_ignition_reclaim", "spot_native", "Wait for fast upside ignition, then require a pullback reclaim before entry.", "needs_backtest"),
    ("dump_exhaustion_rebound", "spot_native", "Detect violent selloff, wait for spread compression and higher-low reclaim, then long rebound.", "needs_backtest"),
    ("cash_as_position", "spot_native", "Treat USD as an active position when the universe is net depreciating.", "needs_metric"),
    ("anti_hold_decay", "spot_native", "Score how much money was saved by not holding live dump rows.", "needs_metric"),
    ("relative_quote_depreciation", "relative_value", "Long BASE/QUOTE when quote asset is falling faster than base and USD conversion still nets positive.", "needs_route_costing"),
    ("strongest_survivor_rotation", "spot_native", "During broad dumps, rotate only into the asset with least negative live velocity plus rebound structure.", "needs_backtest"),
    ("family_leader_follow", "spot_native", "If one microcap family leader ignites, pre-rank siblings but enter only on their own live reclaim.", "needs_clustering"),
    ("family_leader_avoid", "spot_native", "If a family leader dumps, block correlated followers before their own candles confirm.", "needs_clustering"),
    ("spread_compression_after_panic", "microstructure", "After a dump, enter only when spread collapses from panic-wide to normal.", "needs_book_data"),
    ("volume_climax_reversal", "spot_native", "Require extreme sell volume followed by green close and live bid lift.", "needs_volume_cache"),
    ("failed_breakdown_reclaim", "spot_native", "Buy when price breaks prior low, snaps back above it, and bid holds for N polls.", "needs_backtest"),
    ("micro_double_bottom", "spot_native", "Enter after two local lows with second low lower-spread and stronger bid response.", "needs_backtest"),
    ("liquidity_vacuum_snapback", "microstructure", "Watch thin books where tiny ask lift after dump creates oversized snapback.", "needs_l2_book"),
    ("maker_rebound_probe", "microstructure", "Post maker bid after reclaim instead of crossing spread; only count if realistic fill occurs.", "needs_fill_model"),
    ("maker_exit_rachet", "microstructure", "Use maker exits for profit lock when book allows; taker-exit only on failure.", "needs_fill_model"),
    ("two_stage_entry", "spot_native", "Use small scout after signal, add only after net-positive confirmation.", "needs_sizing_sim"),
    ("profit_only_redeploy", "spot_native", "Deploy principal only into first trade; later high-risk probes use realized profit only.", "needs_sizing_sim"),
    ("velocity_budget_allocator", "spot_native", "Allocate more notional only when live universe shows multiple fee-cleared targets.", "needs_portfolio_sim"),
    ("idle_alpha_score", "spot_native", "Reward the bot for staying flat when no fee-cleared target exists.", "needs_metric"),
    ("reentry_lower_after_loss", "spot_native", "After stopped entry, allow reentry only below prior entry plus stronger live signal.", "implemented_partial"),
    ("ghost_negative_veto", "spot_native", "Use ghost losers to block bad timing, not permanently ban the coin.", "implemented"),
    ("ghost_winner_acceleration", "spot_native", "If ghost wins on a product/playbook, temporarily lower confirmation latency.", "needs_guardrails"),
    ("product_personality_prior", "spot_native", "Use candle cache to classify which products historically produce capturable upside.", "needs_feature_store"),
    ("bear_velocity_rebound_queue", "spot_native", "Convert direct dump rows into a rebound watch queue with reclaim triggers.", "needs_backtest"),
    ("blue_chip_cash_filter", "spot_native", "Avoid BTC/ETH/SOL for 5%/h target except as cash-like shelter or regime signal.", "validated_context"),
    ("microcap_only_target_pool", "spot_native", "Limit 5%/h attempts to products with historical hourly excursion density.", "needs_capturability_filter"),
    ("false_bubble_classifier", "spot_native", "Train rules to reject ignition moves that historically become loser entries.", "needs_features"),
    ("wick_absorption_entry", "spot_native", "After sell wick, enter only if next bid holds above wick midpoint.", "needs_backtest"),
    ("inside_bar_after_burst", "spot_native", "After burst, wait for tight inside bar then break upward.", "needs_backtest"),
    ("range_expansion_continuation", "spot_native", "Enter when current range expansion is high and close is near high, not mid-bar.", "needs_backtest"),
    ("range_expansion_fade_block", "spot_native", "Block entries when range expands but close is below midpoint.", "needs_backtest"),
    ("quote_inventory_surfing", "relative_value", "Hold non-USD quote asset only when it is the strongest available cash substitute.", "needs_route_costing"),
    ("triangular_spot_route", "relative_value", "Route USD -> quote -> target only if pair edge beats both conversion legs.", "needs_route_engine"),
    ("stable_quote_dislocation", "relative_value", "Exploit temporary USDC/USDT/USD quote differences only if fees do not erase edge.", "needs_route_engine"),
    ("depreciating_quote_long", "relative_value", "Use BASE/QUOTE to express bearish quote asset while base is stable or rising.", "needs_route_costing"),
    ("cross_quote_momentum_vote", "relative_value", "Require same base to strengthen across multiple quote pairs before entry.", "needs_universe_pairs"),
    ("microcap_rotation_heatmap", "spot_native", "Rank symbols by live heat and historical capturability instead of volume alone.", "needs_feature_store"),
    ("profit_lock_tightening_by_regime", "spot_native", "Tighten profit lock when live radar says broad market is rolling over.", "needs_runner_patch"),
    ("exit_on_bear_leader_dump", "spot_native", "Exit current hold if correlated bear leader dumps and held bid weakens.", "needs_clustering"),
    ("fee_tier_breakpoint_mode", "execution", "Switch aggressiveness based on actual Coinbase fee tier; taker 120bps requires huge moves.", "implemented_partial"),
    ("maker_only_microalpha", "execution", "Only trade setups that remain positive under realistic maker fill, not taker fills.", "needs_fill_model"),
    ("queue_position_decay", "execution", "Cancel maker orders if not filled quickly; stale fills are often adverse selection.", "needs_fill_model"),
    ("book_imbalance_reclaim", "microstructure", "Enter after bid depth rebuilds faster than ask depth after dump.", "needs_l2_book"),
    ("toxic_spread_filter", "microstructure", "Block products whose spread widens during signal rather than compresses.", "needs_book_data"),
    ("session_heat_filter", "spot_native", "Learn hours when microcap bubbles are most capturable and size only then.", "needs_backtest"),
    ("newsless_pump_decay", "spot_native", "Fade our willingness to enter repeated pumps without follow-through closes.", "needs_features"),
    ("capital_velocity_stop", "risk", "If realized account pace is below target and recent attempts are negative, require stronger signals.", "implemented_partial"),
    ("opportunity_cost_router", "risk", "Compare current hold to live radar top target and rotate only if edge beats churn tax.", "implemented_partial"),
    ("external_short_overlay", "external_only", "Use perps/options/margin on another venue for true bearish profit; not Coinbase spot-native.", "not_spot"),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


TRIGGERS = [
    ("live_bid_burst", "live bid jumps over the short-window threshold"),
    ("five_min_ignition", "5m high/low expansion shows fresh upside"),
    ("dump_reclaim", "fast dump reclaims a local breakdown level"),
    ("spread_compression", "panic-wide spread compresses back toward normal"),
    ("volume_climax", "sell or buy volume climaxes then price holds"),
    ("failed_breakdown", "new low fails and bid returns above the prior floor"),
    ("inside_bar_break", "post-burst compression breaks upward"),
    ("range_close_high", "range expands and close/bid holds near high"),
    ("relative_quote_slip", "quote asset weakens faster than base"),
    ("leader_followthrough", "cluster leader moves and follower confirms locally"),
]

CONFIRMATIONS = [
    ("one_poll_hot", "single hot radar poll; only for shadow scouting"),
    ("two_poll_hold", "two consecutive radar polls hold the signal"),
    ("three_poll_hold", "three consecutive polls reduce fakeout risk"),
    ("higher_low", "price forms a local higher low after trigger"),
    ("midpoint_hold", "bid holds above wick midpoint"),
    ("cross_quote_vote", "same base strengthens across multiple quote pairs"),
    ("spread_not_widening", "spread stays flat or narrows during trigger"),
    ("book_bid_rebuild", "bid depth rebuilds faster than ask depth"),
    ("ghost_winner_bias", "recent ghost simulation for the setup is positive"),
    ("dissonance_clear", "broad dump/spread stress board is not toxic"),
]

EXITS = [
    ("tight_fee_paid_trail", "trail only after round-trip fee is paid"),
    ("wide_bubble_trail", "give bubbles more room after strong ignition"),
    ("maker_profit_rachet", "try maker exit before taker emergency"),
    ("failed_reclaim_cut", "exit when reclaim fails within N polls"),
    ("broad_rollover_exit", "exit when dissonance board flips toxic"),
    ("opportunity_rotation", "rotate only if challenger beats churn tax"),
    ("time_decay_exit", "exit if no progress after the hold budget"),
    ("spread_toxic_exit", "exit when spread expansion makes execution toxic"),
    ("profit_bond_stop", "protect principal plus round-trip fee once earned"),
    ("cash_shelter_exit", "move to USD when universe pressure is net bearish"),
]

SIZING = [
    ("scout_20", "20% scout; add only after confirmation"),
    ("standard_50", "50% deployment into a validated signal"),
    ("hot_80", "80% single-baton deployment for strongest target"),
    ("profit_only_probe", "risk realized profit only"),
    ("vol_scaled", "smaller size for wider spread and larger wick risk"),
    ("cluster_cap", "cap total exposure to one correlated family"),
    ("fee_tier_scaled", "more aggressive only at lower verified fee tier"),
    ("drawdown_throttle", "reduce size after recent negative attempts"),
    ("idle_rewarded", "size zero when no fee-cleared target exists"),
    ("rebalance_baton", "one open symbol; redeploy only after banked exit"),
]


def generated_variants() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for archetype_idx, (name, category, idea, status) in enumerate(THEORIES, start=1):
        for variant_idx in range(100):
            trigger = TRIGGERS[variant_idx % len(TRIGGERS)]
            confirmation = CONFIRMATIONS[(variant_idx // 10) % len(CONFIRMATIONS)]
            exit_rule = EXITS[(variant_idx + archetype_idx) % len(EXITS)]
            sizing = SIZING[(variant_idx * 3 + archetype_idx) % len(SIZING)]
            spot_status = "spot_native" if category not in {"external_only"} else "external_required"
            feasibility = "testable_now"
            if "book" in confirmation[0] or "maker" in exit_rule[0]:
                feasibility = "needs_l2_or_fill_model"
            if category == "relative_value":
                feasibility = "needs_route_costing"
            if category == "external_only":
                feasibility = "not_coinbase_spot_native"
            rows.append(
                {
                    "id": len(rows) + 1,
                    "archetype_id": archetype_idx,
                    "archetype": name,
                    "category": category,
                    "status": status,
                    "spot_status": spot_status,
                    "feasibility": feasibility,
                    "trigger": trigger[0],
                    "confirmation": confirmation[0],
                    "exit": exit_rule[0],
                    "sizing": sizing[0],
                    "hypothesis": (
                        f"{idea} Variant requires {trigger[1]}, confirms with {confirmation[1]}, "
                        f"exits via {exit_rule[1]}, and sizes as {sizing[1]}."
                    ),
                }
            )
    return rows


def main() -> int:
    rows = generated_variants()
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_money_velocity_theory_map",
        "leadership_read": [
            "This is a generated hypothesis factory: 50 archetypes x 100 trigger/confirmation/exit/sizing variants.",
            "There are thousands of creative paths, but every spot-native path must still become executable bid/ask PnL after fees.",
            "Direct bearish profit needs short/margin/derivatives; Coinbase spot can only avoid, rotate relatively, or buy rebounds.",
            "The next edge is not raw movement detection; it is filtering for capturable movement before fees eat the account.",
        ],
        "axes": {
            "triggers": [name for name, _ in TRIGGERS],
            "confirmations": [name for name, _ in CONFIRMATIONS],
            "exits": [name for name, _ in EXITS],
            "sizing": [name for name, _ in SIZING],
        },
        "rows": rows,
    }
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "id",
        "archetype_id",
        "archetype",
        "category",
        "status",
        "spot_status",
        "feasibility",
        "trigger",
        "confirmation",
        "exit",
        "sizing",
        "hypothesis",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    lines = [
        "# Coinbase Spot Money Velocity Theory Map",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Theories: `{len(rows)}`",
        "- Construction: `50 archetypes x 100 variants`",
        "",
        "## Read",
        "",
    ]
    lines.extend([f"- {item}" for item in payload["leadership_read"]])
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row["feasibility"])
        counts[key] = counts.get(key, 0) + 1
    lines.extend(["", "## Feasibility Counts", ""])
    for key, count in sorted(counts.items()):
        lines.append(f"- `{key}`: `{count}`")
    lines.extend(
        [
            "",
            "## Theory Rows",
            "",
            "| # | Archetype | Category | Feasibility | Trigger | Confirmation | Exit | Sizing |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['id']} | `{row['archetype']}` | {row['category']} | {row['feasibility']} | "
            f"{row['trigger']} | {row['confirmation']} | {row['exit']} | {row['sizing']} |"
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "csv_path": str(CSV_PATH), "md_path": str(MD_PATH)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
