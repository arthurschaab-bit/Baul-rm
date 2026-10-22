# Öffentliche Pipeline, private Berichtsdaten

Adresse, Name, Messdateien, Kalibrierfotos, Tagebuch, Berichtsvorlagen mit Falltexten und erzeugte PDFs bleiben außerhalb dieses Repositorys. Der öffentliche Rechenkern enthält ausschließlich allgemeine Regeln und synthetische Tests.

Ein privates Python-Profil definiert `run(core)` und ruft die lokale Berichtserzeugung auf. Der Pfad wird nur beim lokalen Start übergeben:

```powershell
python run_private_report.py --profile C:\PrivateReports\profile.py
```

Das Profil kann eine ebenfalls private JSON-Datei mit Adresse, Name, Eingabepfaden und Belegen lesen. Es wird ausdrücklich nicht im Checkout gespeichert. Der Launcher lehnt Profile innerhalb des Repositorys ab.

Der Rechenkern bietet die PCE-Quellenauswahl mit DB-Vorrang für gemischte Sekunden, energetischen LAeq und die lauteste Stunde ohne Vorrang leiserer, vollständigerer Fenster. Er verändert keine Eingabewerte. Herkunft und ausgeschlossene Sekunden werden zurückgegeben, damit das private Profil sie im Prüfprotokoll dokumentieren kann.

`run_pce_inputs.py` importiert ältere Geräte-CSV, App-Aggregate, App-Sitzungen und reine PCE-Datenbankzeilen. Die lokalen Eingaben und Ausgaben werden ausschließlich per Parameter übergeben. Beispiel mit fiktiven Pfaden und Daten:

Fehlen PCE-Zeilen eines Tages in `measurements` vollständig, kann `level_samples` als PCE-Datenbankquelle einspringen.
Mit `--session-manifest` werden ausschließlich extern zugeordnete und per SHA-256 geprüfte PCE-Session-Exporte zusätzlich eingelesen.

```powershell
python run_pce_inputs.py --input C:\PrivateReports\exports --database C:\PrivateReports\backup.sqlite --start 2001-01-01 --end 2001-01-31 --output C:\PrivateReports\result
```

`duration_phases` zählt eine Phase erst bei mindestens zehn qualifizierten energetischen Minuten und mindestens 600 tatsächlich gemessenen Sekunden über 60 dB. Gemessene niedrige Minuten trennen Phasen. Die lauteste zulässige Stunde kann bei kurzen, isolierten lauten Abschnitten unter dem Gesamt-LAeq liegen; die Berechnung erzwingt deshalb keine mathematisch unzulässige Ungleichung.

```python
def run(core):
    # Load local inputs and case configuration here.
    selected, excluded, audit = core.resolve_pce_seconds(exports, database_pce)
    # Render local reports from selected seconds and local templates.
```

Dieser Entwurf ist nur eine Schnittstellenillustration; ein ausführbares Profil muss seine lokalen Eingaben selbst laden. Die Originale und der vollständige Fallrenderer bleiben im privaten Arbeitsordner. Keine realen Messwerte werden für Tests benötigt.

Nur vertrauenswürdige lokale Profile ausführen: Das Python-Profil kann beliebigen Code mit den Rechten des laufenden Prozesses ausführen. Die Checkout-Wurzel wird vom Launcher explizit übergeben; die Prüfung funktioniert auch mit installiertem Paket. Import- und Ausgabepfade müssen außerhalb des Checkouts liegen. Es wird kein Pickle exportiert. Die breiten PDF-/Pickle-/NPZ-Ausschlüsse sind absichtlich zusätzliche Vorsorge gegen versehentliches Veröffentlichen von Belegen; öffentliche synthetische Fixtures sollen Textdateien sein.

Die Kriterien für Dauerphasen sind getrennt: energetischer Minuten-LAeq >=60 dB qualifiziert eine Minute; nur tatsächlich gemessene Sekunden >60 dB zählen zur Dauer (mindestens 600). Genau 60 dB liefert keine Dauer über 60. Explizite Wetterausschlüsse trennen Phasen auch bei kurzen Lücken.

Das lokale Profil gibt seit der Wettererweiterung seinen privaten Ausgabeordner zurück. Jeder reguläre Lauf erzeugt beide vollständigen Varianten; der Launcher prüft alle vier PDFs. Die bevorzugte Variante lässt sich mit --meteo-mode ausweisen bzw. ausschliessen wählen. Details und Hash-Prüfung: METEOROLOGIE.md.
