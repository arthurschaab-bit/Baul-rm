# -*- coding: utf-8 -*-
"""
04_excel.py
Baut die Auswertungs-Arbeitsmappe Verknuepfung/Laermquellen_Verknuepfung.xlsx aus
relevante_ereignisse.csv und episoden.csv:
  - Blatt 'Anleitung'
  - Blatt 'Episoden'   (Haupt-Pruefflaeche; je Laermphase 1 Zeile, Link zum lautesten Clip)
  - Blatt 'Ereignisse' (jedes laute Ereignis, Link zum WAV)
Spalte 'Laermquelle_geprueft' mit Dropdown zum manuellen Korrigieren.
"""
import os, csv
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter

VK = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(VK, "Laermquellen_Verknuepfung.xlsx")

QUELLEN = ["Bagger","Bohrgeraet/schweres Geraet","Motor/Diesel","Schlagen/Bohren",
           "Saege","Fahrzeug","Signal/Warnton","Sprache","Umgebung/Sonstiges","unklar"]

def read_csv(name):
    with open(os.path.join(VK, name), newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

ev = read_csv("relevante_ereignisse.csv")
ep = read_csv("episoden.csv")
dl = read_csv("dauerlaerm.csv") if os.path.exists(os.path.join(VK, "dauerlaerm.csv")) else []

HEAD = Font(bold=True, color="FFFFFF")
HFILL = PatternFill("solid", fgColor="305496")
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
GEPRUEFT_FILL = PatternFill("solid", fgColor="FFF2CC")

wb = openpyxl.Workbook()

# ---------- Anleitung ----------
ws = wb.active; ws.title = "Anleitung"
lines = [
    ("Lärmquellen-Verknüpfung – WAV ↔ Schallmessung", 16, True),
    ("", 11, False),
    ("Diese Mappe verbindet die getriggerten WAV-Aufnahmen mit der dBA-Dauermessung", 11, False),
    ("für die LAUTEN/relevanten Ereignisse (Pegelspitzen).", 11, False),
    ("", 11, False),
    ("Blätter:", 12, True),
    ("• Dauerlaerm – lang anhaltende Lärmphasen aus der dBA-Dauermessung", 11, False),
    ("              (≥60dB/≥10min, ≥65dB/≥5min, ≥70dB/≥3min). Das WICHTIGSTE fürs Protokoll.", 11, False),
    ("• Episoden  – zusammenhängende Phasen der lauten Einzel-Ereignisse (≥75 dB).", 11, False),
    ("• Ereignisse – jedes einzelne laute Ereignis (≥75 dB) mit WAV-Link + KI-Quelle.", 11, False),
    ("• Pruefen_NichtBaulaerm – laute Ereignisse, die NICHT als Baulärm erkannt wurden", 11, False),
    ("              (Sprache/Umgebung) — mit Audio-Link zum Gegenhören/Korrigieren.", 11, False),
    ("", 11, False),
    ("So arbeitest du damit:", 12, True),
    ("1. 'Laermquelle_KI' = Klassifikation durch vortrainiertes Audio-Modell (PANNs/", 11, False),
    ("   AudioSet). 'KI_AudioSet' zeigt die rohen Modell-Labels; 'KI_Konfidenz' die Stärke.", 11, False),
    ("2. Clip anhören (WAV-Link), dann in 'Laermquelle_geprueft' die echte Quelle wählen", 11, False),
    ("   (Dropdown). Niedrige Konfidenz und das Blatt 'Pruefen_NichtBaulaerm' zuerst prüfen.", 11, False),
    ("3. Hinweis: Das anhaltende Dauerrumpeln des schweren Bohrgeräts erkennt AudioSet als", 11, False),
    ("   'Train/Rail' → hier als 'Bohrgeraet/schweres Geraet' gewertet (Annahme, bitte gegenhören).", 11, False),
    ("", 11, False),
    ("Hinweis: Die vollständige Verknüpfung ALLER Ereignisse (auch leise) liegt in", 11, False),
    ("master_index.csv. WAV-Dateien der lauten Ereignisse: Ordner relevante_wavs/.", 11, False),
]
for i, (txt, sz, b) in enumerate(lines, 1):
    c = ws.cell(row=i, column=1, value=txt)
    c.font = Font(size=sz, bold=b)
ws.column_dimensions["A"].width = 95

def build_sheet(title, rows, fields, link_col, link_path_col, conf_col=None, dba_col=None,
                geprueft_col="Laermquelle_geprueft", dba_min=70, dba_max=92):
    ws = wb.create_sheet(title)
    # Header
    for j, h in enumerate(fields, 1):
        c = ws.cell(row=1, column=j, value=h); c.font = HEAD; c.fill = HFILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    # Daten
    for i, r in enumerate(rows, 2):
        for j, h in enumerate(fields, 1):
            val = r.get(h, "")
            if h not in (link_col,):
                # numerisch konvertieren wo moeglich
                try:
                    if val != "" and h not in ("Datum","Uhrzeit","Start","Ende","Laermquelle_Auto",
                                               "Laermquelle_geprueft","scores","WAV"):
                        val = float(val) if ("." in str(val)) else int(val)
                except (ValueError, TypeError):
                    pass
            c = ws.cell(row=i, column=j, value=val); c.border = BORDER
            if h == link_col and r.get(link_path_col):
                c.hyperlink = r[link_path_col].replace("\\", "/")
                c.font = Font(color="0563C1", underline="single")
            if h == geprueft_col:
                c.fill = GEPRUEFT_FILL
    # Dropdown fuer geprueft-Spalte
    if geprueft_col in fields:
        gi = fields.index(geprueft_col) + 1
        dv = DataValidation(type="list", formula1='"%s"' % ",".join(QUELLEN), allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{get_column_letter(gi)}2:{get_column_letter(gi)}{len(rows)+1}")
    # Farbskalen
    n = len(rows) + 1
    if dba_col and dba_col in fields:
        ci = get_column_letter(fields.index(dba_col) + 1)
        ws.conditional_formatting.add(f"{ci}2:{ci}{n}",
            ColorScaleRule(start_type="num", start_value=dba_min, start_color="FFEB84",
                           end_type="num", end_value=dba_max, end_color="F8696B"))
    if conf_col and conf_col in fields:
        ci = get_column_letter(fields.index(conf_col) + 1)
        ws.conditional_formatting.add(f"{ci}2:{ci}{n}",
            ColorScaleRule(start_type="num", start_value=0, start_color="F8696B",
                           mid_type="num", mid_value=0.5, mid_color="FFEB84",
                           end_type="num", end_value=1, end_color="63BE7B"))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(fields))}{n}"
    # Spaltenbreiten
    for j, h in enumerate(fields, 1):
        w = {"scores": 40, "WAV": 26, "lautester_Clip": 26, "repr_Clip": 26,
             "Quellen_Detail": 38, "Kriterium": 15, "Laermquelle_Auto": 20,
             "Laermquelle_KI": 28, "KI_AudioSet": 46, "KI_Konfidenz": 11,
             "Laermquelle_Cluster": 28, "Cluster_ID": 12,
             "Cluster_Verifikation": 48, "Dauerbetrieb_Regel": 16,
             "Laermquelle_geprueft": 28}.get(h, max(9, min(16, len(h) + 3)))
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.row_dimensions[1].height = 30
    return ws

# ---------- Dauerlaerm ----------
if dl:
    dl_fields = ["Kriterium","Datum","Start","Ende","Dauer_min","Leq_dBA","Lmax_dBA","Lmin_dBA",
                 "Abdeckung_%","Anzahl_WAV","Dauerbetrieb_Regel","Laermquelle_Auto",
                 "Laermquelle_geprueft","Quellen_Detail","repr_Clip"]
    build_sheet("Dauerlaerm", dl, dl_fields, link_col="repr_Clip",
                link_path_col="repr_Clip_Pfad", dba_col="Leq_dBA", dba_min=60, dba_max=85)

# ---------- Episoden ----------  (KI-Quelle je Episode aus den Ereignissen)
from collections import defaultdict
def src_of(r): return r.get("Laermquelle_Cluster") or r.get("Laermquelle_KI") or r.get("Laermquelle_Auto", "")
ep_ki = defaultdict(lambda: defaultdict(float))
for r in ev:
    if r.get("Episode") and src_of(r):
        try: ep_ki[r["Episode"]][src_of(r)] += float(r["dBA"])
        except (ValueError, TypeError): pass
ep_ki_dom = {k: max(v, key=v.get) for k, v in ep_ki.items()}
for r in ep:
    r["Laermquelle_KI"] = ep_ki_dom.get(r.get("Episode"), "")
ep_fields = ["Episode","Datum","Start","Ende","Dauer_min","Ereignisse","dBA_Spitze","dBA_Mittel",
             "Laermquelle_KI","Laermquelle_geprueft","lautester_Clip"]
build_sheet("Episoden", ep, ep_fields, link_col="lautester_Clip",
            link_path_col="lautester_Clip_Pfad", dba_col="dBA_Spitze")

# ---------- Ereignisse ----------  (KI primär; Heuristik + Merkmale als Referenz)
ev_fields = ["Datum","Uhrzeit","dBA","Amplitude","Dauerbetrieb_Regel","Laermquelle_Cluster",
             "Cluster_ID","Cluster_Verifikation","Laermquelle_KI","KI_Konfidenz",
             "Laermquelle_geprueft","KI_AudioSet","Laermquelle_Auto","Episode","WAV",
             "centroid_Hz","dom_Hz","e_tief_<250","e_hoch_>2k","impuls_crest",
             "silbentakt","grundton","scores"]
build_sheet("Ereignisse", ev, ev_fields, link_col="WAV", link_path_col="WAV_Pfad",
            conf_col="KI_Konfidenz", dba_col="dBA")

# ---------- Pruefen_NichtBaulaerm ----------
# Laute Ereignisse (≥75 dB), die das Modell NICHT als Baulärm einstuft (Sprache/
# Umgebung) -> mit Audio-Link zum Gegenhören. Lauteste zuerst.
NICHT_BAU = {"Sprache", "Umgebung/Sonstiges"}
def _dba(r):
    try: return float(r.get("dBA") or 0)
    except (ValueError, TypeError): return 0.0
ev_nb = sorted([r for r in ev if (r.get("Laermquelle_KI") or "") in NICHT_BAU],
               key=_dba, reverse=True)
nb_fields = ["Datum","Uhrzeit","dBA","Dauerbetrieb_Regel","Laermquelle_KI","KI_Konfidenz",
             "Laermquelle_geprueft","KI_AudioSet","WAV","Episode"]
if ev_nb:
    build_sheet("Pruefen_NichtBaulaerm", ev_nb, nb_fields, link_col="WAV",
                link_path_col="WAV_Pfad", conf_col="KI_Konfidenz", dba_col="dBA")
else:
    ws_nb = wb.create_sheet("Pruefen_NichtBaulaerm")
    ws_nb["A1"] = "Keine lauten Nicht-Baulärm-Ereignisse (Sprache/Umgebung) gefunden."

wb.save(OUT)
print("gespeichert:", OUT)
print("Episoden:", len(ep), "| Ereignisse:", len(ev))
