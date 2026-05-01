import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

MODULE_PATH = Path(__file__).with_name("benchmark_live_btcusd_h1_rearm_gate.py")
SPEC = importlib.util.spec_from_file_location("benchmark_live_btcusd_h1_rearm_gate", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class DummyEngine:
    def __init__(self) -> None:
        self.momentum_gate = True

    def _momentum_gate_allows(self, direction: str, level: float, tick: dict[str, float]) -> bool:
        if str(direction or "").upper() == "SELL":
            return float(tick["bid"]) < float(level)
        return float(tick["ask"]) > float(level)


class LiveBTCUSDH1RearmGateBenchmarkTests(unittest.TestCase):
    def test_patch_buy_rearm_gate_makes_buy_check_consistent(self) -> None:
        engine = DummyEngine()
        self.assertFalse(engine._momentum_gate_allows("BUY", 100.0, {"ask": 99.5, "bid": 99.0}))
        self.assertTrue(engine._momentum_gate_allows("BUY", 100.0, {"ask": 100.5, "bid": 100.0}))

        benchmark.patch_buy_rearm_gate(engine)

        self.assertTrue(engine._momentum_gate_allows("BUY", 100.0, {"ask": 99.5, "bid": 99.0}))
        self.assertFalse(engine._momentum_gate_allows("BUY", 100.0, {"ask": 100.5, "bid": 100.0}))
        self.assertTrue(engine._momentum_gate_allows("SELL", 100.0, {"ask": 100.5, "bid": 99.0}))


if __name__ == "__main__":
    unittest.main()
