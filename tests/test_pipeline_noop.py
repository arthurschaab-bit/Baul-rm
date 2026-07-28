from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class PipelineNoopTests(unittest.TestCase):
    def test_unchanged_state_exits_without_downstream_work(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        source_scripts = repo / "pipeline" / "Verknuepfung" / "scripts"
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            scripts = runtime / "Verknuepfung" / "scripts"
            scripts.mkdir(parents=True)
            for name in ("auto_pipeline_v8.py", "auto_pipeline_v10.py"):
                shutil.copy2(source_scripts / name, scripts / name)

            raw = runtime / "2026-07-28 07-00-00.csv"
            raw.write_text("header only\n", encoding="utf-8")
            stat = raw.stat()
            state_dir = runtime / "Aufbereit_v2"
            state_dir.mkdir()
            state = {
                "version": "v10",
                "last_run": "2026-07-28T12:00:00",
                "raw_files": [
                    {
                        "path": raw.name,
                        "name": raw.name,
                        "bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                ],
                "control_files": [],
                "pending_audio": [],
            }
            (state_dir / "automation_state_v10.json").write_text(
                json.dumps(state), encoding="utf-8"
            )

            result = subprocess.run(
                [sys.executable, str(scripts / "auto_pipeline_v10.py")],
                cwd=runtime,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Keine Aenderung erkannt", result.stdout)
            self.assertFalse((runtime / "Verknuepfung" / "master_index.csv").exists())


if __name__ == "__main__":
    unittest.main()
