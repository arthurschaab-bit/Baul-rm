# Ergänzung zur Belastungsdokumentation

Die Ergänzung verwendet geprüfte Tageswerte, die vorhandene DWD-Auswertung
und eine private Quellenkonfiguration. Rohdaten, Protokolle, Adressprofile,
Fotos, PDF-Ausgaben und daraus erzeugte Vorschaudateien bleiben außerhalb
des Repositorys.

## Auswertung

Die bestehende DWD-Pipeline erzeugt für den gewünschten Zeitraum ein
vollständiges, per SHA-256 geprüftes Wetterpaket. Der private Ausgabeordner
enthält dieses Paket unter `dwd` und `wetterstand.json` mit dem Feld `end`.
Der Stichtag berücksichtigt den von der DWD-Pipeline geforderten Nachlauf.

```powershell
python run_supplement_analysis.py --config <private-auswertung.json>
python run_supplement_report.py --data-dir <privater-ordner> --profile <privates-deckblatt.yaml> --assets-manifest <private-medien.json> --stand YYYY-MM-DD
```

Die Auswertungskonfiguration enthält `output`, `predecessor`, `signal_base`,
`app_exports`, `site_sources`, `prior_media_inventory` und `diary` als
lokale Pfade. Der Vorgänger enthält die geprüften Zahlen, Klassifikation
und Nachweise des Basislaufs. Die Signalbasis enthält die geprüfte
Sekundenauswahl sowie die Wetter- und Konfigurationsnachweise. Den
Einlesezeitraum beschreiben `extension_start`, `intake_through` und
`base_end_exclusive`. Die Listen `confirmed_so_days` und `holidays`,
die Zuordnungen `saturday_rest` und `additional_diary_examples` sowie
`calibration_exclusions` mit Datum, Beginn und Dauer in Sekunden halten
die vom Betreiber geprüften Belege fest. Die Abdeckungsgrenzen stehen im
Auswertungsmodul.

Der Renderer liest die daraus erzeugten Zahlen, Klassifikation und
Sekundenwerte im Datenordner. Das Medienprofil referenziert den Lageplan
und datierte Bilder. Zusätzlich übergibt es Referenztext, Aktenzeichen,
Beispieltabelle und die bereits bestätigten Frühstarts. Neue Frühstarts
erfordern eine passende, vorher belegte Tagebuchzeit und den gemessenen
Anstieg nach der definierten Minutenregel. Vollständig durch den
Wetterfilter entfernte Tage werden separat ausgewiesen.

Für eine Folgeversion einen neuen Ausgabeordner verwenden. Der Renderer
verweigert das Überschreiben einer PDF mit identischem Namen.
