from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


class PipelineStateTests(unittest.TestCase):
    def test_portable_hash_cache_key_is_relative(self) -> None:
        scripts = (
            Path(__file__).resolve().parents[1]
            / "pipeline"
            / "Verknuepfung"
            / "scripts"
        )
        sys.path.insert(0, str(scripts))
        old_config = os.environ.get("SCHALLBERICHT_CONFIG")
        os.environ["SCHALLBERICHT_CONFIG"] = str(
            scripts / "pipeline_config_v10.json"
        )
        try:
            spec = importlib.util.spec_from_file_location(
                "gesamtbericht_lib_v4_test",
                scripts / "gesamtbericht_lib_v4.py",
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "sample.csv"
                path.write_text("data", encoding="utf-8")
                key = module._cache_key(str(path))
                self.assertFalse(Path(key).is_absolute())
        finally:
            if old_config is None:
                os.environ.pop("SCHALLBERICHT_CONFIG", None)
            else:
                os.environ["SCHALLBERICHT_CONFIG"] = old_config


if __name__ == "__main__":
    unittest.main()
