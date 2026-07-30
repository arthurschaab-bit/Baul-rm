from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "pipeline"
    / "Verknuepfung"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from panns_cache import (  # noqa: E402
    load_probability_cache,
    save_probability_cache,
    unique_pending_rows,
)
import panns_classify as pc  # noqa: E402


class PannsCacheTests(unittest.TestCase):
    def test_pending_rows_are_unique_per_wav(self) -> None:
        rows = [
            {"WAV": "a.wav", "WAV_Pfad": "first/a.wav"},
            {"WAV": "a.wav", "WAV_Pfad": "duplicate/a.wav"},
            {"WAV": "b.wav", "WAV_Pfad": "b.wav"},
            {"WAV": "cached.wav", "WAV_Pfad": "cached.wav"},
        ]

        pending = unique_pending_rows(rows, {"cached.wav"})

        self.assertEqual(["a.wav", "b.wav"], [row["WAV"] for row in pending])
        self.assertEqual("first/a.wav", pending[0]["WAV_Pfad"])

    def test_cache_round_trip_and_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            npy = tmp / "panns_probs.npy"
            index = tmp / "panns_probs_index.json"
            probabilities = {
                "a.wav": np.full(527, 0.25, dtype=np.float32),
                "b.wav": np.full(527, 0.75, dtype=np.float32),
            }

            count = save_probability_cache(
                probabilities,
                npy,
                index,
                keep_names=["b.wav", "b.wav"],
            )
            loaded = load_probability_cache(npy, index)

            self.assertEqual(1, count)
            self.assertEqual(["b.wav"], list(loaded))
            np.testing.assert_array_equal(probabilities["b.wav"], loaded["b.wav"])

    def test_inconsistent_cache_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            npy = tmp / "panns_probs.npy"
            index = tmp / "panns_probs_index.json"
            np.save(npy, np.zeros((2, 527), dtype=np.float32))
            index.write_text('["only-one.wav"]', encoding="utf-8")

            with self.assertRaises(ValueError):
                load_probability_cache(npy, index)

    def test_batch_inference_preserves_order_and_values(self) -> None:
        class FakeModel:
            def inference(self, batch):
                return batch[:, :527], None

        previous_at = pc._AT
        previous_labels = pc._LABELS
        previous_idx = pc._CAT_IDX
        pc._AT = FakeModel()
        pc._LABELS = []
        pc._CAT_IDX = {}
        try:
            clips = [
                np.arange(527, dtype=np.float32),
                np.arange(527, dtype=np.float32) + 1000,
            ]
            output = pc.infer_probs_batch(clips, 32000)
            np.testing.assert_array_equal(output, np.stack(clips))
        finally:
            pc._AT = previous_at
            pc._LABELS = previous_labels
            pc._CAT_IDX = previous_idx


if __name__ == "__main__":
    unittest.main()
