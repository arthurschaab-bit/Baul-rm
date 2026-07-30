# -*- coding: utf-8 -*-
"""
07_gesamtbericht_v4.py
Vollständiger Gesamtbericht – konfigurierbarer Messstandort.
Basis: v3. v4-Änderungen (Aufgabenpaket):
  A Fehlerkorrekturen: A1 Referenztag→Vergleichstag (01./02./08.06.), A2 Hagel-Hinweis 11.06.,
    A3 Mikrofonhöhe-Konfighinweis, A4 Titel neutralisiert, A5 Quelle "Tiefbohrer/Schwerlast".
  B Neue Seiten: B1 Kernbefunde, B2 Belastungsdauer-Balken, B3 Lauteste Stunde,
    B4 Innenraumbetroffenheit, B5 Referenzpegel (30.05.), B6 Videoliste 12.06. markiert.
  + 26.06. neu aufgenommen (Außen, NW-Balkon).
Hilfsskripte separat: erstelle_rohdaten_manifest.py, video_triage.py.
v1/v2/v3-PDF unangetastet (v3 archiviert).
Ursprüngliche v3-Basis:
  1) NACHT GANZ RAUS: alle Tage auf 07:00–20:00 begrenzt; kein Nachtblock,
     keine Nacht-Grenzlinien — AUSNAHME 24.06. (Post-20:00 mit Videobeleg).
  2) KI-Terminologie entfernt; Quellen-Ebene bleibt (umbenannt "Quellenverteilung").
     Quellen-Ehrlichkeit (nur 8,6% manuell geprüft) = OFFENES TODO Mandant — kein Überklaim.
  3) Videobelege mit Zeitpunkten: Marker im Chart + Zeile je Tag + Gesamtliste.
  4) Messaufbau-Foto-Seite (Platzhalter bis Fotos vorliegen).
  5) Gutachter-Annäherungen: Mikrofonhöhe, Messunsicherheit ±1,4 dB, Meteo-Feld,
     Baseline-Hinweis, Lageplan-Hinweis, Rohdaten-Statement.
  6) Restfehler v2: Innen ohne AVV-Schwellen-Kennzahlen; Lückenliste explizit;
     "Lr (Takt 5s)" überall entfernt.
  + 25.06. neu aufgenommen (Außen, SO→NW-Balkon Wechsel ~10:08).
v1- und v2-PDF bleiben unangetastet (v2 archiviert).
Guardrail: Keine berechneten Pegelzahlen geändert (LAeq, LAFmax, L₁, Phasen,
           Abdeckung, Nachtpegel sind identisch zu v2).
"""
import os, sys, csv, glob, datetime, re, hashlib, json, shutil
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.lines as mlines
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle, Patch

# ============================================================
# KONFIG (identisch zu 06_report_v2.py — GUARDRAIL)
# ============================================================
COVERAGE_VALID  = 0.90
COVERAGE_WINDOW = 0.70
TAG_REF_SEC     = 13 * 3600   # 46800 s
RW_TAG_WA       = 55.0
EINGREIF_TAG    = 60.0
RW_NACHT_WA     = 40.0
EINGREIF_NACHT  = 45.0
NACHT_SPITZE    = 60.0
KONSERVATIV_FENSTER_START = 15    # Messende in diesem Fenster (15-19 Uhr) -> Rest bis 20h bruecken
KONSERVATIV_FENSTER_ENDE  = 19
KONSERVATIV_ANNAHME_DB    = RW_TAG_WA  # Annahme: Grenzwert-Konformitaet im unerfassten Rest (Tier=window)
STERN_ANNAHME_DB          = 50.0  # "hohe Abdeckung*": Annahme fuer Restzeit bei Tier=partial (>=70% echt)
KRITERIUM_60_10 = ">=60dB/>=10min"
KT              = 0.0
GAP_MIN_SEC     = 120         # Pseudo-Lücken < 2 min ignorieren (Geräte-Restart)
GERAET_TOL_DB   = 1.4         # Messunsicherheit PCE-323 Klasse 2 (IEC 61672)
LAUTESTE_STUNDE_MIN_SEC = 1800 # 60-min-Fenster wird ab 30 min Messanteil ausgewiesen
DAY_AXIS_DB_MIN = 25.0        # feste y-Achse fuer alle Tages-Schallgrafiken inkl. Referenz-/Ruhetage
DAY_AXIS_DB_MAX = 105.0
VERSION_STR     = "07_gesamtbericht v7 (2026-06)"
ADRESSE         = os.environ.get("BAUL_RM_ADDRESS", "Messadresse")
MIETER          = os.environ.get("BAUL_RM_TENANT", "Auftraggeber")
STANDORT_KURZ   = os.environ.get("BAUL_RM_SITE_TITLE", "Messstandort")
STANDORT_RECHTSHINWEIS = os.environ.get("BAUL_RM_LEGAL_NOTE", "Gebietscharakter lokal dokumentiert.")

# Tag mit dokumentierter Bautätigkeit im Nachtzeitraum (Videobeleg) — Ausnahme von "Nacht raus"
NACHT_AUSNAHME_TAG = "2026-06-24"

# A1: Vergleichstage (vormals "Referenztag") — Bautagebuch widerspricht "ohne Bautätigkeit"
VERGLEICHSTAG_LABEL = {
    "2026-06-08": "Vergleichstag — reduzierte Bautätigkeit (lange Betriebspause Tiefbohrer 09:00–13:00)",
    "2026-06-01": "Vergleichstag — geringe Bautätigkeit (nachmittags Baggerarbeiten ab 14:15)",
    "2026-06-02": "Vergleichstag — geringer Baulärm (Baubeginn 07:30)",
}

# Tage, an denen das Bautagebuch ein Arbeitsende VOR dem Messende dokumentiert (nicht pauschal
# "Messende = Bauende" -- nur die konkret belegten Faelle). Wert = dokumentiertes Bauzeit-Ende.
FEIERABEND_BELEGT = {
    "2026-06-18": "16:00",   # Bautagebuch: "ganztägig (ca. 07:15–16:00)"; Messung bis 16:43
    "2026-06-15": "16:30",   # Bautagebuch: "ganztägig (ca. 07:50–16:30)"; Messung bis 17:38
}

# Lageplan-Bilder (Geometrie/Kausalität) — vom Mandanten beizustellen
LAGEPLAN_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "Fotos_Videos", "Lageplan"))

# Video-Ordner (Beleg-Integration, v3 Punkt 3)
VIDEO_DIR   = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "Fotos_Videos", "Schallmessvideos"))
# Messaufbau-Fotos (v3 Punkt 4) — echter Ordner Fotos_Aufbau
MESSAUFBAU_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "Fotos_Videos", "Fotos_Aufbau"))

# Der Starter kann die selektiv lokal gespiegelten Berichtsbilder explizit setzen.
_PHOTO_ROOT_OVERRIDE = os.environ.get("BAUL_RM_PHOTO_ROOT")
if _PHOTO_ROOT_OVERRIDE:
    LAGEPLAN_DIR = os.path.join(_PHOTO_ROOT_OVERRIDE, "Lageplan")
    MESSAUFBAU_DIR = os.path.join(_PHOTO_ROOT_OVERRIDE, "Fotos_Aufbau")
    VIDEO_DIR = os.path.join(_PHOTO_ROOT_OVERRIDE, "Schallmessvideos")
# Foto-Zuordnung nach Aufbau-Regime (Dateibasis ohne .jpg) — Angabe Mandant
try:
    _setup_groups = json.loads(os.environ.get("BAUL_RM_SETUP_GROUPS_JSON", "[]"))
except (TypeError, ValueError, json.JSONDecodeError):
    _setup_groups = []
MESSAUFBAU_GROUPS = _setup_groups if isinstance(_setup_groups, list) else []

DAYS_ALL_DEFAULT = [
    "2026-06-26","2026-06-25","2026-06-24","2026-06-23","2026-06-22","2026-06-19",
    "2026-06-18","2026-06-17","2026-06-16","2026-06-15",
    "2026-06-11","2026-06-10","2026-06-09","2026-06-08",
    "2026-06-02","2026-06-01",
    "2026-05-26","2026-05-22","2026-05-21","2026-05-20",
]

CONFIG_PATH = os.environ.get(
    "SCHALLBERICHT_CONFIG",
    os.path.join(os.path.dirname(__file__), "pipeline_config_v10.json"),
)

def load_pipeline_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:
        print(f"[lib] Konfiguration nicht lesbar ({CONFIG_PATH}): {exc}")
        return {}

PIPELINE_CONFIG = load_pipeline_config()
DAYS_ALL = PIPELINE_CONFIG.get("report_days", DAYS_ALL_DEFAULT)
if not isinstance(DAYS_ALL, list) or not DAYS_ALL:
    DAYS_ALL = DAYS_ALL_DEFAULT
VERSION_STR = PIPELINE_CONFIG.get("version_str", VERSION_STR)
MANIFEST_CSV_NAME = PIPELINE_CONFIG.get("manifest_csv", "Rohdaten_Manifest_v7.csv")
VIDEO_AUDIT_CSV_NAME = PIPELINE_CONFIG.get("video_audit_csv", "Videobelege_Pruefung_v7.csv")
REPORT_FILENAME = PIPELINE_CONFIG.get(
    "gesamtbericht_pdf", "Gesamtbericht_Schallmessung_v10.pdf"
)

# Zweite Berichtsversion ohne Laermquellen-/WAV-Klassifikation (ab jetzt bei jedem Lauf zusaetzlich
# erzeugt, siehe 07_gesamtbericht_v9_ohne_wav.py). Env-Var-gesteuert statt eigener Config-Datei,
# damit report_days/version_str synchron mit der Normalversion bleiben (kein Duplikat-Pflegeaufwand).
NO_AUDIO_MODE = os.environ.get("SCHALLBERICHT_NO_AUDIO", "0") == "1"
_OUTPUT_OVERRIDE = os.environ.get("SCHALLBERICHT_OUTPUT_OVERRIDE")
if _OUTPUT_OVERRIDE:
    REPORT_FILENAME = _OUTPUT_OVERRIDE
if NO_AUDIO_MODE:
    VERSION_STR += "  [ohne WAV-/Quellen-Erkennung]"

# ============================================================
# FARBEN — zurückhaltend (E: Ästhetik)
# ============================================================
C_TITLE  = "#1F4E79"   # Dunkelblau — Primärfarbe
C_ACCENT = "#2E75B6"   # Hellblau — Akzent
C_MOM    = "#BBBBBB"   # LAF 1s-Linie
C_LAEQ   = "#1F4E79"   # LAeq-Linie
C_RICHT  = "#C00000"   # Richtwert-Linie
C_EING   = "#C05000"   # Eingreif-Linie (gedämpfter als Orange)
C_VIDDB  = "#6A3D9A"   # Video-dB-Schaetzung in Messluecken (v8, unkalibriert, klar abgegrenzt)
C_NACHT_BG = "#E8EFF6" # Nacht-Hintergrund
C_WARN   = "#8B1A00"   # Warnung

SRC_COLOR = {
    "Bagger":"#D4860A",
    "Bohrgeraet/schweres Geraet":"#7B4030",
    "Motor/Diesel":"#8B8B00",
    "Schlagen/Bohren":"#A01010",
    "Saege":"#C04800",
    "Fahrzeug":"#6040A0",
    "Signal/Warnton":"#008090",
    "Sprache":"#1A7030",
    "Umgebung/Sonstiges":"#999999",
}
NONBAU_COLOR = "#5A8A80"
NODATA_COLOR = "#E0E0E0"
BAU_RELEVANT = ["Bagger","Bohrgeraet/schweres Geraet","Motor/Diesel",
                "Schlagen/Bohren","Saege","Fahrzeug","Signal/Warnton"]
ALIAS = {"Tiefbohrer":"Bohrgeraet/schweres Geraet"}

DISP = {
    "Bagger":"Bagger",
    "Bohrgeraet/schweres Geraet":"Tiefbohrer/Schwerlast",
    "Motor/Diesel":"Motor/Diesel",
    "Schlagen/Bohren":"Schlagen/Bohren",
    "Saege":"Säge",
    "Fahrzeug":"Fahrzeug",
    "Signal/Warnton":"Signal/Warnton",
    "Sprache":"Sprache",
    "Umgebung/Sonstiges":"Umgebung/Sonstiges",
}
DISP_S = {  # Kurzform für Mono-Tabellen
    "Bagger":"Bagger            ",
    "Bohrgeraet/schweres Geraet":"Tiefbohrer/Schwl. ",
    "Motor/Diesel":"Motor/Diesel      ",
    "Schlagen/Bohren":"Schlagen/Bohren   ",
    "Saege":"Säge              ",
    "Fahrzeug":"Fahrzeug          ",
    "Signal/Warnton":"Signal/Warnton    ",
    "Sprache":"Sprache           ",
    "Umgebung/Sonstiges":"Umgebung/So.      ",
}
WOCHENTAGE = {0:"Mo",1:"Di",2:"Mi",3:"Do",4:"Fr",5:"Sa",6:"So"}

def src_color(s): return SRC_COLOR.get(s,"#777777")
def disp(s):      return DISP.get(s,s)
def disp_s(s):    return DISP_S.get(s,(s[:18]).ljust(18))

def mini_bar(pct, total=12):
    n = max(0, min(total, round(pct/100*total)))
    return '█'*n + '░'*(total-n)

# ============================================================
# SETUP
# ============================================================
BASE    = os.path.abspath(os.path.join(os.path.dirname(__file__),"..",".."))
OUTDIR  = os.path.join(BASE,"Aufbereit_v2"); os.makedirs(OUTDIR,exist_ok=True)
OUT_PDF = os.path.join(OUTDIR, REPORT_FILENAME)

import importlib.util as _ilu
_sp=_ilu.spec_from_file_location("tb",os.path.join(os.path.dirname(__file__),"tiefbohrer.py"))
tb=_ilu.module_from_spec(_sp); _sp.loader.exec_module(tb)

_POS_CACHE = None
def load_pos(day):
    """Messpositionen einmal einlesen und cachen (lib_v3: vorher 1 Datei-Read pro Aufruf)."""
    global _POS_CACHE
    if _POS_CACHE is None:
        _POS_CACHE = {}
        p=os.path.join(BASE,"Verknuepfung","messpositionen.csv")
        if os.path.exists(p):
            with open(p,encoding="utf-8-sig",newline='') as f:
                for row in csv.DictReader(f,delimiter=";"):
                    if row.get("Datum"): _POS_CACHE[row["Datum"]] = row
    return _POS_CACHE.get(day, {})

# ============================================================
# VIDEOBELEGE (v7+) — robuste Dateiname-Erkennung:
#   PXL_YYYYMMDD_HH_MM...mp4 oder PXL_YYYYMMDD_HHMMSSmmm...mp4
# ============================================================
_VID_RE = re.compile(r'^PXL_(\d{8})_(?:(\d{2})_(\d{2})(.*)|(\d{2})(\d{2})(\d{2})(\d{3})(.*))$', re.I)
_DBA_RE = re.compile(r'(?<!\d)(\d{1,3})(?:\s*[-_]\s*\.?\s*(\d{1,3}))?\s*dBA(?=$|[^A-Za-z0-9])', re.I)
_BARE_DB_RE = re.compile(r'(?:^|[_.\s])(\d{1,3})(?:\s*[-_]\s*\.?\s*(\d{1,3}))(?=(?:[_.\s]|$))', re.I)
VIDEO_PARSE_MISSING = []

def _parse_video(fn, in_sub):
    base = fn[:-4] if fn.lower().endswith('.mp4') else fn
    m = _VID_RE.match(base)
    if not m: return None
    ymd = m.group(1)
    if m.group(2) is not None:
        hh, mm, rest = m.group(2), m.group(3), m.group(4)
    else:
        hh, mm, rest = m.group(5), m.group(6), m.group(9)
    try:
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60): return None
        day = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
    except Exception:
        return None
    dba = ""
    matches = list(_DBA_RE.finditer(rest))
    if matches:
        vals = []
        for dm in matches:
            vals.append(f"{dm.group(1)}-{dm.group(2)}" if dm.group(2) else dm.group(1))
        dba = " / ".join(vals)
        for dm in reversed(matches):
            rest = rest[:dm.start()] + rest[dm.end():]
    else:
        bare_matches = list(_BARE_DB_RE.finditer(rest))
        if bare_matches:
            dm = bare_matches[-1]
            dba = f"{dm.group(1)}-{dm.group(2)}"
            rest = rest[:dm.start()] + rest[dm.end():]
    ort = re.sub(r'^[_.\s]*\(\d+\)', '', rest)         # führendes (2)
    ort = re.sub(r'^[_.\s]*\d+(?=[_.\s])', '', ort)     # führendes _2
    ort = ort.replace('_', ' ').replace('.', ' ').strip(' -_.')
    ort = re.sub(r'\s+', ' ', ort) or "—"
    return dict(day=day, time=f"{hh}:{mm}", ort=ort, dba=dba, file=fn, sub=in_sub)

_ORT_FIX = [
    (re.compile(r'\bKInderzimmer\b'), 'Kinderzimmer'),
    (re.compile(r'\bgeschlosse\b'),   'geschlossen'),
    (re.compile(r'\bgeschloss\b'),    'geschlossen'),
    (re.compile(r'\bSüdostbalkon\b'), 'SO-Balkon'),
    (re.compile(r'\bSüdost[- ]Balkon\b'), 'SO-Balkon'),
    (re.compile(r'\bNordwest[- ]Balkon\b'), 'NW-Balkon'),
    (re.compile(r'\bNW Balkon\b'),    'NW-Balkon'),
    (re.compile(r'\bSO Balkon\b'),    'SO-Balkon'),
]
def _fix_ort(ort):
    for pat, rep in _ORT_FIX:
        ort = pat.sub(rep, ort)
    return ort

def load_videos():
    vids = []
    VIDEO_PARSE_MISSING.clear()
    if os.path.isdir(VIDEO_DIR):
        for root, _, files in os.walk(VIDEO_DIR):
            in_sub = os.path.basename(root).lower() != os.path.basename(VIDEO_DIR).lower()
            for fn in files:
                if not fn.lower().endswith('.mp4'): continue
                v = _parse_video(fn, in_sub)
                if v:
                    v['ort'] = _fix_ort(v['ort'])
                    v['rel'] = os.path.relpath(os.path.join(root, fn), VIDEO_DIR)
                    vids.append(v)
                else:
                    rel = os.path.relpath(os.path.join(root, fn), VIDEO_DIR)
                    VIDEO_PARSE_MISSING.append(rel)
    vids.sort(key=lambda v: (v['day'], v['time']))
    return vids

VIDEOS = load_videos()
from collections import defaultdict as _dd
VIDEOS_BY_DAY = _dd(list)
for _v in VIDEOS: VIDEOS_BY_DAY[_v['day']].append(_v)

def write_video_audit(all_data):
    """Schreibt nach jedem Lauf eine Prüfliste: erkannt, Messtag, in Tagesgrafik sichtbar."""
    out_csv = os.path.join(OUTDIR, VIDEO_AUDIT_CSV_NAME)
    mday = set(DAYS_ALL)
    day_windows = {}
    for d in all_data:
        x_right = max(d['t20'], d['t'][-1]) if d['day'] == NACHT_AUSNAHME_TAG else d['t20']
        day_windows[d['day']] = (d['t7'], x_right)

    rows = []
    for v in VIDEOS:
        in_report = v['day'] in mday
        visible = False
        reason = ""
        try:
            hh, mm = map(int, v['time'].split(':'))
            vdt = pd.Timestamp(v['day']) + pd.Timedelta(hours=hh, minutes=mm)
            if not in_report:
                reason = "Videobeleg ohne parallele CSV-Schallmessung"
            elif v['day'] not in day_windows:
                reason = "Messtag ohne auswertbare CSV"
            else:
                left, right = day_windows[v['day']]
                visible = bool(left <= vdt <= right)
                reason = "sichtbar in Tagesgrafik" if visible else "außerhalb dargestelltem Messfenster"
        except Exception:
            reason = "Zeit nicht auswertbar"
        rows.append({
            "Datei": v.get('rel', v['file']),
            "Erkannt": "ja",
            "Datum": v['day'],
            "Uhrzeit": v['time'],
            "Ort": v['ort'],
            "Video_dB": v['dba'],
            "Unterordner": "ja" if v['sub'] else "nein",
            "Im_Berichtstag": "ja" if in_report else "nein",
            "In_Tagesgrafik": "ja" if visible else "nein",
            "Als_Beleg_beruecksichtigt": "ja",
            "Belegart": ("PCE-Messtag + Tagesgrafik" if visible else
                         "Videobeleg ohne parallele CSV-Schallmessung" if not in_report else
                         "Videobeleg am Messtag außerhalb Tagesgrafik"),
            "Hinweis": reason,
        })
    for rel in VIDEO_PARSE_MISSING:
        rows.append({
            "Datei": rel,
            "Erkannt": "nein",
            "Datum": "",
            "Uhrzeit": "",
            "Ort": "",
            "Video_dB": "",
            "Unterordner": "ja" if os.path.dirname(rel) else "nein",
            "Im_Berichtstag": "nein",
            "In_Tagesgrafik": "nein",
            "Als_Beleg_beruecksichtigt": "nein",
            "Belegart": "nicht erkannt",
            "Hinweis": "Dateiname nicht automatisch erkannt",
        })
    rows.sort(key=lambda r: (r["Datum"] or "9999-99-99", r["Uhrzeit"], r["Datei"]))
    fields = ["Datei","Erkannt","Datum","Uhrzeit","Ort","Video_dB","Unterordner",
              "Im_Berichtstag","In_Tagesgrafik","Als_Beleg_beruecksichtigt",
              "Belegart","Hinweis"]
    with open(out_csv, "w", newline='', encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    return out_csv, rows

# ============================================================
# MIKROFONHÖHE-REGIME (Angabe Mandant) — v3 Gruppe J
#   01.06.–14.06.: 98–100 cm (auf Möbeln: Tisch 100 / Stuhl+Box 98)
#   ab 15.06.:     140 cm (freier Ständer, messtechnisch belastbarer)
#   Mai (innen):   nicht dokumentiert
# ============================================================
def mic_height(day, indoor):
    if indoor:
        return "nicht dokumentiert (Innenraum)"
    if day < "2026-06-15":
        return "98–100 cm (Möbel-Aufbau)"
    return "140 cm (freier Ständer)"

REFERENZ_TAGE = {"2026-06-01", "2026-06-02", "2026-06-08"}  # ohne signifikante Bautätigkeit (Angabe Mandant)

# ============================================================
# BAUTAGEBUCH-SYNC (v3 Gruppe E) — höchste Vxx.xlsx, Spalten:
#   0 Datum / 1 Wochentag / 2 Zeit / 3 Phase / 4 Beschreibung / 5 Bewertung / 6 PDF-Ref
# ============================================================
def load_bautagebuch():
    base_par = os.path.abspath(os.path.join(BASE, ".."))
    cands = [c for c in glob.glob(os.path.join(base_par, os.environ.get("BAUL_RM_DIARY_GLOB", "Bautagebuch_*.xlsx")))
             if "~$" not in c]
    if not cands:
        return {}
    def vnum(p):
        m = re.search(r'_V(\d+)\.xlsx$', p)
        return int(m.group(1)) if m else -1
    src = max(cands, key=vnum)
    out = {}
    try:
        df = pd.read_excel(src, header=None, dtype=str)
        for _, row in df.iterrows():
            vals = ["" if (v is None or (isinstance(v, float) and np.isnan(v))) else str(v).strip()
                    for v in row.values]
            if not vals: continue
            m = re.match(r'(\d{2})\.(\d{2})\.(\d{4})', vals[0])
            if not m: continue
            key = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            out[key] = dict(phase=vals[3] if len(vals) > 3 else "",
                            beschr=vals[4] if len(vals) > 4 else "")
    except Exception as e:
        print("Bautagebuch-Load-Fehler:", e)
    return out

BAUTAGEBUCH = load_bautagebuch()

# ============================================================
# METEOROLOGIE je Messtag (DWD München-Stadt 03379, via meteo_dwd.py)
# ============================================================
def load_meteo():
    p = os.path.join(os.path.dirname(__file__), "meteo_je_messtag.csv")
    out = {}
    if os.path.exists(p):
        for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
            out[r["Datum"]] = r
    return out

METEO = load_meteo()
METEO_STATION = "DWD München-Stadt (03379)"

def meteo_line(day):
    """Kompakte Wetterzeile je Tag oder None."""
    m = METEO.get(day)
    if not m or m.get("Wind_mittel_ms", "—") == "—":
        return None
    return (f"Wetter (07–20 h): Wind {m['Wind_mittel_ms']}/{m['Wind_max_ms']} m/s {m['Windrichtung']}, "
            f"{m['Temp_min_C']}–{m['Temp_max_C']} °C, Regen {m['Niederschlag_mm']} mm")

# ============================================================
# FORMAT-HILFEN
# ============================================================
def fmt(v):  return "—" if(v is None or(isinstance(v,float)and np.isnan(v)))else f"{v:.1f}"
def fmt0(v): return "—" if(v is None or(isinstance(v,float)and np.isnan(v)))else f"{v:.0f}"
def fmt_dur(sec):
    h=sec//3600; m=(sec%3600)//60
    return f"{h}h {m:02d}min" if h else f"{m}min"

def fig_text_wrap(fig, x, y, s, right_x, line_h, **kw):
    """Platziert s bei (x,y) und bricht WÖRTER so um, dass die rechte Kante <= right_x
    (Figur-Bruchteil 0..1) bleibt. Misst die gerenderte Ausdehnung VOR dem Platzieren
    (Teil-2-Regel 1). Gibt das y nach der letzten platzierten Zeile zurück.
    line_h = Zeilenhöhe in Figur-Bruchteil."""
    min_y = kw.pop("min_y", 0.042)
    PW = fig.get_figwidth()*fig.get_dpi()
    right_px = right_x*PW
    try:
        rend = fig.canvas.get_renderer()
    except Exception:
        fig.canvas.draw(); rend = fig.canvas.get_renderer()
    def _fits(txt):
        tobj = fig.text(x, y, txt, **kw)
        bb = tobj.get_window_extent(rend)
        tobj.remove()
        return bb.x1 <= right_px

    # lib_v3 Fastpath: erst die GANZE Restzeile messen (1 Messung statt 1 pro Wort);
    # nur bei Ueberlauf inkrementell umbrechen (identisches Umbruchverhalten wie vorher).
    words = s.split(' ')
    wi = 0
    while wi < len(words):
        rest = ' '.join(words[wi:]).strip()
        if rest and _fits(rest):
            if y < min_y:
                return y
            fig.text(x, y, rest, **kw)
            y -= line_h
            return y
        cur = ''
        while wi < len(words):
            trial = (cur+' '+words[wi]).strip()
            if cur and not _fits(trial):
                break
            cur = trial
            wi += 1
        if cur:
            if y < min_y:
                return y
            fig.text(x, y, cur, **kw)
            y -= line_h
        else:
            wi += 1  # Einzelwort breiter als Spalte: trotzdem weiter (wie vorher: platziert)
    return y

# ============================================================
# ABDECKUNGS-LOGIK — v2: Lücken-Bug behoben (C)
# ============================================================
def merge_sessions(sessions, gap_sec=0):
    """Sortiert und mergt Sessions, die weniger als gap_sec auseinanderliegen."""
    if not sessions: return []
    srt=sorted(sessions)
    merged=[list(srt[0])]
    for a,b in srt[1:]:
        if (a-merged[-1][1]).total_seconds()<=gap_sec:
            merged[-1][1]=max(merged[-1][1],b)
        else:
            merged.append([a,b])
    return [tuple(x) for x in merged]

def sessions_in_tag(sessions,day0):
    """Clipped sessions auf [t7,t20], mergt Überlappungen (Fix: Duplikate durch t7-Clip)."""
    t7=day0+pd.Timedelta(hours=7); t20=day0+pd.Timedelta(hours=20)
    within=[]
    for a,b in sessions:
        ca=max(a,t7); cb=min(b,t20)
        if ca<cb: within.append((ca,cb))
    return merge_sessions(within, gap_sec=0)  # strikt mergen nach Clip

def get_gaps(sessions,day0):
    """Echte Lücken (>GAP_MIN_SEC) im Tageszeitraum. Kein Duplikat, keine Pseudo-Lücken."""
    t7=day0+pd.Timedelta(hours=7); t20=day0+pd.Timedelta(hours=20)
    raw_merged=merge_sessions(sessions, gap_sec=GAP_MIN_SEC)
    within=sessions_in_tag(raw_merged,day0)
    gaps=[]; cursor=t7
    for a,b in within:
        if (a-cursor).total_seconds()>GAP_MIN_SEC:
            gaps.append((cursor,a))
        cursor=max(cursor,b)
    if (t20-cursor).total_seconds()>GAP_MIN_SEC:
        gaps.append((cursor,t20))
    return gaps, within

def get_tier(abd):
    if abd>=COVERAGE_VALID: return "valid"
    elif abd>=COVERAGE_WINDOW: return "partial"
    return "window"

def coverage_label_short(laeq,abd,sessions,day0):
    if np.isnan(laeq): return "LAeq: —"
    pct=round(abd*100)
    gaps,within=get_gaps(sessions,day0)
    def fdt(dt): return dt.strftime("%H:%M")
    if abd>=COVERAGE_VALID:
        return f"► LAeq Tag (07–20h): {laeq:.1f} dB(A)  [Abdeckung {pct}%]"
    elif abd>=COVERAGE_WINDOW:
        # v3 Punkt 6: ALLE Lücken explizit ausschreiben (kein "+N weitere")
        if gaps:
            gs=" — Lücke(n): " + "; ".join(
                f"{fdt(a)}–{fdt(b)} ({fmt_dur(int((b-a).total_seconds()))})" for a,b in gaps)
        else: gs=""
        return f"(~) LAeq Teilerfassung 07–20h: {laeq:.1f} dB(A) — {pct}% erfasst{gs}"
    else:
        if len(within)>=2:
            segs=" + ".join(f"{fdt(a)}–{fdt(b)}" for a,b in within)
            return f"(M) LAeq {len(within)} Segmente ({segs}): {laeq:.1f} dB(A) — {pct}%"
        else:
            seg=within[0] if within else (sessions[0][0],sessions[-1][1])
            return f"(M) LAeq Messfenster {fdt(seg[0])}–{fdt(seg[1])}: {laeq:.1f} dB(A) — {pct}%"

def coverage_supplement(abd,sessions,day0,tag_sec):
    """Ergänzungstext mit vollständiger Lückenliste — kein '+N weitere'."""
    gaps,within=get_gaps(sessions,day0)
    def fdt(dt): return dt.strftime("%H:%M")
    pct=round(abd*100)
    if abd>=COVERAGE_VALID:
        return f"Vollständig erfasster Tages-LAeq — {pct}% des Bezugszeitraums 07–20h erfasst."
    elif abd>=COVERAGE_WINDOW:
        if gaps:
            gs="; ".join(f"{fdt(a)}–{fdt(b)} ({fmt_dur(int((b-a).total_seconds()))})" for a,b in gaps)
        else: gs="keine Lücken erkennbar"
        return f"Weitgehend repräsentativ für Tagesmittel ({pct}% Abdeckung). Nicht erfasst: {gs}."
    else:
        dur_h=round(tag_sec/3600,1)
        gap_strs=[f"{fdt(a)}–{fdt(b)} ({fmt_dur(int((b-a).total_seconds()))})" for a,b in gaps]
        uncov=", ".join(gap_strs) if gap_strs else "—"
        return (f"LAeq für das gemessene Fenster ({dur_h:.1f}h = {pct}%). "
                f"Nicht Tagesdurchschnitt. Nicht erfasst: {uncov}.")

# ============================================================
# DATEN LADEN (GUARDRAIL: identische Berechnungen zu v1)
# ============================================================
def compute_day(day):
    day0=pd.Timestamp(day)
    pos=load_pos(day)
    indoor=pos.get("Umgebung","").strip().lower().startswith("innen")
    position=pos.get("Position","").strip() or "lt. Bautagebuch"

    frames,sessions=[],[]
    for c in sorted(glob.glob(os.path.join(BASE,f"{day} *.csv"))):
        if "(1)"in c or "(2)"in c: continue
        # lib_v3: vektorisiertes Einlesen (vorher ~36k strptime-Aufrufe pro Tag in Python-
        # Schleife). Ergebnis 1:1 identisch verifiziert (Text-Diff aller Berichtseiten).
        try:
            raw=pd.read_csv(c,header=None,names=["idx","datum","zeit","dba","c4","c5","c6"],
                            usecols=[0,1,2,3],dtype=str,encoding='utf-8',
                            encoding_errors='replace',on_bad_lines='skip',engine='c')
        except Exception:
            raw=None
        recs_df=None
        if raw is not None and len(raw):
            mask=raw["idx"].str.strip().str.isdigit().fillna(False)
            sub=raw[mask]
            if len(sub):
                dts=pd.to_datetime(sub["datum"].str.strip()+" "+sub["zeit"].str.strip(),
                                   format="%m-%d-%Y %H:%M:%S",errors="coerce")
                vals=pd.to_numeric(sub["dba"],errors="coerce")
                ok=dts.notna()&vals.notna()
                if ok.any():
                    recs_df=pd.DataFrame({"dt":dts[ok].values,"dba":vals[ok].values})
        if recs_df is not None and len(recs_df):
            sessions.append((recs_df["dt"].iloc[0].to_pydatetime(),
                             recs_df["dt"].iloc[-1].to_pydatetime()))
            frames.append(recs_df)
    if not frames: return None

    df=pd.concat(frames).drop_duplicates("dt").set_index("dt").sort_index()
    full=pd.date_range(df.index[0],df.index[-1],freq="1s")
    df=df.reindex(full)
    sessions=sorted(set(sessions))

    dba=df["dba"]; energy=10**(dba/10)
    laeq1m=10*np.log10(energy.rolling(60,min_periods=20).mean())
    t=df.index
    t7=day0+pd.Timedelta(hours=7); t20=day0+pd.Timedelta(hours=20)
    hour=t.hour+t.minute/60
    m_tag=(hour>=7)&(hour<20); m_b7=t.hour<7; m_a20=t.hour>=20

    def leq_of(mask):
        e=energy[mask].dropna()
        return 10*np.log10(e.mean()) if len(e) else float('nan')

    tag_sec=int(dba[m_tag].notna().sum())
    vor7_sec=int(dba[m_b7].notna().sum())
    na20_sec=int(dba[m_a20].notna().sum())
    abd_tag=tag_sec/TAG_REF_SEC

    laeq_tag=leq_of(m_tag); laeq_vor7=leq_of(m_b7); laeq_na20=leq_of(m_a20)
    tagsec=dba[m_tag].dropna()
    hoechst=float(dba.max())
    l1_tag=float(np.nanpercentile(tagsec.values,99)) if len(tagsec) else float('nan')
    lmax_b7=float(dba[m_b7].max()) if vor7_sec>0 else float('nan')
    lmax_a20=float(dba[m_a20].max()) if na20_sec>0 else float('nan')

    pct_thr=100*float((tagsec>55.0).mean()) if len(tagsec) else 0.0
    n_above_sec=int((tagsec>55.0).sum())
    n_above_60_sec=int((tagsec>60.0).sum())   # B2: Minuten > 60 dB(A)
    mess_min=tag_sec//60

    def beurt(laf,laeq):
        v=laf.dropna().values
        if len(v)<5 or np.isnan(laeq): return float('nan'),float('nan')
        n=(len(v)//5)*5; takt=v[:n].reshape(-1,5).max(axis=1)
        lt=10*np.log10(np.mean(10**(takt/10))); return lt,lt-laeq
    lr_tag,ki_tag=beurt(tagsec,laeq_tag)

    leq60=10*np.log10(energy.rolling(3600,min_periods=LAUTESTE_STUNDE_MIN_SEC).mean())
    if leq60.notna().any():
        it=leq60.idxmax(); lh_val=float(leq60.max())
        lh_start=it-pd.Timedelta(seconds=3599)
        lh_txt=f"{lh_start.strftime('%H:%M')}–{it.strftime('%H:%M')}"
        lh_cov=int(dba[(t>=lh_start)&(t<=it)].notna().sum())
    else:
        it=None; lh_val=float('nan'); lh_txt="—"; lh_cov=0

    phases=[r for r in csv.DictReader(
        open(os.path.join(BASE,"Verknuepfung","dauerlaerm.csv"),encoding="utf-8-sig")
    ) if r["Datum"]==day]
    def to_dt(hms):
        h,m,s=map(int,hms.split(":")); return day0+pd.Timedelta(hours=h,minutes=m,seconds=s)
    ph60_10=[p for p in phases if p["Kriterium"]==KRITERIUM_60_10]
    ph60_min=sum(float(p.get("Dauer_min",0)) for p in ph60_10)

    ph_src_iv=[]
    for p in phases:
        s="Bohrgeraet/schweres Geraet" if p.get("Dauerbetrieb_Regel")=="Ja" else(p.get("Laermquelle_Auto") or "")
        s=ALIAS.get(s,s)
        if s and s!="—": ph_src_iv.append((to_dt(p["Start"]),to_dt(p["Ende"]),s))
    def phase_src_at(ts):
        hit=None
        for a,b,s in ph_src_iv:
            if a<=ts<=b: hit=s
        return hit

    ev=[r for r in csv.DictReader(
        open(os.path.join(BASE,"Verknuepfung","relevante_ereignisse.csv"),encoding="utf-8-sig")
    ) if r["Datum"]==day]
    has_verified_src=any((r.get("Laermquelle_geprueft") or "").strip() for r in ev)
    def ev_dt(r):
        h,m,s=map(int,r["Uhrzeit"].split(":")); return day0+pd.Timedelta(hours=h,minutes=m,seconds=s)
    def src_of(r):
        g=(r.get("Laermquelle_geprueft") or "").strip()
        c=(r.get("Laermquelle_Cluster") or "").strip()
        s=g if g else c if c else(r.get("Laermquelle_KI") or r.get("Laermquelle_Auto",""))
        return ALIAS.get(s,s)
    ev_bau=[r for r in ev if src_of(r) in BAU_RELEVANT]

    BIN=10
    bins={}
    for r in ev_bau:
        b=day0+pd.Timedelta(minutes=BIN*int(((ev_dt(r)-day0).total_seconds())//(BIN*60)))
        bins.setdefault(b,{}); q=src_of(r)
        bins[b][q]=bins[b].get(q,0)+(float(r["dBA"]) if r["dBA"] else 0.0)
    bin_dom={b:max(d,key=d.get) for b,d in bins.items()}
    bin_any=set()
    for r in ev:
        bin_any.add(day0+pd.Timedelta(minutes=BIN*int(((ev_dt(r)-day0).total_seconds())//(BIN*60))))

    src_stats={}
    for s in BAU_RELEVANT:
        vals=[float(r["dBA"]) for r in ev_bau if src_of(r)==s and r["dBA"]]
        if vals:
            src_stats[s]=dict(n=len(vals),leq=10*np.log10(np.mean([10**(v/10) for v in vals])),mx=max(vals))

    total_bins=len(bin_dom); src_pct={}
    if total_bins>0:
        from collections import Counter
        cnt=Counter(bin_dom.values())
        src_pct={s:cnt.get(s,0)/total_bins*100 for s in BAU_RELEVANT if s in cnt}

    def night_bau():
        for r in ev:
            try: h,mi,s_=map(int,r["Uhrzeit"].split(":")); sod=h*3600+mi*60+s_
            except: continue
            if sod<7*3600 or sod>=20*3600:
                if src_of(r) in BAU_RELEVANT: return True
        return False

    tb_spans=tb.detect_spans(df["dba"]) if day>=tb.AB_DATUM else []
    tb_min=sum((b-a).total_seconds() for a,b,_,_ in tb_spans)/60.0

    tier=get_tier(abd_tag)

    # --- Konservative Volltag-Hochrechnung (Tier=window, <70% echt) ---
    # Rein additiv: ersetzt NIEMALS laeq_tag/tier/abd_tag. Ausloeser: die Messung endet tatsaechlich
    # irgendwann zwischen KONSERVATIV_FENSTER_START und KONSERVATIV_FENSTER_ENDE Uhr (Geraet stoppt
    # nachmittags/frueh abends -- typischer Akku-/Speicher-Ausfall). Nur fuer Tier=window: Tier=partial
    # nutzt ab jetzt die neue, allgemeinere "hohe Abdeckung*"-Regel unten (sonst zwei ueberlappende
    # Annahmen auf derselben Zeile). Es ist eine Aussenmessung (RW_TAG_WA ist der WA-Aussen-Richtwert,
    # auf Innenraeume nicht anwendbar). Gebrueckt wird NUR der Rest ab dem tatsaechlichen Messende bis
    # 20 Uhr -- nicht pauschal ab 15 Uhr.
    _gemessen_tag=dba[m_tag].dropna()
    letzte_messung=_gemessen_tag.index.max() if len(_gemessen_tag) else None
    letzte_messung_std=(letzte_messung.hour+letzte_messung.minute/60+letzte_messung.second/3600
                         ) if letzte_messung is not None else None
    konservativ_aktiv=(not indoor) and (tier=='window') and (letzte_messung is not None) \
                       and (KONSERVATIV_FENSTER_START<=letzte_messung_std<=KONSERVATIV_FENSTER_ENDE) \
                       and (tag_sec>0) and (not np.isnan(laeq_tag))
    if konservativ_aktiv:
        gap_sec_konserv=int((t20-letzte_messung).total_seconds())
        e_gemessen=10**(laeq_tag/10); e_annahme=10**(KONSERVATIV_ANNAHME_DB/10)
        laeq_tag_konservativ=10*np.log10(
            (tag_sec*e_gemessen+gap_sec_konserv*e_annahme)/(tag_sec+gap_sec_konserv))
        abd_tag_konservativ=(tag_sec+gap_sec_konserv)/TAG_REF_SEC
        konservativ_ende_str=letzte_messung.strftime("%H:%M")
    else:
        laeq_tag_konservativ=float('nan'); abd_tag_konservativ=float('nan'); konservativ_ende_str=""

    # --- NEU: "hohe Abdeckung*" (Tier=partial, >=70% echt) ---
    # Allgemeiner als obige Regel: brueckt ALLE Luecken im 07-20h-Fenster (nicht nur einen
    # bestimmten Nachmittags-Rest) mit einer Annahme von STERN_ANNAHME_DB=50 dB(A). Nur fuer Tage,
    # die bereits Tier=partial sind (>=70% echt gemessen) -- fuer Tier=window bleibt es bei der
    # obigen, engeren Regel, da dort der Grossteil des Tages reine Annahme waere.
    hohe_abdeckung_stern=(not indoor) and (tier=='partial') and (tag_sec>0) and (not np.isnan(laeq_tag))
    if hohe_abdeckung_stern:
        gap_sec_stern=int(TAG_REF_SEC-tag_sec)
        e_gemessen_stern=10**(laeq_tag/10); e_annahme_stern=10**(STERN_ANNAHME_DB/10)
        laeq_tag_stern=10*np.log10(
            (tag_sec*e_gemessen_stern+gap_sec_stern*e_annahme_stern)/(tag_sec+gap_sec_stern))
    else:
        laeq_tag_stern=float('nan')

    laeq_tag_str=coverage_label_short(laeq_tag,abd_tag,sessions,day0)
    laeq_supp=coverage_supplement(abd_tag,sessions,day0,tag_sec)
    wd=WOCHENTAGE[day0.weekday()]
    mess_txt=" / ".join(f"{a.strftime('%H:%M')}–{b.strftime('%H:%M')}" for a,b in sessions)

    return dict(
        day=day,day0=day0,wd=wd,indoor=indoor,position=position,
        df=df,dba=dba,energy=energy,laeq1m=laeq1m,t=t,t7=t7,t20=t20,
        sessions=sessions,tag_sec=tag_sec,vor7_sec=vor7_sec,na20_sec=na20_sec,
        abd_tag=abd_tag,tier=tier,
        laeq_tag=laeq_tag,laeq_vor7=laeq_vor7,laeq_na20=laeq_na20,
        laeq_tag_konservativ=laeq_tag_konservativ,abd_tag_konservativ=abd_tag_konservativ,
        konservativ_aktiv=konservativ_aktiv,konservativ_ende_str=konservativ_ende_str,
        hohe_abdeckung_stern=hohe_abdeckung_stern,laeq_tag_stern=laeq_tag_stern,
        hoechst=hoechst,l1_tag=l1_tag,lmax_b7=lmax_b7,lmax_a20=lmax_a20,
        pct_thr=pct_thr,n_above_sec=n_above_sec,n_above_60_sec=n_above_60_sec,mess_min=mess_min,
        lr_tag=lr_tag,ki_tag=ki_tag,
        lh_val=lh_val,lh_txt=lh_txt,lh_cov=lh_cov,lh_it=it,
        ph60_10=ph60_10,ph60_min=ph60_min,
        phase_src_at=phase_src_at,ev=ev,ev_bau=ev_bau,
        src_of=src_of,ev_dt=ev_dt,
        bin_dom=bin_dom,bin_any=bin_any,src_stats=src_stats,src_pct=src_pct,
        has_verified_src=has_verified_src,
        night_bau_ki=night_bau(),tb_spans=tb_spans,tb_min=tb_min,
        laeq_tag_str=laeq_tag_str,laeq_supp=laeq_supp,mess_txt=mess_txt,BIN=BIN,
    )

# ============================================================
# SELBST-CHECK (Teil 2: Regel 6) — prüft Text-Overlaps + Randüberlauf
# ============================================================
REVIEW_LOG=[]
ABORT_FLAG=[False]

# (Hinweis lib_v3: alte selfcheck()-Variante entfernt -- toter Code; save_page nutzt strict_selfcheck.)

def strict_selfcheck(fig, page_label, abort_on_error=True):
    """Dauerhafter v7-Check: Rand, Text/Text, Text/Grafik und Grafik-Erklaerung."""
    fig.canvas.draw()
    rend=fig.canvas.get_renderer()
    DPI=fig.get_dpi()
    PW=fig.get_figwidth()*DPI; PH=fig.get_figheight()*DPI
    MARGIN=10
    errors=[]

    def area(bb): return max(0,bb.width)*max(0,bb.height)
    def frac_overlap(b1,b2):
        ix=max(0,min(b1.x1,b2.x1)-max(b1.x0,b2.x0))
        iy=max(0,min(b1.y1,b2.y1)-max(b1.y0,b2.y0))
        if ix*iy==0: return 0.0
        sm=min(area(b1),area(b2))
        return (ix*iy/sm) if sm else 0.0
    def full_bleed(bb):
        return bb.width>0.94*PW and bb.height>0.05*PH

    # Dokumentenrand: Text, Achsen/Grafiken und Legenden duerfen nicht aus dem Blatt laufen.
    legends=[ax.get_legend() for ax in fig.axes if ax.get_legend() is not None]
    for art in list(fig.texts)+list(fig.axes)+legends:
        try:
            if hasattr(art,"get_visible") and not art.get_visible(): continue
            if isinstance(art, plt.Axes) and not art.axison: continue
            bb=art.get_window_extent(rend)
            if area(bb)==0 or full_bleed(bb): continue
            if bb.x0<MARGIN: errors.append(f"Rand links unterschritten: {type(art).__name__} x0={bb.x0:.0f}")
            if bb.x1>PW-MARGIN: errors.append(f"Rand rechts unterschritten: {type(art).__name__} x1={bb.x1:.0f} > {PW-MARGIN:.0f}")
            if bb.y0<MARGIN: errors.append(f"Rand unten unterschritten: {type(art).__name__} y0={bb.y0:.0f}")
            if bb.y1>PH-MARGIN: errors.append(f"Rand oben unterschritten: {type(art).__name__} y1={bb.y1:.0f} > {PH-MARGIN:.0f}")
        except: pass

    texts=[]
    for art in fig.texts:
        try:
            if not art.get_visible() or not art.get_text().strip(): continue
            bb=art.get_window_extent(rend)
            if bb.width>4 and bb.height>4:
                texts.append((art.get_text()[:32].replace('\n',' '),bb))
        except: pass

    for i,(n1,b1) in enumerate(texts):
        for n2,b2 in texts[i+1:]:
            frac=frac_overlap(b1,b2)
            if frac>0.20:
                errors.append(f"TEXT/TEXT-OVERLAP {frac*100:.0f}%: '{n1}' <-> '{n2}'")

    plot_axes=[]
    for ax in fig.axes:
        try:
            if not ax.get_visible() or not ax.axison: continue
            pos=ax.get_position()
            if pos.width>0.95 and pos.height>0.90: continue
            plot_axes.append((ax.get_title() or "Grafik",ax.get_window_extent(rend)))
        except: pass
    for tn,tb in texts:
        for an,ab in plot_axes:
            frac=frac_overlap(tb,ab)
            if frac>0.08:
                errors.append(f"TEXT/GRAFIK-OVERLAP {frac*100:.0f}%: '{tn}' <-> '{an}'")

    for ax in fig.axes:
        if getattr(ax,"_requires_graphic_explanation",False):
            has_legend=ax.get_legend() is not None
            has_note=bool(getattr(ax,"_graphic_explanation",False))
            if not (has_legend and has_note):
                errors.append(f"GRAFIK-ERKLAERUNG FEHLT: {getattr(ax,'_graphic_label','Grafik')}")

    if errors:
        print(f"  [CHECK] {page_label}: {len(errors)} Probleme")
        for e in errors: print(f"    - {e}")
        REVIEW_LOG.append({'page':page_label,'errors':errors})
        if abort_on_error: ABORT_FLAG[0]=True
    else:
        print(f"  [CHECK] {page_label}: OK")
    return errors

def save_page(pp, fig, page_label, do_check=True):
    strict_selfcheck(fig, page_label)
    pp.savefig(fig); plt.close(fig)

# ============================================================
# SEITE 1: DECKBLATT — v2 (E: Ästhetik, D: LAeq-Trennung, Nacht-Label)
# ============================================================
def page_cover(pp, all_data):
    n_days=len(all_data)
    dates=[d['day'] for d in all_data]
    d_start=min(dates); d_end=max(dates)
    n_outdoor=sum(1 for d in all_data if not d['indoor'])
    n_indoor =n_days-n_outdoor

    # D: Höchster VALIDER Tages-LAeq (nur Tier ►) vs. Messfenster-Spitze
    outdoor_valid=[d for d in all_data if not d['indoor'] and d['tier']=='valid' and not np.isnan(d['laeq_tag'])]
    outdoor_any  =[d for d in all_data if not d['indoor'] and not np.isnan(d['laeq_tag'])]
    indoor_any   =[d for d in all_data if d['indoor']     and not np.isnan(d['laeq_tag'])]

    # A) Strikte Trennung nach Beweisklasse (Tier) — nicht klassenübergreifend zählen
    TIER_SYM={"valid":"►","partial":"(~)","window":"(M)"}
    outdoor_valid_sorted=sorted(outdoor_valid,key=lambda x:-x['laeq_tag'])
    n_valid=len(outdoor_valid_sorted)
    valid_list=(" / ".join(f"{d['day0'].strftime('%d.%m.')} {d['laeq_tag']:.1f}"
                           for d in outdoor_valid_sorted)) or "—"
    valid_all_over_ei=bool(outdoor_valid_sorted) and all(
        d['laeq_tag']>EINGREIF_TAG for d in outdoor_valid_sorted)
    best_v=outdoor_valid_sorted[0] if outdoor_valid_sorted else None
    best_v_txt=(f"{best_v['laeq_tag']:.1f} dB(A) ({best_v['day0'].strftime('%d.%m.')}) — "
                f"+{best_v['laeq_tag']-RW_TAG_WA:.1f} über RW 55" if best_v else "—")
    outdoor_pw=[d for d in outdoor_any if d['tier']!='valid']
    n_pw=len(outdoor_pw)
    n_pw_over_rw=sum(1 for d in outdoor_pw if d['laeq_tag']>RW_TAG_WA)
    best_pw=max(outdoor_pw,key=lambda x:x['laeq_tag']) if outdoor_pw else None
    best_pw_txt=(f"Messfenster-Spitze: {best_pw['laeq_tag']:.1f} dB(A) "
                 f"({best_pw['day0'].strftime('%d.%m.')}, {TIER_SYM.get(best_pw['tier'],'?')}"
                 f"{best_pw['abd_tag']*100:.0f}%)"
                 if best_pw else "")
    n_konservativ=sum(1 for d in all_data if d.get('konservativ_aktiv'))
    n_hohe_abdeckung_stern=sum(1 for d in all_data if d.get('hohe_abdeckung_stern'))

    # Höchster LAFmax
    lmax_all=max((d['hoechst'] for d in all_data if not np.isnan(d['hoechst'])),default=float('nan'))
    lmax_day=next((d for d in all_data if abs(d['hoechst']-lmax_all)<0.01),None)
    lmax_txt=(f"{lmax_all:.1f} dB(A)  ({lmax_day['day0'].strftime('%d.%m.')})"
              if lmax_day else "—")

    # v3: dokumentierte Bautätigkeit im Nachtzeitraum = nur Ausnahmetag (24.06., Videobeleg)
    n_nacht_bau=sum(1 for d in all_data if d['day']==NACHT_AUSNAHME_TAG and d['na20_sec']>0)
    nacht_bau_txt=("1 Tag (24.06., nach 20:00, Videobeleg)" if n_nacht_bau else "—")

    max_ph=max(len(d['ph60_10']) for d in all_data)
    max_ph_day=next((d for d in all_data if len(d['ph60_10'])==max_ph),None)

    fig=plt.figure(figsize=(14,10.5))
    fig.patch.set_facecolor('white')
    ax=fig.add_axes([0,0,1,1]); ax.axis('off')

    # ---- Banner ----
    ax.add_patch(Rectangle((0,0.88),1,0.12,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.958,"SCHALLMESSUNG — BAULÄRMDOKUMENTATION",
             color='white',fontsize=19,fontweight='bold')
    fig.text(0.06,0.916,ADRESSE,color='#A8CCE8',fontsize=13)
    fig.text(0.06,0.893,
             f"Mieter: {MIETER}  ·  Messzeitraum: {d_start} – {d_end}",
             color='#88AACC',fontsize=10)

    # ---- Rahmen-Tabelle (Sektion 1) ----
    fig.text(0.06,0.862,"RAHMENDATEN",color=C_TITLE,fontsize=10,fontweight='bold')
    ax.add_patch(Rectangle((0.06,0.778),0.88,0.075,color='#F4F7FA',
                            ec='#C8D8E8',linewidth=0.8,transform=ax.transAxes))
    # 2 Spalten je 4 Zeilen
    L1=[(f"Messtage:",f"{n_days}  ({n_outdoor} Außen, {n_indoor} Innenraum)"),
        (f"Messort:",f"Balkon 3. OG, NW/SO (Außen); Innenraum (Mai)"),
        (f"Gerät:",f"PCE-323, Klasse 2 (IEC 61672-1:2013)"),
        (f"Bewertung:",f"AVV Baulärm; LAeq — WA §34 BauGB bestätigt")]
    yy=0.840
    for lbl,val in L1:
        fig.text(0.075,yy,lbl,fontsize=9.5,color='#555555',fontweight='bold')
        fig.text(0.190,yy,val,fontsize=9.5,color='#222222')
        yy-=0.017

    # ---- Kernergebnisse (Sektion 2) ----
    fig.text(0.06,0.760,"KERNERGEBNISSE AUSSENMESSUNGEN",color=C_TITLE,fontsize=10,fontweight='bold')

    def krow(y,lbl,val,note,col_val='#222222',bold_val=False,note_wrap=False,note_right=0.930):
        fig.text(0.075,y,lbl,fontsize=9,color='#555555')
        fig.text(0.370,y,val,fontsize=9,color=col_val,
                 fontweight='bold' if bold_val else 'normal')
        if note and note_wrap:
            # Lange, wachsende Notiz (z.B. valid_list): volle Zeilenbreite, mehrzeilig statt ueber
            # den Rand hinauslaufend (Bugfix: frueher fig.text ohne Umbruch -> Randueberlauf).
            return fig_text_wrap(fig,0.075,y-0.0135,note,note_right,0.0135,
                                  fontsize=7.6,color='#666666',fontstyle='italic',min_y=0.30)
        elif note:
            fig.text(0.660,y,note,fontsize=8.5,color='#666666',fontstyle='italic')
        return None

    # Abdeckungsstufe 1: hohe Tagesabdeckung (vollständige Tagesmessung)
    # v3: valid_list waechst mit jedem weiteren validen Tag -> Notiz wird umgebrochen statt
    # ueber den Rand zu laufen; nachfolgende Zeilen kaskadieren relativ dazu (dynamische Hoehe).
    _y=krow(0.737,"Tagesmessungen mit hoher Abdeckung (►, ≥90%):  ",
         f"{n_valid} Tage" + ("  — alle > Eingreif 60 dB(A)" if valid_all_over_ei else ""),
         valid_list,
         col_val=C_RICHT if valid_all_over_ei else '#222222', bold_val=True,
         note_wrap=True, note_right=0.930) - 0.006
    krow(_y,"   höchster Tages-LAeq mit hoher Abdeckung:  ",
         best_v_txt,"vollständig erfasster Tag",
         col_val=C_RICHT if best_v else '#666666', bold_val=bool(best_v))
    _y-=0.019
    # Abdeckungsstufe 2: Teilerfassung / Messfenster (nachrangig)
    krow(_y,"Teilerfassungen (~) / Messfenster (M):  ",
         f"{n_pw} Tage — LAeq nur fürs Fenster gültig",
         best_pw_txt,
         col_val='#555555')
    _y-=0.025
    krow(_y,"   davon > RW 55 dB(A) im Fenster:  ",
         f"{n_pw_over_rw} von {n_pw}","nachrangige Abdeckungsstufe — kein Tagesbild",
         col_val='#8B4500')
    _y-=0.019
    # Weitere Kennzahlen (alle Tage)
    krow(_y,"Höchster LAFmax (1s Fast, alle Tage):  ",
         lmax_txt,"Momentanspitze, nicht Mittelungspegel",col_val='#444444')
    _y-=0.025
    krow(_y,"Max. Dauerlärm ≥60dB/≥10min (ein Tag):  ",
         f"{max_ph} Phasen  ({max_ph_day['day0'].strftime('%d.%m.') if max_ph_day else '—'})",
         "eigene Kennzahl — keine AVV-Norm",
         col_val=C_RICHT if max_ph>0 else '#222222',bold_val=max_ph>0)
    _y-=0.019
    krow(_y,"Bautätigkeit im Nachtzeitraum (20–07 h):  ",
         nacht_bau_txt,"übrige Tage: Auswertung 07–20 h",
         col_val=C_RICHT if n_nacht_bau else '#444444', bold_val=bool(n_nacht_bau))
    _y-=0.022
    if n_konservativ>0:
        krow(_y,"Zusätzlich konservativ hochgerechnet (§287 ZPO):  ",
             f"{n_konservativ} Tage",
             f"Annahme RW {KONSERVATIV_ANNAHME_DB:.0f} dB(A) ab tats. Messende ({KONSERVATIV_FENSTER_START}–{KONSERVATIV_FENSTER_ENDE} Uhr) bis 20 Uhr",
             col_val='#555555')
        _y-=0.019
    if n_hohe_abdeckung_stern>0:
        krow(_y,"Zusätzlich als 'hohe Abdeckung*' eingestuft (§287 ZPO):  ",
             f"{n_hohe_abdeckung_stern} Tage",
             f"Annahme {STERN_ANNAHME_DB:.0f} dB(A) für Restzeit (Tier Teilerfassung ≥70%)",
             col_val='#555555')
        _y-=0.019

    # Box-Hoehe dynamisch: passt sich an, falls die Notiz oben mehrzeilig wurde (oder kuenftig
    # weitere Zeilen dazukommen) -- statt fix 0.157, damit nie in den Inhalt darunter reinlaeuft.
    _box_bottom=min(0.595,_y-0.006)
    ax.add_patch(Rectangle((0.06,_box_bottom),0.88,0.752-_box_bottom,color='white',
                            ec='#C8D8E8',linewidth=0.8,transform=ax.transAxes))
    _shift=max(0.0,0.595-_box_bottom)   # wie weit alles darunter mitverschoben werden muss

    # Trennlinie
    fig.add_artist(mlines.Line2D([0.06,0.94],[0.597-_shift,0.597-_shift],
                                  color='#DDDDDD',lw=0.6,transform=fig.transFigure))

    # ---- Inhalt (Sektion 3) ----
    fig.text(0.06,0.572-_shift,"INHALT DIESES BERICHTS",color=C_TITLE,fontsize=10,fontweight='bold')
    struct=[
        ("Seite 2   ","Juristische Kurzfassung: stärkste Argumente und Beweisanker"),
        ("Seiten 3–4","Rechtliche Einordnung: AVV Baulärm, §34 BauGB, §4 BauNVO, Methodik"),
        ("Seite 5   ","Berechnung der Kennwerte: Formeln und Definitionen aller Kennzahlen"),
        ("Seite 6   ","Dokumentation Messort / Messaufbau (Fotos, Aufstellung, Mikrofonhöhe)"),
        ("Seite 7   ","Lageplan / Geometrie zur Quelle (Abrisskante, Tiefbohrer-Positionen)"),
        ("Seite 8   ","Kernbefunde — strategische Zusammenfassung"),
        ("Seiten 9–10","Belastungsdauer je Messtag · Lauteste Stunde je Messtag"),
        ("Seiten 11–12","Innenraumbetroffenheit · Referenzpegel (Wohnung ohne Bautätigkeit)"),
        ("Seite 13  ",f"Tagesübersicht — alle {n_days} Messtage nach Abdeckungsstufe"),
        ("Seiten 14+","Einzeldiagramme je Messtag: Pegel, AVV-Grenzwerte, Videobelege"),
        ("Anhang    ","Videoliste inkl. Video-only-Belege + Rohdaten-Manifest (SHA-256)"),
    ]
    # v3-Fix: Zeilenabstand von 0.0158 auf 0.0143 verkleinert -- bei 11 Zeilen sass der letzte
    # Eintrag ("Anhang") sonst nur 0.007 ueber dem Hinweis-Box-Header (vorbestehender, bisher
    # durch die inerte ABORT_FLAG-Pruefung nie sichtbarer Ueberlapp).
    yy=0.552-_shift
    for pg,txt in struct:
        fig.text(0.075,yy,pg,fontsize=9,color=C_ACCENT,fontweight='bold')
        fig.text(0.175,yy,txt,fontsize=9,color='#333333')
        yy-=0.0143

    # Hinweis-Box (mit klarem Abstand zum Inhalt-Block oben)
    ax.add_patch(Rectangle((0.06,0.345-_shift),0.88,0.058,color='#FFF8F0',
                            ec='#E0C090',linewidth=0.8,transform=ax.transAxes))
    fig.text(0.075,0.387-_shift,"Hinweis Abdeckungsstufen:",fontsize=8.5,fontweight='bold',color='#7A4000')
    fig.text(0.075,0.369-_shift,
             "► ≥90% = Tages-LAeq mit hoher Abdeckung    (~) 70–90% = Teilerfassung    (M) <70% = Messfenster",
             fontsize=8.5,color='#555555')
    fig.text(0.075,0.351-_shift,
             "Tages-LAeq und Messfenster-LAeq sind NICHT gleichrangig — Abdeckungsstufe immer prüfen.",
             fontsize=8.5,color='#8B4000',fontstyle='italic')

    # ---- Fußzeile (mit Abstand) ----
    fig.add_artist(mlines.Line2D([0.06,0.94],[0.332-_shift,0.332-_shift],
                                  color='#DDDDDD',lw=0.5,transform=fig.transFigure))
    fig.text(0.06,0.310-_shift,
             "Messwerte: gemessene Pegel PCE-323, IEC 61672-1:2013 Klasse 2 "
             f"(Messunsicherheit ±{GERAET_TOL_DB:.1f} dB(A)). Auswertung Tagzeit 07–20 h.",
             fontsize=8,color='#777777')
    fig.text(0.06,0.293-_shift,
             "Erstellt mit 07_gesamtbericht_v7.py  |  " + VERSION_STR,
             fontsize=7.5,color='#AAAAAA')

    save_page(pp,fig,"Deckblatt", do_check=True)

# ============================================================
# SEITE 2: JURISTISCHE KURZFASSUNG
# ============================================================
def page_juristische_kurzfassung(pp, all_data):
    fig=plt.figure(figsize=(14,10.5))
    ax=fig.add_axes([0,0,1,1]); ax.axis('off')
    ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.951,"JURISTISCHE KURZFASSUNG — tragende Argumente",
             color='white',fontsize=14,fontweight='bold')

    outdoor=[d for d in all_data if not d['indoor'] and not np.isnan(d['laeq_tag'])]
    high=[d for d in outdoor if d['tier']=='valid']
    over_ei=[d for d in high if d['laeq_tag']>EINGREIF_TAG]
    over_rw=[d for d in outdoor if d['laeq_tag']>RW_TAG_WA]
    best=max(high,key=lambda d:d['laeq_tag']) if high else None
    best_txt=(f"{best['laeq_tag']:.1f} dB(A) am {best['day0'].strftime('%d.%m.%Y')}"
              if best else "—")
    high_vals=[d['laeq_tag'] for d in high]
    high_span=(f"{min(high_vals):.1f}–{max(high_vals):.1f} dB(A)" if high_vals else "—")
    lmax_day=max(outdoor,key=lambda d:d['hoechst']) if outdoor else None
    lmax_txt=(f"{lmax_day['hoechst']:.1f} dB(A) am {lmax_day['day0'].strftime('%d.%m.%Y')}"
              if lmax_day else "—")
    ph_days=[d for d in outdoor if len(d['ph60_10'])>0]
    ph_total=sum(len(d['ph60_10']) for d in outdoor)
    ph_min_total=sum(d['ph60_min'] for d in outdoor)
    ph_best=max(outdoor,key=lambda d:len(d['ph60_10'])) if outdoor else None
    ph_best_txt=(f"{len(ph_best['ph60_10'])} Phasen / {ph_best['ph60_min']:.0f} min am {ph_best['day0'].strftime('%d.%m.%Y')}"
                 if ph_best else "—")
    min55_total=sum(d['n_above_sec'] for d in outdoor)/60.0
    min60_total=sum(d['n_above_60_sec'] for d in outdoor)/60.0
    report_days=set(DAYS_ALL)
    video_days=sorted(set(v['day'] for v in VIDEOS if v['day'] in report_days))
    video_on_report=sum(1 for v in VIDEOS if v['day'] in report_days)
    video_only=sum(1 for v in VIDEOS if v['day'] not in report_days)

    def h(y,t):
        fig.text(0.06,y,t,fontsize=11.5,fontweight='bold',color=C_TITLE)
        return y-0.030
    def p(y,t,fs=9.2,col='#222222',bold=False):
        return fig_text_wrap(fig,0.075,y,t,0.945,0.023,fontsize=fs,color=col,
                             fontweight='bold' if bold else 'normal')
    def bullet(y,t,col='#222222',bold=False):
        return fig_text_wrap(fig,0.085,y,"• "+t,0.945,0.021,fontsize=9.0,color=col,
                             fontweight='bold' if bold else 'normal')

    y=0.880
    y=h(y,"1 — Anwendbarer Maßstab")
    y=bullet(y,"Für Baustellenlärm ist die AVV Baulärm der sachnähere Bewertungsmaßstab; die TA Lärm wird nicht als Anlagenlärm-Maßstab gleichgesetzt.")
    y=bullet(y,"Für das allgemeine Wohngebiet werden tags 55 dB(A) und nachts 40 dB(A) als Richtwerte angesetzt; die Eingreifschwelle liegt jeweils 5 dB höher.")
    y-=0.010

    y=h(y,"2 — Überschreitung und behördlicher Handlungsbedarf")
    y=bullet(y,
        f"{len(over_ei)} vollständig erfasste Außentage überschreiten die Tag-Eingreifschwelle von 60 dB(A); "
        f"höchster Tages-LAeq mit hoher Abdeckung: {best_txt}.",
        col=C_RICHT,bold=True)
    y=bullet(y,
        f"Spanne der vollständig erfassten Tages-LAeq: {high_span}; insgesamt liegen {len(over_rw)} Außentage "
        "oberhalb des Wohngebiets-Richtwerts von 55 dB(A).")
    y=bullet(y,
        f"Über alle Außentage: rund {min55_total:.0f} Minuten über 55 dB(A) und {min60_total:.0f} Minuten "
        "über 60 dB(A) in den gemessenen Tagzeit-Fenstern; Teilerfassungen bleiben getrennt.")
    y-=0.010

    y=h(y,"3 — Kausalität: Baustelle statt allgemeiner Stadtlärm")
    y=bullet(y,
        f"Höchster Momentanpegel LAFmax: {lmax_txt}; {ph_total} Dauerlärm-Phasen ≥60 dB/≥10 min "
        f"auf {len(ph_days)} Außentagen, zusammen rund {ph_min_total:.0f} min.")
    y=bullet(y,
        f"Stärkster Dauerlärm-Tag: {ph_best_txt}; Pegelspitzen, Dauerlärmphasen und laute Quellenereignisse "
        "fallen zeitlich mit dokumentierten Bauphasen zusammen.")
    y=bullet(y,"Der Ausnahmefall 24.06. dokumentiert Bautätigkeit auch nach 20:00 Uhr; dieser Nachtbezug wird separat ausgewiesen.")
    y-=0.010

    y=h(y,"4 — Wohnnutzungsbezug und Betroffenheit")
    y=bullet(y,"Betroffen sind Nordwest- und Südostseite sowie Innenräume; damit wird nicht nur ein einzelner Balkon, sondern die Wohnnutzung als Ganzes berührt.")
    y=bullet(y,"Innenraumwerte werden vorsichtig als Orientierungswerte nach §287 ZPO behandelt und nicht mit AVV-Außenrichtwerten vermischt.")
    y-=0.010

    y=h(y,"5 — Beweisqualität und konservative Darstellung")
    y=bullet(y,
        f"{len(VIDEOS)} Videobelege im Beweisordner; {video_on_report} auf Berichtstagen und "
        f"{video_only} ohne parallele CSV-Schallmessung. Video-only-Belege werden eigenständig geführt.")
    y=bullet(y,
        "Video-only ersetzt keine kalibrierte PCE-Messung, stützt aber Zeitpunkt, Ort, Wohnbetroffenheit "
        "und Plausibilität; Handy-dB aus Dateinamen nur als Orientierung.",
        col='#555555')
    y=bullet(y,"Rohdaten-Manifest mit SHA-256-Hashes, getrennte Abdeckungsstufen, Messunsicherheit und Messfensterhinweise verhindern eine Überdehnung der Zahlen.")
    y=bullet(y,"Die Kernaussage stützt sich auf die Gesamtschau: Messpegel, Dauer, Zeitpunkt, Quellenbezug, Bautagebuch, Videos und Lagegeometrie.")

    fig.text(0.06,0.025,f"{VERSION_STR}  |  Juristische Kurzfassung",fontsize=7.5,color='#999999')
    save_page(pp,fig,"JuristischeKurzfassung", do_check=True)

# ============================================================
# SEITE 3–4: RECHTLICHE EINORDNUNG
# ============================================================
def page_legal(pp):
    for seite, titel, content_fn in [
        (3,"RECHTLICHE EINORDNUNG — Teil 1: Normen und Richtwerte",_legal_p2),
        (4,"RECHTLICHE EINORDNUNG — Teil 2: Messmethodik und Bewertungsgrundlagen",_legal_p3),
    ]:
        fig=plt.figure(figsize=(14,10.5))
        ax=fig.add_axes([0,0,1,1]); ax.axis('off')
        ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
        fig.text(0.06,0.951,titel,color='white',fontsize=14,fontweight='bold')
        content_fn(fig, ax)
        fig.text(0.06,0.025,f"{VERSION_STR}  |  Seite {seite}/4 Rechtliche Einordnung",
                 fontsize=7.5,color='#999999')
        save_page(pp,fig,f"Rechtl_Teil{seite}", do_check=False)

def _legal_p2(fig,ax):
    y=0.885
    def h1(t,yp): fig.text(0.06,yp,t,fontsize=11.5,fontweight='bold',color=C_TITLE); return yp-0.027
    def h2(t,yp): fig.text(0.06,yp,t,fontsize=10,fontweight='bold',color='#333333'); return yp-0.023
    def norm(t,yp):
        fig.text(0.075,yp,t,fontsize=9,color='#111111',
                 bbox=dict(boxstyle='round,pad=0.25',fc='#F0F4F8',ec='#C0CCDD')); return yp-0.029
    def body(t,yp): fig.text(0.075,yp,t,fontsize=9,color='#333333'); return yp-0.022
    def bul(t,yp): fig.text(0.085,yp,f"• {t}",fontsize=9,color='#222222'); return yp-0.020

    y=h1("1. AVV Baulärm — Lex specialis für Baustellen",y)
    y=body("Allg. Verwaltungsvorschrift zum Schutz gegen Baulärm, 19.08.1970 (BAnz. Nr. 160 v. 01.09.1970).",y)
    y=body("Als lex specialis geht sie der TA Lärm (Anlagenlärm) vor, wenn eine Baustelle den Lärm verursacht.",y-0.003)
    y-=0.006
    y=h2("Nr. 3.1.1 — Immissionsrichtwerte",y)
    y=norm("Allgemeines Wohngebiet (§ 4 BauNVO):   Tagzeit 07–20 Uhr: 55 dB(A)   |   Nachtzeit 20–07 Uhr: 40 dB(A)",y)
    y=body("Beurteilungsgröße: A-bewerteter äquivalenter Dauerschallpegel LAeq (energetischer Mittelungspegel).",y)
    y-=0.005
    y=h2("Nr. 3.2 — Definition Tagzeit / Nachtzeit",y)
    y=norm('»Als Tagzeit gilt die Zeit von 7 bis 20 Uhr, als Nachtzeit 20 bis 7 Uhr.«  (AVV Baulärm Nr. 3.2)',y)
    y-=0.005
    y=h2("Nr. 4 — Eingreifschwelle (+5 dB)",y)
    y=norm("Eingreifschwelle = Richtwert + 5 dB(A):   Tag 60 dB(A)   |   Nacht 45 dB(A)",y)
    y=body("Überschreitung der Eingreifschwelle begründet regelmäßig behördlichen Prüf- und Handlungsbedarf "
           "bzw. Anlass für Maßnahmen nach AVV Baulärm. Nacht-Spitze >60 dB(A): gesondert relevant.",y)
    y-=0.012
    y=h1("2. §34 BauGB — Gebietscharakter",y)
    y=norm('§34 Abs. 1 BauGB: „Innerhalb der im Zusammenhang bebauten Ortsteile ist ein Vorhaben zulässig,',y)
    y=norm('wenn es sich nach Art und Maß der baulichen Nutzung […] in die Eigenart der näheren Umgebung einfügt."',y)
    y=body(f"WA-Charakter (§4 BauNVO) für {STANDORT_KURZ} behördlich bestätigt",y)
    y=body(STANDORT_RECHTSHINWEIS,y-0.003)
    y-=0.010
    y=h1("3. §4 BauNVO — Allgemeines Wohngebiet",y)
    y=norm('§4 Abs. 1 BauNVO: „Allgemeine Wohngebiete dienen vorwiegend dem Wohnen."',y)
    y=body("Strengste Schutzklasse neben reinen Wohngebieten. Begründet die 55/40 dB(A)-Richtwerte der AVV.",y)
    y-=0.006
    fig.text(0.06,y,"WA-Qualifikation ist Voraussetzung für diese Bewertung — kein anderer Gebietstyp (GI, GE, MI) trifft zu.",
             fontsize=8.5,color=C_RICHT,fontstyle='italic')
    y-=0.020
    y=h1("4. Rechtliche Tragweite (Einordnung)",y)
    y=body("Die AVV-Richtwerte sind ein objektiver Anhaltspunkt für die Beurteilung der Lärmbelastung.",y)
    y=body("Die zivilrechtliche Minderungsquote folgt aus der konkreten Beeinträchtigung der Wohnnutzung,",y-0.003)
    y=body("nicht automatisch aus der Überschreitung. Die behördliche Eingreifschwelle (Tag 60 / Nacht 45)",y-0.003)
    y=body("begründet eine behördliche Handlungspflicht, ist aber keine zivilrechtliche Minderungs-Automatik.",y-0.003)

def _legal_p3(fig,ax):
    y=0.885
    def h1(t,yp): fig.text(0.06,yp,t,fontsize=11.5,fontweight='bold',color=C_TITLE); return yp-0.027
    def h2(t,yp): fig.text(0.06,yp,t,fontsize=10,fontweight='bold',color='#333333'); return yp-0.022
    def norm(t,yp):
        fig.text(0.075,yp,t,fontsize=9,color='#111111',
                 bbox=dict(boxstyle='round,pad=0.25',fc='#F0F4F8',ec='#C0CCDD')); return yp-0.029
    def body(t,yp): fig.text(0.075,yp,t,fontsize=9,color='#333333'); return yp-0.022
    def bul(t,yp): fig.text(0.085,yp,f"• {t}",fontsize=9,color='#222222'); return yp-0.019

    y=h1("5. Messmethodik",y)
    y=h2("Gerät und Kalibrierung",y)
    y=bul("PCE-323, Schallpegelmessgerät Klasse 2 nach IEC 61672-1:2013",y)
    y=bul("1-Sekunden-LAF-Messwerte (A-Bewertung, Fast-Zeitkonstante 125 ms), kontinuierlich",y)
    y=bul("Kalibrierung auf 94 dB(A) mit Kalibrator PCE-SC 43; ab 22.06.2026 Feldkalibrierung vor Messung protokolliert; Mai/früh-Juni: Werkskalibrierung",y)
    y=bul(f"Messunsicherheit Klasse 2: ±{GERAET_TOL_DB:.1f} dB(A) — offen ausgewiesen (nicht herausgerechnet)",y)
    y=bul("Messort: Außen-/Innenmessung; Details und Positionen siehe Messaufbau-Seite",y)
    y-=0.008
    y=h2("Beurteilungsgröße nach AVV Baulärm",y)
    y=norm("LAeq (energieäquivalent, A-bewertet) = primäre Bewertungsgröße. Kein Taktmaximalverfahren.",y)
    y=bul("DIN 45645-1 / TA-Lärm-Taktmaximalverfahren gilt NICHT für AVV Baulärm (lex specialis).",y)
    y=bul("Innenraum-Messungen: kein AVV-Grenzwert → Orientierungswert §287 ZPO, keine AVV-Grenzlinien.",y)
    y-=0.008
    y=h2("Auswertungszeitraum",y)
    y=bul("Auswertung auf die AVV-Tagzeit 07–20 Uhr begrenzt; Diagramme zeigen 07–20 Uhr.",y)
    y=bul("Ausnahme 24.06.: dokumentierte Bautätigkeit nach 20:00 (Video/Bautagebuch) gesondert ausgewiesen.",y)
    y-=0.008
    y=h1("6. Abdeckungsstufen (3-stufig)",y)
    for sym,titel,beschr,beisp,col in [
        ("► ≥90%","Tages-LAeq mit hoher Abdeckung",
         "≥11h42min erfasst — repräsentativ. Energetisches Mittel über Bezugszeitraum.",
         "23.06. (96%), 24.06. (100%)",C_TITLE),
        ("(~) partial 70–90%","Teilerfassung",
         "LAeq für das gemessene Zeitfenster; weitgehend repräsentativ. Lücken benannt.",
         "15.06. (82%), 17.06. (85%), 18.06. (75%)",C_EING),
        ("(M) window <70%","Messfenster",
         "LAeq gilt für Messfenster, nicht Tagesdurchschnitt. NICHT mit Tages-LAeq gleichsetzen.",
         "16.06. (31%), 22.06. (40%), 10.06. (12%)",C_RICHT),
    ]:
        ax.add_patch(Rectangle((0.06,y-0.058),0.88,0.068,color=col+'18',
                                ec=col+'60',linewidth=0.8,transform=ax.transAxes))
        fig.text(0.075,y-0.008,sym,fontsize=9.5,fontweight='bold',color=col)
        fig.text(0.255,y-0.008,titel,fontsize=9.5,fontweight='bold',color='#222222')
        fig.text(0.075,y-0.028,beschr,fontsize=8.5,color='#333333')
        fig.text(0.075,y-0.046,f"Beispiele: {beisp}",fontsize=8,color='#666666',fontstyle='italic')
        y-=0.076
    fig.text(0.075,y+0.004,
             "Hinweis: Tier-Schwellen (70/90%) und „Dauerlärm ≥60dB/≥10min“ sind eigene, anschauliche "
             "Kennzahlen dieser Auswertung — keine AVV-Norm.",
             fontsize=8,color='#8B4000',fontstyle='italic')
    y-=0.016
    if NO_AUDIO_MODE:
        y=h1("7. Quellenzuordnung",y)
        y=bul("Nicht Bestandteil dieser Berichtsversion (siehe Vollversion mit WAV-/KI-Auswertung).",y)
    else:
        y=h1("7. Quellenzuordnung der lauten Ereignisse",y)
        y=bul("Grundlage: laute Ereignisse (WAV-Clips > 60 dB) der Dauermessung; Hörprüfung der Clips.",y)
        y=bul("Manuell geprüfte Zuordnungen (Laermquelle_geprueft) sind maßgeblich und haben Vorrang.",y)
        y=bul("Verifikationsstand der Quellenzuordnung ist je Tag unterschiedlich — offen (To-Do Mandant).",y)
        y=bul("Leq je Quelle = energetischer Mittelwert. Arithmetisches dB-Mittel ist akustisch unzulässig.",y)

# ============================================================
# SEITE 5 (NEU): BERECHNUNG DER KENNWERTE — technische Herleitung jeder Kennzahl
# (ergänzt die rechtliche Einordnung oben um die formelmäßige Berechnung; keine
#  Dopplung der dortigen AVV-Richtwerte-Darstellung.)
# ============================================================
def page_berechnung_kennwerte(pp):
    fig=plt.figure(figsize=(14,10.5)); ax=fig.add_axes([0,0,1,1]); ax.axis('off')
    ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.951,"BERECHNUNG DER KENNWERTE",color='white',fontsize=14,fontweight='bold')

    y=0.885
    def h1(t,yp): fig.text(0.06,yp,t,fontsize=10.5,fontweight='bold',color=C_TITLE); return yp-0.023
    def norm(t,yp):
        fig.text(0.075,yp,t,fontsize=8.3,color='#111111',
                 bbox=dict(boxstyle='round,pad=0.22',fc='#F0F4F8',ec='#C0CCDD')); return yp-0.023
    def body(t,yp): fig.text(0.075,yp,t,fontsize=8.3,color='#333333'); return yp-0.015

    y=h1("LAeq — äquivalenter Dauerschallpegel",y)
    y=norm("LAeq = 10·log₁₀( Mittelwert(10^(L/10)) )  über den Bezugszeitraum (07–20 h bzw. Nachtsegment)",y)
    y=body("Energetischer Mittelwert der Sekundenwerte — kein arithmetisches Mittel (akustisch unzulässig).",y)
    y-=0.006

    y=h1("LAFmax — Momentanspitze",y)
    y=body("Höchster gemessener 1-Sekunden-Fast-Pegel (125 ms Zeitkonstante) im Betrachtungszeitraum.",y)
    y-=0.006

    y=h1("L₁ — 99. Perzentil",y)
    y=body("Pegel, der in 1% der Messzeit überschritten wird — Spitzenkennwert, robust gegen Einzelausreißer.",y)
    y-=0.006

    y=h1("Abdeckungsstufen (► / (~) / (M))",y)
    y=body(f"Anteil tatsächlich gemessener Sekunden am 13h-Bezugszeitraum (07–20 Uhr, {int(TAG_REF_SEC)} s):",y)
    y=norm("► ≥90% hohe Abdeckung   |   (~) 70–90% Teilerfassung   |   (M) <70% Messfenster",y)
    y=body("Nur ► erlaubt einen Tages-LAeq mit voller Aussagekraft; (~)/(M) gelten nur fürs Messfenster.",y)
    y-=0.006

    y=h1("Konservative Volltag-Hochrechnung bei Messfenster + Messende 15–19 Uhr",y)
    y=body("Nur für Tier (M) Messfenster (<70% echt): Wenn die Messung tatsächlich irgendwann zwischen",y)
    y=body("15:00 und 19:00 Uhr endet (Gerät stoppt nachmittags/früh abends, z.B. Akku/Speicher), wird",y-0.003)
    y=body("zusätzlich ein hilfsweiser Ganztages-LAeq ausgewiesen: für den Rest ab dem tatsächlichen",y-0.003)
    y=norm("LAeq_konservativ = 10·log₁₀( (T_gemessen·10^(LAeq/10) + T_Lücke·10^(55/10)) / (T_gemessen+T_Lücke) )",y-0.003)
    y=body("Messende bis 20:00 Uhr wird Einhaltung des Richtwerts 55 dB(A) unterstellt (§287 ZPO, denkbar",y)
    y=body("ungünstigste Annahme zulasten des Mieters). Ersetzt NIE den echten LAeq/die Abdeckungsstufe —",y-0.003)
    y=body("rein additiv, stets als Schätzung gekennzeichnet. Marker (+) in der Tagesübersicht.",y-0.003)
    y-=0.006

    y=h1("NEU: 'hohe Abdeckung*' bei Teilerfassung (≥70% echt)",y)
    y=body("Nur für Tier (~) Teilerfassung (≥70% echt gemessen): für die GESAMTE nicht erfasste Restzeit",y)
    y=body("im 07–20h-Fenster (unabhängig von deren Lage im Tagesverlauf) wird ein Pegel von 50 dB(A)",y-0.003)
    y=norm("LAeq* = 10·log₁₀( (T_gemessen·10^(LAeq/10) + T_Lücke·10^(50/10)) / (T_gemessen+T_Lücke) )",y-0.003)
    y=body("unterstellt — konservativ, da unterhalb des Richtwerts 55 dB(A) (§287 ZPO). Die Aussagekraft",y)
    y=body("wird für diese Tage als 'hohe Abdeckung*' ausgewiesen (Marker * in der Tagesübersicht); die",y-0.003)
    y=body("reale Abdeckungsstufe (~) und der reale LAeq bleiben auf der jeweiligen Tagesseite einsehbar.",y-0.003)
    y-=0.006

    y=h1("Lr — Beurteilungspegel (TA-Lärm-Methodik, informativ)",y)
    y=body("5-Sekunden-Taktmaximalpegel, energetisch gemittelt über den Tag; Differenz zu LAeq = Zuschlag.",y)
    y=body("Nur informativ — für AVV Baulärm ist LAeq maßgeblich, nicht das Taktmaximalverfahren.",y)
    y-=0.006

    y=h1("Dauerlärm-Phasen (eigene Kennzahl, keine AVV-Norm)",y)
    y=body("Zusammenhängende Phasen mit Pegel ≥60/≥65/≥70 dB(A) über ≥10/≥5/≥3 Min. (Kriterien "
           "60/5, 60/10, 65/5, 70/3); kurze Unterschreitungen ≤20 s werden überbrückt.",y)
    y-=0.006

    y=h1("Dauerbetrieb-Regel (Tiefbohrer-Erkennung, separat von den Phasen oben)",y)
    y=body("Gleitendes 30-s-Mittel >70 dB(A) bei gleitender Standardabweichung <4 dB über ≥60 s "
           "(kurze Unterschreitungen ≤15 s überbrückt) = Dauerbetrieb schweres Gerät.",y)
    y=body("Diese automatische Pegel-/Dauerregel gilt nur vom 08.06. bis einschliesslich 19.07.2026; "
           "ab 20.07.2026 wird daraus keine automatische Tiefbohrer-Zuordnung mehr abgeleitet.",y)
    y-=0.006

    y=h1("Nachtsegmente (vor 07:00 / ab 20:00 Uhr)",y)
    y=body("Gleiche LAeq-Formel wie Tag, für die jeweiligen Nachtzeiträume. Bauzuordnung im Nachtsegment "
           "nur am dokumentierten Ausnahmetag (24.06.), sonst keine Bauzuordnung.",y)
    y-=0.006

    if NO_AUDIO_MODE:
        y=h1("Quellenzuordnung",y)
        y=body("Nicht Bestandteil dieser Berichtsversion (siehe Vollversion mit WAV-/KI-Auswertung).",y)
    else:
        y=h1("Beweisstufen der Quellenzuordnung",y)
        y=body("Dreistufig, absteigende Rangfolge: manuell geprüft (Gold-Standard) > clusterbasiert bestätigt "
               "(dokumentierte Stichproben) > KI-vorläufig (automatische AudioSet-Zuordnung).",y)
        y=body("Die OpenAI-Audio-Clusterstufe ist ebenfalls automatisch und keine manuelle Einzelpruefung; "
               "bei Mischgeraeuschen oder geringer Sicherheit bleibt die Zuordnung offen.",y)
    y-=0.006

    y=h1("Messunsicherheit",y)
    y=body(f"±{GERAET_TOL_DB:.1f} dB(A) (Geräteklasse 2, IEC 61672-1:2013) — offen ausgewiesen, nicht herausgerechnet.",y)

    fig.text(0.06,0.025,f"{VERSION_STR}  |  Seite 5  |  Berechnung der Kennwerte",
             fontsize=7.5,color='#999999')
    save_page(pp,fig,"BerechnungKennwerte", do_check=True)

# ============================================================
# SEITE 4: TAGESÜBERSICHT-TABELLE — v2 (Tabellenbreite angepasst)
# ============================================================
def page_summary(pp, all_data):
    fig=plt.figure(figsize=(14,10.5))
    # Tabellen-Axes mit explizitem Rand, damit letzte Spalte nicht abgeschnitten wird
    ax=fig.add_axes([0.035,0.07,0.930,0.82]); ax.axis('off')
    fig.suptitle("Tagesübersicht — alle Messtage (neuester zuerst)",
                 x=0.055,y=0.955,ha='left',fontsize=14,fontweight='bold',color=C_TITLE)
    _dom_clause="" if NO_AUDIO_MODE else "  ·  Dominante Quelle = vorläufig, soweit nicht manuell verifiziert"
    fig_text_wrap(fig,0.055,0.917,
             "Abdeckungsstufe: ► hohe Abdeckung ≥90% | (~) Teilerfassung 70–90% | (M) Messfenster <70%  "
             f"·  LAeq = gemessener Pegel (Tag 07–20 h){_dom_clause}  "
             "·  Marker (+) = Tier (M) konservativ auf 07–20h hochgerechnet (Messende 15–19 Uhr: Annahme RW "
             "55 dB(A) für den Rest, §287 ZPO)  ·  Marker (*)/Aussagekraft 'hohe Abdeckung*' = Tier (~) mit "
             "Annahme 50 dB(A) für die Restzeit ohne Messwerte (§287 ZPO)",
             0.965,0.013,fontsize=8.5,color='#555555')

    TIER_SYM={"valid":"►","partial":"(~)","window":"(M)"}
    TIER_LABEL={"valid":"hohe Abdeckung","partial":"Teilerfassung","window":"Messfenster"}
    col_labels=["Datum","Wt","Tier","Aussagekraft","LAeq\ndB(A)","LAFmax\ndB(A)","L₁\ndB(A)",
                "Ph≥60/10","Abdeckg.","Nacht\n20–07","Dominante Quelle"]
    rows=[]; src_info=[]; row_tier=[]

    for d in all_data:
        tier=d['tier']; tier_sym=TIER_SYM.get(tier,"?")
        if d.get('hohe_abdeckung_stern'):
            aussagekraft_s="hohe Abdeckung*"
            laeq_s=fmt(d['laeq_tag_stern']) + "*"
        else:
            aussagekraft_s=TIER_LABEL.get(tier,"?")
            laeq_s=fmt(d['laeq_tag']) + (" +" if d.get('konservativ_aktiv') else "")
        ph_s=str(len(d['ph60_10']))
        abd_s=f"{d['abd_tag']*100:.0f}%"
        # v3: Nacht-Spalte nur dokumentierte Bautätigkeit (Ausnahmetag 24.06., Post-20:00)
        if d['day']==NACHT_AUSNAHME_TAG and d['na20_sec']>0 and not np.isnan(d['laeq_na20']):
            nacht_s=f"{fmt(d['laeq_na20'])} (ab20h)"
        else:
            nacht_s="—"
        dom=max(d['src_pct'],key=d['src_pct'].get) if d['src_pct'] else "—"
        dom_pct=d['src_pct'].get(dom,0)
        dom_s=f"{disp(dom)[:15]} {dom_pct:.0f}%" if dom!="—" else "—"

        rows.append([d['day0'].strftime("%d.%m.%Y"),d['wd'],tier_sym,aussagekraft_s,
                     laeq_s,fmt(d['hoechst']),fmt(d['l1_tag']),ph_s,abd_s,nacht_s,dom_s])
        src_info.append(dom); row_tier.append(tier)

    tab=ax.table(cellText=rows,colLabels=col_labels,loc='upper center',
                 cellLoc='center',bbox=[0,0,1,0.97])
    tab.auto_set_font_size(False); tab.set_fontsize(8.0)
    nrows=len(rows)+1
    # A: Einfärbung nach BEWEISKLASSE (Tier), nicht allein nach LAeq.
    TIER_BG={"valid":"#EAF1FB","partial":"#FFFFFF","window":"#F2F2F2"}  # ► hervorgehoben, (M) entsättigt
    TIER_TC={"valid":C_TITLE,"partial":C_EING,"window":"#8A8A8A"}
    for(r,c),cell in tab.get_celld().items():
        cell.set_height(0.97/nrows); cell.set_edgecolor("#DDDDDD")
        if r==0:
            cell.set_facecolor(C_TITLE)
            cell.set_text_props(color='white',fontweight='bold',fontsize=7.3)
            continue
        ri=r-1; tier=row_tier[ri]
        cell.set_facecolor(TIER_BG.get(tier,"#FFFFFF"))
        if c in (2,3):                      # Tier-Symbol + Aussagekraft je Klasse
            cell.set_text_props(color=TIER_TC.get(tier,'#222222'),
                                fontweight='bold' if tier in ('valid','window') else 'normal',
                                fontsize=7.4)
        elif c==10:                          # Dominante Quelle (Farbchip)
            dom=src_info[ri]
            cell.set_facecolor(src_color(dom) if dom!="—" else "#EDEDED")
            cell.set_text_props(color='white' if dom!="—" else '#888888',
                                fontsize=7.2,fontweight='bold')
        elif c==4:                           # LAeq: Schwellen-Textfarbe; (M) entsättigt
            _d=all_data[ri]
            lv=_d['laeq_tag_stern'] if _d.get('hohe_abdeckung_stern') else _d['laeq_tag']
            if tier=='window':
                cell.set_text_props(color='#999999')
            elif not np.isnan(lv) and lv>EINGREIF_TAG:
                cell.set_text_props(color=C_RICHT,fontweight='bold')
            elif not np.isnan(lv) and lv>RW_TAG_WA:
                cell.set_text_props(color='#8B4500',fontweight='bold')
        else:
            if tier=='window':               # (M)-Zeilen durchgehend gedämpft
                cell.set_text_props(color='#8A8A8A')

    # Spaltenbreiten (11 Spalten) — Summe < 1, letzte Spalte breit, nicht abgeschnitten
    col_widths=[0.085,0.028,0.034,0.092,0.058,0.064,0.052,0.052,0.058,0.072,0.150]
    for c,w in enumerate(col_widths):
        for r in range(nrows):
            tab[(r,c)].set_width(w)

    # ---- Bottom-Bereich: sequenziell von der Tabellen-Unterkante nach unten berechnet ----
    # v3-Fix: frueher 3 unabhaengige Fixpositionen (0.061/0.038/0.018), die bei wachsender
    # Tagesliste (laengerer Vergleichstext) zu eng aneinanderrueckten. Jetzt ein Cursor von
    # oben nach unten mit festem Mindestabstand GUTTER -- waechst ein Block, rutschen die
    # folgenden automatisch mit statt zu kollidieren.
    TABLE_BOTTOM=0.07   # muss mit ax=fig.add_axes([...]) oben uebereinstimmen
    GUTTER=0.010
    y_cursor=TABLE_BOTTOM-GUTTER

    # Vergleich: Wochenende/Referenztage (keine dokumentierte Bautätigkeit) vs. Bautage.
    # Nur Tage mit belastbarem LAeq (Tier != window) je Gruppe gemittelt; rein deskriptiv,
    # keine Kausal-/Minderungsaussage.
    ref_vals=[d['laeq_tag'] for d in all_data
              if d['wd'] in ('Sa','So') and d['tier']!='window' and not d['indoor'] and not np.isnan(d['laeq_tag'])]
    bau_vals=[d['laeq_tag'] for d in all_data
              if d['wd'] not in ('Sa','So') and d['tier']!='window' and not d['indoor'] and not np.isnan(d['laeq_tag'])]
    if ref_vals and bau_vals:
        ref_avg=sum(ref_vals)/len(ref_vals); bau_avg=sum(bau_vals)/len(bau_vals)
        y_cursor=fig_text_wrap(fig,0.055,y_cursor,
                 f"Vergleich (Anhaltspunkt, kein Kausalitätsbeweis): Wochenende/Referenz ohne dok. Bautätigkeit "
                 f"(n={len(ref_vals)}) Ø LAeq {ref_avg:.1f} dB(A) · Bautage Mo–Fr (n={len(bau_vals)}) "
                 f"Ø LAeq {bau_avg:.1f} dB(A) · Differenz {bau_avg-ref_avg:+.1f} dB(A)",
                 0.965,0.013,fontsize=7.0,color='#8B4500',fontweight='bold',min_y=0.030)
        y_cursor-=GUTTER

    y_cursor=fig_text_wrap(fig,0.055,y_cursor,
             "Zeilenfarbe = Abdeckungsstufe: ► hohe Abdeckung (blau hervorgehoben) · (M) Messfenster (grau entsättigt) · (~) Teilerfassung (weiß).  "
             "LAeq-Text: rot > 60 / orange > 55 dB(A).  Bautätigk. Nacht: nur Ausnahmetag 24.06.",
             0.965,0.011,fontsize=7,color='#666666',min_y=0.022)
    y_cursor-=GUTTER

    # Legende (Quellen-Farbcodierung entfaellt in der Version ohne Quellenzuordnung)
    if not NO_AUDIO_MODE:
        lg_x=0.035; lgy=max(0.008,y_cursor)
        for s in BAU_RELEVANT:
            col=src_color(s)
            lax=fig.add_axes([lg_x,lgy,0.012,0.018]); lax.axis('off')
            lax.add_patch(Rectangle((0,0),1,1,color=col))
            fig.text(lg_x+0.014,lgy+0.005,disp(s),fontsize=7,color='#444444',va='bottom')
            lg_x+=0.112

    save_page(pp,fig,"Tagesübersicht", do_check=True)

# ============================================================
# SEITE: DOKUMENTATION MESSORT / MESSAUFBAU (v3 Punkt 4 + 5)
# ============================================================
def _load_aufbau_img(bases):
    """Lädt erstes vorhandenes Foto (EXIF-korrigiert, herunterskaliert) zu einer Basisnamenliste."""
    if not os.path.isdir(MESSAUFBAU_DIR): return None
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None
    files=os.listdir(MESSAUFBAU_DIR)
    for b in bases:
        for fn in files:
            if fn.lower().endswith(('.jpg','.jpeg','.png')) and fn.startswith(b):
                try:
                    im=Image.open(os.path.join(MESSAUFBAU_DIR,fn))
                    im=ImageOps.exif_transpose(im); im.thumbnail((1000,1000))
                    return np.asarray(im)
                except Exception:
                    pass
    return None

def page_messaufbau(pp, all_data):
    fig=plt.figure(figsize=(14,10.5))
    ax=fig.add_axes([0,0,1,1]); ax.axis('off')
    ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.951,"DOKUMENTATION MESSORT / MESSAUFBAU",color='white',
             fontsize=14,fontweight='bold')

    # ---- Fotos nach Aufstellungs-Regime ----
    fig.text(0.06,0.892,"Messaufbau-Fotos nach Aufstellungs-Regime (Angabe Mandant)",
             color=C_TITLE,fontsize=11,fontweight='bold')
    n=len(MESSAUFBAU_GROUPS); w=0.275; gap=(0.88-n*w)/max(1,n-1)
    for i,(titel,zeit,bases) in enumerate(MESSAUFBAU_GROUPS):
        x=0.06+i*(w+gap)
        axi=fig.add_axes([x,0.605,w,0.235]); axi.axis('off')
        img=_load_aufbau_img(bases)
        if img is not None:
            axi.imshow(img)
        else:
            axi.add_patch(Rectangle((0,0),1,1,transform=axi.transAxes,fc='#F2F2F2',
                                     ec='#CCCCCC',ls='--'))
            axi.text(0.5,0.5,"(kein Foto)",ha='center',va='center',
                     transform=axi.transAxes,color='#999999',fontsize=9)
        fig.text(x,0.590,titel,fontsize=8.2,fontweight='bold',color='#333333')
        fig.text(x,0.575,zeit,fontsize=7.6,color='#7A4000')

    # ---- Gutachterliche Angaben (Punkt 5) ----
    def h2(t,yp): fig.text(0.06,yp,t,fontsize=10.5,fontweight='bold',color=C_TITLE); return yp-0.025
    def wbul(t,yp,col='#222222'):
        return fig_text_wrap(fig,0.075,yp,"• "+t,0.945,0.020,fontsize=9,color=col)

    y=0.548
    y=h2("Messort und Aufstellung",y)
    y=wbul(f"Standort: {ADRESSE}. {STANDORT_RECHTSHINWEIS}",y)
    y=wbul("Messpunkte: Außenbalkon Südost (SO) / Nordwest (NW); Mai-Reihe im Innenraum.",y)
    y=wbul("Mikrofonhöhe: 01.06.–14.06. 98–100 cm (auf Tisch bzw. Stuhl+Box mit kleinem Ständer); "
           "ab 15.06. 140 cm (vollständig freier Ständer).",y)
    y=wbul("Belastbarkeit: Aufstellung ab 15.06. (freier Ständer, 140 cm) messtechnisch belastbarer; "
           "früher Aufbau (98–100 cm, auf Möbeln) nahe Reflexionsflächen — bei Vergleichen berücksichtigen.",y,'#8B4000')
    y=wbul("Aufgrund der unterschiedlichen Konfiguration ist ein direkter quantitativer Pegelvergleich "
           "zwischen diesen Zeiträumen nicht beabsichtigt; die Abweichungen zwischen den Perioden liegen "
           "jedoch weit oberhalb des durch die Konfigurationsänderung möglichen Einflusses (≤ 3 dB).",y,'#8B4000')
    y=wbul("Messung an/nahe Fassade: möglicher Reflexionseinfluss (Freifeld–Fassade bis ca. +3 dB) "
           "NICHT herausgerechnet (zugunsten Überprüfbarkeit offen ausgewiesen).",y)
    y-=0.006
    y=h2("Messunsicherheit, Rohdaten und offene Punkte",y)
    y=wbul(f"Geräte-Toleranz Klasse 2 (IEC 61672): ±{GERAET_TOL_DB:.1f} dB(A) — offen ausgewiesen.",y)
    y=wbul("Rohdaten archiviert und auf Anforderung überprüfbar: CSV-Pegeldaten (1 Wert/s) je Messtag + "
           "WAV-Clips (>60 dB); Hashes s. Rohdaten-Manifest (Anhang).",y)
    y=wbul("Lageplan / Geometrie zur Quelle: siehe Seite 5 (Kausalitätsseite).",y)
    y=wbul(f"Meteorologie je Messtag (Wind/Temperatur/Niederschlag): {METEO_STATION} — als Anhaltspunkt "
           "auf den Tagesseiten ausgewiesen (Station ≠ Messpunkt; keine rechnerische Pegelkorrektur).",y)
    y-=0.006

    # ---- Positionen + Mikrofonhöhe je Messtag ----
    y=h2("Messpositionen und Mikrofonhöhe je Tag",y)
    y=0.205
    pos_lines=[]
    for d in all_data:
        umg="Innen" if d['indoor'] else "Außen"
        h_short=("98–100cm" if (not d['indoor'] and d['day']<"2026-06-15")
                 else "140cm" if not d['indoor'] else "—")
        pos_lines.append(f"{d['day0'].strftime('%d.%m.')} {umg:<5} {h_short:<8} {d['position'][:30]}")
    half=(len(pos_lines)+1)//2
    for i,line in enumerate(pos_lines[:half]):
        yy=y-i*0.0148
        if yy>=0.050:
            fig.text(0.075, yy, line, fontsize=6.8, family='DejaVu Sans Mono', color='#444444')
    for i,line in enumerate(pos_lines[half:]):
        yy=y-i*0.0148
        if yy>=0.050:
            fig.text(0.520, yy, line, fontsize=6.8, family='DejaVu Sans Mono', color='#444444')

    fig.text(0.06,0.026,f"{VERSION_STR}  |  Messort/Messaufbau  |  orange = methodische Hinweise / eingeschränkte Belastbarkeit",
             fontsize=7.5,color='#999999')
    save_page(pp,fig,"Messaufbau", do_check=False)

# ============================================================
# SEITE: LAGEPLAN / GEOMETRIE ZUR QUELLE (Kausalität)
# ============================================================
def _load_lageplan(keys):
    """Erstes Bild im Lageplan-Ordner, dessen Name eines der Keywords enthält."""
    if not os.path.isdir(LAGEPLAN_DIR): return None
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None
    files=os.listdir(LAGEPLAN_DIR)
    for k in keys:
        for fn in files:
            if fn.lower().endswith(('.png','.jpg','.jpeg')) and k in fn.lower():
                try:
                    im=Image.open(os.path.join(LAGEPLAN_DIR,fn))
                    im=ImageOps.exif_transpose(im); im.thumbnail((1400,1400))
                    return np.asarray(im)
                except Exception:
                    pass
    return None

def page_lageplan(pp):
    fig=plt.figure(figsize=(14,10.5)); ax=fig.add_axes([0,0,1,1]); ax.axis('off')
    ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.951,"LAGEPLAN / GEOMETRIE ZUR QUELLE",color='white',fontsize=14,fontweight='bold')

    imgs=[("Lageplan 1 — Abrisskante (Abriss 1: 5-geschossig, direkt angrenzend)",
           ["abriss","abrisskante"]),
          ("Lageplan 2 — Tiefbohrer-Positionen (< 4 m zur Brandmauer, freie Sichtlinie SO-Balkon)",
           ["tiefbohr","tiefbohrer","bohr"])]
    x0=[0.055,0.515]; w=0.43
    for i,(cap,keys) in enumerate(imgs):
        axi=fig.add_axes([x0[i],0.55,w,0.33]); axi.axis('off')
        img=_load_lageplan(keys)
        if img is not None:
            axi.imshow(img)
        else:
            axi.add_patch(Rectangle((0,0),1,1,transform=axi.transAxes,fc='#F2F2F2',
                                     ec='#CCCCCC',ls='--'))
            axi.text(0.5,0.55,"[ Platzhalter — Lageplan ]",ha='center',va='center',
                     transform=axi.transAxes,color='#999999',fontsize=11,fontweight='bold')
            axi.text(0.5,0.42,"Bild ablegen in Fotos_Videos/Lageplan/",ha='center',va='center',
                     transform=axi.transAxes,color='#AAAAAA',fontsize=8)
        fig.text(x0[i],0.535,cap,fontsize=8.0,fontweight='bold',color='#333333',wrap=True)

    # ---- Geometrie-Fakten (Kausalität) ----
    def h2(t,yp): fig.text(0.06,yp,t,fontsize=11,fontweight='bold',color=C_TITLE); return yp-0.027
    def wbul(t,yp,col='#222222'):
        return fig_text_wrap(fig,0.075,yp,"• "+t,0.945,0.022,fontsize=9.5,color=col)
    y=0.475
    y=h2("Geometrie Wohnung ↔ Baufeld (lt. Lageplan, Angabe Mandant)",y)
    y=wbul("Die Abrisskante verläuft unmittelbar an der Gebäudekante (NW-Balkon/Küche). "
           "Abriss 1: 5-geschossig, direkt angrenzend; Abriss 2: 1-geschossig.",y)
    y=wbul("Tiefbohrer-Positionen in durchgehender Reihe entlang des Baufelds; "
           "Abstand < 4 m zur Brandmauer der Wohnung.",y,'#8B1A00')
    y=wbul("Direkte Sichtverbindung OHNE Schallhindernis vom SO-Balkon zur Tiefbohrer-Position "
           "(kein abschirmendes Bauteil dazwischen).",y,'#8B1A00')
    y=wbul("Maßstab im Luftbild: 5 m. Messpunkt am konfigurierten Standort (Markierung im Plan).",y)
    y-=0.008
    y=fig_text_wrap(fig,0.06,y,
        "Bedeutung (Kausalität): Geringer Abstand (< 4 m) und freie Sichtlinie zur Quelle stützen die "
        "direkte Zuordnung der dokumentierten Pegel zur Tiefbohr-/Abbruchtätigkeit; eine relevante "
        "Abschirmung zwischen Quelle und Messpunkt besteht nicht. Rechtliche Würdigung bleibt offen.",
        0.945,0.022,fontsize=9.5,color='#333333')
    fig.text(0.06,0.045,
        "Lagepläne: Luftbild mit Eintragungen des Mandanten (Abstände ca., kein Vermessungsanspruch).",
        fontsize=7.8,color='#888888',fontstyle='italic')
    fig.text(0.06,0.022,f"{VERSION_STR}  |  Lageplan / Geometrie",fontsize=7.5,color='#999999')
    save_page(pp,fig,"Lageplan", do_check=False)

# ============================================================
# SEITE: REFERENZMESSUNG — bauarme Tage (v3 Gruppe I)
# ============================================================
def page_referenz(pp, all_data):
    fig=plt.figure(figsize=(14,10.5))
    ax=fig.add_axes([0,0,1,1]); ax.axis('off')
    ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.951,"REFERENZMESSUNG — bauarme Tage (Vergleichsbasis)",
             color='white',fontsize=14,fontweight='bold')

    ref=sorted([d for d in all_data if d['day'] in REFERENZ_TAGE], key=lambda d:d['day'])
    valid_active=[d for d in all_data if not d['indoor'] and d['tier']=='valid'
                  and not np.isnan(d['laeq_tag'])]
    def body(t,yp,fs=9.5,col='#333333',bold=False):
        return fig_text_wrap(fig,0.06,yp,t,0.945,0.022,fontsize=fs,color=col,
                             fontweight='bold' if bold else 'normal')

    y=0.885
    y=body("Die folgenden Tage wurden vom Mandanten als Tage OHNE signifikante Bautätigkeit benannt; "
           "sie dienen als Vergleichsbasis (Umgebungs-/Fremdgeräusch).",y)
    y-=0.008
    fig.text(0.06,y,"Referenztage (bauarm):",fontsize=10.5,fontweight='bold',color=C_TITLE); y-=0.028
    for d in ref:
        tier_ref={"valid":"hohe Abdeckung","partial":"Teilerfassung","window":"Messfenster"}.get(d['tier'],d['tier'])
        y=fig_text_wrap(fig,0.06,y,
            f"  {d['day0'].strftime('%d.%m.%Y')} ({d['wd']}, {d['position']}): "
            f"LAeq {fmt(d['laeq_tag'])} / LAFmax {fmt(d['hoechst'])} dB(A) — "
            f"Abdeckung {d['abd_tag']*100:.0f}% ({tier_ref}, Aufbau 98–100 cm)",
            0.945,0.021,fontsize=8.8,color='#222222')
    ref_e=[10**(d['laeq_tag']/10) for d in ref if not np.isnan(d['laeq_tag'])]
    ref_leq=10*np.log10(np.mean(ref_e)) if ref_e else float('nan')
    ref_lo=min((d['laeq_tag'] for d in ref),default=float('nan'))
    ref_hi=max((d['laeq_tag'] for d in ref),default=float('nan'))
    y-=0.004
    y=body(f"Referenz-Spanne: {fmt(ref_lo)}–{fmt(ref_hi)} dB(A)  (energetisches Mittel {fmt(ref_leq)} dB(A)).",
           y,col='#7A4000',bold=True)
    y-=0.012

    fig.text(0.06,y,"Gegenüberstellung aktiv vs. bauarm (Beobachtung):",
             fontsize=10.5,fontweight='bold',color=C_TITLE); y-=0.028
    if valid_active and ref:
        va_lo=min(d['laeq_tag'] for d in valid_active)
        va_hi=max(d['laeq_tag'] for d in valid_active)
        days_va=', '.join(d['day0'].strftime('%d.%m.') for d in sorted(valid_active,key=lambda x:x['day']))
        y=body(f"Aktive Bautage mit hoher Abdeckung (►, ≥90%): {fmt(va_lo)}–{fmt(va_hi)} dB(A)  ({days_va}).",y)
        y=body(f"Differenz aktiv − bauarm: rund +{va_lo-ref_hi:.0f} bis +{va_hi-ref_lo:.0f} dB(A).",
               y,col=C_RICHT,bold=True)
    y-=0.012

    fig.text(0.06,y,"Einordnung / Vorbehalte:",fontsize=10.5,fontweight='bold',color=C_TITLE); y-=0.026
    for c in [
        "Die Referenztage wurden mit dem niedrigeren Aufbau (98–100 cm, auf Möbeln) und nur als "
        "Teil-/Messfenster (7–63% Abdeckung) erfasst — die Vergleichsbasis ist daher mit Vorbehalt zu sehen.",
        "Die Referenzpegel (~55–58 dB(A)) liegen selbst nahe am AVV-Richtwert 55 dB(A) "
        "(Umgebungs-/Verkehrslärm).",
        "Dies ist eine reine Beobachtung der Differenz — KEINE Kausalitäts- oder Minderungsbehauptung; "
        "die rechtliche Bewertung bleibt dem Mandanten/Gericht überlassen.",
    ]:
        y=fig_text_wrap(fig,0.075,y,"• "+c,0.945,0.020,fontsize=9,color='#333333')

    fig.text(0.06,0.025,f"{VERSION_STR}  |  Referenzmessung (bauarme Tage)",fontsize=7.5,color='#999999')
    save_page(pp,fig,"Referenz", do_check=False)

# ============================================================
# ROHDATEN-MANIFEST (v3 Gruppe G) — Dateien + Größe + SHA-256
# ============================================================
def _sha256_raw(path, chunk=1<<20):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda: f.read(chunk), b''): h.update(b)
    return h.hexdigest()

# lib_v3: Hash-Cache -- Rohdaten sind append-only; unveraenderte Dateien (gleiche Groesse +
# mtime) werden nicht erneut gelesen (~15 GB / ~95 s pro Lauf gespart). Der Hash selbst ist
# identisch zur Direktberechnung; bei jeder Aenderung an Groesse/mtime wird neu gehasht.
# Kompletter Neuaufbau: Cache-Datei loeschen oder SCHALLBERICHT_REHASH=1 setzen.
_HASH_CACHE_PATH = os.path.join(BASE, "Verknuepfung", "manifest_hash_cache.json")
_HASH_CACHE = None

def _load_hash_cache():
    global _HASH_CACHE
    if _HASH_CACHE is not None: return _HASH_CACHE
    _HASH_CACHE = {}
    if os.environ.get("SCHALLBERICHT_REHASH") != "1" and os.path.exists(_HASH_CACHE_PATH):
        try:
            with open(_HASH_CACHE_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict): _HASH_CACHE = data
        except Exception as exc:
            print(f"[lib] Hash-Cache nicht lesbar (wird neu aufgebaut): {exc}")
    return _HASH_CACHE

def _save_hash_cache():
    if _HASH_CACHE is None: return
    try:
        tmp = _HASH_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_HASH_CACHE, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _HASH_CACHE_PATH)
    except Exception as exc:
        print(f"[lib] Hash-Cache nicht schreibbar: {exc}")

def _sha256(path, chunk=1<<20):
    cache = _load_hash_cache()
    try:
        st = os.stat(path)
        key = os.path.abspath(path)
        ent = cache.get(key)
        if ent and ent.get("bytes")==st.st_size and ent.get("mtime_ns")==st.st_mtime_ns:
            return ent["sha256"]
        digest = _sha256_raw(path, chunk)
        cache[key] = {"bytes": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
        return digest
    except OSError:
        return _sha256_raw(path, chunk)

def build_manifest(all_data):
    rows=[]
    zips_by_day = {}
    idx = os.path.join(BASE, "Verknuepfung", "master_index.csv")
    if os.path.exists(idx):
        try:
            with open(idx, newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if r.get("Datum") and r.get("ZIP"):
                        zips_by_day.setdefault(r["Datum"], set()).add(r["ZIP"])
        except Exception as exc:
            print(f"[lib] master_index.csv fuer Manifest nicht lesbar: {exc}")
    for d in all_data:
        day=d['day']
        for c in sorted(glob.glob(os.path.join(BASE, f"{day} *.csv"))):
            if "(1)" in c or "(2)" in c: continue
            rows.append(dict(Datum=day, Typ="CSV", Datei=os.path.basename(c),
                             Bytes=os.path.getsize(c), SHA256=_sha256(c)))
        zip_names = set(zips_by_day.get(day, set()))
        zip_names.update(
            os.path.basename(p)
            for p in glob.glob(os.path.join(BASE, f"Laermprotokoll_{day[8:10]}.{day[5:7]}.{day[:4]}*.zip"))
        )
        for zname in sorted(zip_names):
            zp=os.path.join(BASE, zname)
            if os.path.exists(zp):
                rows.append(dict(Datum=day, Typ="WAV-ZIP", Datei=zname,
                                 Bytes=os.path.getsize(zp), SHA256=_sha256(zp)))
    _save_hash_cache()
    return rows

def page_manifest(pp, manifest):
    fig=plt.figure(figsize=(14,10.5))
    ax=fig.add_axes([0,0,1,1]); ax.axis('off')
    ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.951,"ROHDATEN-MANIFEST — Dateien, Größe, SHA-256",color='white',
             fontsize=14,fontweight='bold')
    fig.text(0.06,0.902,
             "Im PDF sind gekürzte SHA-256-Hashes dargestellt. Vollständige Hashes liegen in der "
             f"Auswertungsdatei {MANIFEST_CSV_NAME} vor und können auf Anforderung bereitgestellt werden.",
             fontsize=8.5,color='#555555')
    fig.text(0.06,0.880,
             f"Skript: {VERSION_STR}  ·  Konfig: COVERAGE_VALID={COVERAGE_VALID}, COVERAGE_WINDOW={COVERAGE_WINDOW}, "
             f"RW_TAG={RW_TAG_WA:.0f}/EIN={EINGREIF_TAG:.0f}, RW_N={RW_NACHT_WA:.0f}/EIN_N={EINGREIF_NACHT:.0f}, "
             f"GAP_MIN_SEC={GAP_MIN_SEC}, GERAET_TOL={GERAET_TOL_DB}",
             fontsize=6.8,color='#666666')
    y0=0.852
    fig.text(0.06,y0,f"{'Datum':<11}{'Typ':<9}{'Datei':<32}{'MB':>7}  SHA-256 (gekürzt, 24)",
             fontsize=7.0,family='DejaVu Sans Mono',fontweight='bold',color=C_TITLE)
    lh=0.0140; cap=56
    for i,r in enumerate(manifest[:cap]):
        yy=y0-0.016-(i*lh)
        line=(f"{r['Datum']:<11}{r['Typ']:<9}{r['Datei'][:30]:<32}"
              f"{r['Bytes']/1e6:>7.1f}  {r['SHA256'][:24]}")
        fig.text(0.06,yy,line,fontsize=6.4,family='DejaVu Sans Mono',color='#333333')
    if len(manifest)>cap:
        fig.text(0.06,y0-0.016-cap*lh,
                 f"… {len(manifest)-cap} weitere — vollständige Liste in der Auswertungsdatei {MANIFEST_CSV_NAME}.",
                 fontsize=7.5,color='#C00000')
    tot_mb=sum(r['Bytes'] for r in manifest)/1e6
    fig.text(0.06,0.040,f"Summe: {len(manifest)} Dateien, {tot_mb:.0f} MB.",
             fontsize=8.0,color='#444444',fontweight='bold')
    fig.text(0.06,0.022,f"{VERSION_STR}  |  Rohdaten-Manifest",fontsize=7.5,color='#999999')
    save_page(pp,fig,"Manifest", do_check=False)

# ============================================================
# B1 — KERNBEFUNDE (strategische Zusammenfassung)
# ============================================================
def page_kernbefunde(pp, all_data):
    fig=plt.figure(figsize=(14,10.5)); ax=fig.add_axes([0,0,1,1]); ax.axis('off')
    ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.951,"KERNBEFUNDE — ZUSAMMENFASSUNG",color='white',fontsize=15,fontweight='bold')

    valid=sorted([d for d in all_data if not d['indoor'] and d['tier']=='valid'
                  and not np.isnan(d['laeq_tag'])], key=lambda d:d['day'])
    # Abschnitt 1
    fig.text(0.06,0.893,f"1 — Vollständig erfasste Tage (►, ≥90% Abdeckung): {len(valid)}",
             fontsize=12,fontweight='bold',color=C_TITLE)
    col=["Tag","LAeq","Abdeckung","Zeit > 55 dB","Dauerlärm ≥60 dB","Kalibrierung"]
    rows=[]
    for d in valid:
        cal="Feldkal." if d['day']>="2026-06-22" else "Werkskal."
        rows.append([d['day0'].strftime("%d.%m."), f"{d['laeq_tag']:.1f} dB",
                     f"{d['abd_tag']*100:.0f} %",
                     f"{d['pct_thr']:.0f} % ({d['n_above_sec']//60} min)",
                     f"{d['ph60_min']:.0f} min / {len(d['ph60_10'])} Phasen", cal])
    # v3-Fix: Zeilenhoehe war fix 0.040 -> Tabelle wuchs mit wachsender Zahl valider Tage
    # unbeschraenkt (bei 13 Tagen bereits 56% der Seite), was Abschnitt 2/3 darunter von der
    # Seite drueckte. Jetzt gedeckelt: Zeilenhoehe schrumpft ab MAX_TABLE_H, Schrift mit.
    MAX_TABLE_H=0.32
    row_h=min(0.040,MAX_TABLE_H/(len(rows)+1)) if rows else 0.040
    th=row_h*(len(rows)+1)
    tab=ax.table(cellText=rows,colLabels=col,loc='upper left',cellLoc='center',
                 bbox=[0.06,0.86-th,0.88,th])
    tab.auto_set_font_size(False)
    tab.set_fontsize(10 if row_h>=0.030 else max(6.0,10*row_h/0.040))
    for (r,c),cell in tab.get_celld().items():
        cell.set_edgecolor("#CCCCCC")
        if r==0: cell.set_facecolor(C_TITLE); cell.set_text_props(color='white',fontweight='bold',fontsize=9)
        elif r%2==0: cell.set_facecolor("#EAF1FB")
    y=0.86-th-0.030
    _dmin=min((d['laeq_tag']-RW_TAG_WA for d in valid),default=0)
    _dmax=max((d['laeq_tag']-RW_TAG_WA for d in valid),default=0)
    y=fig_text_wrap(fig,0.06,y,
        f"Alle {len(valid)} vollständig erfassten Tage überschreiten die AVV-Eingreifschwelle (60 dB(A)); der Tages-LAeq "
        f"liegt {_dmin:.0f}–{_dmax:.0f} dB oberhalb des Wohngebietsrichtwerts (55 dB(A)).",
        0.945,0.026,fontsize=11,color=C_RICHT,fontweight='bold')
    y-=0.012

    # Abschnitt 2: Lauteste Stunde Mittags (11–15h)
    fig.text(0.06,y,"2 — Kein Erholungsfenster zur Mittagszeit",fontsize=12,fontweight='bold',color=C_TITLE); y-=0.026
    mitt=[]
    for d in sorted([x for x in all_data if not x['indoor'] and not np.isnan(x['lh_val']) and x['lh_it'] is not None],
                    key=lambda x:x['day']):
        mid=d['lh_it']-pd.Timedelta(minutes=30)
        if 11<=mid.hour<15 and d['lh_val']>=EINGREIF_TAG:
            mitt.append(f"{d['day0'].strftime('%d.%m.')}: lauteste Stunde {d['lh_txt']} Uhr mit {d['lh_val']:.1f} dB(A)")
    for line in mitt[:7]:
        y=fig_text_wrap(fig,0.075,y,"• "+line,0.945,0.021,fontsize=9.5,color='#222222')
    if not mitt:
        y=fig_text_wrap(fig,0.075,y,"• (keine lauteste Stunde im Mittagsfenster)",0.945,0.021,fontsize=9.5,color='#888888')
    y-=0.012

    # Abschnitt 3
    fig.text(0.06,y,"3 — Beide Wohnungsseiten betroffen",fontsize=12,fontweight='bold',color=C_TITLE); y-=0.026
    y=fig_text_wrap(fig,0.075,y,
        "Messungen erfolgten an Südost- und Nordwest-Balkon sowie im Innenraum. Videobelege dokumentieren "
        "Pegel in Schlafzimmer, Kinderzimmer und Küche. Eine Ausweichmöglichkeit in unbetroffene "
        "Wohnbereiche ist nicht vorhanden.",0.945,0.022,fontsize=10,color='#222222')
    y-=0.012

    # Abschnitt 4
    fig.text(0.06,y,"4 — Baustellenspezifische Ursache",fontsize=12,fontweight='bold',color=C_TITLE); y-=0.026
    y=fig_text_wrap(fig,0.075,y,
        "Beim Wegfall der Bautätigkeit sinkt der Pegel auf das städtische Hintergrundrauschen "
        "(sichtbar in den Tagesgrafiken sowie im Referenzpegel-Abschnitt). Die Gesamtschau aus "
        "Pegelverlauf, Bautagebuch, Videobelegen, Lagegeometrie und Quellenereignissen spricht "
        "überwiegend für eine baustellenspezifische Ursache und gegen bloßen allgemeinen Stadtlärm.",
        0.945,0.022,fontsize=10,color='#222222')

    # Punkt 5: Quellenverteilung-Einordnung (entfaellt in der Version ohne Quellenzuordnung)
    if not NO_AUDIO_MODE:
        y-=0.010
        y=fig_text_wrap(fig,0.075,y,
            "Hinweis Quellenverteilung: Die Zuordnung von Lärmereignissen zu Lärmquellen ist je nach Messtag "
            "unterschiedlich verifiziert. Manuell geprüfte Zuordnungen (Laermquelle_geprueft) haben Vorrang. "
            "Nicht verifizierte Tage werden auf den Tagesseiten ausdrücklich als vorläufig gekennzeichnet.",
            0.945,0.019,fontsize=8.5,color='#666666',fontstyle='italic')

    fig.text(0.06,0.022,f"{VERSION_STR}  |  Kernbefunde",fontsize=7.5,color='#999999')
    save_page(pp,fig,"Kernbefunde", do_check=False)

# ============================================================
# B2 — BELASTUNGSDAUER JE MESSTAG (Balkendiagramm)
# ============================================================
def page_belastungsdauer(pp, all_data):
    fig=plt.figure(figsize=(14,10.5)); ax0=fig.add_axes([0,0,1,1]); ax0.axis('off')
    ax0.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax0.transAxes))
    fig.text(0.06,0.951,"BELASTUNGSDAUER JE MESSTAG — Minuten oberhalb der Richtwerte",
             color='white',fontsize=13.5,fontweight='bold')
    out=sorted([d for d in all_data if not d['indoor']],key=lambda d:d['day'])
    labels=[f"{d['wd'][:2]} {d['day0'].strftime('%d.%m.')}" for d in out]
    m55=[d['n_above_sec']/60.0 for d in out]
    m60=[d['n_above_60_sec']/60.0 for d in out]
    mdl=[d['ph60_min'] for d in out]
    x=np.arange(len(out)); w=0.27
    ax=fig.add_axes([0.07,0.16,0.88,0.70])
    b1=ax.bar(x-w,m55,w,color='#E69500',label='Minuten > 55 dB(A)')
    b2=ax.bar(x,  m60,w,color='#C00000',label='Minuten > 60 dB(A)')
    b3=ax.bar(x+w,mdl,w,color='#7A0000',label='Dauerlärm ≥60 dB / ≥10 min')
    for i,d in enumerate(out):
        if d['tier']=='window':
            for bb in (b1[i],b2[i],b3[i]):
                bb.set_hatch('//'); bb.set_edgecolor('#555555'); bb.set_linewidth(0.6)
    ax.plot([],[],' ',label='schraffiert = Messfenster <70% (kein Tagesbild)')
    ax.set_xticks(x); ax.set_xticklabels(labels,rotation=45,ha='right',fontsize=8)
    ax.set_ylim(0,800); ax.set_ylabel("Minuten (Tagzeit 07–20 h)",fontsize=9.5)
    ax.grid(True,axis='y',ls=':',color='#CCCCCC',alpha=0.7)
    for sp in ("top","right"): ax.spines[sp].set_visible(False)
    ax.legend(loc='upper right',fontsize=8.5,framealpha=0.9)
    ax._requires_graphic_explanation = True
    ax._graphic_explanation = True
    ax._graphic_label = "Belastungsdauer je Messtag"
    fig.text(0.07,0.096,
        "Farben/Interpretation: Orange = Minuten über 55 dB(A), Rot = Minuten über 60 dB(A), "
        "Dunkelrot = zusammenhängender Dauerlärm ≥60 dB/≥10 min; Schraffur = Messfenster <70%.",
        fontsize=8.2,color='#444444')
    fig.text(0.07,0.071,
        "Eigene Kennzahl (keine AVV-Norm). Darstellung auf Basis der gemessenen 07–20 h-Fenster; "
        "Abdeckungsstufe beachten (schraffierte Balken = Messfenster <70%).",
        fontsize=8.5,color='#555555')
    fig.text(0.06,0.022,f"{VERSION_STR}  |  Belastungsdauer",fontsize=7.5,color='#999999')
    save_page(pp,fig,"Belastungsdauer", do_check=False)

# ============================================================
# B3 — LAUTESTE STUNDE JE MESSTAG (Pegel + Tageszeit)
# ============================================================
def page_lauteste_stunde(pp, all_data):
    fig=plt.figure(figsize=(14,10.5)); ax0=fig.add_axes([0,0,1,1]); ax0.axis('off')
    ax0.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax0.transAxes))
    fig.text(0.06,0.951,"LAUTESTE STUNDE JE MESSTAG — Pegel und Tageszeit",
             color='white',fontsize=14,fontweight='bold')
    out=[d for d in all_data if not d['indoor'] and not np.isnan(d['lh_val']) and d['lh_it'] is not None]
    out=sorted(out,key=lambda d:d['day'])           # ältester unten
    labels=[f"{d['wd'][:2]} {d['day0'].strftime('%d.%m.')}" for d in out]
    vals=[d['lh_val'] for d in out]
    yv=np.arange(len(out))
    def barcol(v): return '#C00000' if v>70 else '#E69500' if v>=60 else '#999999'
    # Pegel-Balken (links breit)
    axb=fig.add_axes([0.10,0.16,0.52,0.72])
    bars=axb.barh(yv,vals,color=[barcol(v) for v in vals],height=0.6)
    for bb,d in zip(bars,out):
        if d.get('lh_cov',3600) < 3600:
            bb.set_hatch('//'); bb.set_edgecolor('#444444'); bb.set_linewidth(0.5)
    axb.axvline(55,ls='--',color=C_RICHT,lw=1); axb.axvline(60,ls=':',color=C_EING,lw=1)
    axb.set_xlim(0,90); axb.set_yticks(yv); axb.set_yticklabels(labels,fontsize=8)
    axb.set_xlabel("LAeq lauteste Stunde [dB(A)]",fontsize=9)
    axb.set_title("Pegel",fontsize=10,color=C_TITLE)
    for i,v in enumerate(vals):
        axb.text(v+1,yv[i],f"{v:.1f} dB",va='center',fontsize=7.5,color='#333333')
    for sp in ("top","right"): axb.spines[sp].set_visible(False)
    legend_handles=[
        Patch(facecolor='#C00000',label='>70 dB(A): sehr hohe lauteste Stunde'),
        Patch(facecolor='#E69500',label='60–70 dB(A): oberhalb Eingreifschwelle'),
        Patch(facecolor='#999999',label='<60 dB(A): unter Eingreifschwelle'),
        Patch(facecolor='white',edgecolor='#444444',hatch='//',label='<3600 s Messanteil im 60-min-Fenster'),
        mlines.Line2D([],[],color=C_RICHT,ls='--',label='55 dB(A) Richtwert'),
        mlines.Line2D([],[],color=C_EING,ls=':',label='60 dB(A) Eingreifschwelle'),
        Patch(facecolor='#FFF6CC',label='gelb: 11–15 Uhr'),
    ]
    axb.legend(handles=legend_handles,loc='lower right',fontsize=7.0,framealpha=0.92)
    axb._requires_graphic_explanation=True
    axb._graphic_explanation=True
    axb._graphic_label="Lauteste Stunde Pegel"
    # Tageszeit (rechts, Streudiagramm)
    axt=fig.add_axes([0.66,0.16,0.30,0.72])
    axt.axvspan(11,15,color='#FFF6CC',alpha=0.8,zorder=0)
    # fix v6 Punkt 8: kein Overlap mit Achsentitel — Mittagszeit-Label in axes-Koordinaten
    axt.text(0.5,0.88,"Mittagszeit\n(11–15 Uhr)",ha='center',va='top',fontsize=6.5,
             color='#8A7000',transform=axt.transAxes)
    for i,d in enumerate(out):
        mid=d['lh_it']-pd.Timedelta(minutes=30)
        h=mid.hour+mid.minute/60.0
        axt.plot([h],[yv[i]],'o',color=barcol(vals[i]),ms=6,zorder=3)
        axt.text(h+0.25,yv[i],d['lh_txt'],va='center',fontsize=6.6,color='#444444')
    axt.set_xlim(7,20); axt.set_ylim(-0.6,len(out)-0.4); axt.set_yticks([])
    axt.set_xlabel("Tageszeit (lauteste Stunde)",fontsize=9)
    axt.set_title("Tageszeit",fontsize=10,color=C_TITLE)
    for sp in ("top","right","left"): axt.spines[sp].set_visible(False)
    fig.text(0.06,0.080,
        "Farben/Interpretation: Balken- und Punktfarbe zeigen die Pegelklasse; links steht die Höhe der lautesten Stunde, "
        "rechts ihr Tageszeitpunkt. Gelb markiert die Mittags-/frühe Nachmittagszeit.",
        fontsize=8.1,color='#444444')
    fig.text(0.06,0.054,
        f"Lautestes 60-Minuten-Zeitfenster je Messtag; bei Messlücken wird es ab {LAUTESTE_STUNDE_MIN_SEC//60} min "
        "Messanteil ausgewiesen und schraffiert markiert. 27.06. wird dadurch als Teilfenster sichtbar.",
        fontsize=8.1,color='#555555')
    fig.text(0.06,0.022,f"{VERSION_STR}  |  Lauteste Stunde",fontsize=7.5,color='#999999')
    save_page(pp,fig,"LautesteStunde", do_check=False)

# ============================================================
# B4 — INNENRAUMBETROFFENHEIT (§287 ZPO, Wohnnutzungsbezug)
# Diese Seite stellt Innenraumwerte als §287-ZPO-Orientierungswerte dar.
# NIEMALS als AVV-Grenzwertverstöße formulieren. Nur Wohnnutzungsbezug.
# ============================================================
def page_innenraum(pp):
    fig=plt.figure(figsize=(14,10.5)); ax=fig.add_axes([0,0,1,1]); ax.axis('off')
    ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.951,"INNENRAUMBETROFFENHEIT — Wohnnutzungsbezug",color='white',
             fontsize=14,fontweight='bold')

    fig.text(0.06,0.895,"1 — Innenraummessungen (PCE-323)",fontsize=11,fontweight='bold',color=C_TITLE)
    t1=[["20.05.","Innenraum","51,5 dB(A)","84,4 dB(A)","69 %"],
        ["21.05.","Innenraum","50,9 dB(A)","81,6 dB(A)","73 %"],
        ["22.05.","Innenraum","50,1 dB(A)","80,8 dB(A)","41 %"]]
    c1=["Datum","Ort","LAeq im Fenster","LAFmax","Abdeckung"]
    tab=ax.table(cellText=t1,colLabels=c1,loc='upper left',cellLoc='center',bbox=[0.06,0.78,0.70,0.085])
    tab.auto_set_font_size(False); tab.set_fontsize(9.5)
    for (r,c),cell in tab.get_celld().items():
        cell.set_edgecolor("#CCCCCC")
        if r==0: cell.set_facecolor(C_TITLE); cell.set_text_props(color='white',fontweight='bold',fontsize=9)
        elif r%2==0: cell.set_facecolor("#F2F2F2")
    fig.text(0.06,0.762,
        "Innenraum-Messungen: Orientierungswert nach §287 ZPO — kein AVV-Außengrenzwert anwendbar. "
        "Messung ohne AVV-Grenzlinien.",fontsize=8.5,color='#555555',fontstyle='italic')

    fig.text(0.06,0.728,"2 — Videobelege Fensterrealität",fontsize=11,fontweight='bold',color=C_TITLE)
    # fix e: Tabelle um "geschlossen"-Einträge ergänzt (27./28./29.05. — Videobelege)
    t2=[["27.05.","07:30","Schlafzimmer","geschlossen","43–46 dB"],
        ["27.05.","07:55","Schlafzimmer","gekippt","50–70 dB"],
        ["27.05.","14:23","Schlafzimmer","gekippt","63–68 dB"],
        ["27.05.","15:11","Kinderzimmer","geöffnet","78–88 dB"],
        ["27.05.","15:14","Schlafzimmer","gekippt","66–76 dB"],
        ["28.05.","07:31","Schlafzimmer","geschlossen","40–46 dB"],
        ["28.05.","07:31","Schlafzimmer","gekippt","65–80 dB"],
        ["29.05.","07:31","Schlafzimmer","geschlossen","50–64 dB"],
        ["20.05.","14:30","Küche","—","55–60 dB"]]
    c2=["Datum","Uhrzeit","Ort","Fensterzustand","Pegel (lt. Video)"]
    tab2=ax.table(cellText=t2,colLabels=c2,loc='upper left',cellLoc='center',bbox=[0.06,0.392,0.78,0.313])
    tab2.auto_set_font_size(False); tab2.set_fontsize(8.5)
    for (r,c),cell in tab2.get_celld().items():
        cell.set_edgecolor("#CCCCCC")
        if r==0: cell.set_facecolor(C_TITLE); cell.set_text_props(color='white',fontweight='bold',fontsize=8.0)
        elif r%2==0: cell.set_facecolor("#F2F2F2")
    # fix e: softer formulation — claim only what the data supports
    y=fig_text_wrap(fig,0.06,0.370,
        "Bei gekippten oder geöffneten Fenstern treten erhebliche Pegel auf; auch bei geschlossenen "
        "Fenstern zeigen Videobelege (27./28./29.05.) verbleibende Störpegel (40–64 dB). "
        "Ein dauerhaft lärmfreier Wohnbereich ist nicht dokumentiert.",
        0.945,0.024,fontsize=10,color=C_RICHT,fontweight='bold')
    y-=0.012
    fig.text(0.06,y,"3 — Ausweichunmöglichkeit",fontsize=11,fontweight='bold',color=C_TITLE); y-=0.026
    y=fig_text_wrap(fig,0.075,y,
        "Messungen an beiden Balkonen (SO und NW) sowie Videobelege aus Schlafzimmer, Kinderzimmer, "
        "Küche und Flur belegen, dass kein Wohnbereich von der Belastung ausgenommen ist.",
        0.945,0.022,fontsize=10,color='#222222')
    fig.text(0.06,0.04,
        "Pegel lt. Videoaufnahmen (Handy-Schätzung, keine kalibrierte Messung). Orientierung nach §287 ZPO.",
        fontsize=7.8,color='#888888',fontstyle='italic')
    fig.text(0.06,0.022,f"{VERSION_STR}  |  Innenraumbetroffenheit",fontsize=7.5,color='#999999')
    save_page(pp,fig,"Innenraum", do_check=False)

# ============================================================
# B5 — REFERENZPEGEL (informelle Baseline, 30.05. Abend)
# ============================================================
def page_referenzpegel(pp):
    fig=plt.figure(figsize=(14,10.5)); ax=fig.add_axes([0,0,1,1]); ax.axis('off')
    ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.951,"REFERENZPEGEL — Wohnung ohne Bautätigkeit",color='white',
             fontsize=14,fontweight='bold')
    y=fig_text_wrap(fig,0.06,0.885,
        "Am Abend des 30.05.2026 (Sa), nach Baustellenbetrieb bzw. ohne erkennbare Bautätigkeit, "
        "wurden Videobelege zur Dokumentation des Ruhezustands der Wohnung aufgenommen. Diese Aufnahmen "
        "dienen als informeller Referenzpegel und sind keine formale Baseline-Messung nach AVV.",
        0.945,0.023,fontsize=10,color='#333333')
    y-=0.010
    t=[["30.05.","18:49","Schlafzimmer","geschlossen","26 dB"],
       ["30.05.","18:50","Schlafzimmer","geschlossen","25 dB"],
       ["30.05.","18:53","Küche","geschlossen","38 dB"],
       ["30.05.","18:54","Küche","geschlossen","29 dB"],
       ["30.05.","18:55","NW-Balkon","geschlossen","52 dB"],
       ["30.05.","18:55","NW-Balkon","geschlossen","48 dB"]]
    c=["Datum","Uhrzeit","Ort","Fensterzustand","Pegel (lt. Video)"]
    th=0.040*(len(t)+1)
    tab=ax.table(cellText=t,colLabels=c,loc='upper left',cellLoc='center',bbox=[0.06,y-th,0.78,th])
    tab.auto_set_font_size(False); tab.set_fontsize(9.5)
    for (r,cc),cell in tab.get_celld().items():
        cell.set_edgecolor("#CCCCCC")
        if r==0: cell.set_facecolor(C_TITLE); cell.set_text_props(color='white',fontweight='bold',fontsize=9)
        elif r%2==0: cell.set_facecolor("#F2F2F2")
    y=y-th-0.030
    y=fig_text_wrap(fig,0.06,y,
        "Zum Vergleich: In den späteren Außenmessungen mit hoher Abdeckung wurden Tages-LAeq von 63,9 bis 71,9 dB(A) "
        "gemessen; am NW-Balkon insbesondere 63,9 dB(A) am 26.06.2026 und 68,0 dB(A) am 25.06.2026 "
        "(nach Messortwechsel SO→NW). Die Belastung durch den Tiefbohrer übersteigt die Abendruhepegel "
        "um mehr als 15 dB(A). Der Vergleich dient als Orientierung; die Referenzwerte beruhen auf "
        "Handy-Schätzungen und stellen keine formale AVV-Baseline dar.",
        0.945,0.024,fontsize=10,color=C_RICHT,fontweight='bold')
    fig.text(0.06,0.05,
        "Pegel lt. Videoaufnahmen (Handy-Schätzung, keine kalibrierte Messung). Verwendung ausschließlich "
        "als Orientierungspunkt nach §287 ZPO.",fontsize=7.8,color='#888888',fontstyle='italic')
    fig.text(0.06,0.022,f"{VERSION_STR}  |  Referenzpegel (30.05.)",fontsize=7.5,color='#999999')
    save_page(pp,fig,"Referenzpegel", do_check=False)

# ============================================================
# SEITE: REFERENZPEGEL AUSSENBEREICH -- KALIBRIERTE BAURUHE-MESSUNG (v8)
#   27.06. (SO-Balkon, Sa) und 28.06. (NW-Balkon, So): echte PCE-Messungen ohne
#   dokumentierte Bautaetigkeit -- staerkere Evidenz als die Video-Schaetzungen (30.05.).
# ============================================================
REF_RUHETAGE = ["2026-06-27", "2026-06-28"]

def page_referenzpegel_aussen(pp, all_data):
    ref_days = [d for d in all_data if d['day'] in REF_RUHETAGE]
    bau_days = [d for d in all_data if d['wd'] not in ('Sa','So') and d['tier']!='window' and not d['indoor']]

    fig=plt.figure(figsize=(14,10.5)); ax=fig.add_axes([0,0,1,1]); ax.axis('off')
    ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
    fig.text(0.06,0.951,"REFERENZPEGEL AUSSENBEREICH -- kalibrierte Bauruhe-Messung",color='white',
             fontsize=14,fontweight='bold')
    y=fig_text_wrap(fig,0.06,0.885,
        "Am 27.06.2026 (Sa, SO-Balkon) und 28.06.2026 (So, NW-Balkon) wurde jeweils eine vollständige, "
        "kalibrierte PCE-Messung ohne dokumentierte Bautätigkeit durchgeführt (Wochenende, Bauruhe laut "
        "Bautagebuch). Im Unterschied zur Video-Referenz (vorige Seite) handelt es sich hier um echte "
        "Dauermessungen mit demselben Messgerät und derselben Methodik wie an den Bautagen -- daher die "
        "belastbarere Vergleichsbasis für den Außenbereich.",
        0.945,0.021,fontsize=9.6,color='#333333')
    y-=0.012

    rows=[]
    for d in sorted(ref_days,key=lambda x:x['day']):
        rows.append([d['day0'].strftime('%d.%m.%Y')+f" ({d['wd']})", d['position'],
                     f"{fmt(d['laeq_tag'])} dB(A)", f"{fmt(d['hoechst'])} dB(A)",
                     f"{fmt(d['l1_tag'])} dB(A)", f"{d['abd_tag']*100:.0f}%"])
    cols=["Datum","Position","LAeq (Tag)","LAFmax","L₁ (99%)","Abdeckung"]
    th=0.045*(len(rows)+1)
    tab=ax.table(cellText=rows,colLabels=cols,loc='upper left',cellLoc='center',bbox=[0.06,y-th,0.88,th])
    tab.auto_set_font_size(False); tab.set_fontsize(10)
    for (r,cc),cell in tab.get_celld().items():
        cell.set_edgecolor("#CCCCCC")
        if r==0: cell.set_facecolor(C_TITLE); cell.set_text_props(color='white',fontweight='bold',fontsize=9.5)
        elif r%2==0: cell.set_facecolor("#F2F2F2")
    y=y-th-0.035

    if ref_days and bau_days:
        _rv=[d['laeq_tag'] for d in ref_days if not np.isnan(d['laeq_tag'])]
        _bv=[d['laeq_tag'] for d in bau_days if not np.isnan(d['laeq_tag'])]
        ref_avg=sum(_rv)/len(_rv); bau_avg=sum(_bv)/len(_bv)
        y=fig_text_wrap(fig,0.06,y,
            f"Vergleich: Ø LAeq Referenztage (Außen, n={len(_rv)}) {ref_avg:.1f} dB(A) -- "
            f"Ø LAeq Bautage Mo-Fr (n={len(_bv)}) {bau_avg:.1f} dB(A) -- "
            f"Differenz {bau_avg-ref_avg:+.1f} dB(A). Als Anhaltspunkt zu werten, kein automatischer "
            f"Kausalitätsbeweis (unterschiedliche Wetterlagen/Tage möglich); vollständige Tagesgrafiken "
            f"siehe jeweilige Tagesseite (27.06./28.06.).",
            0.945,0.023,fontsize=9.8,color=C_RICHT,fontweight='bold')

    fig.text(0.06,0.05,
        "Kalibrierte Messung (PCE-323, 94 dB(A) Referenzkalibrierung), identische Methodik wie an den "
        "Bautagen. Keine dokumentierte Bautätigkeit lt. Bautagebuch an beiden Tagen (Wochenende).",
        fontsize=7.8,color='#888888',fontstyle='italic')
    fig.text(0.06,0.022,f"{VERSION_STR}  |  Referenzpegel Außenbereich (27./28.06.)",fontsize=7.5,color='#999999')
    save_page(pp,fig,"Referenzpegel Außen")

# ============================================================
# SEITE: VIER-KURVEN-VERGLEICH -- RUHETAG / MIN- / DURCHSCHNITTS- / MAX-BAUTAG (v8)
# ============================================================
def _timeofday_series(d, grid_min=1):
    """LAeq-1min-Verlauf, auf Uhrzeit (unabhaengig vom Kalenderdatum) ausgerichtet, 07-20h.
    NaN wo keine Messung -- keine Interpolation/Fabrikation ueber echte Luecken hinweg."""
    day0=pd.Timestamp(d['day0']); t7=day0+pd.Timedelta(hours=7); t20=day0+pd.Timedelta(hours=20)
    grid=pd.date_range(t7,t20,freq=f"{grid_min}min")
    ser=d['laeq1m'].reindex(grid,method='nearest',tolerance=pd.Timedelta(minutes=grid_min))
    ser.index=((grid-t7).total_seconds()/60).astype(int)
    return ser

def page_referenz_vergleich(pp, all_data, page_num):
    ref_days=[d for d in all_data if d['day'] in REF_RUHETAGE]
    bau_days=[d for d in all_data if d['wd'] not in ('Sa','So') and d['tier']!='window'
              and not d['indoor'] and not np.isnan(d['laeq_tag'])]
    if not ref_days or not bau_days:
        return False

    ref_ser=pd.concat([_timeofday_series(d) for d in ref_days],axis=1).mean(axis=1,skipna=True)
    bau_ser_map={d['day']:_timeofday_series(d) for d in bau_days}
    avg_ser=pd.concat(bau_ser_map.values(),axis=1).mean(axis=1,skipna=True)
    min_day=min(bau_days,key=lambda d:d['laeq_tag']); max_day=max(bau_days,key=lambda d:d['laeq_tag'])
    min_ser=bau_ser_map[min_day['day']]; max_ser=bau_ser_map[max_day['day']]

    anchor=pd.Timestamp("2000-01-01 07:00:00")
    def to_dt(ser):
        s=ser.copy(); s.index=anchor+pd.to_timedelta(s.index,unit="m"); return s

    fig=plt.figure(figsize=(14,9))
    ax=fig.add_axes([0.065,0.30,0.885,0.54])
    fig.text(0.06,0.965,"VERGLEICH: RUHETAG VS. BAUTAGE (MIN / DURCHSCHNITT / MAX)",
              fontsize=13,fontweight='bold',color=C_TITLE)
    fig_text_wrap(fig,0.06,0.935,
        f"Zeitverlauf (07-20 h) von vier Referenzkurven: Ruhetag = Mittel aus {len(ref_days)} kalibrierten "
        f"Bauruhe-Messungen (27./28.06.); Bautage Mo-Fr (n={len(bau_days)}, Tier ► oder (~)) als niedrigster "
        f"Tag ({min_day['day0'].strftime('%d.%m.')}, {min_day['laeq_tag']:.1f} dB(A)), Durchschnitt und "
        f"höchster Tag ({max_day['day0'].strftime('%d.%m.')}, {max_day['laeq_tag']:.1f} dB(A)).",
        0.965,0.016,fontsize=8.3,color='#555555')

    ax.set_xlim(anchor,anchor+pd.Timedelta(hours=13)); ax.set_ylim(DAY_AXIS_DB_MIN,DAY_AXIS_DB_MAX)
    _rd=to_dt(ref_ser); _mnd=to_dt(min_ser); _avd=to_dt(avg_ser); _mxd=to_dt(max_ser)
    ax.plot(_rd.index,_rd.values,color='#228B22',lw=2.1,zorder=5,
            label=f"Ruhetag (Ø, n={len(ref_days)})")
    ax.plot(_mnd.index,_mnd.values,color='#1F77B4',lw=1.3,zorder=4,
            label=f"Min-Bautag ({min_day['day0'].strftime('%d.%m.')})")
    ax.plot(_avd.index,_avd.values,color='#E07B00',lw=1.7,zorder=4,
            label=f"Ø-Bautag (n={len(bau_days)})")
    ax.plot(_mxd.index,_mxd.values,color=C_RICHT,lw=1.3,zorder=4,
            label=f"Max-Bautag ({max_day['day0'].strftime('%d.%m.')})")

    ax.hlines([RW_TAG_WA],anchor,anchor+pd.Timedelta(hours=13),color=C_RICHT,lw=1.1,zorder=2)
    ax.hlines([EINGREIF_TAG],anchor,anchor+pd.Timedelta(hours=13),color=C_EING,lw=1.1,ls='--',zorder=2)
    ax.text(0.993,RW_TAG_WA,f"{RW_TAG_WA:.0f} RW-Tag",transform=ax.get_yaxis_transform(),zorder=10,
            va='center',ha='right',fontsize=6.5,color=C_RICHT,clip_on=True,
            bbox=dict(boxstyle='round,pad=0.15',facecolor='white',edgecolor='none',alpha=0.80))
    ax.text(0.993,EINGREIF_TAG,f"{EINGREIF_TAG:.0f} Eingreif",transform=ax.get_yaxis_transform(),zorder=10,
            va='center',ha='right',fontsize=6.5,color=C_EING,clip_on=True,
            bbox=dict(boxstyle='round,pad=0.15',facecolor='white',edgecolor='none',alpha=0.80))

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    ax.set_ylabel("Schalldruckpegel dB(A)",fontsize=8.5)
    ax.set_yticks(range(int(DAY_AXIS_DB_MIN),int(DAY_AXIS_DB_MAX)+1,10))
    ax.tick_params(labelsize=7.5)
    ax.grid(True,ls=':',color='#CCCCCC',alpha=0.6)
    for sp in ('top','right'): ax.spines[sp].set_visible(False)
    ax.legend(loc='upper left',fontsize=8,framealpha=0.9)

    y=0.235
    y=fig_text_wrap(fig,0.06,y,
        "Methodik: Alle Kurven sind LAeq-1-Minuten-Verläufe, auf die Uhrzeit (unabhängig vom Kalenderdatum) "
        "ausgerichtet -- echte Messlücken bleiben als Lücke in der Kurve sichtbar, keine Interpolation. "
        "Durchschnitt = punktweises Mittel je Uhrzeit über alle einbezogenen Bautage (nur Tier ► oder (~), "
        "Fenster-Tage (M) ausgeschlossen wegen geringer Aussagekraft). Min-/Max-Bautag sind reale, einzelne "
        "Messtage (kein Kunstwert). Ruhetag = Mittel der zwei kalibrierten Bauruhe-Messungen 27./28.06.2026.",
        0.94,0.019,fontsize=8.2,color='#444444')
    y=fig_text_wrap(fig,0.06,y,
        "Deskriptiver Vergleich, kein automatischer Kausalitätsbeweis (Wetterlagen, Verkehr u.a. können "
        "beitragen). Alle zugrunde liegenden Tages-LAeq unverändert gegenüber den Tagesseiten dieses Berichts.",
        0.94,0.019,fontsize=7.9,color='#888888',fontstyle='italic')
    fig.text(0.06,0.022,f"Seite {page_num}  ·  Vier-Kurven-Vergleich  ·  {VERSION_STR}",fontsize=7,color='#AAAAAA')

    save_page(pp,fig,"Referenzvergleich 4 Kurven")
    return True

# ============================================================
# SEITEN: TAGE OHNE KALIBRIERTE MESSUNG, NUR VIDEOBELEGE (v8, To-Do Punkt 3)
#   Keine LAeq-Kurve, keine AVV-Grenzlinien (Raeume z.T. innen/aussen gemischt,
#   kein kalibrierter Tages-Pegel berechenbar) -- rein deskriptive Punktdarstellung
#   der Handy-dB-Schaetzungen aus den Videobelegen.
# ============================================================
def _video_room_cat(ort):
    o = (ort or "").lower()
    if 'balkon' in o: return 'Außen (Balkon)'
    if 'schlaf' in o: return 'Schlafzimmer'
    if 'kind' in o: return 'Kinderzimmer'
    if 'küche' in o or 'kueche' in o: return 'Küche'
    return 'Sonstige/unklar'

_VIDEO_CAT_COLOR = {'Außen (Balkon)':'#1F77B4','Schlafzimmer':'#6A3D9A','Kinderzimmer':'#E07B00',
                     'Küche':'#2E8B57','Sonstige/unklar':'#888888'}

def page_video_only_day(pp, day, day_videos, page_num):
    """Tag mit ausschließlich Videobelegen, keine kalibrierte PCE-Messung vorhanden."""
    day0 = datetime.date.fromisoformat(day)
    wd = WOCHENTAGE[day0.weekday()]
    fig = plt.figure(figsize=(11.69,8.27))
    ax = fig.add_axes([0.075,0.42,0.895,0.48])

    fig.text(0.055,0.965,
              f"{day0.strftime('%d.%m.%Y')} ({wd})  —  Nur Videobelege (keine komplette Tagesmessung)",
              fontsize=13,fontweight='bold',color=C_TITLE)
    fig_text_wrap(fig,0.055,0.935,
        f"{len(day_videos)} Video(s) an diesem Tag — Handy-Schätzungen aus verschiedenen Räumen/Positionen. "
        "Kein Tages-LAeq, kein AVV-Grenzwertvergleich möglich (unkalibriert, teils Innenraum §287 ZPO, "
        "teils Außenposition).",
        0.965,0.017,fontsize=8.2,color='#666666')

    t7=pd.Timestamp(day)+pd.Timedelta(hours=7); t20=pd.Timestamp(day)+pd.Timedelta(hours=20)
    ax.set_xlim(t7,t20); ax.set_ylim(DAY_AXIS_DB_MIN,DAY_AXIS_DB_MAX)
    ax.axvspan(t7,t20,facecolor='#DBDBDB',alpha=0.55,hatch='////',edgecolor='#BFBFBF',
               linewidth=0.0,zorder=0,label="keine kalibrierte Messung")

    seen_cats=set()
    for v in day_videos:
        try: hh,mm=map(int,v['time'].split(':'))
        except Exception: continue
        vdt=pd.Timestamp(day)+pd.Timedelta(hours=hh,minutes=mm)
        seg=(v.get('dba') or '').split(' / ')[0]
        nums=[float(x) for x in re.findall(r'\d+(?:\.\d+)?',seg)]
        if not nums: continue
        lo,hi=min(nums),max(nums); mid=(lo+hi)/2
        cat=_video_room_cat(v['ort']); col=_VIDEO_CAT_COLOR.get(cat,'#888888')
        lbl=cat if cat not in seen_cats else None; seen_cats.add(cat)
        if hi>lo:
            ax.errorbar([vdt],[mid],yerr=[[mid-lo],[hi-mid]],fmt='D',ms=5,color=col,ecolor=col,
                        elinewidth=1.2,capsize=2.5,zorder=5,clip_on=True,label=lbl)
        else:
            ax.plot([vdt],[mid],marker='D',ms=5,color=col,zorder=5,clip_on=True,label=lbl)

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    ax.set_ylabel("Video-dB-Schätzung (unkalibriert)",fontsize=8.3)
    ax.set_yticks(range(int(DAY_AXIS_DB_MIN),int(DAY_AXIS_DB_MAX)+1,10))
    ax.tick_params(labelsize=7.5)
    ax.grid(True,ls=':',color='#CCCCCC',alpha=0.6)
    for sp in ('top','right'): ax.spines[sp].set_visible(False)
    ax.legend(loc='upper right',fontsize=7.2,framealpha=0.9,ncol=2,handlelength=1.3)
    ax.text(0.012,0.03,
            "Grau schraffiert = keine kalibrierte Messung an diesem Tag. Punkte/Fehlerbalken = "
            "Handy-Video-Schätzung je Zeitpunkt, keine Dauermessung.",
            transform=ax.transAxes,fontsize=6.2,color='#333333',
            bbox=dict(boxstyle='round,pad=0.22',fc='white',ec='#CCCCCC',alpha=0.88),zorder=12)

    # Kompakte Liste (3 Spalten) darunter
    y0=0.335
    fig.text(0.055,y0,f"VIDEOBELEGE ({len(day_videos)})  —  Zeit · Ort · Video-dB",
              fontsize=9,fontweight='bold',color='#333333')
    col_x=[0.055,0.375,0.695]; per_col=max(1,-(-len(day_videos)//3))
    yy=[y0-0.028]*3
    for idx,v in enumerate(day_videos):
        ci=min(idx//per_col,2)
        dba_txt=f"{v['dba']} dB" if v.get('dba') else "—"
        txt=f"{v['time']} {v['ort'][:26]}: {dba_txt}"
        yy[ci]=fig_text_wrap(fig,col_x[ci],yy[ci],txt,col_x[ci]+0.305,0.0135,
                              fontsize=6.5,color='#2E5A8A',min_y=0.045)
    fig.text(0.055,0.032,
        "Video-dB = Handy-Schätzung, keine kalibrierte PCE-Messung. Kein Tages-LAeq, kein AVV-Vergleich "
        "möglich — nur punktuelle Anhaltswerte.",
        fontsize=6.8,color='#999999',fontstyle='italic')
    fig_text_wrap(fig,0.055,0.018,f"Seite {page_num}  ·  {day} (nur Video)  ·  {VERSION_STR}",
              0.965,0.013,fontsize=7,color='#AAAAAA')

    save_page(pp,fig,f"VideoOnly {day}", do_check=True)

# ============================================================
# SEITE (Ende): VIDEOBELEGE — GESAMTLISTE (v3 Punkt 3)
# ============================================================
def page_videoliste(pp, all_data=None):
    measured_days = set(d['day'] for d in all_data) if all_data is not None else set(DAYS_ALL)
    lines=[]
    for v in VIDEOS:
        is_m = v['day'] in measured_days
        is_luecke = (v['day']=="2026-06-12")          # B6: Lückenbeleg ohne CSV-Messtag
        dd = v['day'][8:10]+"."+v['day'][5:7]+"."
        dba = (v['dba']+" dB") if v['dba'] else "—"
        sub = " [unkl.]" if v['sub'] else ""
        ort = (v['ort'][:20]) if len(v['ort'])>20 else v['ort']
        zu = "CSV" if is_m else ("Lücke" if is_luecke else "Video")
        kind = 'm' if is_m else ('l' if is_luecke else 'o')
        lines.append((f"{dd} {v['time']}  {ort:<20}{sub}", f"{dba:>8}  {zu}", kind))

    KCOL={'m':'#1F4E79','l':'#002060','o':'#6A3D9A'}
    per_col=36; per_page=per_col*3
    cols=[(0.06,0.0),(0.375,0.0),(0.69,0.0)]
    page_count=max(1,int(np.ceil(len(lines)/per_page)))
    n_csv=sum(1 for v in VIDEOS if v['day'] in measured_days)
    n_only=len(VIDEOS)-n_csv

    for pageno,start in enumerate(range(0,max(1,len(lines)),per_page),start=1):
        chunk=lines[start:start+per_page]
        fig=plt.figure(figsize=(14,10.5))
        ax=fig.add_axes([0,0,1,1]); ax.axis('off')
        ax.add_patch(Rectangle((0,0.92),1,0.08,color=C_TITLE,transform=ax.transAxes))
        suffix=f" ({pageno}/{page_count})" if page_count>1 else ""
        fig.text(0.06,0.951,"VIDEOBELEGE — GESAMTLISTE"+suffix,
                 color='white',fontsize=14,fontweight='bold')
        fig.text(0.06,0.905,
                 f"{len(VIDEOS)} Videos im Ordner Fotos_Videos/Schallmessvideos: "
                 f"{n_csv} mit CSV-Messtag, {n_only} Video-only-Belege ohne parallele CSV-Schallmessung.",
                 fontsize=8.5,color='#555555')
        fig.text(0.06,0.886,
                 "Video-only wird als Beleg zugelassen, aber nicht als kalibrierte PCE-Messung bewertet; "
                 "Handy-dB aus Dateinamen nur orientierend.",
                 fontsize=7.9,color='#444444')
        fig.text(0.06,0.868,
                 "Farben: Blau = CSV-Messtag/Tagesgrafik · Violett = Video-only · Dunkelblau = Lückenbeleg. "
                 "Format: Datum · Uhrzeit · Ort · dB(A) · Belegart.",
                 fontsize=7.5,color='#666666')

        y0=0.844; lh=0.0205
        for idx,(txt,tail,kind) in enumerate(chunk):
            c=idx//per_col
            x=cols[c][0]; yy=y0-(idx%per_col)*lh
            col=KCOL.get(kind,'#999999'); fw='bold' if kind=='l' else 'normal'
            fig.text(x, yy, txt, fontsize=6.7, family='DejaVu Sans Mono', color=col, fontweight=fw)
            fig.text(x+0.220, yy, tail, fontsize=6.6, family='DejaVu Sans Mono', color=col, fontweight=fw)

        fig.text(0.06,0.025,
                 f"{VERSION_STR}  |  Videoliste {pageno}/{page_count}  |  CSV-Messtag: {n_csv} · Video-only: {n_only}",
                 fontsize=7.5,color='#999999')
        save_page(pp,fig,f"Videoliste {pageno}", do_check=False)
    return page_count

# ============================================================
# SEITEN 5+: MESSTAGE — v2 Layout (A, D behoben)
# Kein Text über Chart-Achse; Chart-Bottom ≥ 0.385 für x-Tick-Raum;
# Grenzwert-Labels inside Achse (ha='right'); rechte Spalte: Mono-Text, kein nested ax
# Innenraum: keine AVV-Grenzlinien (D)
# ============================================================
def page_day(pp, d, page_num):
    day=d['day']; day0=d['day0']; wd=d['wd']
    dba=d['dba']; laeq1m=d['laeq1m']
    t=d['t']; t7=d['t7']; t20=d['t20']; sessions=d['sessions']
    BIN=d['BIN']

    fig=plt.figure(figsize=(14,10.5))

    # ---- Kopfzeile (figure coords, über Chart-Bereich) ----
    tier_sym={"valid":"►","partial":"(~)","window":"(M)"}.get(d['tier'],"?")
    if d['indoor']:
        bewert_txt="Innenraum — Orientierungswert §287 ZPO (kein AVV-Grenzwert)"
    else:
        dr=d['laeq_tag']-RW_TAG_WA if not np.isnan(d['laeq_tag']) else float('nan')
        bewert_txt=(f"+{dr:.1f} dB über AVV-RW {RW_TAG_WA:.0f} dB(A)" if not np.isnan(dr) and dr>0
                    else f"AVV-RW {RW_TAG_WA:.0f} eingehalten" if not np.isnan(dr) else "")

    art_txt='Innenraum' if d['indoor'] else 'Außenmessung'
    pos_txt=d['position'].strip()
    # Redundanz vermeiden: wenn Position = Mess-Art (z.B. "Innenraum"), nicht doppeln
    loc_txt=art_txt if (not pos_txt or pos_txt.lower()==art_txt.lower()) else f"{art_txt}  ·  {pos_txt}"
    fig.text(0.055,0.962,
        f"{day0.strftime('%d.%m.%Y')} ({wd})  —  {loc_txt}",
        fontsize=13,fontweight='bold',color=C_TITLE)
    fig.text(0.055,0.937,
        f"Messung: {d['mess_txt']}  ·  Abdeckungsstufe: {tier_sym} {d['abd_tag']*100:.0f}%  ·  "
        f"LAeq: {fmt(d['laeq_tag'])} dB(A)  {bewert_txt}",
        fontsize=9.5,color='#444444')

    # Separator Kopf / Chart
    fig.add_artist(mlines.Line2D([0.055,0.970],[0.928,0.928],
                                  color='#CCCCCC',lw=0.7,transform=fig.transFigure))

    # ---- Chart-Axes
    # A: Bottom=0.388 lässt ~0.025 Spielraum für x-Tick-Labels bis Textbereich (TY=0.355)
    # A: Right=0.940 → Grenzwert-Labels inside Axes (ha='right', x=0.993)
    ax=fig.add_axes([0.055, 0.388, 0.885, 0.530])

    # H: EINHEITLICHE y-Achse über ALLE Diagramme (innen wie außen) für Vergleichbarkeit
    # 25-105 dB(A): Referenz-/Ruhetage bleiben vergleichbar, Überschreitungen wirken nicht künstlich heruntergezogen.
    YLO, YMAX = DAY_AXIS_DB_MIN, DAY_AXIS_DB_MAX
    ax.set_ylim(YLO,YMAX)

    # v3: Tagzeitraum 07–20h. Nacht NUR am Ausnahmetag (24.06., Post-20:00, Videobeleg).
    is_night_day = (day == NACHT_AUSNAHME_TAG)
    tmin_p=t[0]; tmax_p=t[-1]
    x_left  = t7                                        # Vor-07h immer raus
    x_right = (max(t20, tmax_p) if is_night_day else t20)
    ax.set_xlim(x_left, x_right)

    # Nacht-Hintergrund nur am Ausnahmetag (Post-20:00)
    if is_night_day and tmax_p > t20:
        ax.axvspan(t20, min(tmax_p, x_right), color=C_NACHT_BG, alpha=0.6, zorder=0)

    # B: nicht gemessene Zeit GRAU SCHRAFFIEREN (statt weiß) — Messfenster sichtbar
    cov = list(sessions_in_tag(d['sessions'], day0))     # gemessene Intervalle in [t7,t20]
    if is_night_day and tmax_p > t20:
        cov.append((t20, min(tmax_p, x_right)))
    cov.sort()
    _cursor = x_left; _nogap_lbl = False
    def _hatch(a, b):
        nonlocal _nogap_lbl
        if b <= a: return
        ax.axvspan(a, b, facecolor='#DBDBDB', alpha=0.55, hatch='////',
                   edgecolor='#BFBFBF', linewidth=0.0, zorder=0,
                   label=(None if _nogap_lbl else "nicht gemessen"))
        _nogap_lbl = True
    for _a,_b in cov:
        _a=max(_a,x_left); _b=min(_b,x_right)
        if _a > _cursor: _hatch(_cursor, _a)
        _cursor=max(_cursor,_b)
    if _cursor < x_right: _hatch(_cursor, x_right)

    # Bin-Flächen (nur sichtbarer Bereich x_left..x_right)
    mstart=max(sessions[0][0], x_left); mend=min(sessions[-1][1], x_right)
    bstart=day0+pd.Timedelta(minutes=BIN*int(((mstart-day0).total_seconds())//(BIN*60)))
    def bin_in_tb(bs,be):
        return any(bs<b0 and be>a0 for(a0,b0,_,_) in d['tb_spans'])
    b=bstart
    while b<=mend:
        bend=b+pd.Timedelta(minutes=BIN)
        dom=d['bin_dom'].get(b)
        psrc=d['phase_src_at'](b+pd.Timedelta(minutes=BIN/2))
        m=(t>=b)&(t<bend)
        if m.any():
            bv=laeq1m[m].dropna()
            brep=float(bv.mean()) if len(bv) else float('nan')
            _ei=55.0 if d['indoor'] else EINGREIF_TAG
            if dom:
                col,al=src_color(dom),0.60
            elif psrc and psrc in BAU_RELEVANT:
                col,al=src_color(psrc),0.60
            elif bin_in_tb(b,bend):
                col,al=SRC_COLOR["Bohrgeraet/schweres Geraet"],0.60
            elif psrc or b in d['bin_any']:
                col,al=(NODATA_COLOR,0.30) if(not np.isnan(brep)and brep>_ei) else(NONBAU_COLOR,0.50)
            else:
                col,al=NODATA_COLOR,0.30
            ax.fill_between(t[m],YLO,laeq1m[m].values,color=col,alpha=al,lw=0,zorder=1)
        b=bend

    # Signallinien
    ax.plot(t,dba,color=C_MOM,lw=0.4,zorder=2,label="$L_{AF}$ 1s")
    ax.plot(t,laeq1m,color=C_LAEQ,lw=1.2,zorder=4,label="LAeq 1min gleit.")

    # Grenzwerte: Außen → AVV-Tag-Linien (07–20h); Innen → KEINE AVV-Linien
    if not d['indoor']:
        x_tag_r = min(t20, x_right)
        ax.hlines([RW_TAG_WA], x_left, x_tag_r, color=C_RICHT, lw=1.5, zorder=5)
        ax.hlines([EINGREIF_TAG], x_left, x_tag_r, color=C_EING, lw=1.5, ls='--', zorder=5)
        ax.text(0.993,RW_TAG_WA, f"{RW_TAG_WA:.0f} RW-Tag",
                transform=ax.get_yaxis_transform(),zorder=10,
                va='center',ha='right',fontsize=6.5,color=C_RICHT,clip_on=True,
                bbox=dict(boxstyle='round,pad=0.15',facecolor='white',edgecolor='none',alpha=0.80))
        ax.text(0.993,EINGREIF_TAG, f"{EINGREIF_TAG:.0f} Eingreif",
                transform=ax.get_yaxis_transform(),zorder=10,
                va='center',ha='right',fontsize=6.5,color=C_EING,clip_on=True,
                bbox=dict(boxstyle='round,pad=0.15',facecolor='white',edgecolor='none',alpha=0.80))
        # Nacht-Grenzwerte NUR am Ausnahmetag im Post-20:00-Bereich
        if is_night_day and x_right > t20:
            ax.axvline(t20, color='#999999', lw=0.8, ls=':', zorder=4)
            ax.hlines([RW_NACHT_WA], t20, x_right, color=C_RICHT, lw=1.5, zorder=5)
            ax.hlines([EINGREIF_NACHT], t20, x_right, color=C_EING, lw=1.5, ls='--', zorder=5)
            ax.text(0.993,RW_NACHT_WA, f"{RW_NACHT_WA:.0f} RW-N",
                    transform=ax.get_yaxis_transform(),zorder=10,
                    va='bottom',ha='right',fontsize=6,color=C_RICHT,clip_on=True,
                    bbox=dict(boxstyle='round,pad=0.15',facecolor='white',edgecolor='none',alpha=0.80))
            ax.text(0.993,EINGREIF_NACHT, f"{EINGREIF_NACHT:.0f} Eingreif-N",
                    transform=ax.get_yaxis_transform(),zorder=10,
                    va='bottom',ha='right',fontsize=6,color=C_EING,clip_on=True,
                    bbox=dict(boxstyle='round,pad=0.15',facecolor='white',edgecolor='none',alpha=0.80))

    # Lauteste Stunde (nur wenn im sichtbaren Tagzeitraum)
    if not np.isnan(d['lh_val']) and d['lh_it'] is not None:
        lh_mark=d['lh_it']-pd.Timedelta(minutes=30)
        if x_left<=lh_mark<=x_right:
            ax.plot([lh_mark],[YMAX-2],
                    marker='v',ms=6,color='#888888',clip_on=True,zorder=6,
                    label=f"lauteste Std. {d['lh_txt']} ({fmt(d['lh_val'])} dB)")

    # Videobelege als Marker (v3 Punkt 3)
    _vid_n=0; _vid_gap_n=0
    _laeq_ser=d['laeq1m'].dropna()
    for v in VIDEOS_BY_DAY.get(day, []):
        try: hh,mm=map(int,v['time'].split(':'))
        except: continue
        vdt=day0+pd.Timedelta(hours=hh,minutes=mm)
        if x_left<=vdt<=x_right:
            ax.axvline(vdt,color="#111111",lw=0.8,alpha=0.65,zorder=6)
            ax.plot([vdt],[YMAX-1.0],marker=7,ms=7,color="#111111",clip_on=True,zorder=7)
            _vid_n+=1
            # Faellt der Video-Zeitpunkt in eine Messluecke (kein PCE-Wert < 5min entfernt)?
            # Dann Video-dB-Schaetzung selbst als Punkt/Fehlerbalken einzeichnen (v8, Punkt 3
            # der Nutzer-To-Do: Video-Werte in Luecken sichtbar machen). Keine Interpolation --
            # nur der punktuelle Handy-Schaetzwert, klar als solcher gekennzeichnet.
            _has_meas=False
            if len(_laeq_ser):
                _pos=_laeq_ser.index.get_indexer([vdt],method='nearest')[0]
                if _pos>=0 and abs((_laeq_ser.index[_pos]-vdt).total_seconds())<=300:
                    _has_meas=True
            if not _has_meas and v.get('dba'):
                _seg=v['dba'].split(' / ')[0]
                _nums=[float(x) for x in re.findall(r'\d+(?:\.\d+)?', _seg)]
                if _nums:
                    _lo,_hi=min(_nums),max(_nums)
                    _mid=(_lo+_hi)/2
                    if _hi>_lo:
                        ax.errorbar([vdt],[_mid],yerr=[[_mid-_lo],[_hi-_mid]],
                                    fmt='D',ms=5,color=C_VIDDB,ecolor=C_VIDDB,
                                    elinewidth=1.4,capsize=3,zorder=8,clip_on=True)
                    else:
                        ax.plot([vdt],[_mid],marker='D',ms=5,color=C_VIDDB,zorder=8,clip_on=True)
                    _vid_gap_n+=1
    if _vid_n:
        ax.plot([],[],marker=7,ms=7,color="#111111",lw=0.8,label=f"Videobeleg ({_vid_n})")
    if _vid_gap_n:
        ax.plot([],[],marker='D',ms=5,color=C_VIDDB,lw=1.4,
                label=f"Video-Schätzung, Lücke ({_vid_gap_n})")

    # H: Messortwechsel als vertikale Trennlinie (nur bei echtem Standortwechsel)
    _pos_row=load_pos(day); _hinweis=(_pos_row.get("Hinweis","") or "")
    if ("->" in d['position']) or ("Standortwechsel" in _hinweis):
        _wm=re.search(r'(?:Wechsel|Standortwechsel)\D{0,10}(\d{1,2}):(\d{2})', d['position']+" "+_hinweis)
        if _wm:
            _wh,_wmin=int(_wm.group(1)),int(_wm.group(2))
            if 0<=_wh<24 and 0<=_wmin<60:
                _wdt=day0+pd.Timedelta(hours=_wh,minutes=_wmin)
                if x_left<_wdt<x_right:
                    ax.axvline(_wdt,color='#6A3D9A',lw=1.1,ls=(0,(4,2)),zorder=6)
                    ax.text(_wdt,YMAX-7,f" Wechsel Messort {_wh:02d}:{_wmin:02d} ",
                            fontsize=6.6,color='#6A3D9A',ha='left',va='top',rotation=90,zorder=7,
                            clip_on=True)

    # B: Messfenster-Banner bei (M)-Tagen
    if d['tier']=='window':
        ax.text(0.5,0.93,f"Messfenster {d['abd_tag']*100:.0f}% — kein Tagesbild",
                transform=ax.transAxes,ha='center',va='center',
                fontsize=11,fontweight='bold',color=C_RICHT,
                bbox=dict(boxstyle='round,pad=0.35',fc='#FFF0F0',ec=C_RICHT,alpha=0.92),zorder=10)

    ax.set_ylabel("Schalldruckpegel dB(A)",fontsize=8.5)
    ax.set_yticks(range(int(YLO),int(YMAX)+1,5))
    ax.xaxis.set_major_locator(mdates.HourLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.tick_params(axis='x',labelsize=8); ax.tick_params(axis='y',labelsize=8)
    ax.grid(True,ls=":",color="#CCCCCC",alpha=0.6)
    for sp in("top","right"): ax.spines[sp].set_visible(False)
    ax.legend(loc='lower right',fontsize=7.5,framealpha=0.88,ncol=2,handlelength=1.5)
    ax.text(0.012,0.025,
            f"Farben/Interpretation: feste Achse {DAY_AXIS_DB_MIN:.0f}-{DAY_AXIS_DB_MAX:.0f} dB(A); "
            "grau schraffiert = nicht gemessen; blau = LAeq 1min, grau = Momentanpegel;\n"
            "farbige Flächen = Quellenhinweis; rot/orange = AVV-Schwellen; schwarze Marker = Videobelege.",
            transform=ax.transAxes,fontsize=6.3,color='#333333',
            bbox=dict(boxstyle='round,pad=0.22',fc='white',ec='#CCCCCC',alpha=0.88),
            zorder=12)
    ax._requires_graphic_explanation=True
    ax._graphic_explanation=True
    ax._graphic_label=f"Tagesgrafik {day}"

    # Separator Chart / Textbereich — unter x-Tick-Labels
    fig.add_artist(mlines.Line2D([0.055,0.970],[0.350,0.350],
                                  color='#CCCCCC',lw=0.6,transform=fig.transFigure))

    # ============================================================
    # TEXTBEREICH (y: 0.340 → 0.028)
    # Oben: VOLLBREITER Kopfblock (Headline + Erläuterung + Abweichung)
    # Unten: 3 disjunkte Spalten (Nacht | Pegel-Kennzahlen | KI-Quellen)
    # Jeder proportionale Text wird auf seine Spaltenbreite umbrochen (Teil-2-Regel 1).
    # ============================================================
    LX=0.055; MX=0.385; RX=0.700  # Spalten-Starts
    FULL_R=0.945                   # rechte Druckkante
    L_R=MX-0.015; M_R=RX-0.015; R_R=FULL_R  # Spalten-Rechtsgrenzen (mit Gutter)
    FS=8.5
    tier_col={"valid":C_TITLE,"partial":C_EING,"window":C_RICHT}.get(d['tier'],C_TITLE)

    # ---- Vollbreiter Kopfblock ----
    y=0.338
    y=fig_text_wrap(fig,LX,y,d['laeq_tag_str'],FULL_R,0.024,
                    fontsize=FS+0.5,color=tier_col,fontweight='bold')
    y-=0.004
    y=fig_text_wrap(fig,LX,y,d['laeq_supp'],FULL_R,0.020,
                    fontsize=7.8,color='#555555')
    y-=0.003
    # Konservative Volltag-Hochrechnung (Tier=window): nur wenn die Messung tatsaechlich zwischen
    # KONSERVATIV_FENSTER_START und -ENDE endet. Rein additiv, deutlich als Schaetzung markiert
    # (grau/kursiv, NICHT die Farben C_RICHT/tier_col die fuer echte Messwerte reserviert sind).
    if d.get('konservativ_aktiv'):
        y=fig_text_wrap(fig,LX,y,
            f"Zusätzliche konservative Hochrechnung (§287 ZPO, keine Messung): Da die Messung um "
            f"{d['konservativ_ende_str']} Uhr endete und bis 20:00 Uhr keine weiteren Messwerte "
            f"vorliegen, wird für diesen Zeitraum hilfsweise Einhaltung des Richtwerts "
            f"{KONSERVATIV_ANNAHME_DB:.0f} dB(A) unterstellt (denkbar ungünstigste Annahme zulasten "
            f"des Mieters). Daraus rechnerisch: LAeq Tag konservativ {d['laeq_tag_konservativ']:.1f} "
            f"dB(A) [Abdeckung {d['abd_tag_konservativ']*100:.0f}%]. Kein Messwert — nur Schätzung.",
            FULL_R,0.018,fontsize=7.3,color='#555555',fontstyle='italic')
        y-=0.003
    # NEU: "hohe Abdeckung*" (Tier=partial, >=70% echt): brueckt ALLE Luecken mit 50 dB(A).
    if d.get('hohe_abdeckung_stern'):
        y=fig_text_wrap(fig,LX,y,
            f"Einstufung 'hohe Abdeckung*' (§287 ZPO, keine Vollmessung): Real gemessen wurden "
            f"{d['abd_tag']*100:.0f}% des Bezugszeitraums (LAeq {d['laeq_tag']:.1f} dB(A)). Für die "
            f"restliche, nicht erfasste Zeit wird hilfsweise ein Pegel von {STERN_ANNAHME_DB:.0f} dB(A) "
            f"unterstellt (konservativ, unterhalb des Richtwerts {RW_TAG_WA:.0f} dB(A)). Daraus "
            f"rechnerisch: LAeq Tag* {d['laeq_tag_stern']:.1f} dB(A) [Abdeckung 100%*]. Kein Messwert "
            f"für die Restzeit — nur Schätzung.",
            FULL_R,0.018,fontsize=7.3,color='#555555',fontstyle='italic')
        y-=0.003
    # Feierabend-Hinweis NUR fuer Tage mit belegtem Bautagebuch-Arbeitsende vor Messende
    # (kein Rueckschluss "Messende = Bauende" ohne Beleg -- Guardrail keine Daten erfinden).
    if day in FEIERABEND_BELEGT:
        y=fig_text_wrap(fig,LX,y,
            f"Hinweis: Bautagebuch dokumentiert Bauarbeiten bis {FEIERABEND_BELEGT[day]} Uhr — "
            "die nicht erfasste Restzeit bis 20:00 liegt damit außerhalb der dokumentierten "
            "Arbeitszeit (kein reiner Datenausfall).",
            FULL_R,0.018,fontsize=7.3,color='#227744')
        y-=0.002
    if not np.isnan(d['laeq_tag']) and not d['indoor']:
        dr=d['laeq_tag']-RW_TAG_WA; ei=d['laeq_tag']-EINGREIF_TAG
        col_rw=C_RICHT if dr>0 else '#228B22'
        dr_s=f"+{dr:.1f}" if dr>=0 else f"{dr:.1f}"
        ei_s=(f"+{ei:.1f} dB über Eingreifschwelle {EINGREIF_TAG:.0f} dB(A)"
              if ei>0 else f"{ei:.1f} dB unter Eingreifschwelle {EINGREIF_TAG:.0f} dB(A)")
        y=fig_text_wrap(fig,LX,y,
            f"Abweichung: {dr_s} dB ggü. AVV-Richtwert {RW_TAG_WA:.0f} dB(A)  ·  {ei_s}",
            FULL_R,0.022,fontsize=FS,color=col_rw,
            fontweight='bold' if dr>0 else 'normal')
    elif d['indoor']:
        y=fig_text_wrap(fig,LX,y,
            "Innenraum-Messung: Orientierungswert nach §287 ZPO — kein AVV-Außengrenzwert "
            "anwendbar; daher keine 55/40-dB-Grenzlinien im Diagramm.",
            FULL_R,0.020,fontsize=7.8,color='#666666')

    # A1: Vergleichstag-Label (korrigiert — Bautagebuch widerspricht "ohne Bautätigkeit")
    if day in VERGLEICHSTAG_LABEL:
        y-=0.002
        y=fig_text_wrap(fig,LX,y,VERGLEICHSTAG_LABEL[day],
            FULL_R,0.019,fontsize=7.8,color='#7A4000',fontweight='bold')

    # A2: Hagel-Hinweis 11.06. (Witterungs-Methodikvorbehalt)
    if day=="2026-06-11":
        y-=0.002
        y=fig_text_wrap(fig,LX,y,
            "Hinweis: Witterungsereignis (Hagel ca. 12:30 Uhr, lt. Bautagebuch) — LAFmax mit Vorbehalt; "
            "LAeq durch kurzes Ereignis weniger beeinflusst.",
            FULL_R,0.018,fontsize=7.4,color='#A06000')

    # E: Bautagebuch-Kontextzeile (Kausalität)
    bt=BAUTAGEBUCH.get(day)
    if bt and (bt['phase'] or bt['beschr']):
        _bp=bt['phase'].strip(); _bb=bt['beschr'].strip()
        # fix f: kein Abschneiden mit "…" — bei Satz-/Wortgrenze kürzen, nie Ellipsis
        MAX_BT=220
        if len(_bb)>MAX_BT:
            cut=_bb[:MAX_BT].rfind(". ")
            if cut<80: cut=_bb[:MAX_BT].rfind("; ")
            if cut<80: cut=_bb[:MAX_BT].rfind(" ")
            _bb=_bb[:cut+1].rstrip() if cut>0 else _bb[:MAX_BT].rstrip()
        bt_txt=f"Bautagebuch {day0.strftime('%d.%m.')}: " + _bp + ((" — " if _bp and _bb else "")+_bb)
        y=fig_text_wrap(fig,LX,y,bt_txt,FULL_R,0.018,fontsize=7.5,color='#444444')
    else:
        # fix g/v6: 26.06. kein gesonderter Eintrag im Berichtstext; neutral formuliert
        if day=="2026-06-26":
            y=fig_text_wrap(fig,LX,y,
                f"Bautagebuch {day0.strftime('%d.%m.')}: kein gesonderter Eintrag im Berichtstext "
                "ausgewertet; Pegelverlauf und Messparameter siehe Tagesgrafik.",
                FULL_R,0.018,fontsize=7.5,color='#777777')
        else:
            y=fig_text_wrap(fig,LX,y,f"Bautagebuch {day0.strftime('%d.%m.')}: kein Eintrag",
                            FULL_R,0.018,fontsize=7.5,color='#999999')

    # Separator + gemeinsame Spalten-Oberkante
    y-=0.008
    fig.add_artist(mlines.Line2D([LX,FULL_R],[y,y],color='#E0E0E0',lw=0.5,
                                  transform=fig.transFigure))
    y-=0.020
    col_top=y

    # ---- LINKE Spalte: Videobelege (+ 24.06. Post-20:00-Ausnahme) ----
    yl=col_top
    day_vids=VIDEOS_BY_DAY.get(day,[])
    def _meas_at(hh,mm):
        vdt=day0+pd.Timedelta(hours=hh,minutes=mm)
        try:
            ser=d['laeq1m'].dropna()
            if len(ser)==0: return float('nan')
            pos=ser.index.get_indexer([vdt],method='nearest')[0]
            if pos<0 or abs((ser.index[pos]-vdt).total_seconds())>300: return float('nan')
            return float(ser.iloc[pos])
        except Exception:
            return float('nan')
    if day_vids:
        fig.text(LX,yl,f"VIDEOBELEGE ({len(day_vids)})  —  Zeit · Ort · Video-dB vs. Messung",
                 fontsize=8.0,fontweight='bold',color='#333333'); yl-=0.022
        for v in day_vids[:7]:
            try: _hh,_mm=map(int,v['time'].split(':'))
            except: _hh,_mm=0,0
            mval=_meas_at(_hh,_mm)
            dba_txt=f"Video {v['dba']} dB" if v['dba'] else "Video —"
            mtxt=f"Mess. {mval:.0f}" if not np.isnan(mval) else "Mess. —"
            sub=" [unklass.]" if v['sub'] else ""
            yl=fig_text_wrap(fig,LX,yl,f"{v['time']} {v['ort']}: {dba_txt} | {mtxt}{sub}",
                             L_R,0.019,fontsize=7.4,color='#2E5A8A')
        if len(day_vids)>7:
            yl=fig_text_wrap(fig,LX,yl,f"… +{len(day_vids)-7} weitere (s. Videoliste)",
                             L_R,0.018,fontsize=7.3,color='#888888')
        yl=fig_text_wrap(fig,LX,yl,
            "Video-dB = Handy-Schätzung (andere Position) — Abweichung zur Messung erwartbar.",
            L_R,0.017,fontsize=6.9,color='#999999',fontstyle='italic')
    else:
        fig.text(LX,yl,"VIDEOBELEGE: keine für diesen Tag",
                 fontsize=7.8,color='#999999'); yl-=0.020

    # 24.06.: Bautätigkeit im Nachtzeitraum ab 20:00 (Ausnahme — Video/Bautagebuch)
    if is_night_day and d['na20_sec']>0 and not np.isnan(d['laeq_na20']):
        yl-=0.008
        fig.text(LX,yl,"NACHTZEITRAUM ab 20:00  (RW-N 40 / Eingreif-N 45 dB(A))",
                 fontsize=8.0,fontweight='bold',color='#333333'); yl-=0.022
        dv=d['laeq_na20']-RW_NACHT_WA; cn=C_RICHT if dv>0 else '#228B22'
        yl=fig_text_wrap(fig,LX,yl,
           f"ab 20:00 ({fmt_dur(d['na20_sec'])}): LAeq {fmt(d['laeq_na20'])} / max {fmt(d['lmax_a20'])} dB(A) "
           f"[{'+' if dv>=0 else ''}{dv:.1f} ggü. RW-N]",
           L_R,0.020,fontsize=7.8,color=cn)
        if d['lmax_a20']>NACHT_SPITZE:
            yl=fig_text_wrap(fig,LX+0.008,yl,
               f"! LAFmax > Nacht-Spitze {NACHT_SPITZE:.0f} dB(A)",
               L_R,0.018,fontsize=7.3,color=C_RICHT)
        yl=fig_text_wrap(fig,LX+0.008,yl,
           "Bautätigkeit dokumentiert (Video / Bautagebuch)",
           L_R,0.018,fontsize=7.3,color='#7A4000',fontweight='bold')

    # ---- MITTLERE Spalte: Pegel-Kennzahlen ----
    ym=col_top
    ym=fig_text_wrap(fig,MX,ym,"PEGEL-KENNZAHLEN (Tag 07–20 h)",M_R,0.020,
                     fontsize=8.0,fontweight='bold',color='#333333')
    lmax_col=C_RICHT if (not np.isnan(d['hoechst']) and d['hoechst']>NACHT_SPITZE) else '#222222'
    ym=fig_text_wrap(fig,MX,ym,f"LAFmax (1s Fast): {fmt(d['hoechst'])} dB(A)",
                     M_R,0.0155,fontsize=7.9,color=lmax_col,fontweight='bold')
    ym=fig_text_wrap(fig,MX,ym,f"L₁ (99%-Perzentil): {fmt(d['l1_tag'])} dB(A)",
                     M_R,0.0155,fontsize=7.9,color='#222222')
    if not np.isnan(d['lh_val']):
        ym=fig_text_wrap(fig,MX,ym,
            f"Lauteste Std. {d['lh_txt']}: LAeq {fmt(d['lh_val'])} dB(A) [{d['lh_cov']}/3600s]",
            M_R,0.0155,fontsize=7.9,color='#222222')
    if not d['indoor']:
        # AVV-Schwellen-Kennzahlen nur außen (gelten innen nicht — v3 Punkt 6)
        ym=fig_text_wrap(fig,MX,ym,
            f"Zeit > 55 dB(A): {d['pct_thr']:.0f}%  "
            f"({d['n_above_sec']//60}min {d['n_above_sec']%60}s / {d['mess_min']}min)",
            M_R,0.0155,fontsize=7.9,color='#222222')
        ph_n=len(d['ph60_10'])
        ym=fig_text_wrap(fig,MX,ym,
            f"Dauerlärm ≥60dB/≥10min: {ph_n} Phasen ({d['ph60_min']:.0f} min)",
            M_R,0.0155,fontsize=7.9,
            color=C_RICHT if ph_n>0 else '#222222',fontweight='bold' if ph_n>0 else 'normal')
        if d['tb_spans']:
            ym=fig_text_wrap(fig,MX,ym,f"Tiefbohrer/Schwerlast (≥70dB Dauer): {d['tb_min']:.0f} min",
                             M_R,0.0155,fontsize=7.9,color='#222222')
    else:
        ym=fig_text_wrap(fig,MX,ym,
            "AVV-Schwellen (55/60 dB) gelten innen nicht — §287-ZPO-Orientierung.",
            M_R,0.019,fontsize=7.4,color='#888888')
    # Messunsicherheit offen ausgewiesen (v3 Punkt 5)
    ym=fig_text_wrap(fig,MX,ym,
        f"Messunsicherheit: ±{GERAET_TOL_DB:.1f} dB(A) (Klasse 2, IEC 61672)",
        M_R,0.0145,fontsize=7.4,color='#777777')
    # J: Mikrofonhöhe je Tag (Aufbau-Regime)
    ym=fig_text_wrap(fig,MX,ym,f"Mikrofonhöhe: {mic_height(day, d['indoor'])}",
                     M_R,0.0145,fontsize=7.4,color='#777777')
    # Kalibrierung + Geraet in EINER Zeile zusammengefasst (Platz sparen) und VOR dem
    # optionalen Toleranzband platziert, damit diese Pflichtangaben nie durch Platzmangel
    # am Spaltenende entfallen (fig_text_wrap bricht still ab, siehe min_y).
    cal_txt=("Kalibrierung: 94 dB(A), PCE-SC 43; vor Messung, protokolliert" if day>="2026-06-22"
             else "Kalibrierung: Werkskalibrierung, PCE-SC 43 (94 dB(A))")
    ym=fig_text_wrap(fig,MX,ym,cal_txt,M_R,0.0145,fontsize=7.4,min_y=0.033,
                     color='#228B22' if day>="2026-06-22" else '#888888')
    ym=fig_text_wrap(fig,MX,ym,"Gerät: PCE-323, Klasse 2 (IEC 61672-1:2013)",
                     M_R,0.0145,fontsize=7.4,color='#777777',min_y=0.033)
    # Meteorologie je Messtag (DWD-Anhaltspunkt) — ebenfalls vor dem Toleranzband (Prioritaet
    # vor der optionalen Grenznah-Praezisierung unten). min_y abgesenkt, da diese Pflichtangaben
    # Vorrang haben; strict_selfcheck() bleibt das eigentliche Sicherheitsnetz gegen Randueberlauf.
    # Quellenangabe (DWD-Station) steht einmalig auf der Methodik-Seite -- hier nur die
    # Tageszeile, um Platz fuer Pflichtangaben zu sparen (siehe min_y-Prioritaet oben).
    _ml=meteo_line(day)
    if _ml:
        ym=fig_text_wrap(fig,MX,ym,_ml,M_R,0.015,fontsize=7.3,color='#5A7A9A',min_y=0.033)
    elif not d['indoor']:
        ym=fig_text_wrap(fig,MX,ym,"Wetter (DWD): noch keine Stationsdaten (Archiv-Nachlauf, typ. 3-5 Tage)",
                         M_R,0.015,fontsize=7.0,color='#999999',min_y=0.033)
    # G: Toleranzband bei grenznahen Außentagen (LAeq nahe RW 55 / Eingreif 60) — niedrigste
    # Prioritaet: optionale Praezisierung, faellt bei Platzmangel als erstes weg.
    if (not d['indoor']) and not np.isnan(d['laeq_tag']) and (
            abs(d['laeq_tag']-RW_TAG_WA)<=2.5 or abs(d['laeq_tag']-EINGREIF_TAG)<=2.5):
        _lo=d['laeq_tag']-GERAET_TOL_DB; _hi=d['laeq_tag']+GERAET_TOL_DB
        ym=fig_text_wrap(fig,MX,ym,
            f"  grenznah: LAeq-Bereich {_lo:.1f}–{_hi:.1f} dB(A) (±{GERAET_TOL_DB:.1f})",
            M_R,0.019,fontsize=7.4,color='#8B4500')

    # ---- RECHTE Spalte: Quellenverteilung (Mono, fixe Breite by design) ----
    yr=col_top
    BOTTOM_TEXT_Y=0.052
    fig.text(RX,yr,"QUELLENVERTEILUNG (10-min-Bins)",fontsize=8.0,
             fontweight='bold',color='#333333'); yr-=0.023
    sorted_src=sorted(d['src_pct'].items(),key=lambda x:-x[1]) if d['src_pct'] else []
    if sorted_src:
        for src,pct in sorted_src:
            if pct<1: continue
            if yr < BOTTOM_TEXT_Y:
                yr=BOTTOM_TEXT_Y-0.018
                break
            line=f"{disp_s(src)}{mini_bar(pct,10)} {pct:3.0f}%"
            fig.text(RX,yr,line,fontsize=7.6,family='DejaVu Sans Mono',
                     color=src_color(src)); yr-=0.019
    else:
        if yr >= BOTTOM_TEXT_Y:
            fig.text(RX,yr,"— keine Quellzuordnung",fontsize=7.8,color='#888888')
        yr-=0.020
    if d['src_stats'] and yr >= BOTTOM_TEXT_Y+0.030:
        yr-=0.005
        fig.text(RX,yr,"Quellen-Ereignisse (WAV > 60 dB):",fontsize=7.4,color='#666666')
        yr-=0.019
        for s in BAU_RELEVANT:
            st=d['src_stats'].get(s)
            if st:
                if yr < BOTTOM_TEXT_Y:
                    yr=BOTTOM_TEXT_Y-0.018
                    break
                fig.text(RX,yr,
                    f"{disp_s(s)}n={st['n']:>3} Leq{st['leq']:>4.0f} mx{st['mx']:>4.0f}",
                    fontsize=7.4,family='DejaVu Sans Mono',color='#555555'); yr-=0.018
    # Verifikationsstand (fix d): differenziert nach manuell verifiziert vs. vorläufig
    yr-=0.005
    if NO_AUDIO_MODE:
        yr=fig_text_wrap(fig,RX,yr,
            "Quellenzuordnung in dieser Berichtsversion nicht enthalten (siehe Vollversion mit WAV-/KI-Auswertung).",
            R_R,0.017,fontsize=7.0,color='#999999',fontstyle='italic')
    elif d.get('has_verified_src'):
        yr=fig_text_wrap(fig,RX,yr,
            "Quellenverteilung: vorläufig; einzelne Ereignisse manuell verifiziert (Laermquelle_geprueft).",
            R_R,0.017,fontsize=7.0,color='#228B22',fontstyle='italic')
    else:
        yr=fig_text_wrap(fig,RX,yr,
            "Vorläufige Quellenverteilung — keine manuell verifizierten Ereignisse für diesen Tag.",
            R_R,0.017,fontsize=7.0,color='#999999',fontstyle='italic')

    # Fußzeile
    fig.text(0.055,0.026,
        f"Seite {page_num}  ·  {day}  ·  Abdeckung {d['abd_tag']*100:.0f}%  ·  "
        f"Abdeckungsstufe={tier_sym}  ·  {VERSION_STR}",
        fontsize=7,color='#AAAAAA')

    save_page(pp,fig,f"Tag {day}", do_check=True)

# ============================================================
# HAUPTPROGRAMM
# ============================================================
def main():
    # Windows-Konsole (cp1252) kann Sonderzeichen (►, ₁, —) nicht drucken -> utf-8 erzwingen.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(f"Gesamtbericht-Lauf — {VERSION_STR}")
    print(f"Videobelege geladen: {len(VIDEOS)} ({sum(1 for v in VIDEOS if v['day'] in set(DAYS_ALL))} auf Messtagen)")
    print(f"Lade Daten für {len(DAYS_ALL)} Tage...")
    all_data=[]
    for day in DAYS_ALL:
        print(f"  {day}...",end=" ")
        d=compute_day(day)
        if d:
            if NO_AUDIO_MODE:
                # Quellenzuordnung vollstaendig unterdruecken (WAV-/KI-/Dauerlaerm-Auto-Herkunft);
                # Tiefbohrer-Diagrammfaerbung (tb_spans, reine dBA-Pegel-Regel) bleibt unangetastet.
                d['ev_bau']=[]; d['src_stats']={}; d['src_pct']={}
                d['bin_dom']={}; d['bin_any']=set(); d['has_verified_src']=False
                d['phase_src_at']=lambda ts: None
            all_data.append(d); print(f"OK (Tier={d['tier']}, Abdeckung={d['abd_tag']*100:.0f}%)")
        else: print("KEINE CSV-DATEN")

    video_csv, video_rows = write_video_audit(all_data)
    n_video_visible = sum(1 for r in video_rows if r["In_Tagesgrafik"] == "ja")
    n_video_unparsed = sum(1 for r in video_rows if r["Erkannt"] == "nein")
    n_video_only = sum(1 for r in video_rows if r["Erkannt"] == "ja" and r["Belegart"] == "Videobeleg ohne parallele CSV-Schallmessung")
    print(f"Videobeleg-Pruefung: {n_video_visible} sichtbar in Tagesgrafiken, "
          f"{n_video_only} Video-only-Belege, {n_video_unparsed} nicht erkannt → {video_csv}")

    print("\nRohdaten-Manifest (SHA-256 über CSV + WAV-ZIP je Tag)...")
    manifest=build_manifest(all_data)
    man_csv=os.path.join(OUTDIR, MANIFEST_CSV_NAME)
    with open(man_csv,"w",newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=["Datum","Typ","Datei","Bytes","SHA256"],delimiter=";")
        w.writeheader(); w.writerows(manifest)
    print(f"  {len(manifest)} Dateien gehasht → {man_csv}")

    print(f"\nGeneriere PDF: {OUT_PDF}")
    pp=PdfPages(OUT_PDF)

    page_cover(pp,all_data);       print("  S.1 Deckblatt: OK")
    page_juristische_kurzfassung(pp,all_data); print("  S.2 Juristische Kurzfassung: OK")
    page_legal(pp);                print("  S.3-4 Rechtliche Einordnung: OK")
    page_berechnung_kennwerte(pp); print("  S.5 Berechnung der Kennwerte: OK")
    page_messaufbau(pp,all_data);  print("  S.6 Messaufbau-Dokumentation: OK")
    page_lageplan(pp);             print("  S.7 Lageplan / Geometrie: OK")
    page_kernbefunde(pp,all_data); print("  S.8 Kernbefunde (B1): OK")
    page_belastungsdauer(pp,all_data); print("  S.9 Belastungsdauer (B2): OK")
    page_lauteste_stunde(pp,all_data); print("  S.10 Lauteste Stunde (B3): OK")
    page_innenraum(pp);            print("  S.11 Innenraumbetroffenheit (B4): OK")
    page_referenzpegel(pp);        print("  S.12 Referenzpegel 30.05. (B5): OK")
    page_referenzpegel_aussen(pp,all_data); print("  S.13 Referenzpegel Außen (27./28.06.): OK")
    _has_vergleich=page_referenz_vergleich(pp,all_data,14)
    print("  S.14 Vier-Kurven-Vergleich: OK" if _has_vergleich else "  S.14 Vier-Kurven-Vergleich: uebersprungen (zu wenig Daten)")
    _np_base=15 if _has_vergleich else 14
    page_summary(pp,all_data);     print(f"  S.{_np_base} Tagesübersicht: OK")

    for i,d in enumerate(all_data,start=_np_base+1):
        page_day(pp,d,i)
        print(f"  S.{i} {d['day']}: OK" + (" [INDOOR]" if d['indoor'] else ""))

    # Tage mit ausschließlich Videobelegen (keine kalibrierte CSV/WAV-Messung), z.B. 27.-30.05.
    # (v8, To-Do Punkt 3: Video-Werte statt komplettem Auslassen sichtbar machen)
    _measured=set(DAYS_ALL)
    _video_only_days=sorted(set(v['day'] for v in VIDEOS if v['day'] not in _measured))
    _np_vo=_np_base+1+len(all_data)
    for j,vday in enumerate(_video_only_days):
        vdays_for_day=[v for v in VIDEOS if v['day']==vday]
        page_video_only_day(pp,vday,vdays_for_day,_np_vo+j)
        print(f"  S.{_np_vo+j} {vday}: OK [NUR VIDEO, {len(vdays_for_day)} Belege]")

    _np_vid=_np_vo+len(_video_only_days)
    _vid_pages=page_videoliste(pp,all_data)
    if _vid_pages==1:
        print(f"  S.{_np_vid} Videoliste: OK")
    else:
        print(f"  S.{_np_vid}-{_np_vid+_vid_pages-1} Videoliste: OK")
    page_manifest(pp,manifest);  print(f"  S.{_np_vid+_vid_pages} Rohdaten-Manifest: OK")

    pp.close()

    print(f"\nFertig: {OUT_PDF}")
    n_err=len(REVIEW_LOG); n_total=sum(len(e['errors']) for e in REVIEW_LOG)
    total_pages=_np_base+len(all_data)+len(_video_only_days)+_vid_pages+1
    if n_err==0:
        print(f"Selbst-Check: alle {total_pages} Seiten OK — kein Overlap, kein Randüberlauf.")
    else:
        print(f"Selbst-Check: {n_err} Seiten mit Problemen ({n_total} Einzeltreffer):")
        for entry in REVIEW_LOG:
            print(f"  {entry['page']}:")
            for e in entry['errors']: print(f"    • {e}")

    print(f"\n=== KURZNOTIZ ({VERSION_STR}) ===")
    print(f"Automatisierte Tagesliste aus {os.path.basename(CONFIG_PATH)}; fruehere Versionen bleiben unangetastet.")
    print("Manifest beruecksichtigt CSVs und alle per Masterindex zugeordneten WAV-ZIPs.")
    print("GUARDRAIL: Berechnungsformeln unveraendert gegenueber v7; nur Orchestrierung/Manifest/Ausgabeversion aendern sich je Version.")

    if ABORT_FLAG[0]:
        # PDF bleibt erhalten (zur Durchsicht) -- aber der Fehlerzustand darf nie stillschweigend
        # untergehen: Datei sichtbar markieren + nicht-0-Exitcode, damit auto_pipeline_v8.py
        # (run_step mit required=True) den Autolauf sichtbar stoppt statt es nur zu loggen.
        _base,_ext=os.path.splitext(OUT_PDF)
        _flagged=f"{_base}_SELFCHECK-FEHLER{_ext}"
        try:
            if os.path.exists(_flagged): os.remove(_flagged)
            shutil.copy2(OUT_PDF,_flagged)
            print(f"\n[FEHLER] Selbst-Check fehlgeschlagen -- PDF zusaetzlich markiert unter:\n  {_flagged}")
        except Exception as _e:
            print(f"\n[FEHLER] Selbst-Check fehlgeschlagen -- Markierungskopie fehlgeschlagen: {_e}")
        print(f"[FEHLER] {n_err} Seite(n) mit {n_total} Problem(en) -- siehe Log oben. Abbruch mit Exitcode 1.")
        sys.exit(1)

if __name__=="__main__":
    main()
