from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "pipeline" / "Verknuepfung" / "scripts" / "tiefbohrer.py"
spec = importlib.util.spec_from_file_location("tiefbohrer", SCRIPT)
tiefbohrer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(tiefbohrer)


class TiefbohrerCutoffTests(unittest.TestCase):
    def test_rule_is_not_evaluated_from_20_july(self) -> None:
        with patch.object(tiefbohrer, "load_day_series") as loader:
            result = tiefbohrer.spans_for_day("2026-07-20", Path("unused"))
        self.assertEqual(result, [])
        loader.assert_not_called()

    def test_last_valid_day_still_uses_rule(self) -> None:
        with patch.object(tiefbohrer, "load_day_series", return_value=None) as loader:
            result = tiefbohrer.spans_for_day("2026-07-19", Path("unused"))
        self.assertEqual(result, [])
        loader.assert_called_once()


if __name__ == "__main__":
    unittest.main()
