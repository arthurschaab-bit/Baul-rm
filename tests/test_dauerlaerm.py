from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "pipeline" / "Verknuepfung" / "scripts" / "05_dauerlaerm_v7.py"
spec = importlib.util.spec_from_file_location("dauerlaerm_v7", SCRIPT)
dauerlaerm = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(dauerlaerm)


class DauerlaermSourceTests(unittest.TestCase):
    def test_current_ki_replaces_stale_automatic_source(self) -> None:
        sample = [{"wav": "clip.wav", "dba": 78.0, "day": "2026-07-20"}]
        current = {
            "clip.wav": {
                "Laermquelle_KI": "Schweres Baugeraet/sonstige Maschine"
            }
        }
        old = {
            "Laermquelle_Auto": "Bohrgeraet/schweres Geraet",
            "Laermquelle_geprueft": "Bagger",
            "Quellen_Detail": "alt",
        }

        dom, checked, detail, _, _, reused = dauerlaerm.phase_source(
            sample, current, old
        )

        self.assertEqual("Schweres Baugeraet/sonstige Maschine", dom)
        self.assertEqual("Bagger", checked)
        self.assertIn("Schweres Baugeraet/sonstige Maschine", detail)
        self.assertFalse(reused)

    def test_old_automatic_source_is_only_a_missing_data_fallback(self) -> None:
        old = {
            "Laermquelle_Auto": "Motor/Diesel",
            "Laermquelle_geprueft": "",
            "Quellen_Detail": "Motor/Diesel 100%",
        }

        dom, _, detail, _, _, reused = dauerlaerm.phase_source([], {}, old)

        self.assertEqual("Motor/Diesel", dom)
        self.assertEqual("Motor/Diesel 100%", detail)
        self.assertTrue(reused)


if __name__ == "__main__":
    unittest.main()