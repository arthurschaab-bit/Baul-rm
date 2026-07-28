from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from baul_rm.cli import _check_disk_space, _repair_text_tree


class CliTests(unittest.TestCase):
    def test_disk_preflight_rejects_impossible_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _check_disk_space(root, 0, 0)
            with self.assertRaises(RuntimeError):
                _check_disk_space(root, 10**30, 0)

    def test_repairs_mojibake_in_nested_report_values(self) -> None:
        correct_city = "M\u00fcnchen"
        broken_city = correct_city.encode("utf-8").decode("cp1252")
        double_broken_city = broken_city.encode("utf-8").decode("cp1252")
        value = {"address": double_broken_city, "setup_groups": [{"label": broken_city}]}

        repaired = _repair_text_tree(value)

        self.assertEqual(repaired["address"], correct_city)
        self.assertEqual(repaired["setup_groups"][0]["label"], correct_city)



if __name__ == "__main__":
    unittest.main()
