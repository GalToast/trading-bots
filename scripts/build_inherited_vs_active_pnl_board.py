"""Build inherited vs active P/L separation board from execution monitor report."""
import json
import os
import sys
from datetime import datetime, timezone

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_board():
    em_path = os.path.join(REPORTS_DIR, "execution_monitor_report.json")
    em = load_json(em_path)
    if not em:
        print(f"ERROR: Cannot load execution monitor report from {em_path}")
        sys.exit(1)
    em_rows = em.get("rows", em.get("lanes", []))
    if not em_rows:
        print(f"ERROR: No lanes/rows found in execution monitor report")
        sys.exit(1)

    lanes = []
    total_inherited_realized = 0.0
    total_inherited_closes = 0
    total_active_realized = 0.0
    total_active_closes = 0
    total_carry_realized = 0.0
    total_carry_closes = 0

    for row in em_rows:
        name = row.get("lane", "unknown")
        kind = row.get("kind", "")

        # Inherited: broker sync inherited
        broker_sync_closes = int(row.get("broker_sync_inherited_closes", 0) or 0)
        broker_sync_realized = float(row.get("broker_sync_inherited_realized_usd", 0.0) or 0.0)

        # Inherited: pre-start state carry
        pre_start_closes = int(row.get("pre_start_state_carry_closes", 0) or 0)
        pre_start_realized = float(row.get("pre_start_state_carry_realized_usd", 0.0) or 0.0)

        # Active: clean forward (new closes since repair/reset)
        clean_fwd_closes = int(row.get("clean_forward_new_closes", 0) or 0)
        clean_fwd_realized = float(row.get("clean_forward_realized_delta_usd", 0.0) or 0.0)

        # Active: runner session since start
        session_closes = int(row.get("runner_session_trade_closes", 0) or 0)
        session_realized = float(row.get("runner_session_trade_realized_usd", 0.0) or 0.0)

        # Total closes/realized
        total_closes = int(row.get("close_count", 0) or 0)
        # session_trade_closes is the session total, runner_session is the fresh subset
        session_total_closes = int(row.get("session_trade_closes", 0) or 0)
        session_total_realized = 0.0  # Not always available directly

        # Open positions
        open_count = int(row.get("open_count", 0) or 0)

        # Classification
        inherited_realized = broker_sync_realized + pre_start_realized
        inherited_closes = broker_sync_closes + pre_start_closes

        # Active = clean forward if available, else runner session
        if clean_fwd_closes > 0:
            active_realized = clean_fwd_realized
            active_closes = clean_fwd_closes
        elif session_closes > 0:
            active_realized = session_realized
            active_closes = session_closes
        else:
            active_realized = 0.0
            active_closes = 0

        total_inherited_realized += inherited_realized
        total_inherited_closes += inherited_closes
        total_active_realized += active_realized
        total_active_closes += active_closes
        total_carry_realized += pre_start_realized
        total_carry_closes += pre_start_closes

        # Verdict
        if inherited_closes > 0 and active_closes == 0:
            verdict = "inherited_only"
        elif inherited_closes == 0 and active_closes > 0:
            verdict = "active_only"
        elif inherited_closes > 0 and active_closes > 0:
            verdict = "mixed"
        else:
            verdict = "no_closes"

        lanes.append({
            "lane": name,
            "verdict": verdict,
            "inherited_realized_usd": round(inherited_realized, 2),
            "inherited_closes": inherited_closes,
            "active_realized_usd": round(active_realized, 2),
            "active_closes": active_closes,
            "broker_sync_closes": broker_sync_closes,
            "broker_sync_realized_usd": round(broker_sync_realized, 2),
            "pre_start_closes": pre_start_closes,
            "pre_start_realized_usd": round(pre_start_realized, 2),
            "clean_fwd_closes": clean_fwd_closes,
            "clean_fwd_realized_usd": round(clean_fwd_realized, 2),
            "runner_session_closes": session_closes,
            "runner_session_realized_usd": round(session_realized, 2),
            "total_closes": total_closes,
            "open_positions": open_count,
            "kind": kind,
            "watchdog_status": row.get("watchdog_status", ""),
        })

    # Sort: active_only first (by active closes desc), then mixed, then inherited_only, then no_closes
    verdict_order = {"active_only": 0, "mixed": 1, "inherited_only": 2, "no_closes": 3}
    lanes.sort(key=lambda l: (verdict_order.get(l["verdict"], 4), -l["active_closes"]))

    board = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_inherited_realized_usd": round(total_inherited_realized, 2),
            "total_inherited_closes": total_inherited_closes,
            "total_active_realized_usd": round(total_active_realized, 2),
            "total_active_closes": total_active_closes,
            "total_carry_realized_usd": round(total_carry_realized, 2),
            "total_carry_closes": total_carry_closes,
            "lanes_active_only": sum(1 for l in lanes if l["verdict"] == "active_only"),
            "lanes_mixed": sum(1 for l in lanes if l["verdict"] == "mixed"),
            "lanes_inherited_only": sum(1 for l in lanes if l["verdict"] == "inherited_only"),
            "lanes_no_closes": sum(1 for l in lanes if l["verdict"] == "no_closes"),
        },
        "lanes": lanes,
    }

    # Write JSON
    json_path = os.path.join(REPORTS_DIR, "inherited_vs_active_pnl_board.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(board, f, indent=2, default=str)

    # Write Markdown
    md_path = os.path.join(REPORTS_DIR, "inherited_vs_active_pnl_board.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Inherited vs Active P/L Board\n\n")
        f.write(f"> Generated: `{board['generated_at']}`\n\n")
        s = board["summary"]
        f.write("## Summary\n\n")
        f.write(f"- Total inherited realized: **${s['total_inherited_realized_usd']:.2f}** ({s['total_inherited_closes']} closes)\n")
        f.write(f"- Total active realized: **${s['total_active_realized_usd']:.2f}** ({s['total_active_closes']} closes)\n")
        f.write(f"- Lanes active-only: {s['lanes_active_only']}\n")
        f.write(f"- Lanes mixed: {s['lanes_mixed']}\n")
        f.write(f"- Lanes inherited-only: {s['lanes_inherited_only']}\n")
        f.write(f"- Lanes no closes: {s['lanes_no_closes']}\n\n")

        # Active performers
        active_lanes = [l for l in lanes if l["active_closes"] > 0]
        if active_lanes:
            f.write("## Current runtime earned (active P/L)\n\n")
            f.write("| Lane | Active $ | Active # | Inherited $ | Inherited # | Kind | Watchdog |\n")
            f.write("|---|---:|---:|---:|---:|---|---|\n")
            for lane in active_lanes:
                f.write(f"| `{lane['lane']}` | ${lane['active_realized_usd']:.2f} | {lane['active_closes']}c | ${lane['inherited_realized_usd']:.2f} | {lane['inherited_closes']}c | {lane['kind']} | {lane['watchdog_status']} |\n")

        # Inherited-only
        inherited_lanes = [l for l in lanes if l["verdict"] == "inherited_only"]
        if inherited_lanes:
            f.write(f"\n## Inherited-only ({len(inherited_lanes)} lanes — running on legacy profit)\n\n")
            f.write("| Lane | Inherited $ | Inherited # | Open | Kind |\n")
            f.write("|---|---:|---:|---:|---|\n")
            for lane in inherited_lanes:
                f.write(f"| `{lane['lane']}` | ${lane['inherited_realized_usd']:.2f} | {lane['inherited_closes']}c | {lane['open_positions']} | {lane['kind']} |\n")

        # No closes
        no_close_lanes = [l for l in lanes if l["verdict"] == "no_closes"]
        if no_close_lanes:
            f.write(f"\n## No closes ({len(no_close_lanes)} lanes — idle or grid-building)\n\n")
            f.write("| Lane | Inherited $ | Open | Kind | Watchdog |\n")
            f.write("|---|---:|---:|---|---|\n")
            for lane in no_close_lanes:
                f.write(f"| `{lane['lane']}` | ${lane['inherited_realized_usd']:.2f} | {lane['open_positions']} | {lane['kind']} | {lane['watchdog_status']} |\n")

        f.write("\n## Interpretation\n\n")
        f.write("- `active_realized_usd`: what the current runner earned from its own closes (clean forward or session).\n")
        f.write("- `inherited_realized_usd`: legacy carry from broker sync or pre-start state.\n")
        f.write("- If `active_closes=0` and `inherited_closes>0`, the lane is running on legacy profit only.\n")
        f.write("- `mixed` means the lane has both inherited baggage and fresh profit.\n")

    print(f"Board generated: {len(lanes)} lanes")
    print(f"  Inherited total: ${total_inherited_realized:.2f} / {total_inherited_closes}c")
    print(f"  Active total: ${total_active_realized:.2f} / {total_active_closes}c")
    print(f"  Active-only lanes: {s['lanes_active_only']}")
    print(f"  Mixed lanes: {s['lanes_mixed']}")
    print(f"  Inherited-only lanes: {s['lanes_inherited_only']}")
    print(f"Written: {json_path}")
    print(f"Written: {md_path}")


if __name__ == "__main__":
    build_board()
