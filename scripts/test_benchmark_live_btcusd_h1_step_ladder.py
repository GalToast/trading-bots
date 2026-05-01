import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

MODULE_PATH = Path(__file__).with_name("benchmark_live_btcusd_h1_step_ladder.py")
SPEC = importlib.util.spec_from_file_location("benchmark_live_btcusd_h1_step_ladder", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class LiveBTCUSDH1StepLadderTests(unittest.TestCase):
    def test_parse_steps_dedupes_and_preserves_order(self) -> None:
        self.assertEqual(benchmark.parse_steps("30, 45,45, 75"), [30.0, 45.0, 75.0])

    def test_parse_steps_rejects_nonpositive(self) -> None:
        with self.assertRaises(ValueError):
            benchmark.parse_steps("45, 0")

    def test_rank_step_rows_prefers_higher_marked_net_then_realized(self) -> None:
        rows = [
            {"step": 45.0, "marked_net_usd": 10.0, "realized_net_usd": 9.0, "open_count": 5},
            {"step": 50.0, "marked_net_usd": 15.0, "realized_net_usd": 4.0, "open_count": 7},
            {"step": 60.0, "marked_net_usd": 10.0, "realized_net_usd": 11.0, "open_count": 6},
        ]
        ranked = benchmark.rank_step_rows(rows)
        self.assertEqual([row["step"] for row in ranked], [50.0, 60.0, 45.0])


if __name__ == "__main__":
    unittest.main()
