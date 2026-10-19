"""Regel 1+2: Quellen nur lesen und unverändert nach originals/ kopieren (Hash vor/nach)."""

import csv
import os
import re
import shutil
from dataclasses import dataclass, fields
from pathlib import Path

from .common import sha256_file

# Windows-Dateiattribute von Cloud-Platzhaltern (Google Drive für Desktop, OneDrive)
_ONLINE_ONLY_MASK = 0x400000 | 0x40000 | 0x1000  # RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE
_HASH_PATTERN = re.compile(r"\b[0-9a-fA-F]{64}\b")


@dataclass
class CopyResult:
    pfad: str
    groesse: int
    sha256_quelle: str
    sha256_kopie: str
    status: str  # OK | HASH_ABWEICHUNG | NUR_ONLINE | FEHLER
    im_manifest: str  # JA | NEIN | KEIN_MANIFEST
    hinweis: str = ""


def is_online_only(path: Path) -> bool:
    return bool(getattr(os.stat(path), "st_file_attributes", 0) & _ONLINE_ONLY_MASK)


def load_manifest_hashes(manifest_files: list[Path]) -> set[str]:
    """Liest beliebige Phase-A-Manifeste. Das Spaltenformat ist unbekannt, deshalb zählt jeder
    64-stellige Hex-Wert in der Datei als bekannter SHA-256."""
    hashes: set[str] = set()
    for manifest in manifest_files:
        with open(manifest, encoding="utf-8-sig", errors="replace") as handle:
            for line in handle:
                hashes.update(h.lower() for h in _HASH_PATTERN.findall(line))
    return hashes


def _inside(child: Path, parent: Path) -> bool:
    return parent.resolve() in child.resolve().parents or child.resolve() == parent.resolve()


def copy_tree(source: Path, destination: Path, manifest_hashes: set[str] | None = None) -> list[CopyResult]:
    """Kopiert rekursiv. Die Quelle wird ausschließlich mit open(..., 'rb') gelesen."""
    if _inside(destination, source) or _inside(source, destination):
        raise ValueError("Quelle und Ziel dürfen sich nicht überlappen (Regel 1: nie in die Quelle schreiben)")
    results: list[CopyResult] = []
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        relative = path.relative_to(source)
        target = destination / relative
        size = path.stat().st_size
        try:
            if is_online_only(path):
                results.append(CopyResult(str(relative), size, "", "", "NUR_ONLINE", "KEIN_MANIFEST",
                                          "Datei ist nur online verfügbar - bitte offline verfügbar machen"))
                continue
            hash_source = sha256_file(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise FileExistsError(f"Ziel existiert bereits: {target}")
            shutil.copyfile(path, target)
            hash_copy = sha256_file(target)
            known = "KEIN_MANIFEST" if manifest_hashes is None else ("JA" if hash_source in manifest_hashes else "NEIN")
            status = "OK" if hash_source == hash_copy else "HASH_ABWEICHUNG"
            results.append(CopyResult(str(relative), size, hash_source, hash_copy, status, known))
        except OSError as error:
            results.append(CopyResult(str(relative), size, "", "", "FEHLER", "KEIN_MANIFEST", str(error)))
    return results


def write_results(results: list[CopyResult], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow([f.name for f in fields(CopyResult)])
        for result in results:
            writer.writerow([getattr(result, f.name) for f in fields(CopyResult)])
