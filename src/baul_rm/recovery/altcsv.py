"""Alt-CSV-Export der App (DriveCsv.kt): Parser und Zielmenge je Tagsekunde (Schritt 1)."""

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .common import DAY_WINDOW_SECONDS, day_window_ms

REQUIRED = {"Zeit", "LAeq_dB", "Samples", "Quelle"}
STATUS_BY_SOURCE = {"PCE_323": "PCE", "GEMISCHT": "GEMISCHT", "MIKROFON": "MIKROFON"}
# KEINE_VERBINDUNG / KEINE_AUFZEICHNUNG (und unbekannte Werte) zählen als "FEHLT"


@dataclass
class AltRow:
    start_ms: int
    laeq: float | None
    pegel: float | None
    samples: int
    quelle: str


def _decimal(value: str) -> float | None:
    value = value.strip()
    return float(value.replace(",", ".")) if value else None


def parse_alt_csv(path: Path) -> list[AltRow]:
    """Semikolon, Dezimalkomma, UTF-8 mit BOM. Wirft ValueError bei unbekanntem Kopf (z. B. Session_*.csv)."""
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if not reader.fieldnames or not REQUIRED <= set(reader.fieldnames):
            raise ValueError(f"{path.name}: Spalten {sorted(REQUIRED)} erwartet, gefunden {reader.fieldnames}")
        rows = []
        for record in reader:
            start = datetime.fromisoformat(record["Zeit"].strip())
            rows.append(AltRow(int(round(start.timestamp() * 1000)), _decimal(record["LAeq_dB"]),
                               _decimal(record.get("Pegel_dB", "")), int(record["Samples"] or 0), record["Quelle"].strip()))
    return rows


def window_seconds(rows: list[AltRow]) -> int:
    """Empirisches Aggregationsfenster: häufigster positiver Abstand benachbarter Zeilen (Code: Standard 1 s,
    einstellbar 1-60 s über drive_aggregation_seconds)."""
    starts = sorted(r.start_ms for r in rows)
    diffs = Counter(b - a for a, b in zip(starts, starts[1:]) if b > a)
    if not diffs:
        return 1
    return max(1, round(diffs.most_common(1)[0][0] / 1000))


def has_activity(rows: list[AltRow]) -> bool:
    return any(STATUS_BY_SOURCE.get(r.quelle) and r.samples > 0 for r in rows)


def day_targets(rows: list[AltRow], window_s: int, day: date) -> list[tuple[str, str]]:
    """46.800 Einträge (Status, Quelle der Alt-CSV) für 07:00-20:00; Status PCE|GEMISCHT|MIKROFON|FEHLT.
    Bei Fenstern > 1 s erbt jede Sekunde den Status ihres Fensters."""
    start_ms, end_ms = day_window_ms(day)
    result = [("FEHLT", "")] * DAY_WINDOW_SECONDS
    window_ms = window_s * 1000
    for row in rows:
        row_end = row.start_ms + window_ms
        if row_end <= start_ms or row.start_ms >= end_ms:
            continue
        status = STATUS_BY_SOURCE.get(row.quelle, "FEHLT") if row.samples > 0 else "FEHLT"
        first = max(row.start_ms, start_ms)
        last = min(row_end, end_ms)
        for second in range(-(-(first - start_ms) // 1000), -(-(last - start_ms) // 1000)):
            result[second] = (status, row.quelle)
    return result
