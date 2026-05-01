#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def summarize_lane(name: str, state_path: Path, event_path: Path) -> dict:
    state = load_json(state_path)
    events = load_jsonl(event_path)
    symbols = state.get("symbols") or {}
    close_counts = Counter()
    open_counts = Counter()
    event_counts = Counter()
    realized_by_symbol = defaultdict(float)

    for event in events:
        symbol = event.get("symbol", "")
        action = event.get("action", "")
        event_counts[action] += 1
        if action == "open_ticket":
            open_counts[symbol] += 1
        elif action == "close_ticket":
            close_counts[symbol] += 1
            realized_by_symbol[symbol] += float(event.get("realized_pnl", 0.0) or 0.0)

    return {
        "name": name,
        "updated_at": state.get("updated_at", ""),
        "metadata": state.get("metadata", {}),
        "symbols": symbols,
        "event_counts": dict(event_counts),
        "open_counts": dict(open_counts),
        "close_counts": dict(close_counts),
        "realized_by_symbol": {k: round(v, 3) for k, v in sorted(realized_by_symbol.items())},
    }


def print_summary(summary: dict) -> None:
    print(f"{summary['name']}: updated_at={summary['updated_at']} metadata={summary['metadata']}")
    for symbol in sorted(summary["symbols"].keys()):
        snap = summary["symbols"][symbol]
        open_count = len(snap.get("open_tickets") or [])
        realized = float(snap.get("realized_net_usd", 0.0) or 0.0)
        closes = int(snap.get("realized_closes", 0) or 0)
        mode = snap.get("mode", "")
        print(
            f"  {symbol:<7} mode={mode:<11} open={open_count:<2} "
            f"realized={realized:+8.2f} closes={closes:<4}"
        )
    print(f"  events={summary['event_counts']}")
    print(f"  close_pnl={summary['realized_by_symbol']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare multiple penetration lattice shadow lanes.")
    parser.add_argument(
        "--lane",
        action="append",
        nargs=3,
        metavar=("NAME", "STATE_PATH", "EVENT_PATH"),
        help="Lane triple: name state_path event_path",
    )
    args = parser.parse_args()

    lanes = args.lane or [
        (
            "alpha0_raw",
            str(ROOT / "reports" / "penetration_lattice_shadow_alpha0_raw_state.json"),
            str(ROOT / "reports" / "penetration_lattice_shadow_alpha0_raw_events.jsonl"),
        ),
        (
            "alpha50_raw",
            str(ROOT / "reports" / "penetration_lattice_shadow_alpha50_raw_state.json"),
            str(ROOT / "reports" / "penetration_lattice_shadow_alpha50_raw_events.jsonl"),
        ),
    ]

    summaries = [summarize_lane(name, Path(state), Path(events)) for name, state, events in lanes]
    for summary in summaries:
        print_summary(summary)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
