# -*- coding: utf-8 -*-
"""
tiefbohrer.py
Regelbasierte Tiefbohrer-Erkennung aus der dBA-Dauermessung (CSV), nach Vorgabe
des Nutzers (gehörte Stichproben): Pegel > 65 dB(A) über > 1 min mit WENIG
Pegelschwankung = Tiefbohrer. Gilt nur vom 08.06. bis einschliesslich 19.07.2026.
Ab 20.07.2026 darf ein laengerer hoher Pegel nicht mehr automatisch als
Tiefbohrer gewertet werden.

"Wenig Schwankung" = gleitende Standardabweichung (Fenster WIN s) < MAX_STD dB,
bei gleitendem Mittel > MIN_DB. Kurze Unterschreitungen <= GAP s werden überbrückt;
Mindestdauer MIN_DUR s.
"""
import os, csv, glob, datetime
import numpy as np
import pandas as pd

MIN_DB  = 70.0    # dB(A): Pegel-Schwelle (gleitendes Mittel); darunter -> KI-Einschätzung
MAX_STD = 4.0     # dB:   max. gleitende Std.-Abw. -> "wenig Schwankung"
MIN_DUR = 60      # s:    Mindestdauer der Episode (> 1 min)
GAP     = 15      # s:    kurze Unterschreitungen überbrücken
WIN     = 30      # s:    Fenster für Mittel/Streuung
AB_DATUM = "2026-06-08"   # Regel gilt ab diesem Tag
BIS_DATUM = "2026-07-19"  # ab 20.07. keine automatische Tiefbohrer-Zuordnung

def detect_spans(dba):
    """dba: pd.Series, 1-s-Raster (NaN in Lücken). -> Liste (start_ts, end_ts, leq, lmax)."""
    mp = max(10, WIN // 2)
    rm = dba.rolling(WIN, min_periods=mp, center=True).mean()
    rs = dba.rolling(WIN, min_periods=mp, center=True).std()
    qual = ((rm > MIN_DB) & (rs < MAX_STD)).fillna(False).to_numpy()
    idx = dba.index
    pos = np.where(qual)[0]
    if len(pos) == 0:
        return []
    groups, start, prev = [], pos[0], pos[0]
    for p in pos[1:]:
        if p - prev <= GAP:
            prev = p
        else:
            groups.append((start, prev)); start = prev = p
    groups.append((start, prev))
    out = []
    for a, b in groups:
        if (b - a) + 1 >= MIN_DUR:
            seg = dba.iloc[a:b+1].dropna()
            if len(seg):
                leq = 10*np.log10((10**(seg/10)).mean())
                out.append((idx[a], idx[b], float(leq), float(seg.max())))
    return out

def load_day_series(day, base):
    """Baut die 1-s-dBA-Serie eines Tages aus den CSVs (NaN in Lücken)."""
    recs = []
    for c in sorted(glob.glob(os.path.join(base, f"{day} *.csv"))):
        with open(c, newline='', encoding='utf-8', errors='replace') as f:
            for row in csv.reader(f):
                if len(row) >= 4 and row[0].strip().isdigit():
                    try:
                        dt = datetime.datetime.strptime(f"{row[1]} {row[2]}", "%m-%d-%Y %H:%M:%S")
                        recs.append((dt, float(row[3])))
                    except Exception:
                        pass
    if not recs:
        return None
    s = pd.Series(dict(recs)).sort_index()
    s = s[~s.index.duplicated()]
    return s.reindex(pd.date_range(s.index[0], s.index[-1], freq="1s"))

def spans_for_day(day, base):
    if day < AB_DATUM or day > BIS_DATUM:
        return []
    s = load_day_series(day, base)
    return detect_spans(s) if s is not None else []
