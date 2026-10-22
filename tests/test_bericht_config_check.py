"""Tests der Konfigurationsprüfung; nur synthetische Werte, nichts Fallbezogenes."""
import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import run_bericht_config_check as check  # noqa: E402

VEREINBARUNG = "\n".join(f"{key}: 1" for key in check.FIELDS["vereinbarung.yaml"]) + "\n"
GOOD = {
    "deckblatt.yaml": "titel: T\nuntertitel: U\nzeile: Z\nnamen: N\nadresse: A\n",
    "eingaben.yaml": ("quellen:\n  csv_tagesordner: A\n  datenbank: B\n  beweissicherung: [C]\n"
                      "  kalibrierfotos_drive_ordner_id: D\n  bautaetigkeit: E\n  kalibrierung_lokal: F\n"
                      "nur_lesen: []\nausschluss_muster: ['*tagebuch*']\n"),
    "forderung.yaml": ("phasen:\n  - {name: p1, beginn: 2001-01-01, ende: 2001-03-31}\n"
                       "  - {name: p2, beginn: 2001-04-01, ende: 2001-06-30}\n"
                       "  - {name: p3, beginn: 2001-07-01}\n"),
    "vereinbarung.yaml": VEREINBARUNG,
    "luecken.yaml": ("technischer_ausfall: []\nabwesenheit: []\nruhetag_referenz_ebene1: 2001-02-03\n"
                     "ruhetag_referenz_intern: []\nungeklaerte_quelle: []\n"),
}


def make_root(patterns: bytes = b"Beispiel\n", **changes: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="bericht_check_"))
    (root / "config").mkdir()
    for name, content in {**GOOD, **changes}.items():
        (root / "config" / name).write_text(content, encoding="utf-8")
    (root / "PRIVAT_MUSTER.txt").write_bytes(patterns)
    return root


class ConfigCheckTests(unittest.TestCase):
    def test_valid_configuration_passes(self):
        self.assertEqual(check.validate(make_root()), [])

    def test_missing_file_is_reported(self):
        root = make_root()
        (root / "config" / "luecken.yaml").unlink()
        self.assertIn("luecken.yaml: fehlt", check.validate(root))

    def test_placeholder_values_are_reported(self):
        errors = check.validate(make_root(**{"deckblatt.yaml": "titel: <TITEL>\nuntertitel: U\nzeile: Z\nnamen: N\nadresse: A\n"}))
        self.assertIn("deckblatt.yaml: titel fehlt oder ist Platzhalter", errors)

    def test_root_inside_repository_is_rejected(self):
        self.assertEqual(check.validate(REPO_ROOT / "tests"), ["Privater Arbeitsordner liegt im Repository."])

    def test_source_matching_forbidden_token_is_rejected(self):
        changed = GOOD["eingaben.yaml"].replace("bautaetigkeit: E", "bautaetigkeit: Mein_Tagebuch")
        self.assertIn("eingaben.yaml: quellen.bautaetigkeit trifft Ausschlussmuster",
                      check.validate(make_root(**{"eingaben.yaml": changed})))

    def test_empty_source_list_is_reported(self):
        changed = GOOD["eingaben.yaml"].replace("beweissicherung: [C]", "beweissicherung: []")
        self.assertIn("eingaben.yaml: quellen.beweissicherung fehlt",
                      check.validate(make_root(**{"eingaben.yaml": changed})))

    def test_phase_dates_must_be_iso_ordered_and_not_overlap(self):
        bad_date = GOOD["forderung.yaml"].replace("2001-04-01", "morgen")
        self.assertIn("forderung.yaml: Phase 2 beginn braucht JJJJ-MM-TT",
                      check.validate(make_root(**{"forderung.yaml": bad_date})))
        reversed_phase = GOOD["forderung.yaml"].replace("ende: 2001-06-30", "ende: 2001-01-01")
        self.assertIn("forderung.yaml: Phase 2 endet vor ihrem Beginn",
                      check.validate(make_root(**{"forderung.yaml": reversed_phase})))
        overlap = GOOD["forderung.yaml"].replace("ende: 2001-03-31", "ende: 2001-04-15")
        self.assertIn("forderung.yaml: Phase 2 beginnt nicht nach dem Ende von Phase 1",
                      check.validate(make_root(**{"forderung.yaml": overlap})))

    def test_open_ended_third_phase_is_valid(self):
        self.assertEqual(check.validate(make_root()), [])

    def test_reference_day_must_be_a_date(self):
        changed = GOOD["luecken.yaml"].replace("2001-02-03", "irgendwann")
        self.assertIn("luecken.yaml: ruhetag_referenz_ebene1 braucht JJJJ-MM-TT",
                      check.validate(make_root(**{"luecken.yaml": changed})))

    def test_yaml_with_bom_is_read(self):
        root = make_root()
        (root / "config" / "deckblatt.yaml").write_bytes(b"\xef\xbb\xbf" + GOOD["deckblatt.yaml"].encode("utf-8"))
        self.assertEqual(check.validate(root), [])


class PatternFileTests(unittest.TestCase):
    def test_bom_does_not_break_the_first_pattern(self):
        path = make_root(b"\xef\xbb\xbfBeispiel\nZweites\n") / "PRIVAT_MUSTER.txt"
        patterns = check.load_patterns(path)
        self.assertEqual(patterns[0], "Beispiel")
        # Positivkontrolle: das erste Muster trifft wirklich.
        self.assertTrue(re.search(patterns[0], "text mit BEISPIEL darin", re.IGNORECASE))

    def test_utf16_file_is_read_without_crash(self):
        root = make_root("Beispiel\nZweites\n".encode("utf-16"))
        self.assertEqual(check.load_patterns(root / "PRIVAT_MUSTER.txt"), ["Beispiel", "Zweites"])
        self.assertEqual(check.validate(root), [])

    def test_undecodable_bytes_are_an_error_not_a_crash(self):
        errors = check.validate(make_root(b"\xff\x00\x80\x81\xfe"))
        self.assertTrue(any(error.startswith("PRIVAT_MUSTER.txt:") for error in errors))

    def test_template_placeholder_is_rejected(self):
        errors = check.validate(make_root(b"# Kommentar\n<NAME_ODER_ADRESSTEIL>\n"))
        self.assertIn("PRIVAT_MUSTER.txt: Zeile 1 ist ein Platzhalter", errors)

    def test_comment_only_file_is_empty(self):
        self.assertIn("PRIVAT_MUSTER.txt: leer", check.validate(make_root(b"# nur Kommentar\n\n")))

    def test_invalid_regex_is_reported_without_the_pattern(self):
        errors = check.validate(make_root(b"Beispiel\n(offen\n"))
        self.assertIn("PRIVAT_MUSTER.txt: Regex 2 ungültig", errors)
        self.assertFalse(any("(offen" in error for error in errors))

    def test_missing_pattern_file_is_reported(self):
        root = make_root()
        (root / "PRIVAT_MUSTER.txt").unlink()
        self.assertIn("PRIVAT_MUSTER.txt: fehlt", check.validate(root))


class OutputTests(unittest.TestCase):
    def test_main_prints_no_configuration_values(self):
        secret = "GEHEIMER_TESTWERT_4711"
        root = make_root(b"Beispiel\n", **{"forderung.yaml": GOOD["forderung.yaml"].replace("p1", secret).replace("2001-04-01", secret)})
        stdout = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["run_bericht_config_check.py", "--privat-root", str(root)]
        try:
            with contextlib.redirect_stdout(stdout):
                code = check.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(code, 1)
        self.assertNotIn(secret, stdout.getvalue())
        self.assertIn("Konfiguration unvollständig", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
