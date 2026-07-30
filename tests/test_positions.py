from __future__ import annotations

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "pipeline" / "Verknuepfung" / "scripts" / "auto_pipeline_v8.py"
spec = importlib.util.spec_from_file_location("auto_pipeline_v8_positions", SCRIPT)
pipeline = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(pipeline)


class PositionTests(unittest.TestCase):
    def test_only_undefined_outdoor_positions_become_so_balcony(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vk = Path(temp) / "Verknuepfung"
            vk.mkdir()
            path = vk / "messpositionen.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["Datum", "Umgebung", "Position", "Hinweis"],
                    delimiter=";",
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "Datum": "2026-06-29",
                        "Umgebung": "Aussen",
                        "Position": "Position noch zu dokumentieren",
                        "Hinweis": "Automatisch erkannt; Messposition bitte pruefen/ergaenzen.",
                    }
                )
                writer.writerow(
                    {
                        "Datum": "2026-05-21",
                        "Umgebung": "Innen",
                        "Position": "Innenraum (Zimmer unklar)",
                        "Hinweis": "kein Raumname dokumentiert",
                    }
                )

            with patch.object(pipeline, "VK", vk), patch.object(pipeline, "RUN_ID", "test"):
                pipeline.ensure_positions(["2026-05-21", "2026-06-29"])

            with path.open(newline="", encoding="utf-8-sig") as handle:
                rows = {row["Datum"]: row for row in csv.DictReader(handle, delimiter=";")}
            self.assertEqual(rows["2026-06-29"]["Position"], "SO-Balkon")
            self.assertEqual(rows["2026-06-29"]["Umgebung"], "Aussen")
            self.assertEqual(rows["2026-05-21"]["Position"], "Innenraum (Zimmer unklar)")


if __name__ == "__main__":
    unittest.main()
