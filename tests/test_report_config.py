from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "pipeline" / "Verknuepfung" / "scripts" / "gesamtbericht_lib_v3.py"
spec = importlib.util.spec_from_file_location("gesamtbericht_lib_v3_report_config", SCRIPT)
lib = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(lib)


class PeriodeFuerTests(unittest.TestCase):
    """report.measurement_periods (settings.local.json) - Kernstelle der Umstellung
    von fest einprogrammierten Kalibrier-/Mikrofonhoehe-Daten auf Konfiguration."""

    PERIODEN = [
        {
            "from": "2026-06-01",
            "to": "2026-06-14",
            "indoor": False,
            "mic_height": "98–100 cm (Möbel-Aufbau)",
            "calibration": "Werkskalibrierung, PCE-SC 43 (94 dB(A))",
            "calibration_documented": False,
        },
        {
            "from": "2026-06-15",
            "to": None,
            "indoor": False,
            "mic_height": "140 cm (freier Ständer)",
            "distance_m": 3.5,
            "calibration": "94 dB(A), PCE-SC 43; vor Messung, protokolliert",
            "calibration_documented": True,
        },
        {
            "from": "2026-05-20",
            "to": "2026-05-31",
            "indoor": True,
            "window_state": "geschlossen",
        },
    ]

    def test_findet_passenden_zeitraum_mit_offenem_ende(self) -> None:
        with patch.object(lib, "MESSZEITRAEUME", self.PERIODEN):
            periode = lib.periode_fuer("2026-07-01")
        self.assertEqual(periode["mic_height"], "140 cm (freier Ständer)")

    def test_findet_passenden_zeitraum_mit_geschlossenem_ende(self) -> None:
        with patch.object(lib, "MESSZEITRAEUME", self.PERIODEN):
            periode = lib.periode_fuer("2026-06-08")
        self.assertEqual(periode["mic_height"], "98–100 cm (Möbel-Aufbau)")

    def test_tag_nach_bis_gehoert_nicht_mehr_zum_zeitraum(self) -> None:
        with patch.object(lib, "MESSZEITRAEUME", self.PERIODEN):
            periode = lib.periode_fuer("2026-06-15")
        # ab 15.06. gilt bereits der zweite Zeitraum, nicht mehr der erste (bis 14.06.)
        self.assertEqual(periode["mic_height"], "140 cm (freier Ständer)")

    def test_kein_passender_zeitraum_liefert_none(self) -> None:
        with patch.object(lib, "MESSZEITRAEUME", self.PERIODEN):
            periode = lib.periode_fuer("2026-01-01")
        self.assertIsNone(periode)

    def test_mikrofonhoehe_ohne_passenden_zeitraum_ist_nicht_dokumentiert(self) -> None:
        with patch.object(lib, "MESSZEITRAEUME", []):
            self.assertEqual(lib.mikrofonhoehe_fuer("2026-06-01", False), "nicht dokumentiert")
            self.assertEqual(lib.mikrofonhoehe_fuer("2026-06-01", True), "nicht dokumentiert (Innenraum)")

    def test_kalibrierung_ohne_passenden_zeitraum_ist_nicht_dokumentiert(self) -> None:
        with patch.object(lib, "MESSZEITRAEUME", []):
            self.assertEqual(lib.kalibrierung_fuer("2026-06-01"), "nicht dokumentiert")

    def test_entfernung_nur_wenn_konfiguriert(self) -> None:
        with patch.object(lib, "MESSZEITRAEUME", self.PERIODEN):
            self.assertEqual(lib.entfernung_fuer("2026-07-01"), 3.5)
            self.assertIsNone(lib.entfernung_fuer("2026-06-08"))  # erster Zeitraum hat kein distance_m

    def test_fensterzustand_nur_fuer_konfigurierten_zeitraum(self) -> None:
        with patch.object(lib, "MESSZEITRAEUME", self.PERIODEN):
            self.assertEqual(lib.fensterzustand_fuer("2026-05-25"), "geschlossen")
            self.assertIsNone(lib.fensterzustand_fuer("2026-07-01"))  # kein window_state konfiguriert


class GeraetAnzeigeTests(unittest.TestCase):
    def test_kombiniert_hersteller_modell_und_klasse(self) -> None:
        with patch.object(lib, "GERAET_HERSTELLER", "PCE Instruments"), \
             patch.object(lib, "GERAET_MODELL", "PCE-323"), \
             patch.object(lib, "GERAET_KLASSE", "Klasse 2 (IEC 61672-1:2013)"):
            self.assertEqual(
                lib.geraet_anzeige(),
                "PCE Instruments PCE-323, Klasse 2 (IEC 61672-1:2013)",
            )

    def test_ohne_konfiguration_zeigt_ehrliche_nicht_angegeben_statt_leerstring(self) -> None:
        with patch.object(lib, "GERAET_HERSTELLER", ""), \
             patch.object(lib, "GERAET_MODELL", ""), \
             patch.object(lib, "GERAET_KLASSE", ""):
            self.assertEqual(lib.geraet_anzeige(), "nicht angegeben")

    def test_seriennummer_nur_wenn_angefordert_und_gesetzt(self) -> None:
        with patch.object(lib, "GERAET_HERSTELLER", ""), \
             patch.object(lib, "GERAET_MODELL", "PCE-323"), \
             patch.object(lib, "GERAET_KLASSE", ""), \
             patch.object(lib, "GERAET_SERIENNUMMER", "12345678"):
            self.assertEqual(lib.geraet_anzeige(), "PCE-323")
            self.assertEqual(lib.geraet_anzeige(mit_seriennummer=True), "PCE-323 (Serien-Nr. 12345678)")


class KalibrierungsUebersichtTests(unittest.TestCase):
    def test_fasst_alle_konfigurierten_zeitraeume_zusammen(self) -> None:
        perioden = [
            {"from": "2026-06-01", "to": "2026-06-14", "calibration": "Werkskalibrierung"},
            {"from": "2026-06-15", "to": None, "calibration": "Feldkalibrierung, protokolliert"},
        ]
        with patch.object(lib, "MESSZEITRAEUME", perioden):
            uebersicht = lib.kalibrierungs_uebersicht()
        self.assertIn("2026-06-01–2026-06-14: Werkskalibrierung", uebersicht)
        self.assertIn("ab 2026-06-15: Feldkalibrierung, protokolliert", uebersicht)

    def test_ohne_zeitraeume_ist_nicht_dokumentiert(self) -> None:
        with patch.object(lib, "MESSZEITRAEUME", []):
            self.assertEqual(lib.kalibrierungs_uebersicht(), "nicht dokumentiert")


if __name__ == "__main__":
    unittest.main()
