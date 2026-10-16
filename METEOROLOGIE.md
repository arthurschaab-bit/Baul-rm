# DWD-Wetter und zwei Berichtsvarianten

```powershell
python run_meteo.py --start 2026-05-20 --end 2026-10-10
```

Der erste Abruf lädt ausschließlich Beobachtungen und Stationsmetadaten des Deutschen Wetterdienstes. Ein identischer Aufruf verwendet den geprüften Cache; `--refresh` legt einen neuen unveränderlichen Rohdatenordner an. Alle Abrufdateien bleiben bytegetreu, einschließlich Verzeichnisindizes, Parameterbeschreibungen und Stationshistorien. Das Manifest enthält URL, HTTP-Status, UTC-Abruf, Größe und SHA-256. `processed/audit.json` dokumentiert Korrekturen zwischen Abrufen, Ersatzstationen und den unabhängigen Tagesabgleich.

`data/meteo/processed/meteo_10min.csv` enthält End- und Beginnzeit mit UTC-Offset, Messwerte, QN, Station, Archivklasse und Originaltabellenname je Parameter. Spaltenkennungen und Einheiten werden aus `Metadaten_Parameter` bestimmt. Der Zeitstempel gehört zu den **vorangegangenen** zehn Minuten. UTC wird mit `Europe/Berlin` umgerechnet. Fehlwert -999 wird NaN; keine Nullfüllung. Die Pipeline unterstützt UTC-Daten ab 2000. Stationshistorien und der abweichende Zeitbezug älterer Geräte bleiben explizit dokumentiert.

Archivpriorität: historical, recent, now; now wird nur für die jüngsten Tage geprüft. Recent ist ebenfalls vorläufig (Qualitätsprüfung noch nicht abgeschlossen). Es gibt keinen Fallback auf Modelldaten. Fehlende essentielle Parameter werden mit der nächstgelegenen verfügbaren DWD-Beobachtungsstation ergänzt, ausgehend von der primären Stationsposition; jede verwendete Station und jeder betroffene Zeitstempel sind im Audit. Bleibt eine Lücke oder scheitert ein Download nach drei Versuchen, schlägt der Abruf fehl und ersetzt den letzten vollständigen Stand nicht. HTTPS-Zertifikate werden geprüft, unter Windows bei Bedarf mit dem Windows-Vertrauensspeicher.

## Gemeinsame Berechnung

`MeteoConfig` definiert `meteo_mode = "ausweisen" | "ausschliessen"`, Regen-Nachlauf (10 min), Mittelwind (5 m/s), Böe (10 m/s) und Intensitätsklassen. `prepare_variants` erzeugt **immer beide** Auswahlen, unabhängig von der bevorzugten Anzeigevariante. Nur bereits ausgewählte reine PCE-Sekunden dürfen hinein.

- Regen: Menge >0 oder Dauer >0; eine bekannte trockene Zehn-Minuten-Lücke kann Ereignisse verbinden, fehlende Werte trennen. Intensität = Menge ×6. Klassen: <2,5 / 2,5–10 / >10 mm/h.
- Wind: Mittel ≥5 oder Böe ≥10 m/s; zusammenhängende zehnminütige Intervalle, keine trockene Brückenregel. Richtung zirkulär, nach Windgeschwindigkeit gewichtet; Beaufort aus dem höchsten Mittelwind der Periode.
- A enthält alle gültigen PCE-Sekunden. B entfernt Regenperioden inklusive Nachlauf sowie Windperioden. Grenzen sind [Beginn, Ende), Überlappungen zählen einmal. Abdeckung bleibt n/46.800; Untergrenze LAeq + 10 log10(n/46.800). Keine Pegelkorrektur, Hochrechnung oder Ersatzwerte.
- Für Dauerphasen muss `duration_phases(..., excluded_intervals=...)` verwendet werden. Die Funktion trennt vor der Minutenbildung auch bei nur einer ausgeschlossenen Sekunde. Vollständig gemessene niedrige Minuten trennen ebenfalls.
- Sensitivität: A, ohne Regen/Nachlauf, ohne Wind, B. Jede Auswahl liefert LAeq, gültige Sekunden, Abdeckung, Stufe, Untergrenze und Aussagen >55/>60. Tageswetter enthält 00–07 / 07–20 / 20–24 getrennt.

Der Niederschlagsabgleich verwendet `daily/kl` derselben Station. Die aktuelle RSK-Metadatenzeile muss den Bezugszeitraum 05:51–05:50 UTC Folgetag belegen; abweichende historische Regeln erfordern eine explizite Erweiterung statt still falscher Vergleiche. Differenzen >0,5 mm werden protokolliert. Für den mitgelieferten Beobachtungszeitraum sind alle 144 Vergleiche vollständig; keine Differenz >0,5 mm.

## Private Berichtsanbindung

Adresse, Gebäudekoordinate, Fotos, PCE-Dateien und Falltexte bleiben im externen Profil. Das öffentliche Wettermodul nimmt keine private Adresse entgegen. Ein lokales Profil lädt die ausgewählten PCE-Sekunden, ruft `prepare_variants` auf und lässt den privaten Fallrenderer **für jede Auswahl vollständig** laufen. Es übergibt die Ausschlussintervalle an die Phasenbildung und rendert Kopfzahlen, Monatstabelle, Übersichten, Stunden, Dauerphasen, Tagesseiten, Messfenster und vierseitiges Dossier neu. Das Profil gibt den externen Ausgabeordner zurück; der Launcher lehnt einen Lauf ohne alle vier PDFs ab.

`install_daily_weather` integriert native Regenbalken mit Mustern, Mittelwindlinie, Böen und Schwellen sowie alle Wetterereignisse in die Tagesseite des bestehenden Renderers. Messlücken haben ////; Wetterausschlüsse xxx. Nachtpegel bleiben in den getrennten Kennzahlen. Über 14 Ereignisse werden unmittelbar auf Fortsetzungsseiten im gleichen Stil ausgegeben. Der private Fallrenderer muss seine internen Seitenverweise aus dem resultierenden Inhaltsindex erzeugen.

`comparison_pdf` rendert den gemeinsamen Vergleich einmal aus `Vergleich_A_B.csv`. Derselbe PDF-Abschnitt wird unverändert in beide Anlagenbände eingesetzt. Alle Seiten dieses gemeinsamen Abschnitts tragen beide vollständigen Variantenbezeichnungen; damit bleiben ihre Inhaltsströme byte-identisch. `finalize_pair` prüft diese Identität, sämtliche Variantenkennzeichnungen und alle vier Dateien und schreibt Hashes aller Ausgaben in das private Manifest. Ein Manifest kann sich selbst nicht enthalten.

Ausgaben unter `reports/<datum>/` im **privaten** Arbeitsbereich:

```text
Gesamtbericht_A_Ausweisung.pdf
Gesamtbericht_B_Ausschluss.pdf
Kompaktbericht_A_Ausweisung.pdf
Kompaktbericht_B_Ausschluss.pdf
Vergleich_A_B.csv
ausschluesse_meteo.csv
Wetterereignisse.csv
Wetter_je_Tag_und_Nachtsegment.csv
manifest.json
```

Beide Varianten verwenden denselben vollständigen Code-Commit und denselben Wetterabruf. Alle Wetterzahlen stammen aus der normalisierten CSV. Der Kompaktbericht verwendet seine eigenen Kennwerte und einen aus der Vergleichs-CSV abgeleiteten Satz zur Gegenvariante. Kalibrier- und Quellenregeln bleiben unverändert.

Quellen/Lizenz: [DWD CDC 10-Minuten-Niederschlag](https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/precipitation/), [Wind](https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/wind/), [Extremwind](https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/extreme_wind/), [Tageswerte](https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/daily/kl/). Datenbasis: Deutscher Wetterdienst, CC BY 4.0.
