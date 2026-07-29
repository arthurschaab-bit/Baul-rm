# Baul-rm – Schallmessungs-Pipeline v10

Die Pipeline arbeitet nicht mehr direkt im synchronisierten Cloud-Ordner. Sie
kopiert nur neue oder geänderte Messdateien in einen persistenten lokalen Cache,
führt dort die Auswertung aus und überträgt anschließend nur Ergebnisse zurück.

## Warum diese Architektur?

Im letzten dokumentierten Cloud-Lauf benötigten einzelne Schritte 35,6 Sekunden
für den WAV-/CSV-Index, 73,0 Sekunden für die Excel-Datei und 927,9 Sekunden für
einen am Layout-Check abgebrochenen Gesamtbericht.

GitHub versioniert den Code, nicht die Rohdaten. Die großen CSV-/ZIP-Dateien
bleiben im bestehenden Cloud-Ordner. Der lokale Cache unter
`%LOCALAPPDATA%\Baul-rm\workspace` wird einmal aufgebaut und danach inkrementell
fortgeschrieben. Beim aktuellen Bestand umfasst der Ersttransfer rund 23,8 GB;
spätere Läufe übertragen nur Änderungen. Vor dem Transfer prüft der Starter den
freien lokalen Speicher und hält standardmäßig 5 GB Reserve zurück.

## Start

1. `settings.example.json` als `settings.local.json` kopieren.
2. Dort `cloud_root` und die lokalen Angaben im Abschnitt `report` eintragen.
3. `run_pipeline.bat` starten.

`settings.local.json` wird von Git ignoriert. Adresse, Name und standortbezogene
Dateinamen bleiben damit außerhalb des öffentlichen Repositorys.

```powershell
.\run_pipeline.bat
```

Nützliche Optionen:

```powershell
.\run_pipeline.bat --sync-only
.\run_pipeline.bat --skip-audio
.\run_pipeline.bat --full
.\run_pipeline.bat --no-sync-back
.\run_pipeline.bat --dry-run
```

## Ablauf

1. Ein Lauf-Lock verhindert zwei gleichzeitige Auswertungen.
2. Der kleine Codebestand wird in den lokalen Laufordner gespiegelt.
3. Nur neue/geänderte `20??-??-?? *.csv` und `Laermprotokoll_*.zip` werden kopiert.
4. Bestehende Indizes, geprüfte Zuordnungen und KI-Caches werden beim ersten Lauf
   übernommen, damit nicht alles neu klassifiziert werden muss.
5. Pipeline v10 führt nur die Stufen aus, deren Eingaben geändert wurden.
6. Ergebnisse werden atomar in den Cloud-Ordner zurückkopiert.

Cloud-seitig entfernte Rohdaten werden lokal nicht endgültig gelöscht, sondern
unter `.baul-rm/quarantine/` im Cache abgelegt.

## Wesentliche Verbesserungen gegenüber v8/v9

- No-op-Lauf bei unveränderten Eingaben
- lokale I/O statt wiederholter Direktzugriffe auf den Cloud-Ordner
- portable relative Hash-Cache-Schlüssel; vorhandene Hashes werden migriert
- CSV-Änderungen an Tagen mit WAV-ZIP aktualisieren nun korrekt die Audioauswahl
- `--skip-audio` speichert eine explizite Wiedervorlage
- Steuerdateien und Codeänderungen werden zusätzlich zu Rohdaten verfolgt
- manuell geprüfte Labels werden vor der Dauerlärmberechnung importiert
- atomarer Hin- und Rücktransfer sowie Schutz vor parallelen Läufen
- private Berichtsdaten liegen nur in der ignorierten lokalen Konfiguration
- Messdaten und große Ergebnis-Caches bleiben durch `.gitignore` aus Git heraus

## Neu in v10.1

- PANNs-Inferenz verarbeitet gleich geformte WAV-Clips gebuendelt statt einzeln.
- Der Audio-Cache wird konsistent geprueft, atomar gespeichert und regelmaessig gesichert.
- Doppelte WAV-Verweise werden vor der Inferenz entfernt.
- Verwaiste Lauf-Sperren mit nicht mehr existierendem Prozess werden sofort freigegeben.
- Mehrfach fehlcodierte UTF-8-Berichtstexte werden beim Start sicher normalisiert.
- Breitere KI- und Pruefspalten verbessern die Lesbarkeit der Excel-Arbeitsmappe.
- Tagesbericht-Infoboxen werden ohne Ueberdeckung kontrolliert umgebrochen.


## Neu in v10.2

- Die reine Pegel-/Dauerregel erzeugt ab 20.07.2026 keine automatische
  Tiefbohrer-Zuordnung mehr.
- Noch nicht dokumentierte Aussen-Aufnahmeorte werden als `SO-Balkon` gefuehrt;
  bereits dokumentierte Innen- und Wechselpositionen bleiben unveraendert.
- Lageplan- und Messaufbau-Bilder werden selektiv in den lokalen Cache kopiert.
  Videoarchive bleiben im Cloud-Bestand und werden nicht mitgespiegelt.
- Eine lokale PANNs-Uebersegmentierung bildet bis einschliesslich 08.07.2026
  akustische Cluster. `gpt-audio-1.5` beurteilt in bis zu zwei Runden nur kurze
  repraesentative WAV-Ausschnitte. Mischungen aus Baustellen- und allgemeinen
  Aussengeraeuschen werden ausdruecklich beruecksichtigt.
- API-Antworten werden je Cluster gecacht. Ohne `OPENAI_API_KEY` wird das
  Clusterpaket vorbereitet, aber es werden keine Daten an OpenAI gesendet.

Der API-Schluessel wird nur als lokale Umgebungsvariable erwartet und gehoert
nicht in `settings.local.json` oder Git. Der Stichtag bleibt dort separat als
`openai_audio.until` konfiguriert.
## Abhängigkeiten

```powershell
py -3 -m pip install -r requirements.txt
```

`panns-inference` wird nur benötigt, wenn neue WAV-Ereignisse klassifiziert
werden. Das zugehörige Modell liegt wie bisher außerhalb des Repositorys.

## Tests

```powershell
py -3 -m unittest discover -s tests -v
```

Die Tests verwenden ausschließlich kleine temporäre Dateien; echte Messarchive
werden nicht geöffnet.
