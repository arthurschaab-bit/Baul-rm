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


## Neu in v10.2.1

- Die reine Pegel-/Dauerregel erzeugt ab 20.07.2026 keine automatische
  Tiefbohrer-Zuordnung mehr.
- Noch nicht dokumentierte Aussen-Aufnahmeorte werden als `SO-Balkon` gefuehrt;
  bereits dokumentierte Innen- und Wechselpositionen bleiben unveraendert.
- Lageplan- und Messaufbau-Bilder werden selektiv in den lokalen Cache kopiert.
  Videoarchive bleiben im Cloud-Bestand und werden nicht mitgespiegelt.
- Eine lokale PANNs-Uebersegmentierung bildet ab einschliesslich 08.07.2026
  akustische Cluster. `gpt-audio-1.5` beurteilt in bis zu zwei Runden nur kurze
  repraesentative WAV-Ausschnitte. Mischungen aus Baustellen- und allgemeinen
  Aussengeraeuschen werden ausdruecklich beruecksichtigt.
- API-Antworten werden je Cluster gecacht. Ohne `OPENAI_API_KEY` wird das
  Clusterpaket vorbereitet, aber es werden keine Daten an OpenAI gesendet.

## Neu in v10.3.1

- PANNs bildet lokal die akustischen Cluster. CLAP hoert anschliessend nur drei
  Vertreter je Cluster und ordnet sie per Zero-Shot-Audiovergleich den
  Baustellen- und Umgebungskategorien zu. Es gibt keine API-Aufrufe und keine
  Kosten pro WAV.
- Verarbeitet werden ausschliesslich Ereignisse ab einschliesslich 08.07.2026.
- Am Messort gibt es keinen Zug. `Train`/`Rail` wird als AudioSet-Fehlaehnlichkeit
  zu einer rollenden, rotierenden oder metallischen Baustellenmaschine behandelt,
  nie als reale Zugquelle oder automatischer Tiefbohrernachweis. CLAP trennt dafuer
  spezifische Bohrgeraete von `Schweres Baugeraet/sonstige Maschine`.
- Pegel und Dauer sind keine Erkennungsmerkmale der Audio-Klassifikation.
- Homogene Cluster werden automatisch uebernommen. Uneindeutige Mischcluster
  erhalten `Unklar/Mischgeraeusch` und erscheinen gebuendelt in
  `Aufbereit_v2/Local_Cluster_ab_20260708/pruefliste.csv`.
- Manuell gepruefte Laermquellen haben weiterhin Vorrang. Die OpenAI-Audiostufe
  bleibt optional vorhanden, ist in der Standardkonfiguration aber deaktiviert.

Ein API-Schluessel ist fuer die lokale Erkennung nicht erforderlich. Das
Startdatum und die Pruefschwellen stehen im Abschnitt `local_audio` der lokalen
Konfiguration.

## Abhängigkeiten

```powershell
py -3 -m pip install -r requirements.txt
```

Beim ersten lokalen Clusterlauf wird `laion/clap-htsat-unfused` einmalig
heruntergeladen (rund 618 MB) und danach aus dem lokalen Modellcache verwendet.
PANNs-Wahrscheinlichkeiten und CLAP-Ergebnisse der Clustervertreter werden
separat zwischengespeichert, sodass Folgeläufe nur neue Vertreter berechnen.

## Tests

```powershell
py -3 -m unittest discover -s tests -v
```

Die Tests verwenden ausschließlich kleine temporäre Dateien; echte Messarchive
werden nicht geöffnet.
