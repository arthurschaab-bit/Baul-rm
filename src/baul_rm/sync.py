from __future__ import annotations

import datetime as dt
import errno
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence


RAW_PATTERNS = ("20??-??-?? *.csv", "Laermprotokoll_*.zip")
REPORT_IMAGE_DIRS = ("Fotos_Aufbau", "Lageplan")
REPORT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


BOOTSTRAP_FILES = (
    "Verknuepfung/master_index.csv",
    "Verknuepfung/relevante_ereignisse.csv",
    "Verknuepfung/episoden.csv",
    "Verknuepfung/dauerlaerm.csv",
    "Verknuepfung/tiefbohrer_spans.csv",
    "Verknuepfung/Laermquellen_Verknuepfung.xlsx",
    "Verknuepfung/Laermquellen_Verknuepfung_backup.xlsx",
    "Verknuepfung/panns_probs.npy",
    "Verknuepfung/panns_probs_index.json",
    "Verknuepfung/manifest_hash_cache.json",
    "Aufbereit_v2/automation_state_v8.json",
)

CONTROL_FILES = (
    "Verknuepfung/messpositionen.csv",
    "Verknuepfung/belege.csv",
    "Verknuepfung/Laermquellen_Verknuepfung_backup.xlsx",
)

PUBLISH_FILES = (
    "Verknuepfung/master_index.csv",
    "Verknuepfung/relevante_ereignisse.csv",
    "Verknuepfung/episoden.csv",
    "Verknuepfung/dauerlaerm.csv",
    "Verknuepfung/tiefbohrer_spans.csv",
    "Verknuepfung/Laermquellen_Verknuepfung.xlsx",
    "Verknuepfung/messpositionen.csv",
    "Verknuepfung/panns_probs.npy",
    "Verknuepfung/panns_probs_index.json",
    "Verknuepfung/manifest_hash_cache.json",
    "Aufbereit_v2/automation_state_v10.json",
    "Aufbereit_v2/pipeline_config_v10.runtime.json",
)

PUBLISH_GLOBS = (
    "Aufbereit/*.pdf",
    "Aufbereit_v2/*.pdf",
    "Aufbereit_v2/*.csv",
    "Aufbereit_v2/*.md",
    "Aufbereit_v2/Laermquellen/*.pdf",
    "Aufbereit_v2/Dauerlaermtabelle/*.pdf",
    "Aufbereit_v2/autolauf_v10/*.md",
    "Aufbereit_v2/OpenAI_Cluster_*/*.csv",
    "Aufbereit_v2/OpenAI_Cluster_*/*.json",
    "Aufbereit_v2/OpenAI_Cluster_*/api_cache/*.json",
)

FAILURE_LOG_GLOBS = ("Aufbereit_v2/autolauf_v10/*.md",)


@dataclass
class SyncStats:
    copied: int = 0
    skipped: int = 0
    quarantined: int = 0
    bytes_copied: int = 0
    changed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_copy(self, relative: Path, size: int) -> None:
        self.copied += 1
        self.bytes_copied += size
        self.changed.append(relative.as_posix())

    def merge(self, other: "SyncStats") -> None:
        self.copied += other.copied
        self.skipped += other.skipped
        self.quarantined += other.quarantined
        self.bytes_copied += other.bytes_copied
        self.changed.extend(other.changed)
        self.warnings.extend(other.warnings)


def _same_file(source: Path, destination: Path) -> bool:
    if not destination.is_file():
        return False
    src = source.stat()
    dst = destination.stat()
    return src.st_size == dst.st_size and src.st_mtime_ns == dst.st_mtime_ns


def atomic_copy(source: Path, destination: Path, *, dry_run: bool = False) -> int:
    size = source.stat().st_size
    if dry_run:
        return size
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part-{os.getpid()}")
    try:
        if temporary.exists():
            temporary.unlink()
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return size


def copy_if_changed(
    source: Path,
    destination: Path,
    relative: Path,
    *,
    dry_run: bool = False,
) -> SyncStats:
    stats = SyncStats()
    if _same_file(source, destination):
        stats.skipped = 1
        return stats
    size = atomic_copy(source, destination, dry_run=dry_run)
    stats.add_copy(relative, size)
    return stats


def iter_tree_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


def sync_tree(source_root: Path, destination_root: Path, *, dry_run: bool = False) -> SyncStats:
    stats = SyncStats()
    for source in iter_tree_files(source_root):
        relative = source.relative_to(source_root)
        stats.merge(
            copy_if_changed(
                source,
                destination_root / relative,
                relative,
                dry_run=dry_run,
            )
        )
    return stats

def sync_report_images(
    photo_root: Path,
    destination_root: Path,
    *,
    dry_run: bool = False,
) -> SyncStats:
    """Spiegelt nur die kleinen Bildbestaende fuer Messaufbau und Lageplan."""
    stats = SyncStats()
    if not photo_root.is_dir():
        stats.warnings.append(f"Bildordner nicht gefunden: {photo_root}")
        return stats

    for directory in REPORT_IMAGE_DIRS:
        source_dir = photo_root / directory
        if not source_dir.is_dir():
            stats.warnings.append(f"Bildunterordner nicht gefunden: {source_dir}")
            continue
        for source in iter_tree_files(source_dir):
            if source.suffix.lower() not in REPORT_IMAGE_EXTENSIONS:
                continue
            relative = Path(directory) / source.relative_to(source_dir)
            stats.merge(
                copy_if_changed(
                    source,
                    destination_root / relative,
                    relative,
                    dry_run=dry_run,
                )
            )
    return stats


def _glob_map(root: Path, patterns: Sequence[str]) -> dict[Path, Path]:
    found: dict[Path, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                found[path.relative_to(root)] = path
    return found


def sync_raw_files(
    cloud_root: Path,
    runtime_root: Path,
    *,
    prune: bool = True,
    dry_run: bool = False,
) -> SyncStats:
    stats = SyncStats()
    cloud_files = _glob_map(cloud_root, RAW_PATTERNS)
    runtime_files = _glob_map(runtime_root, RAW_PATTERNS)

    for relative, source in sorted(cloud_files.items(), key=lambda item: item[0].as_posix()):
        stats.merge(
            copy_if_changed(
                source,
                runtime_root / relative,
                relative,
                dry_run=dry_run,
            )
        )

    stale = sorted(set(runtime_files) - set(cloud_files), key=lambda p: p.as_posix())
    if stale and not prune:
        stats.warnings.append(
            f"{len(stale)} lokale Rohdatei(en) fehlen in der Cloud; --no-prune laesst sie aktiv."
        )
        return stats

    if stale:
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        quarantine = runtime_root / ".baul-rm" / "quarantine" / stamp
        for relative in stale:
            stats.quarantined += 1
            stats.changed.append(f"ENTFERNT: {relative.as_posix()}")
            if not dry_run:
                target = quarantine / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(runtime_root / relative, target)
    return stats


def bootstrap_files(
    cloud_root: Path,
    runtime_root: Path,
    *,
    dry_run: bool = False,
) -> SyncStats:
    stats = SyncStats()
    for item in BOOTSTRAP_FILES:
        relative = Path(item)
        source = cloud_root / relative
        destination = runtime_root / relative
        if not source.is_file() or destination.exists():
            stats.skipped += 1
            continue
        size = atomic_copy(source, destination, dry_run=dry_run)
        stats.add_copy(relative, size)
    return stats


def sync_control_files(
    cloud_root: Path,
    runtime_root: Path,
    *,
    dry_run: bool = False,
) -> SyncStats:
    stats = SyncStats()
    for item in CONTROL_FILES:
        relative = Path(item)
        source = cloud_root / relative
        destination = runtime_root / relative
        if not source.is_file():
            continue
        if destination.exists() and destination.stat().st_mtime_ns > source.stat().st_mtime_ns:
            stats.skipped += 1
            stats.warnings.append(
                f"Lokale Datei ist neuer und wurde nicht ueberschrieben: {relative.as_posix()}"
            )
            continue
        stats.merge(copy_if_changed(source, destination, relative, dry_run=dry_run))
    return stats


def _unique_publish_paths(runtime_root: Path, success: bool) -> Iterable[Path]:
    found: set[Path] = set()
    if success:
        for item in PUBLISH_FILES:
            path = runtime_root / item
            if path.is_file():
                found.add(path)
        patterns = PUBLISH_GLOBS
    else:
        patterns = FAILURE_LOG_GLOBS
    for pattern in patterns:
        for path in runtime_root.glob(pattern):
            if path.is_file():
                found.add(path)
    return sorted(found, key=lambda p: p.as_posix())


def publish_outputs(
    runtime_root: Path,
    cloud_root: Path,
    *,
    success: bool,
    dry_run: bool = False,
) -> SyncStats:
    stats = SyncStats()
    for source in _unique_publish_paths(runtime_root, success):
        relative = source.relative_to(runtime_root)
        stats.merge(
            copy_if_changed(
                source,
                cloud_root / relative,
                relative,
                dry_run=dry_run,
            )
        )
    return stats


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.OpenProcess(process_query, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


class RunLock:
    def __init__(self, lock_path: Path, stale_after_hours: int = 24) -> None:
        self.path = lock_path
        self.stale_after = dt.timedelta(hours=stale_after_hours)
        self.acquired = False

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = dt.datetime.now() - dt.datetime.fromtimestamp(self.path.stat().st_mtime)
            details = self.path.read_text(encoding="utf-8", errors="replace").strip()
            try:
                payload = json.loads(details)
                pid = payload.get("pid") if isinstance(payload, dict) else None
            except json.JSONDecodeError:
                pid = None
            active = isinstance(pid, int) and _pid_is_running(pid)
            if active or (pid is None and age <= self.stale_after):
                raise RuntimeError(
                    f"Ein anderer Lauf ist bereits aktiv ({self.path}; {details or 'keine Details'})."
                )
            stale = self.path.with_name(
                f"{self.path.name}.stale-{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
            )
            os.replace(self.path, stale)

        payload = {
            "pid": os.getpid(),
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        self.acquired = True
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.acquired and self.path.exists():
            self.path.unlink()
