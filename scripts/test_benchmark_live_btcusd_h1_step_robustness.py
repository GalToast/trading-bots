import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

MODULE_PATH = Path(__file__).with_name("benchmark_live_btcusd_h1_step_robustness.py")
SPEC = importlib.util.spec_from_file_location("benchmark_live_btcusd_h1_step_robustness", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class LiveBTCUSDH1StepRobustnessTests(unittest.TestCase):
    def test_parse_windows_dedupes_and_preserves_order(self) -> None:
        self.assertEqual(benchmark.parse_windows("3, 5,5, 7"), [3, 5, 7])

    def test_rank_rows_groups_by_days_then_marked_net(self) -> None:
        rows = [
            {"days": 5, "step": 45.0, "marked_net_usd": 10.0, "realized_net_usd": 9.0, "open_count": 5},
            {"days": 3, "step": 45.0, "marked_net_usd": 20.0, "realized_net_usd": 5.0, "open_count": 4},
            {"days": 5, "step": 50.0, "marked_net_usd": 15.0, "realized_net_usd": 8.0, "open_count": 3},
        ]
        ranked = benchmark.rank_rows(rows)
        self.assertEqual([(row["days"], row["step"]) for row in ranked], [(3, 45.0), (5, 50.0), (5, 45.0)])


if __name__ == "__main__":
    unittest.main()
