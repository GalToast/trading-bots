"""Benchmark-style fresh-entry validator for off-session profiles."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_offsession_profile import build_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-unlabeled", action="store_true", help="Include unlabeled entries in the benchmark")
    parser.add_argument("--min-samples", type=int, default=2)
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
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().astimezone()
    run_id = args.run_id or started_at.strftime("%Y%m%d-%H%M%S-%f")
    report = build_report(include_unlabeled=args.include_unlabeled, min_samples=args.min_samples)
    finished_at = datetime.now().astimezone()

    result = {
        "benchmark_type": "fresh_entry_offsession_validation",
        "candidate": "offsession_profiles",
        "benchmark_only": True,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
        **report,
    }

    result_path = out_dir / f"benchmark-offsession-{run_id}.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
