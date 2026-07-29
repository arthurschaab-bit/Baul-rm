from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from baul_rm.sync import (
    RunLock,
    bootstrap_files,
    publish_outputs,
    sync_raw_files,
    sync_report_images,
)


def write_file(path: Path, content: bytes, mtime_ns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, ns=(mtime_ns, mtime_ns))


class SyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cloud = self.root / "cloud"
        self.runtime = self.root / "runtime"
        self.cloud.mkdir()
        self.runtime.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_raw_sync_is_incremental_and_quarantines_removed_files(self) -> None:
        source = self.cloud / "2026-07-28 07-00-00.csv"
        write_file(source, b"first", 1_700_000_000_000_000_000)

        first = sync_raw_files(self.cloud, self.runtime)
        self.assertEqual(first.copied, 1)
        self.assertEqual((self.runtime / source.name).read_bytes(), b"first")

        second = sync_raw_files(self.cloud, self.runtime)
        self.assertEqual(second.copied, 0)
        self.assertEqual(second.skipped, 1)

        write_file(source, b"second", 1_700_000_000_100_000_000)
        third = sync_raw_files(self.cloud, self.runtime)
        self.assertEqual(third.copied, 1)
        self.assertEqual((self.runtime / source.name).read_bytes(), b"second")

        source.unlink()
        fourth = sync_raw_files(self.cloud, self.runtime)
        self.assertEqual(fourth.quarantined, 1)
        self.assertFalse((self.runtime / source.name).exists())
        quarantined = list((self.runtime / ".baul-rm" / "quarantine").rglob(source.name))
        self.assertEqual(len(quarantined), 1)

    def test_bootstrap_never_overwrites_local_state(self) -> None:
        relative = Path("Verknuepfung/master_index.csv")
        write_file(self.cloud / relative, b"cloud", 1_700_000_000_000_000_000)
        write_file(self.runtime / relative, b"local", 1_700_000_000_100_000_000)

        result = bootstrap_files(self.cloud, self.runtime)
        self.assertEqual(result.copied, 0)
        self.assertEqual((self.runtime / relative).read_bytes(), b"local")

    def test_publish_outputs_ignores_raw_and_copies_report(self) -> None:
        raw = self.runtime / "Laermprotokoll_28.07.2026.zip"
        report = self.runtime / "Aufbereit_v2" / "report.pdf"
        write_file(raw, b"raw", 1_700_000_000_000_000_000)
        write_file(report, b"pdf", 1_700_000_000_000_000_000)

        result = publish_outputs(self.runtime, self.cloud, success=True)
        self.assertEqual(result.copied, 1)
        self.assertTrue((self.cloud / "Aufbereit_v2" / "report.pdf").exists())
        self.assertFalse((self.cloud / raw.name).exists())

    def test_report_image_sync_copies_only_required_image_folders(self) -> None:
        photos = self.root / "Fotos_Videos"
        write_file(photos / "Fotos_Aufbau" / "setup.jpg", b"jpg", 1_700_000_000_000_000_000)
        write_file(photos / "Fotos_Aufbau" / "video.mp4", b"video", 1_700_000_000_000_000_000)
        write_file(photos / "Lageplan" / "lage.png", b"png", 1_700_000_000_000_000_000)
        write_file(photos / "Schallmessvideos" / "large.mp4", b"large", 1_700_000_000_000_000_000)

        result = sync_report_images(photos, self.runtime / "Fotos_Videos")

        self.assertEqual(result.copied, 2)
        self.assertTrue((self.runtime / "Fotos_Videos" / "Fotos_Aufbau" / "setup.jpg").is_file())
        self.assertTrue((self.runtime / "Fotos_Videos" / "Lageplan" / "lage.png").is_file())
        self.assertFalse((self.runtime / "Fotos_Videos" / "Fotos_Aufbau" / "video.mp4").exists())
        self.assertFalse((self.runtime / "Fotos_Videos" / "Schallmessvideos").exists())

    def test_run_lock_blocks_a_second_run(self) -> None:
        lock_path = self.root / "locks" / "run.lock"
        with RunLock(lock_path):
            with self.assertRaises(RuntimeError):
                with RunLock(lock_path):
                    pass
        self.assertFalse(lock_path.exists())

    def test_run_lock_recovers_dead_pid_immediately(self) -> None:
        lock_path = self.root / "locks" / "run.lock"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text(json.dumps({"pid": 2147483647}), encoding="utf-8")
        with RunLock(lock_path):
            self.assertTrue(lock_path.exists())
            stale = list(lock_path.parent.glob("run.lock.stale-*"))
            self.assertEqual(len(stale), 1)
        self.assertFalse(lock_path.exists())



if __name__ == "__main__":
    unittest.main()
