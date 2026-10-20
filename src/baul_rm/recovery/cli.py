"""Kommandozeile. Jeder Befehl liest nur; geschrieben wird ausschließlich unter --ziel/--out*/--work."""

import argparse
import csv
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from . import altcsv, db, inventar, kopieren, merge, pruefen
from .common import SOURCE_MIC, days_between, local_day, sha256_file


def _day(value: str) -> date:
    return date.fromisoformat(value)


def _record_evaluated(out_dir: Path, digest: str, method: str, runde: int) -> None:
    path = out_dir / "ausgewertet.csv"
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=";")
        if new:
            writer.writerow(["sha256", "methode", "runde"])
        writer.writerow([digest, method, runde])


def _load_alt_csvs(paths: list[Path]) -> dict[date, tuple[Path, list[altcsv.AltRow], int, str]]:
    """Tag -> (Datei, Zeilen, Fenster_s, SHA-256). Gibt es mehrere Dateien für einen Tag, gewinnt die zuletzt
    geänderte; die Wahl wird laut gemeldet (kein stilles Raten)."""
    by_day: dict[date, list[tuple[Path, list[altcsv.AltRow]]]] = {}
    for path in paths:
        try:
            rows = altcsv.parse_alt_csv(path)
        except (ValueError, KeyError, UnicodeDecodeError) as error:
            print(f"  übersprungen (kein Alt-CSV-Format): {path.name}: {error}", file=sys.stderr)
            continue
        if not rows:
            continue
        day = Counter(local_day(r.start_ms // 1000) for r in rows).most_common(1)[0][0]
        by_day.setdefault(day, []).append((path, rows))
    result = {}
    for day, candidates in by_day.items():
        candidates.sort(key=lambda c: c[0].stat().st_mtime)
        if len(candidates) > 1:
            print(f"  WARNUNG {day}: {len(candidates)} Dateien, gewählt wird die zuletzt geänderte: "
                  f"{candidates[-1][0]} (alle: {[str(c[0]) for c in candidates]})", file=sys.stderr)
        path, rows = candidates[-1]
        result[day] = (path, rows, altcsv.window_seconds(rows), sha256_file(path))
    return result


def _csv_files(folders: list[Path]) -> list[Path]:
    return sorted(p for folder in folders for p in folder.rglob("*.csv"))


def _manifest_files(manifest: Path) -> list[Path]:
    """Dateiliste eines früheren Laufs (Spalten Schema, Pfad, SHA256_lauf, gueltige_Sekunden_im_Zeitraum_Datei).
    Nur App-Aggregat-CSVs mit gültigen Sekunden; jeder Hash wird gegen die Datei geprüft, damit genau die Fassung
    gelesen wird, die der Lauf verwendet hat (nicht eine spätere oder ältere Kopie)."""
    files = []
    with open(manifest, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if row.get("Schema") != "app-aggregate" or not row.get("Pfad"):
                continue
            if int(row.get("gueltige_Sekunden_im_Zeitraum_Datei") or 0) == 0:
                continue
            path = Path(row["Pfad"])
            if not path.is_file():
                raise SystemExit(f"Manifest nennt eine fehlende Datei: {path} (nur online? bitte offline verfügbar machen)")
            if sha256_file(path).lower() != row["SHA256_lauf"].strip().lower():
                raise SystemExit(f"SHA-256 weicht vom Manifest ab: {path} - nicht die Fassung des Laufs")
            files.append(path)
    return sorted(set(files))


def _input_csvs(args) -> list[Path]:
    files = _csv_files(args.csv_ordner or []) + (_manifest_files(args.csv_manifest) if args.csv_manifest else [])
    if not files:
        raise SystemExit("Keine CSV-Dateien: --csv-ordner oder --csv-manifest angeben")
    return sorted(set(files))


def cmd_kopieren(args) -> None:
    hashes = kopieren.load_manifest_hashes(args.manifest) if args.manifest else None
    results = kopieren.copy_tree(args.quelle, args.ziel, hashes)
    kopieren.write_results(results, args.out)
    counts = Counter(r.status for r in results)
    print(f"{len(results)} Dateien: {dict(counts)}; nicht im Manifest: {sum(r.im_manifest == 'NEIN' for r in results)}")
    for result in results:
        if result.status != "OK":
            print(f"  {result.status}: {result.pfad} {result.hinweis}")


def cmd_inventar(args) -> None:
    rows = inventar.build_inventory(args.originals, args.work, args.runde)
    evaluated_path = args.out.parent / "ausgewertet.csv"
    if evaluated_path.exists():
        with open(evaluated_path, newline="", encoding="utf-8") as handle:
            inventar.apply_evaluated(rows, {r["sha256"]: r["methode"] for r in csv.DictReader(handle, delimiter=";")})
    inventar.write_inventory(rows, args.out)
    print(f"{len(rows)} Dateien; Status: {dict(Counter(r.status for r in rows))}; Typen: {dict(Counter(r.typ for r in rows))}")


def cmd_schema(args) -> None:
    conn = db.open_readonly(args.db)
    try:
        dump = db.schema_dump(conn)
        overview = db.level_sample_overview(conn) if db.has_level_samples(conn) else []
        db.write_schema_markdown(dump, overview, args.out, args.db.name)
        if args.integrity:
            print("integrity_check:", conn.execute("PRAGMA integrity_check").fetchall()[:5])
    finally:
        conn.close()
    print(f"Schema nach {args.out}; Tabellen: {[t['name'] for t in dump['tabellen']]}")


def _table_jobs(conn, wal_conn):
    """Je vorhandener Quelltabelle (und je WAL-Ansicht) ein Auftrag (Verbindung, Methode, Tabelle, Funktion, ab_id)."""
    stats = db.MeasurementStats()
    jobs = []
    if db.has_level_samples(conn):
        jobs.append((conn, "DB_INTAKT", "level_samples", db.pce_seconds, None))
        if wal_conn is not None:
            jobs.append((wal_conn, "WAL", "level_samples", db.pce_seconds, db.max_id(conn, "level_samples")))
    if db.has_measurements(conn):
        def measured(connection, day, above=None):
            return db.measurement_pce_seconds(connection, day, stats, above)

        jobs.append((conn, "DB_INTAKT", "measurements", measured, None))
        if wal_conn is not None:
            jobs.append((wal_conn, "WAL", "measurements", measured, db.max_id(conn, "measurements")))
    return jobs, stats


def cmd_extrahieren(args) -> None:
    digest = sha256_file(args.db)
    conn = db.open_readonly(args.db)
    wal_conn = db.open_readonly(args.wal_kopie, immutable=False) if args.wal_kopie else None
    jobs, measurement_stats = _table_jobs(conn, wal_conn)
    if not jobs:
        raise SystemExit(f"{args.db}: weder level_samples {sorted(db.REQUIRED_COLUMNS)} noch measurements/sessions "
                         f"vorhanden - Schema prüfen (Befehl 'schema')")
    for connection, method, table, func, above in jobs:
        out = args.out_dir / f"extrakt_{digest[:12]}_{table}_{method.lower()}.csv"
        meta = {"datei": args.db.name, "sha256": digest, "methode": method, "tabelle": table}
        rows = (row for day in days_between(args.von, args.bis) for row in func(connection, day, above))
        count = db.write_extract(rows, out, meta)
        print(f"{table}/{method}: {count} PCE-Sekunden -> {out}")
        _record_evaluated(args.out_dir, digest, f"{table}/{method}", args.runde)
    if db.has_measurements(conn):
        print(f"measurements gelesen {measurement_stats.rows}, ausgeschlossen: Flags {measurement_stats.excluded_flags}, "
              f"Pegel {measurement_stats.excluded_level}")
    # Tagesstatistik für P1: Samples je Quelle in level_samples, Plausibilität, Zeitprüfung
    if db.has_level_samples(conn):
        stats_by_day = {}
        for line in db.level_sample_overview(conn):
            stats_by_day.setdefault(line["tag"], {})[line["quelle"]] = line["samples"]
        checks = db.time_checks(conn)
        with open(args.out_dir / f"tagesstatistik_{digest[:12]}.csv", "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["tag", "pce_samples_gesamt", "mikrofon_samples_gesamt", "ruecksprung_zeit",
                             "luecken_ueber_120s"])
            for day in sorted(stats_by_day):
                writer.writerow([day, stats_by_day[day].get("PCE_323", 0), stats_by_day[day].get(SOURCE_MIC, 0),
                                 checks.get(day, {}).get("ruecksprung", 0), checks.get(day, {}).get("luecken_ueber_grenze", 0)])
    conn.close()


def cmd_ziel(args) -> None:
    loaded = _load_alt_csvs(_input_csvs(args))
    days = [(d, *loaded[d][1:3], loaded[d][0].name, loaded[d][3]) for d in sorted(loaded) if args.von <= d <= args.bis]
    merge.write_target_parquet(days, args.out_dir / "ziel_sekunden.parquet", args.out_dir / "ziel_tageszusammenfassung.csv")
    print(f"{len(days)} Tage; Fenster-Sekunden je Tag: {sorted({d[2] for d in days})}")


def cmd_pruefen(args) -> None:
    digest = sha256_file(args.db)
    loaded = _load_alt_csvs(_input_csvs(args))
    conn = db.open_readonly(args.db)
    has_samples, has_measure = db.has_level_samples(conn), db.has_measurements(conn)
    checks, reconstructions = [], []
    for day in days_between(args.von, args.bis):
        if day not in loaded:
            continue
        _, rows, window_s, _ = loaded[day]
        samples = db.load_day_samples(conn, day) if has_samples else None
        measured = db.load_day_measurements(conn, day) if has_measure else None
        for row in rows:
            if row.quelle == "PCE_323" and measured is not None and window_s == 1:
                checks.append(pruefen.check_pure_pce_window(row, window_s, measured))
            if row.quelle != "GEMISCHT" or samples is None:
                continue
            result = pruefen.check_mixed_window(row, window_s, samples)
            checks.append(result)
            pce, mic = samples.window(row.start_ms, row.start_ms + window_s * 1000)
            if mic and (not pce or args.auch_mit_pce):
                entry = pruefen.reconstruct_pce(row, mic)
                entry["db_pce_n"] = len(pce)
                entry["hinweis"] = "NICHT VERWENDBAR - nur informativ, kein Messwert (Regel 5)"
                reconstructions.append(entry)
    pruefen.write_dicts(checks, pruefen.CHECK_COLUMNS, args.out_dir / f"pruefung_{digest[:12]}.csv")
    pruefen.write_dicts(reconstructions, ["fenster_start_ms", "csv_laeq", "csv_samples", "mic_n", "db_pce_n", "pce_n_berechnet",
                                          "pce_laeq_berechnet", "pce_laeq_min", "pce_laeq_max", "ergebnis", "hinweis"],
                        args.out_dir / f"rekonstruiert_nicht_verwendbar_{digest[:12]}.csv")
    print(f"4a/Querprüfung: {dict(Counter(c['status'] for c in checks))}; 4c-Zeilen: {len(reconstructions)}")
    _record_evaluated(args.out_dir, digest, "4a-Pruefung", args.runde)
    conn.close()


def cmd_zusammenfuehren(args) -> None:
    stats = merge.merge_extracts(args.extrakte, args.pruefungen or [], args.runde, args.out_dir / "pce_gerettet.parquet",
                                 args.out_dir / "konflikte.csv")
    print(f"Ergebnis: {stats}")


def cmd_bilanz(args) -> None:
    result = merge.daily_balance(args.ziel, args.gerettet, args.out)
    print(f"{len(result)} Tage nach {args.out}")


def cmd_stichprobe(args) -> None:
    print(f"{merge.sample_per_day(args.gerettet, args.out)} Sekunden nach {args.out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recovery", description=__doc__)
    sub = parser.add_subparsers(dest="befehl", required=True)

    def add(name, func, help_text):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        return p

    p = add("kopieren", cmd_kopieren, "Quelle (nur lesend) nach originals/ kopieren, Hash vor/nach")
    p.add_argument("--quelle", type=Path, required=True)
    p.add_argument("--ziel", type=Path, required=True)
    p.add_argument("--manifest", type=Path, action="append")
    p.add_argument("--out", type=Path, required=True)

    p = add("inventar", cmd_inventar, "Inventar mit Magic Bytes, Archive rekursiv in work/ entpacken")
    p.add_argument("--originals", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--runde", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)

    p = add("schema", cmd_schema, "Schema und level_samples-Überblick einer SQLite-Kopie")
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--integrity", action="store_true")

    for name, func, text in (("extrahieren", cmd_extrahieren, "PCE-Sekunden aus einer DB-Kopie"),
                             ("pruefen", cmd_pruefen, "4a Echtheitstest und 4c Rückrechnung")):
        p = add(name, func, text)
        p.add_argument("--db", type=Path, required=True)
        p.add_argument("--von", type=_day, required=True)
        p.add_argument("--bis", type=_day, required=True)
        p.add_argument("--runde", type=int, required=True)
        p.add_argument("--out-dir", type=Path, required=True)
        if name == "extrahieren":
            p.add_argument("--wal-kopie", type=Path, help="Arbeitskopie der DB MIT -wal daneben (Frames werden gesondert markiert)")
        else:
            p.add_argument("--csv-ordner", type=Path, action="append")
            p.add_argument("--csv-manifest", type=Path, help="csv_dateien_je_tag.csv eines früheren Laufs (Hash-geprüft)")
            p.add_argument("--auch-mit-pce", action="store_true", help="4c auch dort rechnen, wo echte PCE-Samples existieren (Methodentest)")

    p = add("ziel", cmd_ziel, "Zielmenge aus den Alt-CSVs")
    p.add_argument("--csv-ordner", type=Path, action="append")
    p.add_argument("--csv-manifest", type=Path, help="csv_dateien_je_tag.csv eines früheren Laufs (Hash-geprüft)")
    p.add_argument("--von", type=_day, required=True)
    p.add_argument("--bis", type=_day, required=True)
    p.add_argument("--out-dir", type=Path, required=True)

    p = add("zusammenfuehren", cmd_zusammenfuehren, "Extrakte zu pce_gerettet.parquet")
    p.add_argument("--extrakte", type=Path, nargs="+", required=True)
    p.add_argument("--pruefungen", type=Path, nargs="*")
    p.add_argument("--runde", type=int, required=True)
    p.add_argument("--out-dir", type=Path, required=True)

    p = add("bilanz", cmd_bilanz, "Tagesbilanz Ziel gegen gerettet")
    p.add_argument("--ziel", type=Path, required=True)
    p.add_argument("--gerettet", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)

    p = add("stichprobe", cmd_stichprobe, "4e: 20 Zufallssekunden je Tag")
    p.add_argument("--gerettet", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)
