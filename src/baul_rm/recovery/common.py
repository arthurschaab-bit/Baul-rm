"""Gemeinsame Konstanten und Hilfsfunktionen. Nur Standardbibliothek."""

import hashlib
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
SOURCE_PCE = "PCE_323"  # LevelSource.PCE_323 in LevelSampleEntity.kt
SOURCE_MIC = "MIKROFON"  # LevelSource.MIKROFON
DAY_START_HOUR = 7
DAY_END_HOUR = 20
DAY_WINDOW_SECONDS = (DAY_END_HOUR - DAY_START_HOUR) * 3600  # 46.800
TOLERANCE_DB = 0.15
PLAUSIBLE_MIN_DB = 50.0
PLAUSIBLE_MAX_DB = 100.0
CSV_ROUNDING_DB = 0.05  # Alt-CSV rundet auf 0,1 dB (String.format "%.1f" in DriveCsv.kt)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def energetic_mean(levels) -> float:
    """10*log10(Mittel(10^(L/10))) wie PegelAggregator.bildeZeile. Leere Eingabe ist ein Fehler."""
    values = list(levels)
    if not values:
        raise ValueError("energetic_mean: keine Werte")
    return 10.0 * math.log10(sum(10.0 ** (v / 10.0) for v in values) / len(values))


def day_window_ms(day: date) -> tuple[int, int]:
    """[Start, Ende) des Auswertefensters 07:00-20:00 Europe/Berlin in Epoch-Millisekunden."""
    start = datetime(day.year, day.month, day.day, DAY_START_HOUR, tzinfo=TZ)
    end = datetime(day.year, day.month, day.day, DAY_END_HOUR, tzinfo=TZ)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def iso_berlin(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, TZ).isoformat()


def local_day(epoch_seconds: int) -> date:
    return datetime.fromtimestamp(epoch_seconds, TZ).date()


def days_between(first: date, last: date):
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


def plausibility(level_db: float) -> str:
    if level_db < PLAUSIBLE_MIN_DB:
        return "UNTER_50"
    if level_db > PLAUSIBLE_MAX_DB:
        return "UEBER_100"
    return "OK"
