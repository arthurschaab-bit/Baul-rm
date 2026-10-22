import datetime as dt
import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from baul_rm.report_inputs import collect


def second(value):
    return int(dt.datetime.fromisoformat(value).timestamp())


class NewSourcesTests(unittest.TestCase):
    def make_db(self, path, with_samples=True):
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE sessions(id INTEGER, deviceName TEXT);
            CREATE TABLE measurements(id INTEGER,sessionId INTEGER,timestamp INTEGER,
                levelDb REAL,weighting TEXT,flags INTEGER,timeWeighting TEXT);
            INSERT INTO sessions VALUES (1,'PCE-323');
        """)
        if with_samples:
            con.execute("CREATE TABLE level_samples(id INTEGER, at INTEGER, levelDb REAL, source TEXT)")
        return con

    def test_fallback_only_for_zero_raw_measurement_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "synthetic.sqlite"
            con = self.make_db(db)
            t = second("2001-01-02T07:00:00+01:00")
            con.executemany("INSERT INTO level_samples VALUES (?,?,?,?)", [
                (1, t*1000, 60, "PCE_323"), (2, t*1000+100, 70, "PCE_323"),
                (3, t*1000, 120, "MIKROFON")])
            con.commit()
            con.close()
            data, audit, _ = collect(root, "2001-01-02", "2001-01-02", [db])
            self.assertEqual(len(data), 1)
            self.assertAlmostEqual(data.loc[t, "leq"], 10*__import__("math").log10((10**6+10**7)/2))
            self.assertEqual(data.loc[t, "maximum"], 70)
            self.assertEqual(data.loc[t, "raw"], 70)
            self.assertEqual(data.loc[t, "origin"], "DB:synthetic.sqlite:level_samples")
            self.assertTrue(any(item.get("fallback") == "level_samples" for item in audit["database_days"]))
            con = sqlite3.connect(db)
            con.execute("INSERT INTO measurements VALUES (1,1,?,?,?,? ,?)", (t*1000, 80, "A", 1, "FAST"))
            con.commit()
            con.close()
            data, audit, _ = collect(root, "2001-01-02", "2001-01-02", [db])
            self.assertEqual(len(data), 0)
            self.assertFalse(any(item.get("fallback") == "level_samples" for item in audit["database_days"]))
            con = sqlite3.connect(db)
            con.execute("UPDATE measurements SET flags=0")
            con.commit()
            con.close()
            data, _, _ = collect(root, "2001-01-02", "2001-01-02", [db])
            self.assertEqual(data.loc[t, "leq"], 80)
            self.assertEqual(data.loc[t, "origin"], "DB:synthetic.sqlite")

    def test_missing_samples_table_is_recorded_without_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "synthetic.sqlite"
            self.make_db(db, False).close()
            data, audit, _ = collect(root, "2001-01-02", "2001-01-02", [db])
            self.assertTrue(data.empty)
            self.assertIn("fallback_unavailable", audit["database_days"][0])

    def test_missing_sample_column_is_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "synthetic.sqlite"
            con = self.make_db(db, False)
            con.execute("CREATE TABLE level_samples(id INTEGER, at INTEGER, levelDb REAL)")
            con.commit()
            con.close()
            data, audit, _ = collect(root, "2001-01-02", "2001-01-02", [db])
            self.assertTrue(data.empty)
            self.assertIn("fallback_unavailable", audit["database_days"][0])

    def test_manifest_sessions_replace_mixed_and_supplement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            day = root / "2001-01-02"
            day.mkdir()
            (day / "laermprotokoll_day.csv").write_text(
                "Zeit;LAeq_dB;LAFmax_dB;Quelle;Samples\n"
                "2001-01-02T07:00:00+01:00;90;90;GEMISCHT;1\n", encoding="utf-8")
            # The ordinary daily-folder Session CSV remains UNBEKANNT and excluded.
            (day / "Session_existing.csv").write_text(
                "Zeit;Pegel_dB;Bewertung;Flags\n"
                "2001-01-02T07:00:02+01:00;90;A;0\n", encoding="utf-8")
            session = root / "mapped.csv"
            session.write_text(
                "\ufeffZeit;Pegel_dB;Bewertung;Zeitbewertung;Bereich;Flags\n"
                "2001-01-02T07:00:00.000+01:00;60,0;A;FAST;RANGE_50_100;0\n"
                "2001-01-02T07:00:01.000+01:00;60,0;A;FAST;RANGE_50_100;0\n"
                "2001-01-02T07:00:01.100+01:00;70,0;A;FAST;RANGE_50_100;0\n"
                "2001-01-02T07:00:03.000+01:00;90,0;A;FAST;RANGE_50_100;1\n"
                "2001-01-02T07:00:04.000+01:00;0;A;FAST;RANGE_50_100;0\n"
                "2001-01-02T07:00:05.000+01:00;161;A;FAST;RANGE_50_100;0\n",
                encoding="utf-8")
            sha = hashlib.sha256(session.read_bytes()).hexdigest()
            mapping = root / "mapping.csv"
            mapping.write_text("datei;sha256;geraet_laut_app;bemerkung\n"
                               f"mapped.csv;{sha};PCE-323;synthetic\n", encoding="utf-8")
            t = second("2001-01-02T07:00:00+01:00")
            data, audit, manifest = collect(root, "2001-01-02", "2001-01-02", [], session_manifest=mapping)
            self.assertEqual(data.index.tolist(), [t, t+1])
            self.assertEqual(data.loc[t, "leq"], 60)
            self.assertEqual(data.loc[t, "origin"], "SESSION:mapped.csv")
            self.assertAlmostEqual(data.loc[t+1, "leq"], 10*__import__("math").log10((10**6+10**7)/2))
            self.assertEqual(audit["sessions"][0]["excluded_rows"], 3)
            self.assertEqual(audit["sessions"][0]["used_seconds"], 2)
            self.assertEqual(manifest[0]["type"], "CSV")
            self.assertEqual(manifest[-1]["type"], "SESSION-CSV")
            mapping.write_text("datei;sha256;geraet_laut_app;bemerkung\n"
                               f"mapped.csv;{'0'*64};PCE-323;synthetic\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                collect(root, "2001-01-02", "2001-01-02", [], session_manifest=mapping)
            mapping.write_text("datei;sha256;geraet_laut_app;bemerkung\n"
                               f"mapped.csv;{sha};Other;synthetic\n", encoding="utf-8")
            data, audit, _ = collect(root, "2001-01-02", "2001-01-02", [], session_manifest=mapping)
            self.assertTrue(data.empty)
            self.assertIn("ignored", audit["sessions"][0])

    def test_old_session_format_and_database_overlap_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "synthetic.sqlite"
            con = self.make_db(db, False)
            t = second("2001-01-02T07:00:00+01:00")
            con.execute("INSERT INTO measurements VALUES (1,1,?,?,?,?,?)",
                        (t*1000, 50, "A", 0, "FAST"))
            con.commit()
            con.close()
            session = root / "old.csv"
            session.write_text("Zeit;Pegel_dB;Bewertung;Flags\n"
                               "2001-01-02T07:00:00+01:00;70,0;A;0\n"
                               "2001-01-02T07:00:01+01:00;60,0;A;0\n",
                               encoding="utf-8")
            sha = hashlib.sha256(session.read_bytes()).hexdigest()
            mapping = root / "mapping.csv"
            mapping.write_text("datei;sha256;geraet_laut_app;bemerkung\n"
                               f"old.csv;{sha};PCE-323;synthetic\n", encoding="utf-8")
            data, audit, _ = collect(root, "2001-01-02", "2001-01-02", [db], session_manifest=mapping)
            self.assertEqual(data.loc[t, "leq"], 50)
            self.assertEqual(data.loc[t+1, "leq"], 60)
            self.assertEqual(data.loc[t+1, "time_weighting"], "")
            self.assertTrue(any(item["origin"] == "SESSION:old.csv"
                                and item["conflicting_seconds"] == 1 for item in audit["overlaps"]))

    def test_manifest_session_outside_day_window_does_not_change_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "early.csv"
            session.write_text("Zeit;Pegel_dB;Bewertung;Flags\n"
                               "2001-01-02T06:59:59+01:00;80;A;0\n"
                               "2001-01-02T20:00:00+01:00;80;A;0\n",
                               encoding="utf-8")
            sha = hashlib.sha256(session.read_bytes()).hexdigest()
            mapping = root / "mapping.csv"
            mapping.write_text("datei;sha256;geraet_laut_app;bemerkung\n"
                               f"early.csv;{sha};PCE-323;synthetic\n", encoding="utf-8")
            data, audit, _ = collect(root, "2001-01-02", "2001-01-02", [], session_manifest=mapping)
            self.assertTrue(data.empty)
            self.assertEqual(audit["sessions"][0]["outside_day_window_seconds"], 2)

    def test_mapped_session_is_not_read_twice_from_daily_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            day = root / "2001-01-02"
            day.mkdir()
            mapped = root / "sessions"
            mapped.mkdir()
            name = "Session_02.01.2001_0700.csv"
            content = ("Zeit;Pegel_dB;Bewertung;Flags\n"
                       "2001-01-02T07:00:00+01:00;60;A;0\n")
            (day / name).write_text(content, encoding="utf-8")
            shutil.copyfile(day / name, mapped / name)
            sha = hashlib.sha256((mapped / name).read_bytes()).hexdigest()
            mapping = root / "mapping.csv"
            mapping.write_text("datei;sha256;geraet_laut_app;bemerkung\n"
                               f"sessions/{name};{sha};PCE-323;synthetic\n", encoding="utf-8")
            without, _, _ = collect(root, "2001-01-02", "2001-01-02", [])
            self.assertTrue(without.empty)
            data, audit, _ = collect(root, "2001-01-02", "2001-01-02", [], session_manifest=mapping)
            self.assertEqual(len(data), 1)
            self.assertEqual(data.iloc[0]["origin"], "SESSION:" + name)
            self.assertEqual(len(audit["session_files_skipped_in_daily_scan"]), 1)


if __name__ == "__main__":
    unittest.main()
