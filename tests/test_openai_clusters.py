from __future__ import annotations

import importlib.util
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "pipeline" / "Verknuepfung" / "scripts" / "09_openai_clusters.py"
spec = importlib.util.spec_from_file_location("openai_clusters", SCRIPT)
openai_clusters = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(openai_clusters)


class OpenAIClusterTests(unittest.TestCase):
    def test_features_and_clusters_are_deterministic(self) -> None:
        probabilities = np.zeros((30, 8), dtype=np.float32)
        probabilities[:15, 0:2] = 0.8
        probabilities[15:, 5:7] = 0.9

        first = openai_clusters.build_cluster_features(probabilities, projection_dim=6)
        second = openai_clusters.build_cluster_features(probabilities, projection_dim=6)
        self.assertTrue(np.allclose(first, second))
        self.assertTrue(np.allclose(np.linalg.norm(first, axis=1), 1.0))

        centers, labels, similarity = openai_clusters.cluster_features(
            first, k=2, fit_limit=30
        )
        self.assertEqual(centers.shape, (2, first.shape[1]))
        self.assertEqual(labels.shape, (30,))
        self.assertEqual(similarity.shape, (30,))
        self.assertEqual(len(set(labels[:15])), 1)
        self.assertEqual(len(set(labels[15:])), 1)
        self.assertNotEqual(labels[0], labels[-1])

    def test_result_normalisation_accepts_german_alias(self) -> None:
        result = openai_clusters._normalise_result(
            {
                "label": "Bohrger\u00e4t/schweres Ger\u00e4t",
                "confidence": 1.4,
                "homogeneous": True,
                "sample_labels": ["S\u00e4ge"],
                "secondary_sources": ["Verkehr"],
            },
            1,
        )
        self.assertEqual(result["label"], "Bohrgeraet/schweres Geraet")
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(result["sample_labels"], ["Saege"])

    def test_single_api_failure_is_converted_to_open_result(self) -> None:
        with patch.object(openai_clusters, "openai_audio_request", side_effect=RuntimeError("offline")):
            result = openai_clusters.safe_openai_audio_request()
        self.assertEqual(result["label"], "Unklar/Mischgeraeusch")
        self.assertIn("offline", result["summary"])

    def test_cluster_results_never_touch_days_before_start_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            event_path = Path(temp) / "relevante_ereignisse.csv"
            event_path.write_text(
                "Datum,WAV,Laermquelle_geprueft\n"
                "2026-07-07,old.wav,\n"
                "2026-07-08,a.wav,\n"
                "2026-07-09,b.wav,\n",
                encoding="utf-8-sig",
            )
            applied, conflicts = openai_clusters.apply_cluster_results(
                event_path=event_path,
                from_date="2026-07-08",
                model="gpt-audio-1.5",
                wav_to_cluster={"old.wav": "C000", "a.wav": "C000", "b.wav": "C000"},
                results={
                    "C000": {
                        "status": "ki_bestaetigt",
                        "label": "Fahrzeug",
                        "confidence": 0.91,
                    }
                },
            )
            self.assertEqual((applied, conflicts), (2, 0))
            rows = openai_clusters.read_event_rows(event_path, "0000-01-01")
            self.assertEqual(rows[0]["Laermquelle_Cluster"], "")
            self.assertEqual(rows[1]["Laermquelle_Cluster"], "Fahrzeug")
            self.assertEqual(rows[2]["Laermquelle_Cluster"], "Fahrzeug")
            selected = openai_clusters.read_event_rows(event_path, "2026-07-08")
            self.assertEqual([row["WAV"] for row in selected], ["a.wav", "b.wav"])


if __name__ == "__main__":
    unittest.main()
