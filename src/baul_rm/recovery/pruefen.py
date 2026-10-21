"""Validierung 4a (Echtheitstest gegen alten GEMISCHT-Wert) und 4c (nur informative Rückrechnung)."""

import csv
import math
from pathlib import Path

from .altcsv import AltRow
from .common import CSV_ROUNDING_DB, TOLERANCE_DB, energetic_mean
from .db import DaySamples

CHECK_COLUMNS = ["fenster_start_ms", "fenster_s", "status", "csv_laeq", "db_gemischt_laeq", "delta_db", "csv_samples",
                 "db_samples", "samples_gleich", "db_pce_n", "db_mic_n"]


def check_mixed_window(row: AltRow, window_s: int, samples: DaySamples) -> dict:
    """4a. Der alte Export mittelte ALLE Roh-Samples des Fensters (PCE und Mikrofon zusammen, nach Probenzahl
    gewichtet - nicht die zwei Quellenmittel). Gleiches Verfahren hier, Vergleich mit dem CSV-Wert."""
    pce, mic = samples.window(row.start_ms, row.start_ms + window_s * 1000)
    entry = {"fenster_start_ms": row.start_ms, "fenster_s": window_s, "csv_laeq": row.laeq, "csv_samples": row.samples,
             "db_samples": len(pce) + len(mic), "db_pce_n": len(pce), "db_mic_n": len(mic)}
    if not pce and not mic or row.laeq is None:
        return {**entry, "status": "NICHT_PRUEFBAR", "db_gemischt_laeq": "", "delta_db": "", "samples_gleich": ""}
    mixed = energetic_mean(pce + mic)
    delta = mixed - row.laeq
    return {**entry, "status": "BESTAETIGT" if abs(delta) <= TOLERANCE_DB else "KONFLIKT",
            "db_gemischt_laeq": round(mixed, 3), "delta_db": round(delta, 3),
            "samples_gleich": "JA" if entry["db_samples"] == row.samples else "NEIN"}


def check_pure_pce_window(row: AltRow, window_s: int, measurements: DaySamples) -> dict:
    """Querprüfung: Ein reines PCE_323-Fenster der Alt-CSV gegen die PCE-Messwerte aus measurements im selben
    Fenster (energetisches Mittel, Toleranz 0,15 dB). Bestätigt, dass measurements dieselben Werte trägt wie der
    App-Export - die Voraussetzung, measurements-Sekunden in GEMISCHT-/Lückenfenstern zu vertrauen."""
    pce, _ = measurements.window(row.start_ms, row.start_ms + window_s * 1000)
    entry = {"fenster_start_ms": row.start_ms, "fenster_s": window_s, "csv_laeq": row.laeq, "csv_samples": row.samples,
             "db_samples": len(pce), "db_pce_n": len(pce), "db_mic_n": 0}
    if not pce or row.laeq is None:
        return {**entry, "status": "PCE_NICHT_PRUEFBAR", "db_gemischt_laeq": "", "delta_db": "", "samples_gleich": ""}
    mean = energetic_mean(pce)
    delta = mean - row.laeq
    return {**entry, "status": "PCE_BESTAETIGT" if abs(delta) <= TOLERANCE_DB else "PCE_KONFLIKT",
            "db_gemischt_laeq": round(mean, 3), "delta_db": round(delta, 3),
            "samples_gleich": "JA" if len(pce) == row.samples else "NEIN"}


def reconstruct_pce(row: AltRow, mic_levels: list[float]) -> dict:
    """4c, nur informativ. Aus GEMISCHT-Leq L, Probenzahl N und den Mikrofonpegeln der Datenbank:
    E_pce = N*10^(L/10) - sum(10^(Lmic/10)), n_pce = N - n_mic. Die Alt-CSV ist auf 0,1 dB gerundet; dieser
    Rundungsfehler wird als Intervall mitgeführt. Ist das Mikrofon laut, wird das Intervall riesig oder
    undefiniert - das ist der Grund, warum das Ergebnis nie ein Messwert ist."""
    n_mic = len(mic_levels)
    n_pce = row.samples - n_mic
    base = {"fenster_start_ms": row.start_ms, "csv_laeq": row.laeq, "csv_samples": row.samples, "mic_n": n_mic,
            "pce_n_berechnet": n_pce, "pce_laeq_berechnet": "", "pce_laeq_min": "", "pce_laeq_max": ""}
    if row.laeq is None or n_mic == 0 or n_pce <= 0:
        return {**base, "ergebnis": "NICHT_BERECHENBAR"}
    mic_energy = sum(10.0 ** (v / 10.0) for v in mic_levels)

    def level(laeq: float) -> float | None:
        pce_energy = row.samples * 10.0 ** (laeq / 10.0) - mic_energy
        return 10.0 * math.log10(pce_energy / n_pce) if pce_energy > 0 else None

    middle, low, high = level(row.laeq), level(row.laeq - CSV_ROUNDING_DB), level(row.laeq + CSV_ROUNDING_DB)
    if middle is None:
        return {**base, "ergebnis": "UNMOEGLICH_NEGATIVE_ENERGIE"}
    return {**base, "pce_laeq_berechnet": round(middle, 2), "pce_laeq_min": "" if low is None else round(low, 2),
            "pce_laeq_max": "" if high is None else round(high, 2), "ergebnis": "BERECHNET"}


def write_dicts(rows: list[dict], columns: list[str], out: Path) -> None:
    """Maschinenlesbar: Semikolon als Trenner, Punkt als Dezimalzeichen."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
