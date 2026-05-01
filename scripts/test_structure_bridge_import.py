import unittest
import sys
from pathlib import Path

# Add scripts directory to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

class TestStructureBridgeImport(unittest.TestCase):
    def test_detector_exports(self):
        """Verify price_structure_detector has the required exports."""
        try:
            from price_structure_detector import detect_structure, structure_to_geometry
            print("SUCCESS: price_structure_detector exports are present.")
        except ImportError as e:
            self.fail(f"price_structure_detector is missing required exports: {e}")

    def test_bridge_import(self):
        """Verify structure_shapeshifter_bridge can be imported without error."""
        try:
            import structure_shapeshifter_bridge
            print("SUCCESS: structure_shapeshifter_bridge imported cleanly.")
        except ImportError as e:
            self.fail(f"structure_shapeshifter_bridge failed to import: {e}")

if __name__ == "__main__":
    unittest.main()
