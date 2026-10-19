"""Schritt 3: Inventar aller Dateien nach Magic Bytes, Archive rekursiv in work/ entpacken."""

import csv
import json
import zipfile
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .common import sha256_file

MAX_DEPTH = 5
MAX_UNPACKED_BYTES = 40 * 1024**3  # Schutz vor Zip-Bomben

SQLITE_MAGIC = b"SQLite format 3\x00"
WAL_MAGICS = (b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83")
JOURNAL_MAGIC = b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"


def detect_type(path: Path) -> str:
    size = path.stat().st_size
    if size == 0:
        return "LEER"
    with open(path, "rb") as handle:
        head = handle.read(4096)
    if head.startswith(SQLITE_MAGIC):
        return "SQLITE"
    if head[:4] in WAL_MAGICS:
        return "SQLITE_WAL"
    if head.startswith(JOURNAL_MAGIC):
        return "SQLITE_JOURNAL"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return "ZIP"
    if head[:2] == b"\x1f\x8b":
        return "GZIP"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "AUDIO_WAV"
    try:
        text = head.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "BINAER_UNBEKANNT"  # u. a. mögliches PCE-Geräteformat - braucht ein Musterexemplar
    stripped = text.lstrip()
    if stripped[:1] in "{[":
        if size >= 50 * 1024 * 1024:
            return "JSON"
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
            return "JSON"
        except ValueError:
            return "TEXT"
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if first_line.count(";") >= 2 or first_line.count(",") >= 2:
        return "CSV"
    return "TEXT"


@dataclass
class InventoryRow:
    runde: int
    pfad: str
    herkunft_archiv: str
    groesse: int
    geaendert_utc: str
    sha256: str
    typ: str
    duplikat_von: str = ""
    status: str = "offen"
    grund: str = ""


@dataclass
class _State:
    rows: list[InventoryRow] = field(default_factory=list)
    first_by_hash: dict[str, str] = field(default_factory=dict)
    unpacked_bytes: int = 0


def _safe_member(name: str) -> PurePosixPath | None:
    member = PurePosixPath(name.replace("\\", "/"))
    if member.is_absolute() or ".." in member.parts or not member.name:
        return None
    return member


def _add(state: _State, runde: int, path: Path, shown: str, archive: str) -> InventoryRow:
    stat = path.stat()
    digest = sha256_file(path)
    kind = detect_type(path)
    row = InventoryRow(runde, shown, archive, stat.st_size, datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                       digest, kind)
    if digest in state.first_by_hash and stat.st_size > 0:
        row.duplikat_von = state.first_by_hash[digest]
        row.status = "Duplikat"
    else:
        state.first_by_hash.setdefault(digest, shown)
        if kind == "LEER":
            row.status, row.grund = "defekt/unlesbar", "0 Byte"
        elif kind == "AUDIO_WAV":
            row.status, row.grund = "kein Messinhalt", "Audio nur im Inventar (Regel 8)"
    state.rows.append(row)
    return row


def _unpack(state: _State, runde: int, archive: Path, shown: str, row: InventoryRow, work: Path, depth: int) -> None:
    target_root = work / "entpackt" / row.sha256[:16]
    try:
        with zipfile.ZipFile(archive) as bundle:
            bad = bundle.testzip()
            if bad is not None:
                row.status, row.grund = "defekt/unlesbar", f"ZIP-Test fehlgeschlagen bei {bad}"
                return
            for info in bundle.infolist():
                if info.is_dir():
                    continue
                member = _safe_member(info.filename)
                if member is None:
                    row.grund += f" unsicherer Eintrag übersprungen: {info.filename};"
                    continue
                state.unpacked_bytes += info.file_size
                if state.unpacked_bytes > MAX_UNPACKED_BYTES:
                    row.status, row.grund = "defekt/unlesbar", "Entpackungsgrenze überschritten"
                    return
                out = target_root.joinpath(*member.parts)
                out.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as src, open(out, "wb") as dst:
                    while block := src.read(1 << 20):
                        dst.write(block)
                inner_shown = f"{shown}!/{member}"
                inner = _add(state, runde, out, inner_shown, shown)
                if inner.typ == "ZIP" and not inner.duplikat_von and depth < MAX_DEPTH:
                    _unpack(state, runde, out, inner_shown, inner, work, depth + 1)
        if not row.grund:
            row.status = "Archiv entpackt"
    except (zipfile.BadZipFile, OSError, RuntimeError) as error:
        row.status, row.grund = "defekt/unlesbar", f"{type(error).__name__}: {error}"


def build_inventory(originals: Path, work: Path, runde: int) -> list[InventoryRow]:
    state = _State()
    for path in sorted(p for p in originals.rglob("*") if p.is_file()):
        shown = str(path.relative_to(originals)).replace("\\", "/")
        row = _add(state, runde, path, shown, "")
        if row.typ == "ZIP" and not row.duplikat_von:
            _unpack(state, runde, path, shown, row, work, 1)
    return state.rows


def apply_evaluated(rows: list[InventoryRow], evaluated_hashes: dict[str, str]) -> None:
    """Setzt Status 'ausgewertet' für Dateien, die ein Auswertelauf (sha256 -> Methode) verarbeitet hat."""
    for row in rows:
        if row.status == "offen" and row.sha256 in evaluated_hashes:
            row.status, row.grund = "ausgewertet", evaluated_hashes[row.sha256]


def write_inventory(rows: list[InventoryRow], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow([f.name for f in fields(InventoryRow)])
        for row in rows:
            writer.writerow([getattr(row, f.name) for f in fields(InventoryRow)])
