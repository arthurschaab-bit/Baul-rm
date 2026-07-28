# Architekturentscheidung

## Entscheidung

Der Code lebt in GitHub und in einem normalen lokalen Checkout. Messdaten bleiben
im Google-Drive-Ordner. Für die Berechnung wird ein persistenter lokaler
Arbeitscache verwendet.

```text
GitHub/Checkout ── Code ──┐
                          ├──> lokaler Cache ──> Pipeline v10
Google Drive ── Rohdaten ─┘                         │
                                                   └── Ergebnisse ──> Google Drive
```

## Warum nicht bei jedem Lauf den Code von GitHub laden?

Ein `git pull` pro Lauf spart praktisch keine Zeit, weil der Code klein ist. Es
macht Ausführungen außerdem weniger reproduzierbar, wenn sich der Stand zwischen
zwei Läufen unbemerkt ändert. Der Checkout wird bewusst aktualisiert; die
Startdatei kopiert genau diesen Stand in den lokalen Laufordner.

## Warum ein persistenter Cache?

Die WAV-ZIPs umfassen viele Gigabyte. Ein vollständiger Transfer bei jedem Lauf
wäre nur eine Verlagerung der Ineffizienz. Der erste Aufbau kostet einmalig Zeit;
danach werden Größe und Änderungszeit verglichen und nur geänderte Dateien
übertragen. `copy2` erhält die Änderungszeit, sodass auch bestehende Pipeline- und
Hash-Caches weiter funktionieren.

## Datenhoheit

- Rohdatenquelle: Google Drive
- Ausführung und große temporäre WAV-Ausschnitte: lokaler Cache
- Code und Konfiguration: Git
- Berichte, Tabellen und Statusdateien: Rücktransfer zu Google Drive
- Entfernte Rohdaten: lokale Quarantäne statt endgültiger Löschung
