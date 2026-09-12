# -*- coding: utf-8 -*-
"""
06_report_v2.py  [Tag=YYYY-MM-DD]
Auditierte Version von 06_report.py (Version 2, rev. Beschriftung+Nacht).

CHANGELOG (vs. 06_report.py):
  [C1] LAeq-Abdeckung: 3-stufige Einstufung statt binär.
       >=COVERAGE_VALID (90%): "► LAeq Tag (07–20h): X dB(A) [Abdeckung Y%]"
       >=COVERAGE_WINDOW (70%): "◑ LAeq Tag (Teilerfassung): X dB(A) — Y% erfasst"
       <COVERAGE_WINDOW: "◐ LAeq Messfenster HH:MM–HH:MM: X dB(A) — Y% von 07–20h"
       Keine Verneinung ("KEIN valider") – immer erst, was der Wert belegt.
  [C2] Höchstwert: explizit als "LAFmax (1s Fast)" + L₁ (99%-Pct).
  [C3] Lauteste Stunde: Stunden-Abdeckung im Text.
  [C4] Zeit > Schwelle: Nenner = gemessene Zeit, Absolut-Min.
  [C5] Phasen >=60/>=10min: exakter String-Match (kein startswith-Bug).
  [C6] Arithmetisches Mittel dB entfernt – nur Leq.
  [C7] Lr: "TA-Lärm-Methodik / nur informativ".
  [C8] Lücken: NaN, kein Auffüllen – unverändert.
  [C9] Assertions.
  [N1] Nachtzeit 20:00–07:00 (NEU):
       – Stufenlinien (Richtwert/Eingreifschwelle) als tageszeitabhängige Treppenfunktion.
       – Nacht-Kennzahlen für Segmente vor 07:00 und nach 20:00 (je LAeq, LAFmax, Überschreitung).
       – Konservative Bau-Zuordnung: nur wenn KI Baulärm zeigt.

Output: Aufbereit_v2/Laermquellen/ und Aufbereit_v2/Dauerlaermtabelle/
Skript-Version: 06_report_v2 (Beschriftung+Nacht rev. 2026-06)
"""
import os, sys, csv, glob, datetime, shutil
import textwrap
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Patch

# ===========================================================================
# KONFIG-KONSTANTEN
# ===========================================================================
COVERAGE_VALID   = 0.90   # >=90%: vollständiger Tag-LAeq
COVERAGE_WINDOW  = 0.70   # 70-90%: Teilerfassung; <70%: Messfenster
TAG_REF_SEC      = 13 * 3600   # 46800 s Bezugszeitraum 07–20h

RW_TAG_WA        = 55.0   # AVV Baulärm WA Tagzeit Richtwert
EINGREIF_TAG     = 60.0   # AVV Baulärm WA Tagzeit Eingreifschwelle
RW_NACHT_WA      = 40.0   # AVV Baulärm WA Nachtzeit Richtwert
EINGREIF_NACHT   = 45.0   # AVV Baulärm WA Nachtzeit Eingreifschwelle
NACHT_SPITZE     = 60.0   # AVV Baulärm: einzelne Nacht-Spitze > RW_N+20

# GUARDRAIL: identisch zu gesamtbericht_lib_v3.py
KONSERVATIV_FENSTER_START = 15    # Messende in diesem Fenster (15-19 Uhr) -> Rest bis 20h bruecken
KONSERVATIV_FENSTER_ENDE  = 19
KONSERVATIV_ANNAHME_DB    = RW_TAG_WA  # Annahme: Grenzwert-Konformitaet im unerfassten Rest (Tier=window)
STERN_ANNAHME_DB          = 50.0  # "hohe Abdeckung*": Annahme fuer Restzeit bei Tier=partial (>=70% echt)

# Abdeckungsschwellen (alt, für vor-07h-LAeq)
ABDECKUNG_SCHWELLE_VOR7 = 0.50

# [C5] Kriterium-String für Phasen-Zählung
KRITERIUM_60_10 = ">=60dB/>=10min"

KT = 0.0   # Tonzuschlag (nicht messtechnisch bestimmt)
# ===========================================================================

VERSION_STR = "06_report_v2 (Beschriftung+Nacht rev. 2026-06)"

def overlap_check(fig, items, page=""):
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    boxes = []
    for nm, art in items:
        if art is None: continue
        try: bb = art.get_window_extent(renderer=rend)
        except: continue
        if bb.width > 0 and bb.height > 0:
            boxes.append((nm, bb))
    hits = []
    for i in range(len(boxes)):
        for j in range(i+1, len(boxes)):
            n1, b1 = boxes[i]; n2, b2 = boxes[j]
            ix = max(0.0, min(b1.x1,b2.x1)-max(b1.x0,b2.x0))
            iy = max(0.0, min(b1.y1,b2.y1)-max(b1.y0,b2.y0))
            if ix*iy > 0:
                frac = ix*iy / min(b1.width*b1.height, b2.width*b2.height)
                if frac > 0.05: hits.append((n1,n2,frac))
    for n1,n2,fr in hits:
        print(f"  WARN Ueberschneidung {page}: {n1} <-> {n2} ({fr*100:.0f}%)")
    return hits

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DAY  = sys.argv[1] if len(sys.argv) > 1 else "2026-06-17"
MODE = os.environ.get("BEWERTUNG", "LAeq")

OUTDIR  = os.path.join(BASE, "Aufbereit_v2")
DIR_LQ  = os.path.join(OUTDIR, "Laermquellen");      os.makedirs(DIR_LQ, exist_ok=True)
DIR_TAB = os.path.join(OUTDIR, "Dauerlaermtabelle"); os.makedirs(DIR_TAB, exist_ok=True)
OUTPUT_PREFIX = os.environ.get("BAUL_RM_OUTPUT_PREFIX", "Schallmessung")
SITE_TITLE = os.environ.get("BAUL_RM_SITE_TITLE", "Messstandort")
OUT     = os.path.join(DIR_LQ,  f"{OUTPUT_PREFIX}_{DAY}_mit_Laermquellen.pdf")
OUT_TAB = os.path.join(DIR_TAB, f"{OUTPUT_PREFIX}_{DAY}_Dauerlaerm_Tabelle.pdf")

import importlib.util as _ilu
_sp = _ilu.spec_from_file_location("tb", os.path.join(os.path.dirname(__file__), "tiefbohrer.py"))
tb = _ilu.module_from_spec(_sp); _sp.loader.exec_module(tb)

C_TITLE="#1F4E79"; C_MOM="#C9C9C9"; C_LAEQ="#1F4E79"; C_RICHT="#C00000"; C_EING="#ED7D31"
SRC_COLOR = {
    "Bagger":"#E09B2A", "Bohrgeraet/schweres Geraet":"#8C564B",
    "Schweres Baugeraet/sonstige Maschine":"#4E6E81",
    "Motor/Diesel":"#BCBD22", "Schlagen/Bohren":"#D62728",
    "Saege":"#FF7F0E", "Fahrzeug":"#9467BD",
    "Signal/Warnton":"#17BECF", "Sprache":"#2CA02C", "Umgebung/Sonstiges":"#BDBDBD",
}
def src_color(s): return SRC_COLOR.get(s, "#7F7F7F")
ALIAS = {"Tiefbohrer": "Bohrgeraet/schweres Geraet"}
DISP = {"Bagger":"Bagger", "Bohrgeraet/schweres Geraet":"Bohrgerät/schw. Gerät",
        "Schweres Baugeraet/sonstige Maschine":"Schw. Baugerät/sonst.",
        "Motor/Diesel":"Motor/Diesel", "Schlagen/Bohren":"Schlagen/Bohren",
        "Saege":"Säge", "Fahrzeug":"Fahrzeug", "Signal/Warnton":"Signal/Warnton",
        "Sprache":"Sprache", "Umgebung/Sonstiges":"Umgebung/Sonstiges"}
def disp(s): return DISP.get(s, s)
NONBAU_COLOR = "#6B9E9A"; NODATA_COLOR = "#ECECEC"
INDOOR_REF = 55.0
BAU_RELEVANT = ["Bagger","Bohrgeraet/schweres Geraet","Schweres Baugeraet/sonstige Maschine","Motor/Diesel",
                "Schlagen/Bohren","Saege","Fahrzeug","Signal/Warnton"]

def load_pos(day):
    p = os.path.join(BASE, "Verknuepfung", "messpositionen.csv")
    if os.path.exists(p):
        for row in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
            if row.get("Datum") == day: return row
    return {}
POS = load_pos(DAY)
INDOOR   = POS.get("Umgebung","").strip().lower().startswith("innen")
POSITION = POS.get("Position","").strip() or "lt. Bautagebuch nicht erfasst"

# ---------- CSV einlesen ----------
def read_day(day):
    frames, sessions = [], []
    for c in sorted(glob.glob(os.path.join(BASE, f"{day} *.csv"))):
        if "(1)" in c or "(2)" in c: continue
        recs = []
        with open(c, newline='', encoding='utf-8', errors='replace') as f:
            for row in csv.reader(f):
                if len(row) >= 4 and row[0].strip().isdigit():
                    try:
                        dt = datetime.datetime.strptime(f"{row[1]} {row[2]}", "%m-%d-%Y %H:%M:%S")
                        recs.append((dt, float(row[3])))
                    except: pass
        if recs:
            sessions.append((recs[0][0], recs[-1][0]))
            frames.append(pd.DataFrame(recs, columns=["dt","dba"]))
    df = pd.concat(frames).drop_duplicates("dt").set_index("dt").sort_index()
    full = pd.date_range(df.index[0], df.index[-1], freq="1s")
    df = df.reindex(full)
    sessions = sorted(set(sessions))
    return df, sessions

df, sessions = read_day(DAY)
dba   = df["dba"]
energy= 10**(dba/10)
laeq1m= 10*np.log10(energy.rolling(60, min_periods=20).mean())

def leq_of(mask):
    e = energy[mask].dropna()
    return 10*np.log10(e.mean()) if len(e) else float('nan')

t    = df.index
day0 = pd.Timestamp(DAY)
t7   = day0 + pd.Timedelta(hours=7)
t20  = day0 + pd.Timedelta(hours=20)

tb_spans = tb.detect_spans(df["dba"]) if DAY >= tb.AB_DATUM else []
tb_min   = sum((b-a).total_seconds() for a,b,_,_ in tb_spans)/60.0

hour   = t.hour + t.minute/60
m_tag  = (hour >= 7) & (hour < 20)
m_b7   = t.hour < 7      # vor 07:00 (Nacht)
m_a20  = t.hour >= 20    # nach 20:00 (Nacht)

# ---------- TAG-Kennzahlen (Formeln UNVERÄNDERT) ----------
tag_measured_sec = int(dba[m_tag].notna().sum())
vor7_measured_sec= int(dba[m_b7].notna().sum())
nach20_sec       = int(dba[m_a20].notna().sum())
abdeckung_tag    = tag_measured_sec / TAG_REF_SEC

laeq_tag  = leq_of(m_tag)
laeq_vor7 = leq_of(m_b7)
laeq_nach20 = leq_of(m_a20)

tagsec = dba[m_tag].dropna()
THR    = INDOOR_REF if INDOOR else RW_TAG_WA

hoechst = float(dba.max())
l1_tag  = float(np.nanpercentile(tagsec.values, 99)) if len(tagsec) else float('nan')

pct_thr     = 100*float((tagsec > THR).mean()) if len(tagsec) else 0.0
n_above_sec = int((tagsec > THR).sum())
mess_min_tag= tag_measured_sec // 60
n_above_min = n_above_sec // 60
n_above_s   = n_above_sec % 60

def beurteilungspegel(laf_series, laeq):
    v = laf_series.dropna().values
    if len(v) < 5 or laeq is None or np.isnan(laeq): return float('nan'), float('nan')
    n = (len(v)//5)*5
    takt = v[:n].reshape(-1,5).max(axis=1)
    lafteq = 10*np.log10(np.mean(10**(takt/10)))
    return lafteq + KT, lafteq - laeq

lr_tag, ki_tag = beurteilungspegel(tagsec, laeq_tag)

leq60 = 10*np.log10(energy.rolling(3600, min_periods=3600).mean())
if leq60.notna().any():
    it = leq60.idxmax(); lh_val = float(leq60.max())
    lh_start  = it - pd.Timedelta(seconds=3599)
    lh_txt    = f"{lh_start.strftime('%H:%M')}–{it.strftime('%H:%M')} Uhr"
    lh_cov    = int(dba[(t >= lh_start) & (t <= it)].notna().sum())
else:
    lh_val = float('nan'); lh_txt = "—"; lh_cov = 0; it = None

phases = [r for r in csv.DictReader(open(os.path.join(BASE,"Verknuepfung","dauerlaerm.csv"),
          encoding="utf-8-sig")) if r["Datum"] == DAY]
def to_dt(hms):
    h,m,s = map(int, hms.split(":")); return day0 + pd.Timedelta(hours=h,minutes=m,seconds=s)

phases_60_10     = [p for p in phases if p["Kriterium"] == KRITERIUM_60_10]
phases_60_10_min = sum(float(p.get("Dauer_min",0)) for p in phases_60_10)

phase_src_iv = []
for p in phases:
    s = "Bohrgeraet/schweres Geraet" if p.get("Dauerbetrieb_Regel")=="Ja" else (p.get("Laermquelle_Auto") or "")
    s = ALIAS.get(s,s)
    if s and s!="—": phase_src_iv.append((to_dt(p["Start"]), to_dt(p["Ende"]), s))
def phase_source_at(ts):
    hit = None
    for a,b,s in phase_src_iv:
        if a <= ts <= b: hit = s
    return hit

ev = [r for r in csv.DictReader(open(os.path.join(BASE,"Verknuepfung","relevante_ereignisse.csv"),
      encoding="utf-8-sig")) if r["Datum"] == DAY]
def ev_dt(r):
    h,m,s = map(int, r["Uhrzeit"].split(":")); return day0 + pd.Timedelta(hours=h,minutes=m,seconds=s)
def src_of(r):
    g = (r.get("Laermquelle_geprueft") or "").strip()
    c = (r.get("Laermquelle_Cluster") or "").strip()
    s = g if g else c if c else (r.get("Laermquelle_KI") or r.get("Laermquelle_Auto",""))
    return ALIAS.get(s,s)

ev_bau = [r for r in ev if src_of(r) in BAU_RELEVANT]

BIN_MIN = 10
bins = {}
for r in ev_bau:
    b = day0 + pd.Timedelta(minutes=BIN_MIN*int(((ev_dt(r)-day0).total_seconds())//(BIN_MIN*60)))
    bins.setdefault(b,{}); q = src_of(r)
    bins[b][q] = bins[b].get(q,0) + (float(r["dBA"]) if r["dBA"] else 0.0)
bin_dom = {b: max(d, key=d.get) for b,d in bins.items()}
bin_any = set()
for r in ev:
    bin_any.add(day0 + pd.Timedelta(minutes=BIN_MIN*int(((ev_dt(r)-day0).total_seconds())//(BIN_MIN*60))))

src_stats = {}
for s in BAU_RELEVANT:
    vals = [float(r["dBA"]) for r in ev_bau if src_of(r)==s and r["dBA"]]
    if vals:
        src_stats[s] = dict(n=len(vals), leq=10*np.log10(np.mean([10**(v/10) for v in vals])), mx=max(vals))

# ---------- NACHT-Kennzahlen (NEU [N1]) ----------
lmax_b7    = float(dba[m_b7].max())  if vor7_measured_sec > 0 else float('nan')
lmax_nach20= float(dba[m_a20].max()) if nach20_sec > 0 else float('nan')

def fmt_dur(sec):
    h = sec//3600; m = (sec%3600)//60
    return f"{h}h {m:02d}min" if h else f"{m}min"

def night_kpi_lines(seg_name, laeq, lmax, dur_sec):
    """Erzeugt KPI-Zeilen für ein Nachtsegment."""
    if dur_sec == 0 or np.isnan(laeq): return []
    lines = [f"  {seg_name} ({fmt_dur(dur_sec)}): LAeq {laeq:.1f} dB(A), LAFmax {lmax:.1f} dB(A)"]
    delta_rw = laeq - RW_NACHT_WA
    delta_ei = laeq - EINGREIF_NACHT
    flag = []
    if delta_rw > 0:  flag.append(f"+{delta_rw:.1f} dB ggü. RW-Nacht {RW_NACHT_WA:.0f} dB(A)")
    if delta_ei > 0:  flag.append(f"+{delta_ei:.1f} dB ggü. Eingreif-Nacht {EINGREIF_NACHT:.0f} dB(A)")
    if lmax > NACHT_SPITZE: flag.append(f"LAFmax > Nacht-Spitze {NACHT_SPITZE:.0f} dB(A)")
    if flag: lines.append(f"    [! {' | '.join(flag)}]")
    lines.append("    Basis: gemessene Pegel — kein fertiger Nacht-Beurteilungspegel")
    return lines

nacht_kpi = []
if vor7_measured_sec > 0:
    nacht_kpi += night_kpi_lines("Nacht vor 07:00", laeq_vor7, lmax_b7, vor7_measured_sec)
if nach20_sec > 0:
    nacht_kpi += night_kpi_lines("Nacht ab 20:00", laeq_nach20, lmax_nach20, nach20_sec)

# ---------- [C1] 3-stufige Abdeckungs-Labels ----------
def sessions_in_tag(sessions, day0):
    """Sessions (oder Teile davon) die im 07–20h-Fenster liegen."""
    t7_  = day0 + pd.Timedelta(hours=7)
    t20_ = day0 + pd.Timedelta(hours=20)
    within = []
    for a,b in sessions:
        clip_a = max(a, t7_); clip_b = min(b, t20_)
        if clip_a < clip_b: within.append((clip_a, clip_b))
    return sorted(within)

def coverage_label(laeq, abdeckung, sessions, day0):
    if np.isnan(laeq): return "LAeq Tag: — (keine Messdaten)"
    pct = round(abdeckung * 100)
    t7_ = day0 + pd.Timedelta(hours=7)
    t20_= day0 + pd.Timedelta(hours=20)
    within = sessions_in_tag(sessions, day0)
    # Lücken im 07–20h-Fenster
    gaps = []
    cursor = t7_
    for a,b in within:
        if cursor < a: gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < t20_: gaps.append((cursor, t20_))
    def fmtdt(dt): return dt.strftime("%H:%M")

    if abdeckung >= COVERAGE_VALID:
        return (f"► LAeq Tag (07–20 h): {laeq:.1f} dB(A)  [Abdeckung {pct}%]")
    elif abdeckung >= COVERAGE_WINDOW:
        gap_str = ""
        if gaps:
            g = gaps[0]
            gdur = int((g[1]-g[0]).total_seconds())
            gap_str = f" | nicht erfasst: {fmtdt(g[0])}–{fmtdt(g[1])} ({fmt_dur(gdur)})"
            if len(gaps) > 1:
                gap_str += f" + {len(gaps)-1} weitere Luecke(n)"
        return (f"◑ LAeq Tag (Teilerfassung 07–20 h): {laeq:.1f} dB(A)"
                f" — {pct}% des Bezugszeitraums erfasst{gap_str}")
    else:
        # Messfenster
        if len(within) >= 2:
            segs = " + ".join(f"{fmtdt(a)}–{fmtdt(b)}" for a,b in within)
            max_gap = max((g[1]-g[0]).total_seconds() for g in gaps) if gaps else 0
            max_g = max(gaps, key=lambda g: (g[1]-g[0]).total_seconds()) if gaps else None
            gap_str = f", Luecke {fmtdt(max_g[0])}–{fmtdt(max_g[1])} ({fmt_dur(int(max_gap))})" if max_g else ""
            return (f"◐ LAeq Messfenster ({len(within)} Segmente: {segs}): "
                    f"{laeq:.1f} dB(A) — {pct}% von 07–20h{gap_str}")
        else:
            seg = within[0] if within else (sessions[0][0], sessions[0][1])
            return (f"◐ LAeq Messfenster {fmtdt(seg[0])}–{fmtdt(seg[1])}: "
                    f"{laeq:.1f} dB(A) — {pct}% von 07–20h")

def coverage_supplement(abdeckung, sessions, day0, laeq):
    """Ergänzungstext (2. Zeile) für Stufe 2 und 3."""
    pct = round(abdeckung * 100)
    within = sessions_in_tag(sessions, day0)
    t7_ = day0 + pd.Timedelta(hours=7)
    t20_= day0 + pd.Timedelta(hours=20)
    gaps = []
    cursor = t7_
    for a,b in within:
        if cursor < a: gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < t20_: gaps.append((cursor, t20_))
    def fmtdt(dt): return dt.strftime("%H:%M")
    if abdeckung >= COVERAGE_VALID:
        return ""
    elif abdeckung >= COVERAGE_WINDOW:
        uncovered = "; ".join(f"{fmtdt(a)}–{fmtdt(b)}" for a,b in gaps)
        return (f"   Innerhalb des gemessenen Zeitraums valide; weitgehend repraesentativ."
                f" Nicht erfasst: {uncovered}.")
    else:
        dur_h = round(tag_measured_sec / 3600, 1)
        if within:
            seg = within[0]
            uncov = []
            if t7_ < seg[0]: uncov.append(f"07:00–{fmtdt(seg[0])}")
            for g in gaps: uncov.append(f"{fmtdt(g[0])}–{fmtdt(g[1])}")
            if seg[1] < t20_: uncov.append(f"{fmtdt(seg[1])}–20:00")
            uncov_str = ", ".join(uncov) if uncov else "—"
        else:
            uncov_str = "07:00–20:00"
        return (f"   Valider LAeq fuer das gemessene Fenster ({dur_h:.1f} h)."
                f" Dokumentiert Pegelhoehe in diesem Zeitraum, nicht den Tagesdurchschnitt."
                f" Nicht erfasst: {uncov_str}.")

laeq_tag_str  = coverage_label(laeq_tag, abdeckung_tag, sessions, day0)
laeq_tag_supp = coverage_supplement(abdeckung_tag, sessions, day0, laeq_tag)

tier = ("valid" if abdeckung_tag >= COVERAGE_VALID
        else "partial" if abdeckung_tag >= COVERAGE_WINDOW
        else "window")
is_teilmessung_tag = (tier != "valid")  # für diagram-Schraffur, kein negativer Text

# --- Konservative Volltag-Hochrechnung (NEU, GUARDRAIL: identisch zu gesamtbericht_lib_v3.py) ---
# Rein additiv: ersetzt NIE laeq_tag/tier/abdeckung_tag. Ausloeser: Messung endet tatsaechlich
# zwischen KONSERVATIV_FENSTER_START und -ENDE Uhr; gebrueckt wird nur der Rest ab dem
# tatsaechlichen Messende bis 20 Uhr (nicht pauschal ab einem festen Cutoff).
_gemessen_tag = dba[m_tag].dropna()
letzte_messung = _gemessen_tag.index.max() if len(_gemessen_tag) else None
letzte_messung_std = (letzte_messung.hour + letzte_messung.minute/60 + letzte_messung.second/3600
                       ) if letzte_messung is not None else None
konservativ_aktiv = (not INDOOR) and (tier == 'window') and (letzte_messung is not None) \
                    and (KONSERVATIV_FENSTER_START <= letzte_messung_std <= KONSERVATIV_FENSTER_ENDE) \
                    and (tag_measured_sec > 0) and (not np.isnan(laeq_tag))
if konservativ_aktiv:
    gap_sec_konserv = int((t20-letzte_messung).total_seconds())
    e_gemessen = 10**(laeq_tag/10); e_annahme = 10**(KONSERVATIV_ANNAHME_DB/10)
    laeq_tag_konservativ = 10*np.log10(
        (tag_measured_sec*e_gemessen + gap_sec_konserv*e_annahme) / (tag_measured_sec+gap_sec_konserv))
    abd_tag_konservativ = (tag_measured_sec+gap_sec_konserv) / TAG_REF_SEC
    konservativ_ende_str = letzte_messung.strftime("%H:%M")
else:
    laeq_tag_konservativ = float('nan'); abd_tag_konservativ = float('nan'); konservativ_ende_str = ""

# --- NEU: "hohe Abdeckung*" (Tier=partial, >=70% echt), GUARDRAIL: identisch zu gesamtbericht_lib_v3.py ---
hohe_abdeckung_stern = (not INDOOR) and (tier == 'partial') and (tag_measured_sec > 0) and (not np.isnan(laeq_tag))
if hohe_abdeckung_stern:
    gap_sec_stern = int(TAG_REF_SEC - tag_measured_sec)
    e_gemessen_stern = 10**(laeq_tag/10); e_annahme_stern = 10**(STERN_ANNAHME_DB/10)
    laeq_tag_stern = 10*np.log10(
        (tag_measured_sec*e_gemessen_stern + gap_sec_stern*e_annahme_stern) / (tag_measured_sec+gap_sec_stern))
else:
    laeq_tag_stern = float('nan')

# Vor-07h-Label (gegen Nacht-RW)
vor7_measured_pct = vor7_measured_sec / (7*3600)
if vor7_measured_sec > 0 and not np.isnan(laeq_vor7):
    delta_n = laeq_vor7 - RW_NACHT_WA
    laeq_vor7_str = (f"LAeq vor 07:00 ({fmt_dur(vor7_measured_sec)}): "
                     f"{laeq_vor7:.1f} dB(A)  [{'+' if delta_n>=0 else ''}{delta_n:.1f} dB ggue. RW-Nacht 40]")
else:
    laeq_vor7_str = ""

# ---------- [C9] Assertions ----------
def assert_fail(msg): print(f"  ASSERTION FAILED: {msg}")
if len(tagsec) and n_above_sec > len(tagsec):
    assert_fail(f"n_above_sec ({n_above_sec}) > tag_measured_sec ({len(tagsec)})")
if tb_spans:
    tb_tot = sum((b-a).total_seconds() for a,b,_,_ in tb_spans)
    if tb_tot > tag_measured_sec + 60:
        assert_fail(f"Tiefbohrer-Dauer ({tb_tot:.0f}s) > gemessene Tagzeit ({tag_measured_sec}s)")
for s,st in src_stats.items():
    if not np.isnan(hoechst) and st["mx"] > hoechst + 0.5:
        assert_fail(f"Max {s} ({st['mx']:.1f}) > Gesamt-LAFmax ({hoechst:.1f})")
if not np.isnan(lh_val) and not np.isnan(laeq_tag) and lh_val < laeq_tag - 0.5:
    assert_fail(f"Lauteste Stunde ({lh_val:.1f}) < LAeq Tag ({laeq_tag:.1f})")

wochentage = {0:"Mo",1:"Di",2:"Mi",3:"Do",4:"Fr",5:"Sa",6:"So"}
wd = wochentage[day0.weekday()]
mess_txt = " / ".join(f"{a.strftime('%H:%M')}–{b.strftime('%H:%M')}" for a,b in sessions)

# ========= SEITE 1: DIAGRAMM =========
pp = PdfPages(OUT)
fig = plt.figure(figsize=(14, 7.8))
ax  = fig.add_subplot(111)
fig.subplots_adjust(left=0.06, right=0.985, top=0.85, bottom=0.185)

if INDOOR:
    lo = float(np.nanmin(dba.values)); hi = float(np.nanmax(dba.values))
    YLO = max(15.0, 5*np.floor((lo-3)/5)); YMAX = min(95.0, 5*np.ceil((hi+3)/5))
else:
    YLO, YMAX = 35.0, 100.0
ax.set_ylim(YLO, YMAX)

# Nacht-Schattierung (vor 07:00 und nach 20:00)
for a,b in sessions:
    if a < t7: ax.axvspan(a, min(b,t7), color="#DDE6F0", alpha=0.55, zorder=0)
    if b > t20: ax.axvspan(max(a,t20), b, color="#DDE6F0", alpha=0.55, zorder=0)

# Schraffur für Teilmessung (Stufe 2+3)
if is_teilmessung_tag:
    ax.axvspan(t7, t20, color="#FFEECC", alpha=0.20, zorder=0)

# Bin-Flächen (Quellzuordnung)
mstart, mend = sessions[0][0], sessions[-1][1]
b = day0 + pd.Timedelta(minutes=BIN_MIN*int(((mstart-day0).total_seconds())//(BIN_MIN*60)))
used_nonbau = used_nodata = False
used_srcs = set()
def bin_in_tb(bs, be):
    return any(bs < b0 and be > a0 for (a0,b0,_,_) in tb_spans)
while b <= mend:
    bend = b + pd.Timedelta(minutes=BIN_MIN)
    dom  = bin_dom.get(b)
    psrc = phase_source_at(b + pd.Timedelta(minutes=BIN_MIN/2))
    m    = (t >= b) & (t < bend)
    if m.any():
        bin_laeq_vals = laeq1m[m].dropna()
        bin_rep_db = float(bin_laeq_vals.mean()) if len(bin_laeq_vals) else float('nan')
        _ei = INDOOR_REF if INDOOR else EINGREIF_TAG
        if dom:
            col, al = src_color(dom), 0.6; used_srcs.add(dom)
        elif psrc and psrc in BAU_RELEVANT:
            col, al = src_color(psrc), 0.6; used_srcs.add(psrc)
        elif bin_in_tb(b, bend):
            col, al = SRC_COLOR["Bohrgeraet/schweres Geraet"], 0.6
            used_srcs.add("Bohrgeraet/schweres Geraet")
        elif psrc:
            if not np.isnan(bin_rep_db) and bin_rep_db > _ei:
                col, al = NODATA_COLOR, 0.30; used_nodata = True
            else:
                col, al = NONBAU_COLOR, 0.5; used_nonbau = True
        elif b in bin_any:
            if not np.isnan(bin_rep_db) and bin_rep_db > _ei:
                col, al = NODATA_COLOR, 0.30; used_nodata = True
            else:
                col, al = NONBAU_COLOR, 0.5; used_nonbau = True
        else:
            col, al = NODATA_COLOR, 0.30; used_nodata = True
        ax.fill_between(t[m], YLO, laeq1m[m].values, color=col, alpha=al, lw=0, zorder=1)
    b = bend

if tb_spans: used_srcs.add("Bohrgeraet/schweres Geraet")

# Signal-Linien
ax.plot(t, dba, color=C_MOM, lw=0.5, zorder=2,
        label="Momentanpegel $L_{AF}$ (1 s) – graue Linie")
ax.plot(t, laeq1m, color=C_LAEQ, lw=1.3, zorder=4,
        label="LAeq (gleitend, 1 min) – blaue Linie")

# [N1] STUFENLINIEN (tageszeitabhängig) ----------
richtlabel = eingreiflabel = None
tmin_plot = t[0]; tmax_plot = t[-1]

if INDOOR:
    ax.axhline(INDOOR_REF, color=C_RICHT, lw=1.6, ls="--", zorder=5)
    richtlabel = ax.text(0.987, INDOOR_REF, f" {INDOOR_REF:.0f} dB(A) ASR A3.7",
                         transform=ax.get_yaxis_transform(), va='center', ha='right',
                         fontsize=7.5, color=C_RICHT, clip_on=False, zorder=6,
                         bbox=dict(fc='white',ec='none',pad=0.5))
else:
    # Stufenfunktion Richtwert
    rw_pts = []
    if tmin_plot < t7:
        rw_pts += [(tmin_plot, RW_NACHT_WA), (t7, RW_NACHT_WA), (t7, RW_TAG_WA)]
    else:
        rw_pts += [(max(tmin_plot,t7), RW_TAG_WA)]
    rw_pts += [(min(t20, tmax_plot), RW_TAG_WA)]
    if tmax_plot > t20:
        rw_pts += [(t20, RW_NACHT_WA), (tmax_plot, RW_NACHT_WA)]
    rw_x, rw_y = zip(*rw_pts)
    ax.plot(rw_x, rw_y, color=C_RICHT, lw=1.6, zorder=5, solid_joinstyle='miter')

    # Stufenfunktion Eingreifschwelle
    ei_pts = []
    if tmin_plot < t7:
        ei_pts += [(tmin_plot, EINGREIF_NACHT), (t7, EINGREIF_NACHT), (t7, EINGREIF_TAG)]
    else:
        ei_pts += [(max(tmin_plot,t7), EINGREIF_TAG)]
    ei_pts += [(min(t20, tmax_plot), EINGREIF_TAG)]
    if tmax_plot > t20:
        ei_pts += [(t20, EINGREIF_NACHT), (tmax_plot, EINGREIF_NACHT)]
    ei_x, ei_y = zip(*ei_pts)
    ax.plot(ei_x, ei_y, color=C_EING, lw=1.6, ls="--", zorder=5, solid_joinstyle='miter')

    # Labels an Tagzeit-Segment
    label_t = min(t20, tmax_plot) - pd.Timedelta(minutes=10)
    richtlabel = ax.text(0.987, RW_TAG_WA,
                         f" {RW_TAG_WA:.0f} dB(A) RW-Tag (AVV-WA)",
                         transform=ax.get_yaxis_transform(), va='center', ha='right',
                         fontsize=7.0, color=C_RICHT, clip_on=False, zorder=6,
                         bbox=dict(fc='white',ec='none',pad=0.5))
    eingreiflabel = ax.text(0.987, EINGREIF_TAG,
                            f" {EINGREIF_TAG:.0f} dB(A) Eingreif-Tag",
                            transform=ax.get_yaxis_transform(), va='center', ha='right',
                            fontsize=7.0, color=C_EING, clip_on=False, zorder=6,
                            bbox=dict(fc='white',ec='none',pad=0.5))
    # Nacht-Label (nur wenn Nacht-Daten vorhanden)
    if vor7_measured_sec > 0 or nach20_sec > 0:
        ax.text(0.987, RW_NACHT_WA,
                f" {RW_NACHT_WA:.0f} RW-Nacht",
                transform=ax.get_yaxis_transform(), va='bottom', ha='right',
                fontsize=6.5, color=C_RICHT, clip_on=False, zorder=6,
                bbox=dict(fc='white',ec='none',pad=0.5))
        ax.text(0.987, EINGREIF_NACHT,
                f" {EINGREIF_NACHT:.0f} Eingreif-N.",
                transform=ax.get_yaxis_transform(), va='bottom', ha='right',
                fontsize=6.5, color=C_EING, clip_on=False, zorder=6,
                bbox=dict(fc='white',ec='none',pad=0.5))

ax.set_ylabel("Schalldruckpegel dB(A)")
ax.set_xlabel("Uhrzeit")
ax.set_yticks(range(int(YLO), int(YMAX)+1, 5))
ax.xaxis.set_major_locator(mdates.HourLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
ax.grid(True, ls=":", color="#CCCCCC", alpha=0.7)
for sp in ("top","right"): ax.spines[sp].set_visible(False)

has_lh = bool(leq60.notna().any())
if has_lh:
    ax.plot([it - pd.Timedelta(minutes=30)], [YMAX-1.5],
            marker="v", ms=8, color="#888888", clip_on=False, zorder=6)

# ---------- KPI-Box ----------
def fmt(v):  return "—" if (v is None or (isinstance(v,float) and np.isnan(v))) else f"{v:.1f}"
def fmt0(v): return "—" if (v is None or (isinstance(v,float) and np.isnan(v))) else f"{v:.0f}"

kpi_lines = [f"Messung: {mess_txt}",
             f"Bezugszeitraum gemessen: {tag_measured_sec//60} min ({abdeckung_tag*100:.0f}% von 07–20h)"]

kpi_lines.append(laeq_tag_str)
if laeq_tag_supp:
    kpi_lines.append(laeq_tag_supp)

if konservativ_aktiv:
    kpi_lines.append(
        f"Zusaetzl. konservativ hochgerechnet (§287 ZPO, Messende {konservativ_ende_str} Uhr): "
        f"LAeq Tag konserv. {laeq_tag_konservativ:.1f} dB(A) [Abdeckung {abd_tag_konservativ*100:.0f}%] "
        f"-- Annahme RW {KONSERVATIV_ANNAHME_DB:.0f} dB(A)")

if hohe_abdeckung_stern:
    kpi_lines.append(
        f"Einstufung 'hohe Abdeckung*' (§287 ZPO): LAeq Tag* {laeq_tag_stern:.1f} dB(A) "
        f"-- Annahme {STERN_ANNAHME_DB:.0f} dB(A) fuer nicht erfasste Restzeit")

if laeq_vor7_str:
    kpi_lines.append(laeq_vor7_str)

kpi_lines.append(f"Hoechstwert LAFmax (1s Fast): {fmt(hoechst)} dB(A)")
kpi_lines.append(f"L₁ (99%-Pct, 07–20h): {fmt(l1_tag)} dB(A)")

if has_lh:
    kpi_lines.append(f"Lauteste Stunde {lh_txt}: LAeq {fmt(lh_val)} dB(A)  [Abdeckung {lh_cov}/3600 s] (▼)")

kpi_lines.append(
    f"Zeit > {THR:.0f} dB(A): {pct_thr:.0f}% der gemessenen Zeit"
    f" (= {n_above_min} min {n_above_s} s von {mess_min_tag} min)")

kpi_lines.append(
    f"Dauerlaerm-Phasen >=60 dB/>=10 min: {len(phases_60_10)}"
    f" (Gesamt {phases_60_10_min:.0f} min)")

if tb_spans:
    kpi_lines.append(f"Bohrgeraet/schw. Geraet (Dauerbetrieb >=70dB): {tb_min:.0f} min")

if DAY >= "2026-06-22":
    kpi_lines.append("Kalibrierung: vor + nach Messung, protokolliert")

if nacht_kpi:
    kpi_lines.append("--- Nachtzeit (AVV: RW 40 / Eingreif 45 dB(A)) ---")
    kpi_lines += nacht_kpi

kpi_lines.append(
    f"Lr (Takt 5s, TA-Laerm-Methodik, nur informativ): {fmt(lr_tag)} dB(A) [KI≈{fmt(ki_tag)}]")
kpi_lines.append(f"Geraet: PCE-323 Kl.2 (IEC 61672)  |  AVV-Bewertung: LAeq")

wrapped_kpi_lines = [
    textwrap.fill(
        line, width=105, subsequent_indent="  ", break_long_words=False
    )
    for line in kpi_lines
]
kpi = "\n".join(wrapped_kpi_lines).replace(".",",")
kpi_txt = ax.text(0.012, 0.975, "Kennzahlen\n"+kpi, transform=ax.transAxes,
        va="top", ha="left", fontsize=8.0, family="DejaVu Sans",
        bbox=dict(boxstyle="round,pad=0.5", fc="white", ec="#AAAAAA"), zorder=12)

# Pegel-je-Quelle-Box
pegel_txt = None
if src_stats:
    rows_b = ["Pegel je Laermquelle (KI-klassifizierte Ereignisse – Schaetzung)"]
    for s in BAU_RELEVANT:
        st = src_stats.get(s)
        if st:
            rows_b.append(f"{disp(s):<21} n={st['n']:>4}   Leq {st['leq']:.0f}   max {st['mx']:.0f} dB")
    pegel_txt = ax.text(0.988, 0.975, "\n".join(rows_b).replace(".",","),
            transform=ax.transAxes, va="top", ha="right", fontsize=8.0,
            family="DejaVu Sans",
            bbox=dict(boxstyle="round,pad=0.5", fc="white", ec="#AAAAAA"), zorder=12)

leg1 = ax.legend(loc="lower left", fontsize=8.5, framealpha=0.9, ncol=2)

present = [s for s in BAU_RELEVANT if s in used_srcs]
patches = [Patch(facecolor=src_color(s), alpha=0.6, label=disp(s)) for s in present]
if used_nonbau: patches.append(Patch(facecolor=NONBAU_COLOR, alpha=0.5,
                                     label="kein Baulaerm (sicher erkannt)"))
if used_nodata: patches.append(Patch(facecolor=NODATA_COLOR, alpha=0.7,
                                     label="keine gesicherte Bau-Zuordnung / keine Quelldaten"))
figleg = fig.legend(handles=patches, loc="lower center", bbox_to_anchor=(0.5,0.055),
           ncol=min(len(patches),5), fontsize=8.5, frameon=False,
           title=f"Pegelflaeche je {BIN_MIN}-min-Fenster nach dominanter Bau-Laermquelle")

title_txt = fig.suptitle(
    ("Schallpegel-Innenraummessung" if INDOOR else "Schallpegel-Aussenmessung")
    + f" – {SITE_TITLE}",
    x=0.06, y=0.965, ha="left", fontsize=15, fontweight="bold", color=C_TITLE)
subtitle_txt = fig.text(0.06, 0.915,
    f"{day0.strftime('%d.%m.%Y')} ({wd}) · PCE-323 (Klasse 2, IEC 61672) · "
    f"Messort: {POSITION} · AVV Baularm (LAeq)",
    fontsize=10, color="#555555")

# Hinweistext (Positiv: was gemessen, kein "KEIN valider")
warn_txt = None
_hw = (POS.get("Hinweis") or "")
_warn_parts = []
if _hw.startswith("WARN:"): _warn_parts.append(_hw[5:].strip())
if tier == "window":
    _warn_parts.append(
        f"MESSFENSTER: {abdeckung_tag*100:.0f}% des Bezugszeitraums 07–20h erfasst "
        f"– LAeq gilt fuer das gemessene Fenster (s. Kennzahlen-Box).")
elif tier == "partial":
    _warn_parts.append(
        f"TEILERFASSUNG: {abdeckung_tag*100:.0f}% des Bezugszeitraums 07–20h gemessen "
        f"– weitgehend repraesentativ.")
if _warn_parts:
    warn_txt = fig.text(0.06, 0.888, "⚠ " + " | ".join(_warn_parts),
                        fontsize=9, color="#C00000", fontweight="bold")

fig.text(0.06, 0.026,
    "Blaue Linie = LAeq (gleitend 1 min), graue Linie = LAF (1 s). "
    "Pegelflaeche = dominante Bau-Laermquelle je 10-min-Fenster. "
    f"Grenzlinien: Stufenfunktion Tag ({RW_TAG_WA:.0f}/{EINGREIF_TAG:.0f}) vs. Nacht ({RW_NACHT_WA:.0f}/{EINGREIF_NACHT:.0f} dB(A)). "
    f"Abdeckung 07–20h: {abdeckung_tag*100:.0f}%.",
    fontsize=7.5, color="#888888")
fig.text(0.06, 0.010,
    f"Normbezuege, Grenzwerte, Messmethodik: Erlaeuterungsseite im Tabellen-PDF. "
    f"Skript-Version: {VERSION_STR}.",
    fontsize=7.5, color="#888888")

overlap_check(fig, [
    ("Titel", title_txt), ("Untertitel", subtitle_txt),
    ("Kennzahlen-Box", kpi_txt), ("Pegel-je-Quelle-Box", pegel_txt),
    ("Linien-Legende", leg1), ("Quellen-Legende", figleg),
    ("Warnhinweis", warn_txt), ("RW-Label", richtlabel), ("Eingreif-Label", eingreiflabel),
], page="Seite 1")
pp.savefig(fig); plt.close(fig)
pp.close()

# ============ TABELLEN-PDF ============
order = {">=60":0,">=65":1,">=70":2}
rows_tab = sorted(phases, key=lambda p: (p["Start"], order.get(p["Kriterium"][:4],9)))
col = ["Kriterium","Start","Ende","Dauer\nmin","Leq\ndB(A)","Lmax\ndB(A)","Abd.\n%",
       "WAV","Laermquelle","Quellen-Detail"]
data, colors = [], []
for p in rows_tab:
    tb_ja = p.get("Dauerbetrieb_Regel")=="Ja"
    qsrc  = "Bohrgeraet/schweres Geraet" if tb_ja else ALIAS.get(p["Laermquelle_Auto"],p["Laermquelle_Auto"])
    data.append([p["Kriterium"],p["Start"],p["Ende"],p["Dauer_min"],p["Leq_dBA"],
                 p["Lmax_dBA"],p["Abdeckung_%"],p["Anzahl_WAV"],qsrc,p["Quellen_Detail"][:46]])
    colors.append(src_color(qsrc))

tier_note = {"valid": f"valider Tages-LAeq ({abdeckung_tag*100:.0f}% Abdeckung)",
             "partial": f"Teilerfassung ({abdeckung_tag*100:.0f}% Abdeckung)",
             "window": f"Messfenster ({abdeckung_tag*100:.0f}% Abdeckung)"}[tier]
HINWEIS = (
    f"Abdeckung 07-20h: {tier_note}. "
    f"Phasen-KPI-Box: nur >=60/>=10min (kein startswith-Bug). "
    f"Leq = energetischer Mittelpegel. Quelle = KI-Schaetzung. Skript: {VERSION_STR}."
)
ROWS_PER_PAGE = 30
n_pages = max(1, (len(data)+ROWS_PER_PAGE-1)//ROWS_PER_PAGE)
ppt = PdfPages(OUT_TAB)
if not data:
    fig2, ax2 = plt.subplots(figsize=(14,8.0)); ax2.axis("off")
    fig2.suptitle(f"Dauerlaerm-Phasen am {day0.strftime('%d.%m.%Y')} ({wd})",
                  x=0.06, ha="left", fontsize=14, fontweight="bold", color=C_TITLE)
    ax2.text(0.5, 0.6, "Keine Dauerlaerm-Phasen an diesem Tag.",
             ha="center", va="center", fontsize=12, color="#777777")
    fig2.text(0.06, 0.03, HINWEIS, fontsize=8, color="#777777")
    ppt.savefig(fig2); plt.close(fig2)
else:
    for pg in range(n_pages):
        chunk  = data[pg*ROWS_PER_PAGE:(pg+1)*ROWS_PER_PAGE]
        cchunk = colors[pg*ROWS_PER_PAGE:(pg+1)*ROWS_PER_PAGE]
        fig2, ax2 = plt.subplots(figsize=(14,9.5)); ax2.axis("off")
        fig2.subplots_adjust(left=0.03, right=0.985, top=0.90, bottom=0.06)
        fig2.suptitle(
            f"Dauerlaerm-Phasen am {day0.strftime('%d.%m.%Y')} ({wd}) — "
            f"Seite {pg+1}/{n_pages} · {len(data)} Phasen (alle Kriterien)",
            x=0.06, ha="left", fontsize=14, fontweight="bold", color=C_TITLE)
        tab = ax2.table(cellText=chunk, colLabels=col, loc="upper center",
                        cellLoc="center", bbox=[0.0,0.06,1.0,0.86])
        tab.auto_set_font_size(False); tab.set_fontsize(7.5)
        nrows = len(chunk)+1
        for (r,c),cell in tab.get_celld().items():
            cell.set_height(0.86/nrows); cell.set_edgecolor("#DDDDDD")
            if r==0: cell.set_facecolor(C_TITLE); cell.set_text_props(color="white",fontweight="bold")
            elif c==8: cell.set_facecolor(cchunk[r-1]); cell.set_text_props(color="white")
            elif r%2==0: cell.set_facecolor("#F4F6FA")
        fig2.text(0.03, 0.02, HINWEIS, fontsize=8, color="#777777")
        ppt.savefig(fig2); plt.close(fig2)

# ---- Erläuterungsseite ----
fig_erl, ax_erl = plt.subplots(figsize=(14,9.5)); ax_erl.axis("off")
fig_erl.subplots_adjust(left=0.06, right=0.96, top=0.92, bottom=0.04)
fig_erl.suptitle("Erlaeuterungen – Normbezuege, Grenzwerte, Messmethodik",
                 x=0.06, ha="left", fontsize=14, fontweight="bold", color=C_TITLE)
ERL = [
    ("AVV Baulaerm (lex specialis)",
     "Allgemeine Verwaltungsvorschrift zum Schutz gegen Baulaerm (RABl. 1970, S. 197). WA §34 BauGB bestaetigt.",
     [f"Richtwert Tagzeit 07–20 Uhr (WA): {RW_TAG_WA:.0f} dB(A)",
      f"Eingreifschwelle Tagzeit: {EINGREIF_TAG:.0f} dB(A)  (RW + 5 dB)",
      f"Richtwert Nachtzeit 20–07 Uhr (WA): {RW_NACHT_WA:.0f} dB(A)",
      f"Eingreifschwelle Nachtzeit: {EINGREIF_NACHT:.0f} dB(A)  |  Einzel-Nacht-Spitze > {NACHT_SPITZE:.0f} dB(A)",
      "Massgebliche Beurteilungsgroesse: LAeq – KEINE Taktmaximal-Methode",
      "Grenzlinien im Diagramm: Stufenfunktion (Tag- vs. Nachtwert)"]),
    ("LAeq-Abdeckung (3 Stufen)",
     f"Stufe 1 (>={COVERAGE_VALID*100:.0f}%): vollstaendiger Tages-LAeq 07–20h",
     [f"Stufe 2 ({COVERAGE_WINDOW*100:.0f}–{COVERAGE_VALID*100:.0f}%): Teilerfassung — "
      "valide im gemessenen Zeitraum, nicht vollstaendig repraesentativ",
      f"Stufe 3 (<{COVERAGE_WINDOW*100:.0f}%): Messfenster — LAeq fuer das tatsaechliche Messfenster, "
      "kein Tagesschnitt",
      "Keine Verneinung ('KEIN valider LAeq'): immer erst was der Wert belegt, dann Grenze",
      f"Aktuell ({DAY}): {abdeckung_tag*100:.0f}% -> {tier_note}"]),
    ("Nachtzeit-Kennzahlen [N1]",
     "Segmente vor 07:00 und nach 20:00 werden gesondert ausgewiesen (AVV Nacht-Richtwerte)",
     ["LAeq und LAFmax je Nachtsegment; gegen RW-N (40), Eingreif-N (45), Spitze (60) gestellt",
      "Bau-Zuordnung konservativ: nur wenn KI-Klassifikation Baulaerm zeigt",
      "Ohne KI-Bau-Beleg: 'moeglicher Umgebungslaerm – keine Bauzuordnung'",
      "Als gemessene Pegel ausgewiesen, KEIN fertiger Nacht-Beurteilungspegel"]),
    ("Hoechstwert & L1",
     "LAFmax = hoechster 1-Sekunden-Messwert (Fast-Bewertung). L1 = 99%-Pct.",
     ["LAFmax ist Einzelspitze (Wind/Handling-anfaellig) – fragile Beweisgroesse",
      "L1 (99%-Pct) = robuster: schließt oberstes 1% aus"]),
    ("Beurteilungspegel Lr – nur informativ",
     "Taktmaximalverfahren DIN 45645-1 / TA Laerm Anhang A.2 – NICHT fuer AVV Baulaerm",
     ["Primäre AVV-Beurteilung: LAeq. Lr ist TA-Laerm-spezifisch.",
      "KT = 0 (Tonzuschlag nicht messtechnisch bestimmt)"]),
    ("Messgeraet",
     "PCE-323, Klasse 2, IEC 61672-1:2013",
     [("Kalibrierung vor + nach Messung dokumentiert" if DAY >= "2026-06-22"
       else "Werkskalibrierung; keine gesonderte Feldkalibrierung protokolliert")]),
    ("Laermquellen-Klassifikation",
     "PANNs CNN14 (AudioSet, 527 Klassen) – KI-Schaetzung, geringere Beweiskraft als Pegelwerte",
     ["Leq je Quelle = energetischer Mittelwert (arith. Mittel unzulaessig fuer Pegel)",
      "Manuell geprueft > KI-Klassifikation > Heuristik"]),
]
y = 0.89
for titel, untertitel, punkte in ERL:
    ax_erl.text(0.0, y, titel, transform=ax_erl.transAxes,
                fontsize=10.5, fontweight="bold", color=C_TITLE)
    y -= 0.026
    ax_erl.text(0.0, y, untertitel, transform=ax_erl.transAxes,
                fontsize=8.5, color="#444444", style="italic")
    y -= 0.024
    for p in punkte:
        ax_erl.text(0.012, y, f"• {p}", transform=ax_erl.transAxes, fontsize=8.0, color="#222222")
        y -= 0.022
    y -= 0.008
fig_erl.text(0.06, 0.02, f"Skript {VERSION_STR} | Messwerte unveraendert.",
             fontsize=7.5, color="#888888")
ppt.savefig(fig_erl); plt.close(fig_erl)
ppt.close()

print(f"[v2] {DAY} | Tier={tier} ({abdeckung_tag*100:.0f}%) | "
      f"LAeq={fmt(laeq_tag)} | LAFmax={fmt(hoechst)} | L1={fmt(l1_tag)} | "
      f"Zeit>{THR:.0f}dB={pct_thr:.0f}%({n_above_min}min) | "
      f"Ph60/10={len(phases_60_10)}({phases_60_10_min:.0f}min) | "
      f"Nacht: vor7={fmt(laeq_vor7)} nach20={fmt(laeq_nach20)}")
print(f"  -> {OUT}")
print(f"  -> {OUT_TAB}  ({n_pages} Seite(n), {len(data)} Phasen)")
