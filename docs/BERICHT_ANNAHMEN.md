# Allgemeine Annahmen der Berichtsrechnung

Dieses Dokument beschreibt Rechenregeln und Grenzen. Fallbezogene Eingaben und Ergebnisse gehören ausschließlich in einen privaten Arbeitsordner.

## Messwerte und Quellen

- Für Schallkennwerte werden nur Sekunden mit eindeutig zugeordnetem Messgerät verwendet. Mikrofonwerte und gemischte Exportwerte sind keine Pegelquelle.
- Die Zuordnung aus App-Sessions ist eine Herkunftsangabe; sie ersetzt keine unabhängige Prüfung der Geräteeinstellung.
- Fehlende Sekunden werden weder aufgefüllt noch interpoliert. Die Abdeckung bezieht sich auf die tatsächlich ausgewerteten Sekunden des definierten Zeitfensters.
- Bei konkurrierenden Exporten wird die Auswahl je Sekunde nachvollziehbar protokolliert. Randbereinigungen müssen vor dem Berichtslauf dokumentiert werden.

## Messbereich und Toleranz

- Die Annahme eines festen Gerätemessbereichs benötigt einen gesonderten Beleg. Werte unterhalb der angenommenen Untergrenze können für Sensitivitätsrechnungen auf die Untergrenze gesetzt werden; der unveränderte Wert bleibt dokumentiert.
- Werte oberhalb einer belegten Obergrenze können für Kennwerte auf diese Grenze gesetzt werden. Die Anzahl betroffener Sekunden und die Maximaldarstellung werden getrennt ausgewiesen.
- Eine Gerätetoleranz ist eine Messunsicherheit. Sie verändert den gemessenen Tageswert nicht automatisch und ersetzt keine rechtliche Einordnung.

## Einordnung und Belege

- Ein Schallpegel allein beweist weder eine konkrete Tätigkeit noch deren Verursacher. Tätigkeitsangaben benötigen eigene zeitlich passende Belege.
- Ein Kalibrierhinweis aus einer App oder vom Messenden ist von einem datierten Kalibrierbeleg zu unterscheiden.
- Wetterfilter, Ausschlüsse und Schwellen werden versioniert und für beide Vergleichsvarianten aus derselben Datenbasis berechnet.
