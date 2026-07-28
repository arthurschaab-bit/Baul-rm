# -*- coding: utf-8 -*-
"""
01_build_index.py
Baut einen Master-Index ueber alle Tage. Verknuepft jede WAV-Datei rein zeitlich:
- dBA-Wert der Dauermessung (CSV) zur selben Sekunde (+-2 s Toleranz)
- Trigger-Amplitude aus dem Tagesbericht

WICHTIG: Das Aufnahmedatum jeder WAV wird aus ihrem Zeitstempel (Dateiname,
Unix-ms) bestimmt - NICHT aus dem ZIP-Namen. So werden falsch benannte ZIPs
(z.B. Laermprotokoll_17.06.2026_1.zip, die in Wahrheit den 16.06. enthaelt)
korrekt dem echten Tag zugeordnet.
Es werden KEINE WAVs entpackt - nur ZIP-Namenslisten + Tagesbericht-Text gelesen.
Ausgabe: Verknuepfung/master_index.csv
"""
import os, re, glob, csv, zipfile, datetime

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT  = os.path.join(BASE, "Verknuepfung", "master_index.csv")

def ts_of(name):
    m = re.search(r'(\d{13})', os.path.basename(name))
    return int(m.group(1)) if m else None

def parse_tagesbericht(text):
    entries = []
    for b in re.split(r'-{5,}', text):
        zt = re.search(r'Zeit:\s*(\d{1,2}):(\d{2}):(\d{2})', b)
        am = re.search(r'Amplitude:\s*([\d.]+)', b)
        if zt:
            h, mi, s = int(zt.group(1)), int(zt.group(2)), int(zt.group(3))
            entries.append((h*3600+mi*60+s, float(am.group(1)) if am else None))
    entries.sort(key=lambda e: e[0])
    return entries

# ---- 1. Alle Ereignisse aus allen ZIPs sammeln (Tag = echtes Datum aus Zeitstempel)
#         Groessere ZIPs zuerst -> bei doppelten Zeitstempeln gewinnt die vollstaendigere.
zips = sorted(glob.glob(os.path.join(BASE, "Laermprotokoll_*.zip")),
              key=os.path.getsize, reverse=True)
seen_ts = set()
events = []   # dicts
for z in zips:
    zf = zipfile.ZipFile(z)
    names = zf.namelist()
    wavs = sorted((ts_of(n), n) for n in names if n.lower().endswith('.wav') and ts_of(n))
    # Tagesbericht (fuer Amplitude, per Index gepaart)
    tb = []
    for n in names:
        if n.lower().endswith('.txt') and 'tagesbericht' in n.lower():
            tb = parse_tagesbericht(zf.read(n).decode('utf-8', errors='replace')); break
    paired = (len(tb) == len(wavs))
    for i, (ts, name) in enumerate(wavs):
        if ts in seen_ts:
            continue
        seen_ts.add(ts)
        dt = datetime.datetime.fromtimestamp(ts/1000.0)
        events.append({
            "day": dt.strftime("%Y-%m-%d"),
            "dt": dt,
            "sod": dt.hour*3600 + dt.minute*60 + dt.second,
            "ts": ts,
            "wav": os.path.basename(name),
            "zip": os.path.basename(z),
            "amp": tb[i][1] if paired else None,
        })

# ---- 2. CSV je Tag -> {sekunde_des_tages: dBA}
def csv_secmap(day):
    secmap = {}
    for c in glob.glob(os.path.join(BASE, f"{day} *.csv")):
        with open(c, newline='', encoding='utf-8', errors='replace') as f:
            for row in csv.reader(f):
                if len(row) >= 4 and row[0].strip().isdigit():
                    try:
                        h, mi, s = (int(x) for x in row[2].split(':'))
                        secmap[h*3600+mi*60+s] = float(row[3])
                    except Exception:
                        pass
    return secmap

days = sorted(set(e["day"] for e in events))
secmaps = {d: csv_secmap(d) for d in days}

rows = []
for e in sorted(events, key=lambda e: (e["day"], e["ts"])):
    sm = secmaps[e["day"]]
    dba = sm.get(e["sod"])
    if dba is None:
        for off in (1, -1, 2, -2):
            if e["sod"]+off in sm:
                dba = sm[e["sod"]+off]; break
    rows.append({
        "Datum": e["day"], "Uhrzeit": e["dt"].strftime("%H:%M:%S"),
        "Timestamp_ms": e["ts"], "WAV": e["wav"], "ZIP": e["zip"],
        "Amplitude": e["amp"], "dBA_Dauermessung": dba,
        "hat_CSV": 1 if dba is not None else 0,
    })

fields = ["Datum","Uhrzeit","Timestamp_ms","WAV","ZIP","Amplitude","dBA_Dauermessung","hat_CSV"]
with open(OUT, "w", newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

# ---- Report
from collections import Counter
per_day = Counter(r["Datum"] for r in rows)
per_day_csv = Counter(r["Datum"] for r in rows if r["hat_CSV"])
print("Tag        WAV   davon mit dBA  Quelle-ZIP(s)")
zip_by_day = {}
for r in rows:
    zip_by_day.setdefault(r["Datum"], set()).add(r["ZIP"])
for d in sorted(per_day):
    print(f"{d}  {per_day[d]:5d}   {per_day_csv[d]:5d}        {sorted(zip_by_day[d])}")
print(f"\nGesamt {len(rows)} Ereignisse -> {OUT}")

dbas = sorted(r["dBA_Dauermessung"] for r in rows if r["dBA_Dauermessung"] is not None)
if dbas:
    for thr in (70, 75):
        print(f"  >= {thr} dBA: {sum(1 for d in dbas if d >= thr)} Ereignisse")
