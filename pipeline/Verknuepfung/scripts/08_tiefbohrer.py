# -*- coding: utf-8 -*-
"""
08_tiefbohrer.py
Wendet die Tiefbohrer-Regel (tiefbohrer.py: >65 dB, >1 min, wenig Schwankung, ab
08.06.) auf alle Tage an. Ergebnis:
  - Verknuepfung/tiefbohrer_spans.csv         (erkannte Tiefbohrer-Zeitfenster)
  - Spalte 'Dauerbetrieb_Regel' (Ja/Nein) in relevante_ereignisse.csv und dauerlaerm.csv
"""
import os, csv, glob, importlib.util

HERE = os.path.dirname(__file__)
BASE = os.path.abspath(os.path.join(HERE, "..", ".."))
VK   = os.path.join(BASE, "Verknuepfung")

spec = importlib.util.spec_from_file_location("tb", os.path.join(HERE, "tiefbohrer.py"))
tb = importlib.util.module_from_spec(spec); spec.loader.exec_module(tb)

def sod(dt):  # Sekunde des Tages
    return dt.hour*3600 + dt.minute*60 + dt.second
def hms_sod(hms):
    h, m, s = map(int, hms.split(":")); return h*3600 + m*60 + s

# ---- Tage aus master_index UND reinen CSV-Tagen (ohne WAV)
days = {r["Datum"] for r in csv.DictReader(open(os.path.join(VK,"master_index.csv"),
        encoding="utf-8-sig"))}
days |= {os.path.basename(c)[:10] for c in glob.glob(os.path.join(BASE, "2026-*.csv"))}
days = sorted(days)

spans_by_day = {}          # day -> Liste (start_sod, end_sod)
span_rows = []
for day in days:
    sp = tb.spans_for_day(day, BASE)
    lst = []
    for (a, b, leq, lmax) in sp:
        lst.append((sod(a.to_pydatetime()), sod(b.to_pydatetime())))
        dur = (b - a).total_seconds()/60.0
        span_rows.append({"Datum": day, "Start": a.strftime("%H:%M:%S"),
                          "Ende": b.strftime("%H:%M:%S"), "Dauer_min": round(dur,1),
                          "Leq_dBA": round(leq,1), "Lmax_dBA": round(lmax,1)})
    spans_by_day[day] = lst

with open(os.path.join(VK,"tiefbohrer_spans.csv"), "w", newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=["Datum","Start","Ende","Dauer_min","Leq_dBA","Lmax_dBA"])
    w.writeheader(); w.writerows(span_rows)

def in_span(day, s, e=None):
    """s..e (sod) in einem Tiefbohrer-Span? Bei e=None Punktprüfung."""
    for (a, b) in spans_by_day.get(day, []):
        if e is None:
            if a <= s <= b: return True
        else:
            if s <= b and e >= a: return True   # Überlappung
    return False

def annotate(path, time_field, get_se):
    if not os.path.exists(path): return 0
    rows = list(csv.DictReader(open(path, newline='', encoding='utf-8-sig')))
    if not rows: return 0
    fields = list(rows[0].keys())
    if "Dauerbetrieb_Regel" not in fields:
        fields.append("Dauerbetrieb_Regel")
    ja = 0
    for r in rows:
        s, e = get_se(r)
        hit = in_span(r["Datum"], s, e)
        r["Dauerbetrieb_Regel"] = "Ja" if hit else "Nein"
        ja += hit
    with open(path, "w", newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in rows: w.writerow({k: r.get(k,"") for k in fields})
    return ja

# Ereignisse: Punktprüfung über Uhrzeit
ja_ev = annotate(os.path.join(VK,"relevante_ereignisse.csv"), "Uhrzeit",
                 lambda r: (hms_sod(r["Uhrzeit"]), None))
# Dauerlärm: Überlappung Start..Ende
ja_dl = annotate(os.path.join(VK,"dauerlaerm.csv"), "Start",
                 lambda r: (hms_sod(r["Start"]), hms_sod(r["Ende"])))

print(f"Tiefbohrer-Spans: {len(span_rows)} (über {sum(1 for d in spans_by_day if spans_by_day[d])} Tage)")
gesamt_min = sum(r["Dauer_min"] for r in span_rows)
print(f"Tiefbohrer-Gesamtdauer: {gesamt_min:.0f} min")
print(f"Ereignisse mit Dauerbetrieb_Regel=Ja: {ja_ev} | Dauerlärm-Phasen=Ja: {ja_dl}")
for day in days:
    n = len(spans_by_day[day])
    if n: print(f"  {day}: {n} Tiefbohrer-Fenster")
