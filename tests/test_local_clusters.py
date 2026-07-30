from __future__ import annotations

import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "pipeline" / "Verknuepfung" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from local_clap import aggregate_scores  # noqa: E402
from local_cluster_model import classify_cluster  # noqa: E402
from panns_classify import CAT_MAP  # noqa: E402

SCRIPT = SCRIPTS / "09_local_clusters.py"
spec = importlib.util.spec_from_file_location("local_clusters", SCRIPT)
local_clusters = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(local_clusters)


LABELS = [
    "Vehicle",
    "Train",
    "Jackhammer",
    "Speech",
    "Wind",
    "Drill",
    "Heavy engine (low frequency)",
]


def matrix(**columns: float) -> np.ndarray:
    values = np.zeros((8, len(LABELS)), dtype=np.float32)
    for label, probability in columns.items():
        values[:, LABELS.index(label)] = probability
    return values


class LocalClusterModelTests(unittest.TestCase):
    def test_rail_labels_never_mean_train_or_drilling_rig(self) -> None:
        rail_labels = [
            "Train",
            "Rail transport",
            "Railroad car, train wagon",
            "Subway, metro, underground",
            "Train wheels squealing",
        ]

        self.assertTrue(
            all(
                CAT_MAP[label] == "Schweres Baugeraet/sonstige Maschine"
                for label in rail_labels
            )
        )
        self.assertEqual("Fahrzeug", CAT_MAP["Truck"])

    def test_train_is_contextualised_as_construction_machine(self) -> None:
        result = classify_cluster(
            matrix(Vehicle=0.35, Train=0.70),
            LABELS,
            np.full(8, 0.80, dtype=np.float32),
            min_confidence=0.60,
        )

        self.assertEqual("Schweres Baugeraet/sonstige Maschine", result["candidate"])
        self.assertEqual("Schweres Baugeraet/sonstige Maschine", result["label"])
        self.assertEqual("automatisch", result["status"])

    def test_road_vehicle_stays_vehicle(self) -> None:
        result = classify_cluster(
            matrix(Vehicle=0.70, Train=0.05),
            LABELS,
            np.full(8, 0.80, dtype=np.float32),
            min_confidence=0.60,
        )

        self.assertEqual("Fahrzeug", result["candidate"])
        self.assertEqual("Fahrzeug", result["label"])
        self.assertEqual("automatisch", result["status"])

    def test_clear_jackhammer_cluster_is_automatic(self) -> None:
        result = classify_cluster(
            matrix(Jackhammer=0.82, Wind=0.03),
            LABELS,
            np.full(8, 0.75, dtype=np.float32),
            min_confidence=0.60,
        )

        self.assertEqual("Schlagen/Bohren", result["label"])
        self.assertEqual("automatisch", result["status"])

    def test_mixed_cluster_is_sent_to_review(self) -> None:
        values = np.zeros((8, len(LABELS)), dtype=np.float32)
        values[:4, LABELS.index("Vehicle")] = 0.80
        values[4:, LABELS.index("Speech")] = 0.80

        result = classify_cluster(
            values,
            LABELS,
            np.full(8, 0.30, dtype=np.float32),
        )

        self.assertEqual("Unklar/Mischgeraeusch", result["label"])
        self.assertEqual("pruefen", result["status"])

    def test_heavy_equipment_requires_audio_composite(self) -> None:
        engine_only = classify_cluster(
            matrix(**{"Heavy engine (low frequency)": 0.75}),
            LABELS,
            np.full(8, 0.80, dtype=np.float32),
            min_confidence=0.60,
        )
        composite = classify_cluster(
            matrix(Drill=0.65, **{"Heavy engine (low frequency)": 0.70}),
            LABELS,
            np.full(8, 0.80, dtype=np.float32),
            min_confidence=0.60,
        )

        self.assertEqual("Motor/Diesel", engine_only["candidate"])
        self.assertEqual("Bohrgeraet/schweres Geraet", composite["candidate"])


class LocalClapAggregationTests(unittest.TestCase):
    def test_clear_construction_tool_scores_are_automatic(self) -> None:
        categories = ["Fahrzeug", "Schlagen/Bohren", "Umgebung/Sonstiges"]
        scores = np.asarray(
            [[0.08, 0.84, 0.08], [0.10, 0.80, 0.10], [0.12, 0.78, 0.10]],
            dtype=np.float32,
        )

        result = aggregate_scores(
            scores,
            categories,
            panns_candidate="Schlagen/Bohren",
        )

        self.assertEqual("Schlagen/Bohren", result["label"])
        self.assertEqual("automatisch", result["status"])

    def test_disagreeing_representatives_are_sent_to_review(self) -> None:
        categories = ["Fahrzeug", "Schlagen/Bohren", "Umgebung/Sonstiges"]
        scores = np.asarray(
            [[0.80, 0.10, 0.10], [0.10, 0.80, 0.10], [0.10, 0.10, 0.80]],
            dtype=np.float32,
        )

        result = aggregate_scores(scores, categories)

        self.assertEqual("Unklar/Mischgeraeusch", result["label"])
        self.assertEqual("pruefen", result["status"])

    def test_drilling_rig_requires_unanimous_representatives(self) -> None:
        categories = ["Bohrgeraet/schweres Geraet", "Motor/Diesel", "Fahrzeug"]
        scores = np.asarray(
            [[0.70, 0.20, 0.10], [0.70, 0.20, 0.10], [0.20, 0.70, 0.10]],
            dtype=np.float32,
        )

        result = aggregate_scores(scores, categories, min_confidence=0.60)

        self.assertEqual("Unklar/Mischgeraeusch", result["label"])
        self.assertEqual("Bohrgeraet/schweres Geraet", result["candidate"])

    def test_drilling_rig_requires_stronger_margin(self) -> None:
        categories = ["Bohrgeraet/schweres Geraet", "Motor/Diesel", "Fahrzeug"]
        scores = np.asarray(
            [[0.36, 0.33, 0.31], [0.35, 0.32, 0.33], [0.34, 0.33, 0.33]],
            dtype=np.float32,
        )

        result = aggregate_scores(
            scores,
            categories,
            panns_candidate="Bohrgeraet/schweres Geraet",
        )

        self.assertEqual("Unklar/Mischgeraeusch", result["label"])
        self.assertEqual("Bohrgeraet/schweres Geraet", result["candidate"])


class LocalClusterApplyTests(unittest.TestCase):
    def test_apply_respects_date_and_manual_label(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "events.csv"
            fields = [
                "Datum",
                "WAV",
                "Laermquelle_geprueft",
                "Laermquelle_Cluster",
            ]
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "Datum": "2026-07-07",
                            "WAV": "before.wav",
                            "Laermquelle_geprueft": "",
                            "Laermquelle_Cluster": "alt",
                        },
                        {
                            "Datum": "2026-07-08",
                            "WAV": "auto.wav",
                            "Laermquelle_geprueft": "",
                            "Laermquelle_Cluster": "",
                        },
                        {
                            "Datum": "2026-07-09",
                            "WAV": "manual.wav",
                            "Laermquelle_geprueft": "Sprache",
                            "Laermquelle_Cluster": "",
                        },
                    ]
                )
            result = {
                "label": "Fahrzeug",
                "candidate": "Fahrzeug",
                "confidence": 0.91,
                "status": "automatisch",
                "top_audioset": ["Vehicle 0.70"],
            }

            applied, conflicts, changed = local_clusters.apply_local_results(
                event_path=path,
                from_date="2026-07-08",
                wav_to_cluster={
                    "auto.wav": "LC001",
                    "manual.wav": "LC001",
                },
                results={"LC001": result},
            )
            with path.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(1, applied)
            self.assertEqual(1, conflicts)
            self.assertTrue(changed)
            self.assertEqual("alt", rows[0]["Laermquelle_Cluster"])
            self.assertEqual("Fahrzeug", rows[1]["Laermquelle_Cluster"])
            self.assertEqual("", rows[2]["Laermquelle_Cluster"])
            self.assertEqual("LC001", rows[2]["Cluster_ID"])

            _, _, changed_again = local_clusters.apply_local_results(
                event_path=path,
                from_date="2026-07-08",
                wav_to_cluster={
                    "auto.wav": "LC001",
                    "manual.wav": "LC001",
                },
                results={"LC001": result},
            )
            self.assertFalse(changed_again)


if __name__ == "__main__":
    unittest.main()
