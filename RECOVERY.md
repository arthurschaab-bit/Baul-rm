# Rückgewinnung reiner PCE-323-Sekunden

Werkzeuge, um aus App-Sicherungen und Alt-CSVs die reinen PCE-323-Sekunden für Zeiträume zurückzugewinnen, die bisher nur als
GEMISCHT, MIKROFON oder gar nicht vorliegen. Alles ist nur lesend; Eingaben werden nie verändert. Der Code steht unter
`src/baul_rm/recovery/`, Start über `run_recovery.py`. Nur Standardbibliothek, für Parquet zusätzlich `pyarrow`
(`pip install .[recovery]`). Wie überall in diesem Repository gilt `PRIVATE_REPORTS.md`: Messdateien, Datenbanken,
Adresse und Name bleiben außerhalb des Repositorys; Tests nutzen nur synthetische Daten.

**Stand:** getestet nur mit synthetischen Daten (`tests/test_recovery.py`). An echten Datenbanken ist bisher nichts davon
gelaufen.

## Welche Tabellen gelesen werden

Die App speichert PCE-Werte in zwei Tabellen, die dieser Code getrennt auswertet und gegeneinander prüft:

| Tabelle | Inhalt | Rolle hier |
|---|---|---|
| `measurements` + `sessions` | dauerhafte Messreihe; PCE-Zeilen über `sessions.deviceName='PCE-323'`; Rohwerte laut App-Code 90 Tage, danach Minutenaggregate | dieselbe Quelle wie `report_inputs.py`; Zeilen mit Flags 1, 2 oder 8 und Pegel außerhalb 0 < L ≤ 160 werden ausgeschlossen und gezählt |
| `level_samples` | Puffer des Drive-Sync mit Quelle `PCE_323`/`MIKROFON`; Löschregeln laut App-Code: 30 Tage, bereits synchronisierte Tage nach 3 Tagen | Grundlage des alten GEMISCHT-Exports, daher nötig für den Echtheitstest 4a |

## Regeln, die der Code erzwingt

| Regel | Umsetzung |
|---|---|
| Nie in die Quelle schreiben | `kopieren` liest die Quelle nur mit `open(..., 'rb')` und bricht ab, wenn Quelle und Ziel sich überlappen |
| SHA-256 vor/nach, Manifeste | Hash der Quelle und der Kopie im Kopierprotokoll; jeder 64-stellige Hex-Wert in Manifestdateien zählt als bekannter Hash (`im_manifest`); Cloud-Platzhalter werden als `NUR_ONLINE` gemeldet |
| SQLite nur lesend | `file:<pfad>?mode=ro&immutable=1`; `immutable=1` ignoriert `-wal`, deshalb gibt es `--wal-kopie` (WAL-Frames getrennt, Methode `WAL`) |
| Nie erzeugen, nie Mikrofon als Pegel | `extrahieren` liest nur PCE-Zeilen; Mikrofonzeilen nutzt nur `pruefen` (4a/4c) |
| Herkunft je Sekunde | `datei`, `sha256`, `tabelle`, `id_von`/`id_bis`, `methode`, `runde` |

## Befehle (Beispiel Runde 1, PowerShell)

Alle Pfade außerhalb des Checkouts; `$R` ist der private Arbeitsordner.

```powershell
python run_recovery.py kopieren --quelle <Quelle> --ziel "$R/originals" --manifest <Manifest.csv> --out "$R/kopierprotokoll_runde1.csv"
python run_recovery.py inventar --originals "$R/originals" --work "$R/work" --runde 1 --out "$R/inventar_runde1.csv"
python run_recovery.py schema --db <db-kopie> --out "$R/schema_<hash>.md" --integrity
python run_recovery.py ziel --csv-ordner <csv-ordner> --von <YYYY-MM-DD> --bis <YYYY-MM-DD> --out-dir $R
python run_recovery.py extrahieren --db <db-kopie> --von <YYYY-MM-DD> --bis <YYYY-MM-DD> --runde 1 --out-dir "$R/work/extrakte"
python run_recovery.py pruefen --db <db-kopie> --csv-ordner <csv-ordner> --von <YYYY-MM-DD> --bis <YYYY-MM-DD> --runde 1 --out-dir "$R/work/pruefungen"
python run_recovery.py zusammenfuehren --extrakte $R/work/extrakte/extrakt_*.csv --pruefungen $R/work/pruefungen/pruefung_*.csv --runde 1 --out-dir $R
python run_recovery.py bilanz --ziel $R/ziel_sekunden.parquet --gerettet $R/pce_gerettet.parquet --out $R/tagesbilanz_runde1.csv
python run_recovery.py stichprobe --gerettet $R/pce_gerettet.parquet --out $R/stichprobe_4e.csv
```

### Mehrere Sicherungen prüfen (z. B. heruntergeladene Drive-Versionen)

Heruntergeladene Dateien heißen alle gleich. Jede Datei **sofort nach dem Download** mit Versionsnummer und Zeitpunkt umbenennen
(z. B. `v21_2026-09-12_1935.zip`) und nach `$R\originals\drive_versionen` legen. Das Inventar entpackt die ZIPs, erkennt die
SQLite-Dateien (Spalte `lokaler_pfad`) und markiert inhaltsgleiche Dateien als Duplikat. Danach je Datenbank das Schema:

```powershell
$R = "D:\recovery_runde2"
python run_recovery.py inventar --originals "$R\originals" --work "$R\work" --runde 2 --out "$R\inventar_runde2.csv"
Import-Csv "$R\inventar_runde2.csv" -Delimiter ';' | Where-Object { $_.typ -eq 'SQLITE' -and -not $_.duplikat_von } | ForEach-Object {
  $name = [IO.Path]::GetFileNameWithoutExtension(($_.herkunft_archiv -split '/')[-1])
  python run_recovery.py schema --db $_.lokaler_pfad --out "$R\schema_$name.md" --integrity
}
```

`schema_<name>.md` nennt Tabellen, Zeilenzahlen und `level_samples` je Tag und Quelle. Damit lässt sich entscheiden, welche Version
`extrahieren` und `pruefen` braucht, ohne die großen Dateien zu übertragen.

`ziel` und `pruefen` nehmen statt `--csv-ordner` auch `--csv-manifest <csv_dateien_je_tag.csv>` (Datei eines früheren Laufs mit den
Spalten `Schema`, `Pfad`, `SHA256_lauf`, `gueltige_Sekunden_im_Zeitraum_Datei`). Gelesen werden nur App-Aggregat-CSVs mit gültigen
Sekunden; der Hash jeder Datei wird gegen das Manifest geprüft, bei Abweichung oder fehlender Datei bricht der Befehl ab. So wird
genau die Fassung gelesen, die der frühere Lauf verwendet hat, und nicht eine spätere oder ältere Kopie in einem Sicherungsordner.

## Methoden

- **Zeitbasis:** Europe/Berlin; Auswertefenster 07:00–20:00, also 46.800 s je Tag.
- **1-s-Raster:** Alle echten PCE-Samples derselben Epoch-Sekunde werden energetisch gemittelt (`laeq`), dazu Min, Max und
  Anzahl `n`. Keine Interpolation; Sekunden ohne PCE-Sample fehlen.
- **Echtheitstest 4a:** Vor Commit `d64e84a` der Android-App mittelte der Export **alle** Roh-Samples des Fensters beider
  Quellen energetisch, also nach Probenzahl gewichtet. `pruefen` rechnet genauso aus `level_samples` und vergleicht mit
  `LAeq_dB` der Alt-CSV (≤ 0,15 dB = `BESTAETIGT`, sonst `KONFLIKT`, ohne DB-Samples `NICHT_PRUEFBAR`). Das Fenster ist
  einstellbar (1–60 s, Standard 1 s); es wird empirisch aus dem Zeilenabstand bestimmt (`ziel_tageszusammenfassung.csv`).
  Bei Fenstern > 1 s erbt jede Sekunde den Status ihres Fensters.
- **Querprüfung `measurements`:** 4a kann `measurements`-Werte nicht bestätigen (der Export entstand aus `level_samples`).
  Deshalb vergleicht `pruefen` bei 1-s-Fenstern jedes reine `PCE_323`-Fenster der Alt-CSV mit dem PCE-Mittel aus
  `measurements` (`PCE_BESTAETIGT`/`PCE_KONFLIKT`). Das zeigt, ob `measurements` dieselben Werte trägt wie der App-Export.
- **4c (nur informativ):** `E_pce = N·10^(L/10) − Σ10^(Lmic/10)`, `n_pce = N − n_mic`, mit Intervall aus der Rundung der
  Alt-CSV (±0,05 dB). Ausgabe nur in `rekonstruiert_nicht_verwendbar_<hash>.csv`, nie in `pce_gerettet.parquet`.
  `--auch-mit-pce` rechnet auch dort, wo echte PCE-Samples existieren (Methodentest). Bei lautem Mikrofon ist die Untergrenze
  undefiniert (leeres Feld) – das ist der Grund, warum das Ergebnis nie ein Messwert ist.
- **Konflikte (4b):** Kandidaten derselben Sekunde innerhalb 0,15 dB bestätigen sich (`weitere_quellen`), sonst stehen alle
  Kandidaten mit `KONFLIKT_QUELLEN` im Ergebnis und in `konflikte.csv`; nichts wird aufgelöst.
- **Validierung je Sekunde** (`validierung`, mit `+` verbunden): `BESTAETIGT_4A`/`KONFLIKT_4A` (nur Sekunden aus
  `level_samples`), `BESTAETIGT_PCE_CSV`/`KONFLIKT_PCE_CSV` (nur Sekunden aus `measurements`),
  `QUERPRUEFUNG_2_TABELLEN` (beide Tabellen stimmen überein), sonst `UNGEPRUEFT`.
- **Priorität:** intakte DB = Geräteexport > WAL > `.recover` > Carving; bei Gleichstand `level_samples` vor `measurements`.
- **Zielmenge:** Status je Sekunde aus der Alt-CSV (`PCE_323`→PCE, `GEMISCHT`, `MIKROFON`; alles andere → FEHLT). Ziel = nicht PCE,
  aber nur an Tagen mit einer Zeile mit Quelle und Samples > 0. **Gibt es mehrere CSV-Versionen für einen Tag, wird die
  zuletzt geänderte gewählt und eine Warnung ausgegeben** – bei Beweisfragen bitte prüfen.

## Bewusst noch nicht enthalten

- `.recover`, Freelist-Auswertung und Page-Carving defekter DBs.
- PCE-Geräteformat und Kreuzkorrelation des Zeitversatzes (braucht ein Musterexemplar).
- `Session_*.csv`: andere Spalten, werden vom Parser abgelehnt und nur im Inventar geführt.
- WAL: erkannt werden nur angehängte Zeilen mit `id` größer als das Maximum der Hauptdatei, keine geänderten.
- Querprüfung `measurements` nur bei 1-s-Fenstern.
- Abgleich mit `report_inputs.collect` und `BERICHT_RECOVERY.md`. Die Werkzeuge übernehmen nichts in bestehende
  Auswertungsdateien; die Übernahme braucht die Freigabe des Owners.
