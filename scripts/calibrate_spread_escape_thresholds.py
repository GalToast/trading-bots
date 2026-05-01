"""Calibrate per-symbol spread escape thresholds from enriched event logs.

Answers:
1. What is "normal" spread per symbol?
2. What spread multiplier prevents escapes without blocking good entries?
3. Per-symbol threshold table for the adaptive controller.
"""
import json
import glob
import os
from collections import defaultdict
from datetime import datetime, timezone

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")


def analyze_event_file(filepath):
    """Analyze event log for spread vs escape relationship."""
    result = {
        "spreads_at_open": [],
        "spreads_at_escape": [],
        "spreads_at_natural_close": [],
        "symbols": set(),
    }

    lines = open(filepath, "r", encoding="utf-8").readlines()

    for line in lines:
        try:
            ev = json.loads(line.strip())
        except Exception:
            continue
        action = ev.get("action", "")
        spread = ev.get("spread_at_entry")
        symbol = ev.get("symbol", "unknown")

        if spread is None:
            continue

        result["symbols"].add(symbol)

        if action == "open_ticket":
            result["spreads_at_open"].append({
                "spread": spread,
                "symbol": symbol,
                "regime": ev.get("regime_at_entry", ""),
            })

        if action in ("close_ticket", "forced_unwind", "escape_tier1", "escape_tier2_surgical"):
            pnl = ev.get("realized_pnl", 0.0) or 0.0
            entry_spread = ev.get("spread_at_entry")
            is_escape = action != "close_ticket"

            if is_escape:
                result["spreads_at_escape"].append({
                    "spread": entry_spread,
                    "symbol": symbol,
                    "pnl": pnl,
                    "action": action,
                    "hold_seconds": ev.get("hold_seconds"),
                })
            else:
                result["spreads_at_natural_close"].append({
                    "spread": entry_spread,
                    "symbol": symbol,
                    "pnl": pnl,
                })

    return result


def build_board():
    files = sorted(glob.glob(os.path.join(REPORTS_DIR, "*events.jsonl")),
                   key=os.path.getmtime, reverse=True)

    # Aggregate by symbol
    symbol_data = defaultdict(lambda: {
        "all_spreads": [],
        "escape_spreads": [],
        "natural_close_spreads": [],
        "escape_pnls": [],
        "natural_close_pnls": [],
        "burst_spreads": [],
        "non_burst_spreads": [],
    })

    for f in files:
        result = analyze_event_file(f)
        bn = os.path.basename(f).replace("_events.jsonl", "")

        for sym in result["symbols"]:
            sd = symbol_data[sym]

            for s in result["spreads_at_open"]:
                if s["symbol"] == sym:
                    sd["all_spreads"].append(s["spread"])
                    regime = s.get("regime", "")
                    if "burst" in regime.lower():
                        sd["burst_spreads"].append(s["spread"])
                    else:
                        sd["non_burst_spreads"].append(s["spread"])

            for s in result["spreads_at_escape"]:
                if s["symbol"] == sym:
                    sd["escape_spreads"].append(s["spread"])
                    sd["escape_pnls"].append(s["pnl"])

            for s in result["spreads_at_natural_close"]:
                if s["symbol"] == sym:
                    sd["natural_close_spreads"].append(s["spread"])
                    sd["natural_close_pnls"].append(s["pnl"])

    # Compute thresholds per symbol
    symbols = []
    for sym, sd in sorted(symbol_data.items()):
        if not sd["all_spreads"]:
            continue

        # Sort spreads
        sd["all_spreads"].sort()
        n = len(sd["all_spreads"])
        median_spread = sd["all_spreads"][n // 2]
        p25 = sd["all_spreads"][n // 4]
        p75 = sd["all_spreads"][3 * n // 4]
        p90 = sd["all_spreads"][int(0.9 * n)]
        p95 = sd["all_spreads"][int(0.95 * n)]

        # Escape analysis
        n_escapes = len(sd["escape_spreads"])
        n_natural = len(sd["natural_close_spreads"])
        total_escapes_pnl = sum(sd["escape_pnls"])
        total_natural_pnl = sum(sd["natural_close_pnls"])

        # If escape spreads are known, compute threshold
        if sd["escape_spreads"]:
            sd["escape_spreads"].sort()
            median_escape_spread = sd["escape_spreads"][len(sd["escape_spreads"]) // 2]
        else:
            median_escape_spread = None

        # What spread threshold would have prevented 50% of escapes?
        if n_escapes > 0:
            sd["escape_spreads"].sort()
            prevent_50_pct = sd["escape_spreads"][len(sd["escape_spreads"]) // 2]
            prevent_90_pct = sd["escape_spreads"][int(0.9 * len(sd["escape_spreads"]))]
        else:
            prevent_50_pct = None
            prevent_90_pct = None

        # Burst vs non-burst spread comparison
        if sd["burst_spreads"]:
            median_burst_spread = sorted(sd["burst_spreads"])[len(sd["burst_spreads"]) // 2]
        else:
            median_burst_spread = None

        if sd["non_burst_spreads"]:
            median_non_burst_spread = sorted(sd["non_burst_spreads"])[len(sd["non_burst_spreads"]) // 2]
        else:
            median_non_burst_spread = None

        # Recommended threshold: 2x median spread (conservative) or 3x (aggressive)
        threshold_2x = round(median_spread * 2, 2)
        threshold_3x = round(median_spread * 3, 2)

        # What % of escapes have spread above 2x/3x median?
        escapes_above_2x = sum(1 for s in sd["escape_spreads"] if s > threshold_2x)
        escapes_above_3x = sum(1 for s in sd["escape_spreads"] if s > threshold_3x)

        symbols.append({
            "symbol": sym,
            "total_opens": n,
            "total_escapes": n_escapes,
            "total_natural_closes": n_natural,
            "escape_pnl": round(total_escapes_pnl, 2),
            "natural_pnl": round(total_natural_pnl, 2),
            "median_spread": round(median_spread, 4),
            "p25_spread": round(p25, 4),
            "p75_spread": round(p75, 4),
            "p90_spread": round(p90, 4),
            "p95_spread": round(p95, 4),
            "median_escape_spread": round(median_escape_spread, 4) if median_escape_spread else None,
            "median_burst_spread": round(median_burst_spread, 4) if median_burst_spread else None,
            "median_non_burst_spread": round(median_non_burst_spread, 4) if median_non_burst_spread else None,
            "threshold_2x": threshold_2x,
            "threshold_3x": threshold_3x,
            "escapes_above_2x": escapes_above_2x,
            "escapes_above_3x": escapes_above_3x,
            "prevent_50_pct_spread": round(prevent_50_pct, 4) if prevent_50_pct else None,
            "prevent_90_pct_spread": round(prevent_90_pct, 4) if prevent_90_pct else None,
        })

    # Sort by escape pnl (worst first)
    symbols.sort(key=lambda s: s["escape_pnl"])

    board = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
    }

    # Write JSON
    json_path = os.path.join(REPORTS_DIR, "spread_escape_threshold_board.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(board, f, indent=2, default=str)

    # Write Markdown
    md_path = os.path.join(REPORTS_DIR, "spread_escape_threshold_board.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Spread Escape Threshold Board\n\n")
        f.write(f"> Generated: `{board['generated_at']}`\n\n")
        f.write(f"> Per-symbol spread thresholds for adaptive controller `guarded_toxic_flow` mode.\n\n")

        f.write("## Per-Symbol Thresholds\n\n")
        f.write("| Symbol | Opens | Escapes | Escape $ | Natural $ | Median Spread | 2x Threshold | 3x Threshold | Escapes > 2x | Escapes > 3x | Median Burst Spread |\n")
        f.write("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        for s in symbols:
            f.write(f"| `{s['symbol']}` | {s['total_opens']} | {s['total_escapes']} | ${s['escape_pnl']:.2f} | ${s['natural_pnl']:.2f} | {s['median_spread']} | {s['threshold_2x']} | {s['threshold_3x']} | {s['escapes_above_2x']}/{s['total_escapes']} | {s['escapes_above_3x']}/{s['total_escapes']} | {s['median_burst_spread'] or 'N/A'} |\n")

        # Recommended spread rules for adaptive controller
        f.write(f"\n## Adaptive Controller Spread Rules\n\n")
        f.write("For each symbol, if `spread_at_entry` exceeds the threshold below, the controller should:\n")
        f.write("1. **Skip the entry** if widening the step would exceed max lattice window\n")
        f.write("2. **Widen step by spread_ratio** otherwise (e.g., if spread is 3x normal, use 3x step)\n\n")
        f.write("| Symbol | Normal Spread | Skip Entry If Spread > | Widen Step If Spread > | Step Multiplier |\n")
        f.write("|---|---:|---:|---:|---:|\n")
        for s in symbols:
            skip_threshold = s["threshold_3x"]  # 3x = skip
            widen_threshold = s["threshold_2x"]  # 2x = widen
            f.write(f"| `{s['symbol']}` | {s['median_spread']} | {skip_threshold} | {widen_threshold} | spread / {s['median_spread']} |\n")

        # Distribution analysis
        f.write(f"\n## Spread Distribution Details\n\n")
        f.write("| Symbol | P25 | Median | P75 | P90 | P95 | Median Escape Spread |\n")
        f.write("|---|---:|---:|---:|---:|---:|---:|\n")
        for s in symbols:
            f.write(f"| `{s['symbol']}` | {s['p25_spread']} | {s['median_spread']} | {s['p75_spread']} | {s['p90_spread']} | {s['p95_spread']} | {s['median_escape_spread'] or 'N/A'} |\n")

        f.write(f"\n## Interpretation\n\n")
        f.write(f"If `median_burst_spread` >> `median_non_burst_spread`, the burst-expansion regime consistently correlates with wider spreads.\n")
        f.write(f"If `escapes_above_2x` is a high fraction of total escapes, the 2x threshold is a reliable early warning signal.\n")
        f.write(f"The adaptive controller should use these thresholds to preemptively widen steps or skip entries before toxic positions accumulate.\n")

    print(f"Board generated: {len(symbols)} symbols")
    for s in symbols[:10]:
        print(f"  {s['symbol']}: median={s['median_spread']}, 2x={s['threshold_2x']}, escapes={s['total_escapes']}, escape$={s['escape_pnl']:.2f}")
    print(f"Written: {json_path}")
    print(f"Written: {md_path}")


if __name__ == "__main__":
    build_board()
