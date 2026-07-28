from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from baul_rm.cli import _check_disk_space


class CliTests(unittest.TestCase):
    def test_disk_preflight_rejects_impossible_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _check_disk_space(root, 0, 0)
            with self.assertRaises(RuntimeError):
                _check_disk_space(root, 10**30, 0)


if __name__ == "__main__":
    unittest.main()
