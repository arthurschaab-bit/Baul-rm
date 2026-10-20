"""Tests der Recovery-Werkzeuge mit ausschließlich synthetischen Daten."""
import csv
import hashlib
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from baul_rm.recovery import altcsv, cli, db, inventar, kopieren, merge, pruefen
from baul_rm.recovery.common import DAY_WINDOW_SECONDS, day_window_ms, energetic_mean, sha256_file

try:
    import pyarrow.parquet as pq
except ImportError:  # pyarrow ist optional (Extra "recovery")
    pq = None

TZ = ZoneInfo("Europe/Berlin")
DAY = date(2001, 9, 14)
START = day_window_ms(DAY)[0]


def make_db(path, level_samples=(), measurements=(), sessions=((1, "PCE-323"), (2, "Smartphone-Mikrofon"))):
    """level_samples = (at_ms, level, source); measurements = (session_id, timestamp_ms, level, flags)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE level_samples (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, at INTEGER NOT NULL, "
        "levelDb REAL NOT NULL, source TEXT NOT NULL);"
        "CREATE INDEX index_level_samples_at ON level_samples (at);"
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY, deviceName TEXT NOT NULL);"
        "CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, sessionId INTEGER NOT NULL, "
        "timestamp INTEGER NOT NULL, levelDb REAL NOT NULL, weighting TEXT, flags INTEGER NOT NULL, "
        "timeWeighting TEXT, range TEXT);")
    conn.executemany("INSERT INTO sessions VALUES (?, ?)", sessions)
    conn.executemany("INSERT INTO level_samples (at, levelDb, source) VALUES (?, ?, ?)", level_samples)
    conn.executemany("INSERT INTO measurements (sessionId, timestamp, levelDb, weighting, flags) VALUES (?, ?, ?, 'A', ?)",
                     measurements)
    conn.commit()
    conn.close()


def write_alt_csv(path, rows):
    """Format von DriveCsv.kt: BOM, Semikolon, Dezimalkomma, CRLF. rows = (start_ms, laeq, samples, quelle)."""
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        handle.write("Zeit;Pegel_dB;LAeq_dB;LAFmax_dB;LAFmin_dB;Bewertung;Zeitbewertung;Messbereich;Samples;Quelle;"
                     "Ereignis;Klassifikation;Notizen\r\n")
        for start_ms, laeq, samples, source in rows:
            text = datetime.fromtimestamp(start_ms / 1000, TZ).isoformat()
            value = f"{laeq:.1f}".replace(".", ",")
            handle.write(f"{text};{value};{value};{value};{value};A;FAST;AUTO;{samples};{source};;;\r\n")


class TempCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp = Path(self._temp.name)


class MixedDay(TempCase):
    """Sekunde 0: PCE 70 dB x2 + Mikrofon 90 dB x3 (alt GEMISCHT); Sekunde 1: nur PCE; Sekunde 2: nur Mikrofon."""

    def setUp(self):
        super().setUp()
        t = START
        samples = sorted([(t, 70.0, "PCE_323"), (t + 400, 70.0, "PCE_323"), (t + 100, 90.0, "MIKROFON"),
                          (t + 300, 90.0, "MIKROFON"), (t + 600, 90.0, "MIKROFON"), (t + 1000, 65.0, "PCE_323"),
                          (t + 2000, 80.0, "MIKROFON")])
        self.mixed = energetic_mean([70.0, 70.0, 90.0, 90.0, 90.0])
        self.database, self.csv_path = self.tmp / "noise_database", self.tmp / "laermprotokoll_2001-09-14.csv"
        make_db(self.database, level_samples=samples)
        write_alt_csv(self.csv_path, [(t, self.mixed, 5, "GEMISCHT"), (t + 1000, 65.0, 1, "PCE_323"),
                                      (t + 2000, 80.0, 1, "MIKROFON")])

    def test_pce_seconds_ignore_microphone_and_do_not_interpolate(self):
        conn = db.open_readonly(self.database)
        result = list(db.pce_seconds(conn, DAY))
        self.assertEqual([r.sekunde for r in result], [START // 1000, START // 1000 + 1])  # Mikrofon-Sekunde fehlt
        self.assertEqual(result[0].n, 2)
        self.assertAlmostEqual(result[0].laeq, 70.0)
        self.assertAlmostEqual(result[1].laeq, 65.0)

    def test_database_is_not_modified_by_reading(self):
        before = sha256_file(self.database)
        conn = db.open_readonly(self.database)
        list(db.pce_seconds(conn, DAY))
        db.schema_dump(conn)
        db.level_sample_overview(conn)
        conn.close()
        self.assertEqual(sha256_file(self.database), before)
        with self.assertRaises(sqlite3.OperationalError):
            db.open_readonly(self.database).execute("INSERT INTO level_samples (at, levelDb, source) VALUES (1, 1, 'X')")

    def test_echtheitstest_confirms_old_mix_weighted_by_sample_count(self):
        alt = altcsv.parse_alt_csv(self.csv_path)
        self.assertEqual(altcsv.window_seconds(alt), 1)
        samples = db.load_day_samples(db.open_readonly(self.database), DAY)
        result = pruefen.check_mixed_window(next(r for r in alt if r.quelle == "GEMISCHT"), 1, samples)
        self.assertEqual(result["status"], "BESTAETIGT")
        self.assertEqual(result["samples_gleich"], "JA")
        self.assertGreater(result["db_gemischt_laeq"], 85)  # nicht das Mittel der Quellenmittel (80 dB)

    def test_echtheitstest_reports_conflict_and_missing(self):
        samples = db.load_day_samples(db.open_readonly(self.database), DAY)
        self.assertEqual(pruefen.check_mixed_window(altcsv.AltRow(START, 81.0, 81.0, 5, "GEMISCHT"), 1, samples)["status"],
                         "KONFLIKT")
        self.assertEqual(pruefen.check_mixed_window(altcsv.AltRow(START + 50_000, 70.0, 70.0, 5, "GEMISCHT"), 1,
                                                    samples)["status"], "NICHT_PRUEFBAR")

    def test_day_targets_status_per_second(self):
        rows = altcsv.parse_alt_csv(self.csv_path)
        targets = altcsv.day_targets(rows, 1, DAY)
        self.assertEqual(len(targets), DAY_WINDOW_SECONDS)
        self.assertEqual([t[0] for t in targets[:4]], ["GEMISCHT", "PCE", "MIKROFON", "FEHLT"])
        self.assertTrue(altcsv.has_activity(rows))


class Reconstruction(unittest.TestCase):
    @staticmethod
    def reconstruct(pce, mic):
        row = altcsv.AltRow(0, round(energetic_mean(pce + mic), 1), None, len(pce) + len(mic), "GEMISCHT")
        return pruefen.reconstruct_pce(row, mic)

    def test_quiet_microphone_stays_inside_rounding_interval(self):
        pce = [70.0, 72.0]
        result = self.reconstruct(pce, [70.0, 71.0, 69.0])
        self.assertEqual(result["ergebnis"], "BERECHNET")
        self.assertEqual(result["pce_n_berechnet"], 2)
        self.assertLessEqual(result["pce_laeq_min"], energetic_mean(pce))
        self.assertLessEqual(energetic_mean(pce), result["pce_laeq_max"])
        self.assertLess(result["pce_laeq_max"] - result["pce_laeq_min"], 0.5)

    def test_loud_microphone_is_unbounded_below(self):
        # Mikrofon (90 dB) dominiert: die Rundung der Alt-CSV auf 0,1 dB macht die Untergrenze undefiniert.
        pce = [70.0, 72.0]
        result = self.reconstruct(pce, [90.0, 91.0, 89.0])
        self.assertEqual(result["ergebnis"], "BERECHNET")
        self.assertEqual(result["pce_laeq_min"], "")
        self.assertLessEqual(energetic_mean(pce), result["pce_laeq_max"])

    def test_not_computable_without_microphone_or_with_impossible_energy(self):
        self.assertEqual(pruefen.reconstruct_pce(altcsv.AltRow(0, 70.0, None, 3, "GEMISCHT"), [])["ergebnis"],
                         "NICHT_BERECHENBAR")
        self.assertEqual(pruefen.reconstruct_pce(altcsv.AltRow(0, 50.0, None, 3, "GEMISCHT"), [90.0])["ergebnis"],
                         "UNMOEGLICH_NEGATIVE_ENERGIE")


class Measurements(TempCase):
    def setUp(self):
        super().setUp()
        t = START
        # (Session, ms, Pegel, Flags): gültig / Mikrofon-Session / HOLD_MAX / GAP / Pegel 0 / NaN-nah außerhalb 160
        rows = [(1, t, 70.0, 0), (1, t + 400, 70.0, 0), (2, t + 100, 88.0, 0), (1, t + 500, 99.0, 1),
                (1, t + 600, 30.0, 8), (1, t + 700, 0.0, 0), (1, t + 800, 200.0, 0), (1, t + 1000, 65.0, 0)]
        self.database = self.tmp / "noise_database"
        make_db(self.database, measurements=rows)

    def test_reads_only_pce_sessions_and_counts_exclusions(self):
        conn = db.open_readonly(self.database)
        self.assertTrue(db.has_measurements(conn))
        stats = db.MeasurementStats()
        seconds = list(db.measurement_pce_seconds(conn, DAY, stats))
        self.assertEqual([s.sekunde for s in seconds], [START // 1000, START // 1000 + 1])
        self.assertAlmostEqual(seconds[0].laeq, 70.0)
        self.assertEqual(seconds[0].n, 2)
        # 7 PCE-Zeilen gelesen (die Mikrofon-Session zählt nicht): 2 Flag-Ausschlüsse, 2 Pegel-Ausschlüsse
        self.assertEqual((stats.rows, stats.excluded_flags, stats.excluded_level), (7, 2, 2))

    def test_pure_pce_window_cross_check_against_csv(self):
        measured = db.load_day_measurements(db.open_readonly(self.database), DAY)
        confirmed = pruefen.check_pure_pce_window(altcsv.AltRow(START, 70.0, 70.0, 2, "PCE_323"), 1, measured)
        self.assertEqual((confirmed["status"], confirmed["samples_gleich"]), ("PCE_BESTAETIGT", "JA"))
        conflict = pruefen.check_pure_pce_window(altcsv.AltRow(START, 72.0, 72.0, 2, "PCE_323"), 1, measured)
        self.assertEqual(conflict["status"], "PCE_KONFLIKT")
        missing = pruefen.check_pure_pce_window(altcsv.AltRow(START + 60_000, 72.0, 72.0, 1, "PCE_323"), 1, measured)
        self.assertEqual(missing["status"], "PCE_NICHT_PRUEFBAR")

    def test_database_without_measurements_is_reported_as_such(self):
        other = self.tmp / "ohne"
        conn = sqlite3.connect(other)
        conn.execute("CREATE TABLE level_samples (id INTEGER PRIMARY KEY, at INTEGER, levelDb REAL, source TEXT)")
        conn.commit()
        conn.close()
        self.assertFalse(db.has_measurements(db.open_readonly(other)))


class Files(TempCase):
    def test_alt_csv_with_other_header_is_rejected(self):
        path = self.tmp / "Session_x.csv"
        path.write_text("Zeit;Wert\n2001-09-07T10:00:00+02:00;50\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            altcsv.parse_alt_csv(path)

    def test_copy_refuses_overlap_and_verifies_hashes(self):
        source = self.tmp / "quelle"
        (source / "unter").mkdir(parents=True)
        (source / "unter" / "a.bin").write_bytes(b"abc")
        with self.assertRaises(ValueError):
            kopieren.copy_tree(source, source / "kopie")
        manifest = self.tmp / "manifest.csv"
        manifest.write_text(f"a.bin;{hashlib.sha256(b'abc').hexdigest()}\n", encoding="utf-8")
        destination = self.tmp / "originals"
        results = kopieren.copy_tree(source, destination, kopieren.load_manifest_hashes([manifest]))
        self.assertEqual([(r.status, r.im_manifest) for r in results], [("OK", "JA")])
        self.assertEqual((destination / "unter" / "a.bin").read_bytes(), b"abc")
        self.assertEqual((source / "unter" / "a.bin").read_bytes(), b"abc")  # Quelle unverändert

    def test_inventory_unpacks_nested_zip_flags_defects_and_duplicates(self):
        originals, work = self.tmp / "originals", self.tmp / "work"
        originals.mkdir()
        database = self.tmp / "x.db"
        make_db(database, level_samples=[(1, 60.0, "PCE_323")])
        inner = self.tmp / "inner.zip"
        with zipfile.ZipFile(inner, "w") as bundle:
            bundle.write(database, "noise_database")
        with zipfile.ZipFile(originals / "outer.zip", "w") as bundle:
            bundle.write(inner, "inner.zip")
            bundle.writestr("../evil.txt", "x")  # Zip-Slip darf nicht entpackt werden
        (originals / "leer.zip").write_bytes(b"")
        (originals / "kaputt.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 30)
        (originals / "a.csv").write_text("a;b;c\n1;2;3\n", encoding="utf-8")
        (originals / "b.csv").write_text("a;b;c\n1;2;3\n", encoding="utf-8")
        rows = {r.pfad: r for r in inventar.build_inventory(originals, work, 1)}
        self.assertEqual(rows["outer.zip!/inner.zip!/noise_database"].typ, "SQLITE")
        self.assertTrue(Path(rows["outer.zip!/inner.zip!/noise_database"].lokaler_pfad).is_file())
        self.assertTrue(Path(rows["outer.zip!/inner.zip!/noise_database"].lokaler_pfad).is_relative_to(work.resolve()))
        self.assertEqual(rows["leer.zip"].status, "defekt/unlesbar")
        self.assertIn("0 Byte", rows["leer.zip"].grund)
        self.assertEqual(rows["kaputt.zip"].status, "defekt/unlesbar")
        self.assertEqual((rows["b.csv"].status, rows["b.csv"].duplikat_von), ("Duplikat", "a.csv"))
        self.assertFalse((self.tmp / "evil.txt").exists() or (work / "evil.txt").exists())
        self.assertFalse(any("evil" in p for p in rows))

    def test_wal_frames_are_reported_separately(self):
        database = self.tmp / "noise_database"
        conn = sqlite3.connect(database)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE level_samples (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, at INTEGER NOT NULL, "
                     "levelDb REAL NOT NULL, source TEXT NOT NULL)")
        conn.execute("INSERT INTO level_samples (at, levelDb, source) VALUES (?, 60, 'PCE_323')", (START,))
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("INSERT INTO level_samples (at, levelDb, source) VALUES (?, 61, 'PCE_323')", (START + 1000,))
        conn.commit()
        snapshot = self.tmp / "kopie"
        snapshot.mkdir()
        for suffix in ("", "-wal"):
            (snapshot / f"noise_database{suffix}").write_bytes((self.tmp / f"noise_database{suffix}").read_bytes())
        conn.close()
        intact = db.open_readonly(snapshot / "noise_database")
        with_wal = db.open_readonly(snapshot / "noise_database", immutable=False)
        self.assertEqual(len(list(db.pce_seconds(intact, DAY))), 1)  # immutable=1 ignoriert -wal
        new_rows = list(db.pce_seconds(with_wal, DAY, only_id_above=db.max_id(intact)))
        self.assertEqual(len(new_rows), 1)
        self.assertAlmostEqual(new_rows[0].laeq, 61.0)


class Manifest(TempCase):
    HEADER = "Tag;Datei;Pfad;SHA256_lauf;Schema;gueltige_Sekunden_im_Zeitraum_Datei;ausgewaehlte_Sekunden_Tag\n"

    def write_manifest(self, rows):
        path = self.tmp / "csv_dateien_je_tag.csv"
        path.write_text(self.HEADER + "".join(";".join(r) + "\n" for r in rows), encoding="utf-8-sig")
        return path

    def test_reads_exactly_the_hashed_app_files_and_skips_empty_and_other_schemas(self):
        good, empty, legacy = (self.tmp / n for n in ("laermprotokoll_2001-09-14.csv", "laermprotokoll_2001-09-14 (1).csv", "x.csv"))
        write_alt_csv(good, [(START, 70.0, 1, "PCE_323")])
        empty.write_text("leer", encoding="utf-8")
        legacy.write_text("leer", encoding="utf-8")
        manifest = self.write_manifest([
            ("2001-09-14", good.name, str(good), sha256_file(good), "app-aggregate", "1", "1"),
            ("2001-09-14", empty.name, str(empty), sha256_file(empty), "app-aggregate", "0", "0"),
            ("2001-09-14", legacy.name, str(legacy), sha256_file(legacy), "legacy-meter", "5", "5"),
            ("2001-09-15", "keine", "", "", "", "0", "0")])
        self.assertEqual(cli._manifest_files(manifest), [good])

    def test_hash_mismatch_and_missing_file_abort(self):
        good = self.tmp / "laermprotokoll_2001-09-14.csv"
        write_alt_csv(good, [(START, 70.0, 1, "PCE_323")])
        wrong = self.write_manifest([("2001-09-14", good.name, str(good), "0" * 64, "app-aggregate", "1", "1")])
        with self.assertRaises(SystemExit):
            cli._manifest_files(wrong)
        missing = self.write_manifest([("2001-09-14", "nicht.csv", str(self.tmp / "nicht.csv"), "0" * 64,
                                        "app-aggregate", "1", "1")])
        with self.assertRaises(SystemExit):
            cli._manifest_files(missing)


@unittest.skipIf(pq is None, "pyarrow nicht installiert (pip install .[recovery])")
class Merge(TempCase):
    def test_priority_conflicts_validation_per_table_balance_and_sample(self):
        t = START
        database = self.tmp / "noise_database"
        make_db(database, level_samples=sorted([(t, 70.0, "PCE_323"), (t, 90.0, "MIKROFON"), (t + 1000, 65.0, "PCE_323")]),
                measurements=[(1, t, 70.05, 0), (1, t + 1000, 75.0, 0), (1, t + 2000, 60.0, 0)])
        csv_path = self.tmp / "laermprotokoll_2001-09-14.csv"
        write_alt_csv(csv_path, [(t, energetic_mean([70.0, 90.0]), 2, "GEMISCHT"), (t + 2000, 60.0, 1, "PCE_323")])
        conn = db.open_readonly(database)
        meta = {"datei": "noise_database", "sha256": "ab" * 32, "methode": "DB_INTAKT"}
        samples_csv, measured_csv = self.tmp / "e1.csv", self.tmp / "e2.csv"
        db.write_extract(db.pce_seconds(conn, DAY), samples_csv, {**meta, "tabelle": "level_samples"})
        db.write_extract(db.measurement_pce_seconds(conn, DAY), measured_csv, {**meta, "tabelle": "measurements"})
        alt = altcsv.parse_alt_csv(csv_path)
        results = [pruefen.check_mixed_window(alt[0], 1, db.load_day_samples(conn, DAY)),
                   pruefen.check_pure_pce_window(alt[1], 1, db.load_day_measurements(conn, DAY))]
        check = self.tmp / "p.csv"
        pruefen.write_dicts(results, pruefen.CHECK_COLUMNS, check)
        out = self.tmp / "pce_gerettet.parquet"
        stats = merge.merge_extracts([samples_csv, measured_csv], [check], 1, out, self.tmp / "konflikte.csv")
        rows = pq.read_table(out).to_pylist()
        self.assertEqual(stats, {"sekunden": 2, "konflikte": 1})
        second0 = [r for r in rows if r["sekunde_utc"] == t // 1000]
        self.assertEqual(len(second0), 1)
        self.assertEqual(second0[0]["tabelle"], "level_samples")
        self.assertEqual(second0[0]["validierung"], "BESTAETIGT_4A+QUERPRUEFUNG_2_TABELLEN")
        self.assertEqual(second0[0]["weitere_quellen"], 1)
        conflicted = [r for r in rows if r["sekunde_utc"] == t // 1000 + 1]  # 65 dB gegen 75 dB: nicht aufgelöst
        self.assertEqual({r["tabelle"] for r in conflicted}, {"level_samples", "measurements"})
        self.assertTrue(all(r["validierung"] == "KONFLIKT_QUELLEN" for r in conflicted))
        second2 = [r for r in rows if r["sekunde_utc"] == t // 1000 + 2]
        self.assertEqual(second2[0]["validierung"], "BESTAETIGT_PCE_CSV")
        target = self.tmp / "ziel.parquet"
        merge.write_target_parquet([(DAY, alt, 1, csv_path.name, "00" * 32)], target, self.tmp / "ziel.csv")
        balance = merge.daily_balance(target, out, self.tmp / "bilanz.csv")[0]
        self.assertEqual((balance["bereits_pce"], balance["gerettet"], balance["davon_bestaetigt"]), (1, 1, 1))
        self.assertEqual(merge.sample_per_day(out, self.tmp / "stichprobe.csv"), len(rows))


if __name__ == "__main__":
    unittest.main()
