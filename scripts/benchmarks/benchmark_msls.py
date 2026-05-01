"""Run a benchmark-only offline validation for the Gemini v2 MSLS prototype."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_msls import DEFAULT_SYMBOLS, run_validation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Historical M1 days to analyze")
    parser.add_argument("--lookback", type=int, default=40, help="MSLS detection lookback window")
    parser.add_argument("--lookahead-bars", type=int, default=60, help="Future bars to score after each entry")
    parser.add_argument("--min-signals", type=int, default=50, help="Minimum per-symbol signals to count as benchmark-worthy")
    parser.add_argument(
        "--out-dir",
        default="reports/bot-benchmarks",
        help="Directory for JSON benchmark results",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional run ID suffix for output filenames.",
    )
    parser.add_argument("symbols", nargs="*", default=DEFAULT_SYMBOLS, help="Symbols to validate")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().astimezone()
    run_id = args.run_id or started_at.strftime("%Y%m%d-%H%M%S-%f")
    report = run_validation(
        symbols=[symbol.upper() for symbol in args.symbols],
        days=args.days,
        lookback=args.lookback,
        lookahead_bars=args.lookahead_bars,
        min_signals=args.min_signals,
        sample_limit=5,
    )
    finished_at = datetime.now().astimezone()
    aggregate = report["aggregate"]
    gate = report["aggregate_promotion_gate"]

    result = {
        "benchmark_type": "offline_signal_validation",
        "candidate": "gemini_v2_msls",
        "benchmark_only": True,
        "strategy_module": "bot.gemini_v2",
        "validator_script": "scripts.validate_msls",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
        "symbols": [symbol.upper() for symbol in args.symbols],
        "days": args.days,
        "lookback": args.lookback,
        "lookahead_bars": args.lookahead_bars,
        "min_signals": args.min_signals,
        "aggregate": aggregate,
        "aggregate_promotion_gate": gate,
        "per_symbol": [
            {
                "symbol": symbol_report["symbol"],
                "summary": symbol_report["summary"],
                "promotion_gate": symbol_report["promotion_gate"],
                "examples": symbol_report["examples"],
            }
            for symbol_report in report["symbols"]
        ],
    }

    result_path = out_dir / f"benchmark-msls-{run_id}.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
