"""Schritt 5: Extrakte zusammenführen, validieren, Stichprobe 4e. Parquet braucht pyarrow."""

import bisect
import csv
import heapq
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

from .altcsv import AltRow, day_targets
from .common import DAY_WINDOW_SECONDS, TOLERANCE_DB, day_window_ms, iso_berlin, local_day

# Priorität: intakte DB = Geräteexport > WAL > .recover > Carving (kleiner = besser)
PRIORITY = {"DB_INTAKT": 0, "GERAETEEXPORT": 0, "WAL": 1, "RECOVER": 2, "CARVING": 3}
GERETTET_COLUMNS = ["sekunde_utc", "zeit_berlin", "tag", "pegel_db", "n_samples", "lmin_db", "lmax_db", "quelle", "datei",
                    "sha256", "tabelle", "id_von", "id_bis", "methode", "runde", "validierung", "plausibilitaet",
                    "weitere_quellen"]


def _pyarrow():
    try:
        import pyarrow
        import pyarrow.parquet
    except ImportError as error:  # pragma: no cover - Hinweis für die lokale Ausführung
        raise SystemExit("pyarrow fehlt: pip install -r tools/recovery/requirements.txt") from error
    return pyarrow, pyarrow.parquet


def _read_extract(path: Path):
    with open(path, newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            record["_s"] = int(record["sekunde"])
            yield record


class WindowStatus:
    """Ergebnis von 4a als nachschlagbare Fensterliste (spart Speicher gegenüber einem Eintrag je Sekunde)."""

    def __init__(self, check_files: list[Path]):
        entries = []
        for path in check_files:
            with open(path, newline="", encoding="utf-8-sig") as handle:
                for record in csv.DictReader(handle, delimiter=";"):
                    start = int(record["fenster_start_ms"])
                    entries.append((start, start + int(record["fenster_s"]) * 1000, record["status"]))
        entries.sort()
        self._starts = [e[0] for e in entries]
        self._entries = entries

    def lookup(self, second: int) -> str | None:
        index = bisect.bisect_right(self._starts, second * 1000) - 1
        if index >= 0 and self._entries[index][0] <= second * 1000 < self._entries[index][1]:
            return self._entries[index][2]
        return None


def _plausibility(row: dict) -> str:
    if float(row["lmin"]) < 50.0:
        return "UNTER_50"
    return "UEBER_100" if float(row["lmax"]) > 100.0 else "OK"


def _validation(best: dict, group: list[dict], windows: WindowStatus) -> str:
    """4a prüft den level_samples-Mix gegen den alten GEMISCHT-Wert und gilt deshalb nur für Sekunden aus
    level_samples. Sekunden aus measurements werden gegen reine CSV-PCE-Fenster geprüft. Stimmt eine zweite Tabelle
    innerhalb der Toleranz überein, steht zusätzlich QUERPRUEFUNG_2_TABELLEN."""
    status = windows.lookup(best["_s"])
    labels = []
    if best["tabelle"] == "level_samples" and status in ("BESTAETIGT", "KONFLIKT"):
        labels.append("BESTAETIGT_4A" if status == "BESTAETIGT" else "KONFLIKT_4A")
    elif best["tabelle"] == "measurements" and status in ("PCE_BESTAETIGT", "PCE_KONFLIKT"):
        labels.append("BESTAETIGT_PCE_CSV" if status == "PCE_BESTAETIGT" else "KONFLIKT_PCE_CSV")
    others = {r["tabelle"] for r in group if r is not best and
              abs(float(r["laeq"]) - float(best["laeq"])) <= TOLERANCE_DB}
    if others - {best["tabelle"]}:
        labels.append("QUERPRUEFUNG_2_TABELLEN")
    return "+".join(labels) or "UNGEPRUEFT"


def merge_extracts(extract_files: list[Path], check_files: list[Path], runde: int, out_parquet: Path,
                   out_conflicts: Path) -> dict:
    """Je Sekunde: bester Kandidat nach Priorität; Kandidaten innerhalb 0,15 dB zählen als Bestätigung
    (weitere_quellen), Abweichungen sind Konflikte - sie werden protokolliert und NICHT aufgelöst: alle
    Kandidaten stehen dann mit Status KONFLIKT_QUELLEN im Ergebnis."""
    pa, pq = _pyarrow()
    windows = WindowStatus(check_files)
    merged = heapq.merge(*[_read_extract(p) for p in extract_files], key=lambda r: r["_s"])
    batch: dict[str, list] = {c: [] for c in GERETTET_COLUMNS}
    writer = None
    stats = defaultdict(int)
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    out_conflicts.parent.mkdir(parents=True, exist_ok=True)
    with open(out_conflicts, "w", newline="", encoding="utf-8-sig") as conflict_handle:
        conflict_writer = csv.writer(conflict_handle, delimiter=";")
        conflict_writer.writerow(["sekunde_utc", "zeit_berlin", "laeq", "datei", "sha256", "methode"])

        def emit(row: dict, validation: str, further: int) -> None:
            second = row["_s"]
            values = [second, iso_berlin(second), local_day(second).isoformat(), float(row["laeq"]), int(row["n"]),
                      float(row["lmin"]), float(row["lmax"]), "PCE_323", row["datei"], row["sha256"], row["tabelle"],
                      int(row["id_von"]), int(row["id_bis"]), row["methode"], runde, validation, _plausibility(row), further]
            for column, value in zip(GERETTET_COLUMNS, values):
                batch[column].append(value)

        def flush() -> None:
            nonlocal writer
            if not batch["sekunde_utc"]:
                return
            table = pa.table(batch)
            writer = writer or pq.ParquetWriter(out_parquet, table.schema)
            writer.write_table(table)
            for column in batch:
                batch[column].clear()

        def handle(group: list[dict]) -> None:
            group.sort(key=lambda r: (PRIORITY.get(r["methode"], 9), r["tabelle"], r["sha256"]))
            best = group[0]
            validation = _validation(best, group, windows)
            agreeing = [r for r in group[1:] if abs(float(r["laeq"]) - float(best["laeq"])) <= TOLERANCE_DB]
            if len(agreeing) == len(group) - 1:
                emit(best, validation, len(agreeing))
                stats["sekunden"] += 1
                return
            stats["konflikte"] += 1
            for candidate in group:
                emit(candidate, "KONFLIKT_QUELLEN", 0)
                conflict_writer.writerow([candidate["_s"], iso_berlin(candidate["_s"]), candidate["laeq"],
                                          candidate["datei"], candidate["sha256"], candidate["methode"]])

        group: list[dict] = []
        for row in merged:
            if group and row["_s"] != group[0]["_s"]:
                handle(group)
                group = []
                if len(batch["sekunde_utc"]) >= 100_000:
                    flush()
            group.append(row)
        if group:
            handle(group)
        flush()
    if writer is None:  # leeres Ergebnis soll trotzdem eine lesbare Datei sein
        pq.write_table(pa.table({c: [] for c in GERETTET_COLUMNS}), out_parquet)
    else:
        writer.close()
    return dict(stats)


def write_target_parquet(days: list[tuple[date, list[AltRow], int, str, str]], out_parquet: Path,
                         summary_csv: Path, extra_active_days: set[date] | None = None) -> None:
    """Schritt 1. days = (Tag, Alt-CSV-Zeilen, Fenster_s, Dateiname, SHA-256). Zielmenge ist GEMISCHT, MIKROFON oder FEHLT,
    aber nur an Tagen mit erkennbarer Messaktivität (Alt-CSV-Zeile mit Quelle und Samples > 0 oder Tag in
    extra_active_days, z. B. aus der Datenbank)."""
    pa, pq = _pyarrow()
    schema = pa.schema([("sekunde_utc", pa.int64()), ("zeit_berlin", pa.string()), ("tag", pa.string()),
                        ("status", pa.string()), ("csv_quelle", pa.string()), ("csv_datei", pa.string()),
                        ("csv_sha256", pa.string()), ("fenster_s", pa.int32()), ("ziel", pa.bool_())])
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    with pq.ParquetWriter(out_parquet, schema) as writer, open(summary_csv, "w", newline="", encoding="utf-8-sig") as handle:
        summary = csv.writer(handle, delimiter=";")
        summary.writerow(["tag", "aktiv", "PCE", "GEMISCHT", "MIKROFON", "FEHLT", "zielsekunden", "csv_datei", "fenster_s"])
        for day, rows, window_s, name, digest in days:
            targets = day_targets(rows, window_s, day)
            active = any(r.samples > 0 and r.quelle in ("PCE_323", "GEMISCHT", "MIKROFON") for r in rows) or \
                bool(extra_active_days and day in extra_active_days)
            start_ms, _ = day_window_ms(day)
            seconds = [start_ms // 1000 + i for i in range(DAY_WINDOW_SECONDS)]
            counts = defaultdict(int)
            for status, _ in targets:
                counts[status] += 1
            writer.write_table(pa.table({
                "sekunde_utc": seconds, "zeit_berlin": [iso_berlin(s) for s in seconds], "tag": [day.isoformat()] * len(seconds),
                "status": [t[0] for t in targets], "csv_quelle": [t[1] for t in targets], "csv_datei": [name] * len(seconds),
                "csv_sha256": [digest] * len(seconds), "fenster_s": [window_s] * len(seconds),
                "ziel": [active and t[0] != "PCE" for t in targets]}, schema=schema))
            summary.writerow([day.isoformat(), "JA" if active else "NEIN", counts["PCE"], counts["GEMISCHT"],
                              counts["MIKROFON"], counts["FEHLT"], (DAY_WINDOW_SECONDS - counts["PCE"]) if active else 0,
                              name, window_s])


def daily_balance(target_parquet: Path, rescued_parquet: Path, out_csv: Path) -> list[dict]:
    """Schritt 7 (Tabelle je Tag): Zielsekunden, gerettet, davon bestätigt, Restlücke, mögliche neue Abdeckung."""
    _, pq = _pyarrow()
    targets = pq.read_table(target_parquet, columns=["sekunde_utc", "tag", "status", "ziel"]).to_pylist()
    rescued = pq.read_table(rescued_parquet, columns=["sekunde_utc", "validierung", "plausibilitaet"]).to_pylist()
    rescued_by_second: dict[int, str] = {}
    for item in rescued:
        if item["validierung"] != "KONFLIKT_QUELLEN":
            rescued_by_second[item["sekunde_utc"]] = item["validierung"]
    rows: dict[str, dict] = {}
    for item in targets:
        day = rows.setdefault(item["tag"], {"tag": item["tag"], "bereits_pce": 0, "zielsekunden": 0, "gerettet": 0,
                                            "davon_bestaetigt": 0})
        if item["status"] == "PCE":
            day["bereits_pce"] += 1
        if item["ziel"]:
            day["zielsekunden"] += 1
            if item["sekunde_utc"] in rescued_by_second:
                day["gerettet"] += 1
                label = rescued_by_second[item["sekunde_utc"]]
                day["davon_bestaetigt"] += "BESTAETIGT_" in label and "KONFLIKT_" not in label
    result = []
    for day in sorted(rows):
        entry = rows[day]
        entry["restluecke"] = entry["zielsekunden"] - entry["gerettet"]
        entry["moegliche_abdeckung"] = round((entry["bereits_pce"] + entry["gerettet"]) / DAY_WINDOW_SECONDS, 4)
        result.append(entry)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["tag", "bereits_pce", "zielsekunden", "gerettet", "davon_bestaetigt",
                                                    "restluecke", "moegliche_abdeckung"], delimiter=";")
        writer.writeheader()
        writer.writerows(result)
    return result


def sample_per_day(rescued_parquet: Path, out_csv: Path, per_day: int = 20, seed: int = 20261018) -> int:
    """4e: je Tag bis zu 20 zufällige gerettete Sekunden mit vollständiger Herkunft (fester Seed = reproduzierbar)."""
    _, pq = _pyarrow()
    rows = pq.read_table(rescued_parquet).to_pylist()
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_day[row["tag"]].append(row)
    rng = random.Random(seed)
    chosen = []
    for day in sorted(by_day):
        chosen += sorted(rng.sample(by_day[day], min(per_day, len(by_day[day]))), key=lambda r: r["sekunde_utc"])
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=GERETTET_COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(chosen)
    return len(chosen)
