# -*- coding: utf-8 -*-
"""
09_uebersicht.py
Tagesübergreifende Übersicht – AUSSEN und INNEN komplett getrennt.
Je Bereich: 1 Balkendiagramm (LAeq Tag + Lmax) + 1 Übersichtstabelle.
- Außen: AVV-Baulärm Richtwert 55 / Eingreifschwelle 60 dB(A).
- Innen: ASR A3.7-Orientierungswert 55 dB(A).
Tage mit unvollständiger Aufzeichnung (Teilmessung / Verbindungsverlust) werden
markiert, damit fehlende Zeiten NICHT als "ruhig" missverstanden werden.

Ausgabe: Aufbereit/<OUTPUT_PREFIX>_Uebersicht.pdf
"""
import os, csv, glob, datetime
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
from matplotlib.backends.backend_pdf import PdfPages

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VK   = os.path.join(BASE, "Verknuepfung")
MODE = os.environ.get("BEWERTUNG", "LAeq")        # LAeq = Standard (kanonisch), Lr = optional
OUTDIR = os.path.join(BASE, "Aufbereit") if MODE == "LAeq" else os.path.join(BASE, "Aufbereit", f"Bewertung_{MODE}")
os.makedirs(OUTDIR, exist_ok=True)
OUTPUT_PREFIX = os.environ.get("BAUL_RM_OUTPUT_PREFIX", "Schallmessung")
SITE_TITLE = os.environ.get("BAUL_RM_SITE_TITLE", "Messstandort")
OUT_AUSSEN = os.path.join(OUTDIR, f"{OUTPUT_PREFIX}_Uebersicht_Aussen.pdf")
OUT_INNEN  = os.path.join(OUTDIR, f"{OUTPUT_PREFIX}_Uebersicht_Innen.pdf")
MLABEL = "Lr (Beurteilungspegel)" if MODE == "Lr" else "LAeq Tag (07–20 h)"
def bewval(r):  # maßgebliche Bewertungsgröße je Modus
    return r.get("lr") if MODE == "Lr" else r.get("laeq_tag")

C_TITLE="#1F4E79"; C_RICHT="#C00000"; C_EING="#ED7D31"; C_INN="#2E75B6"
RICHTWERT, EINGREIF = 55.0, 60.0
INDOOR_REF = 55.0     # ASR A3.7-Orientierungswert
MIN_TAG_SEK = 8*3600  # < 8 h gemessen im Fenster 07-20 -> Teilmessung
wt = {0:"Mo",1:"Di",2:"Mi",3:"Do",4:"Fr",5:"Sa",6:"So"}

def load_pos():
    pos = {}
    p = os.path.join(VK, "messpositionen.csv")
    if os.path.exists(p):
        for row in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
            pos[row["Datum"]] = row
    return pos

def read_day_stats(day, indoor):
    recs = []
    for c in sorted(glob.glob(os.path.join(BASE, f"{day} *.csv"))):
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
    s = pd.Series({dt: v for dt, v in recs}).sort_index()
    s = s[~s.index.duplicated()]
    full = s.dropna()
    s = s.reindex(pd.date_range(s.index[0], s.index[-1], freq="1s"))
    hour = s.index.hour + s.index.minute/60
    tagsec = s[(hour >= 7) & (hour < 20)].dropna()
    energy_tag = 10**(tagsec/10)
    laeq_tag = 10*np.log10(energy_tag.mean()) if len(energy_tag) else float('nan')
    thr = INDOOR_REF if indoor else RICHTWERT
    # Beurteilungspegel Lr (Taktmaximal-Verfahren 5 s, DIN 45645/TA Lärm); KT=0
    v = tagsec.values; lr = ki = float('nan')
    if len(v) >= 5 and not np.isnan(laeq_tag):
        takt = v[:(len(v)//5)*5].reshape(-1, 5).max(axis=1)
        lafteq = 10*np.log10(np.mean(10**(takt/10)))
        lr = lafteq; ki = lafteq - laeq_tag
    return dict(laeq_tag=laeq_tag, lmax=float(s.max()), lr=lr, ki=ki,
                pct=100*float((tagsec > thr).mean()) if len(tagsec) else 0.0,
                n_above=int((tagsec > thr).sum()) if len(tagsec) else 0,
                n_sek=len(tagsec), thr=thr,
                von=full.index.min(), bis=full.index.max())

pos_map = load_pos()

dl_phases = {}
dlp = os.path.join(VK, "dauerlaerm.csv")
if os.path.exists(dlp):
    for row in csv.DictReader(open(dlp, encoding="utf-8-sig")):
        d = row["Datum"]; dl_phases.setdefault(d, {"gesamt":0, "ge60":0})
        dl_phases[d]["gesamt"] += 1
        if row.get("Kriterium","").startswith(">=60dB/>=10"):
            dl_phases[d]["ge60"] += 1

rows_data = []
for day in sorted(pos_map.keys()):
    pos = pos_map[day]
    indoor = pos.get("Umgebung","").strip().lower().startswith("innen")
    hw = (pos.get("Hinweis") or "")
    st = read_day_stats(day, indoor)
    if st is None:
        # Lücken-Tag: keine Eigenmessung, aber im Bautagebuch dokumentiert ("GAP:")
        if hw.startswith("GAP:"):
            rows_data.append(dict(Datum=day, indoor=indoor,
                Umgebung="Innenraum" if indoor else "Außen/Balkon",
                Position=pos.get("Position","—") or "—",
                Ph_gesamt=0, Ph_ge60=0, status="keine Aufzeichnung",
                gap_note=hw[4:].strip(),
                laeq_tag=float('nan'), lmax=float('nan'), pct=0.0, n_above=0,
                n_sek=0, thr=INDOOR_REF if indoor else RICHTWERT, von=None, bis=None))
        continue
    status = ("Störung" if hw.startswith("WARN:")
              else "Teilmessung" if st["n_sek"] < MIN_TAG_SEK else "")
    rows_data.append(dict(Datum=day, indoor=indoor,
        Umgebung="Innenraum" if indoor else "Außen/Balkon",
        Position=pos.get("Position","—") or "—",
        Ph_gesamt=dl_phases.get(day,{}).get("gesamt",0),
        Ph_ge60=dl_phases.get(day,{}).get("ge60",0),
        status=status, gap_note="", **st))

# Belege (Bautagebuch/Video) für Tage OHNE Eigenmessung -> getrennt nach Position
def load_belege():
    p = os.path.join(VK, "belege.csv"); out = {}
    if os.path.exists(p):
        for row in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
            out.setdefault(row["Datum"], []).append(row)
    return out
measured_days = {r["Datum"] for r in rows_data}
for day, items in load_belege().items():
    if day in measured_days:
        continue                       # an Messtagen zählt die Eigenmessung
    for indoor in (True, False):
        grp = [it for it in items if it["Umgebung"].strip().lower().startswith("inn") == indoor]
        if not grp:
            continue
        rows_data.append(dict(Datum=day, indoor=indoor,
            Umgebung="Innenraum" if indoor else "Außen/Balkon",
            Position="(Beleg)", status="Beleg (Bautagebuch)", gap_note="", is_beleg=True,
            beleg=[(it["Position"], it["dB_min"], it["dB_max"], it.get("Zeit","")) for it in grp],
            laeq_tag=float('nan'), lmax=max(float(it["dB_max"]) for it in grp),
            pct=0.0, n_above=0, n_sek=0, thr=INDOOR_REF if indoor else RICHTWERT,
            von=None, bis=None, Ph_gesamt=0, Ph_ge60=0))

def daylabel(d):
    dt = datetime.datetime(int(d[:4]), int(d[5:7]), int(d[8:10]))
    return f"{d[8:10]}.{d[5:7]}.\n({wt[dt.weekday()]})"

def bar_page(pp, subset, titel, indoor):
    if not subset:
        return
    fig, ax = plt.subplots(figsize=(14, 7.0))
    fig.subplots_adjust(left=0.07, right=0.90, top=0.85, bottom=0.20)
    x = np.arange(len(subset)); w = 0.38
    base = C_INN if indoor else C_RICHT
    has_beleg = False
    for xi, r in enumerate(subset):
        if r.get("is_beleg"):         # Beleg-Tag (Bautagebuch/Video): schraffierter Balken bis Doku-Max
            has_beleg = True
            ax.bar(xi, r["lmax"], 0.55, color=base, alpha=0.30, hatch='///', edgecolor=base, lw=0.8)
            ax.text(xi, r["lmax"]+0.5, f"{r['lmax']:.0f}\nBeleg", ha='center', va='bottom',
                    fontsize=7, color="#555555")
            continue
        bv = bewval(r)
        if bv is not None and not np.isnan(bv):
            ax.bar(xi - w/2, bv, w, color=base, alpha=0.85)
            ax.text(xi - w/2, bv+0.5, f"{bv:.1f}", ha='center', va='bottom', fontsize=7.5)
        if not np.isnan(r["lmax"]):
            ax.bar(xi + w/2, r["lmax"], w, color=base, alpha=0.30)
            ax.text(xi + w/2, r["lmax"]+0.5, f"{r['lmax']:.0f}", ha='center', va='bottom', fontsize=7.5, color="#666")
        if r["status"]:           # Aufzeichnungsproblem / Lücke markieren
            ax.text(xi, 31, "⚠", ha='center', va='bottom', fontsize=12, color="#C00000")
    def thr_line(yv, color, ls, txt):           # dicke Linie + Label mit weißer Hinterlegung
        ax.axhline(yv, color=color, lw=2.2, ls=ls, zorder=4)
        ax.text(1.002, yv, f" {txt}", transform=ax.get_yaxis_transform(), va='center', ha='left',
                fontsize=8.5, color=color, fontweight='bold', clip_on=False, zorder=6,
                bbox=dict(fc='white', ec=color, lw=0.6, pad=1.5))
    thr_handles = []
    if indoor:
        thr_line(INDOOR_REF, C_INN, "--", f"{INDOOR_REF:.0f} dB(A)")
        thr_handles = [mlines.Line2D([],[], color=C_INN, ls="--", lw=2.2,
                       label=f"ASR A3.7-Orientierungswert {INDOOR_REF:.0f} dB(A)")]
    else:
        thr_line(RICHTWERT, C_RICHT, "-",  f"{RICHTWERT:.0f} dB(A)")
        thr_line(EINGREIF,  C_EING,  "--", f"{EINGREIF:.0f} dB(A)")
        thr_handles = [mlines.Line2D([],[], color=C_RICHT, lw=2.2, label=f"AVV-Richtwert {RICHTWERT:.0f} dB(A)"),
                       mlines.Line2D([],[], color=C_EING, ls="--", lw=2.2, label=f"Eingreifschwelle {EINGREIF:.0f} dB(A)")]
    ax.set_xticks(x); ax.set_xticklabels([daylabel(r["Datum"]) for r in subset], fontsize=8)
    ax.set_xlim(-0.6, len(subset)-0.4)
    ax.set_ylim(30, max([r["lmax"] for r in subset if not np.isnan(r["lmax"])] + [70]) + 6)
    ax.set_ylabel("Schalldruckpegel dB(A)")
    ax.grid(True, axis='y', ls=':', color='#CCCCCC', alpha=0.7)
    for sp in ("top","right"): ax.spines[sp].set_visible(False)
    leg_h = thr_handles + [
                       mpatches.Patch(color=base, alpha=0.85, label=f"{MLABEL} (voller Balken)"),
                       mpatches.Patch(color=base, alpha=0.30, label="Lmax (heller Balken)"),
                       mpatches.Patch(color="#C00000", label="⚠ unvollständige Aufzeichnung")]
    if has_beleg:
        leg_h.append(mpatches.Patch(facecolor=base, alpha=0.30, hatch='///', edgecolor=base,
                                    label="Beleg lt. Bautagebuch/Video (keine Dauermessung)"))
    ax.legend(handles=leg_h, fontsize=8.5, loc='upper left', framealpha=0.95)
    n_meas = sum(1 for r in subset if not r.get("is_beleg")); n_bel = len(subset) - n_meas
    sub = f"{n_meas} eigengemessene Tage" + (f" + {n_bel} Beleg-Tage (Bautagebuch/Video)" if n_bel else "")
    fig.suptitle(titel, x=0.07, y=0.95, ha='left', fontsize=14, fontweight='bold', color=C_TITLE)
    fig.text(0.07, 0.90, f"PCE-323 (Klasse 2, IEC 61672) · {sub} · Bewertungsgröße: {MLABEL}",
             fontsize=9.5, color="#555555")
    fig.text(0.07, 0.015,
             "LAeq Tag = energetischer Mittelpegel 07–20 h der GEMESSENEN Sekunden. Lr (Beurteilungspegel, "
             "Taktmaximal-Verfahren 5 s nach DIN 45645/TA Lärm; KI datenbasiert, KT=0) siehe Tabelle. "
             "⚠ = unvollständige Aufzeichnung; fehlende Zeiten bedeuten NICHT, dass es leise war.",
             fontsize=7.5, color="#888888")
    pp.savefig(fig); plt.close(fig)

def table_page(pp, subset, titel, indoor):
    if not subset:
        return
    fig, ax = plt.subplots(figsize=(14, 8.5)); ax.axis("off")
    fig.subplots_adjust(left=0.02, right=0.98, top=0.90, bottom=0.06)
    fig.suptitle(titel, x=0.06, ha='left', fontsize=14, fontweight='bold', color=C_TITLE)
    headers = ["Datum\n(Wochentag)", "Messort", "Messzeit", "LAeq Tag\ndB(A)", "Lr (Takt)\ndB(A)",
               "Lmax\ndB(A)", "Anteil >\nSchwelle", "Dauer >\nSchwelle", "Phasen\n≥60/10min", "Aufzeichnung"]
    data, rcol = [], []
    gap = False
    for r in subset:
        dt = datetime.datetime(int(r['Datum'][:4]),int(r['Datum'][5:7]),int(r['Datum'][8:10]))
        dlbl = f"{r['Datum'][8:10]}.{r['Datum'][5:7]}.{r['Datum'][:4]}\n({wt[dt.weekday()]})"
        if r.get("is_beleg"):        # Beleg-Tag: je Position eine Zeile (Bautagebuch/Video)
            for (pos, mn, mx, zt) in r["beleg"]:
                data.append([dlbl, pos[:30], zt or "—", "—", "—", f"{mn}–{mx}", "—", "—", "—",
                             "Beleg (Bautagebuch)"])
                rcol.append("#E7F0FA")   # hellblau: belegt, keine Dauermessung
                gap = True
            continue
        no_meas = r["von"] is None       # Lücken-Tag ohne Eigenmessung
        mz     = "—" if no_meas else f"{r['von'].strftime('%H:%M')}–{r['bis'].strftime('%H:%M')}"
        laeq_s = "—" if np.isnan(r['laeq_tag']) else f"{r['laeq_tag']:.1f}"
        lr_s   = "—" if np.isnan(r.get('lr', float('nan'))) else f"{r['lr']:.1f}"
        lmax_s = "—" if np.isnan(r['lmax'])     else f"{r['lmax']:.0f}"
        pct_s  = "—" if no_meas else f"{r['pct']:.0f} %"
        dau_s  = "—" if no_meas else f"{r['n_above']//60} min {r['n_above']%60} s"
        ph_s   = "—" if no_meas else str(r["Ph_ge60"])
        data.append([dlbl, r["Position"][:26], mz, laeq_s, lr_s, lmax_s, pct_s, dau_s, ph_s,
                     r["status"] or "vollständig"])
        thr = INDOOR_REF if indoor else RICHTWERT
        if no_meas:                                       rcol.append("#E2E2E2"); gap = True  # grau: keine Messung
        elif r["status"]:                                 rcol.append("#FFF3CD")   # gelb: Aufz.-Problem
        elif bewval(r) is not None and not np.isnan(bewval(r)) and bewval(r) > thr: rcol.append("#FDECEA")  # rot: Überschr.
        else:                                             rcol.append("#F0F7EF")   # grün
    tab = ax.table(cellText=data, colLabels=headers, loc='upper center', cellLoc='center',
                   bbox=[0.0, 0.05, 1.0, 0.88])
    tab.auto_set_font_size(False); tab.set_fontsize(8.0)
    nrows = len(data)+1
    for (rr, cc), cell in tab.get_celld().items():
        cell.set_height(0.88/nrows); cell.set_edgecolor("#DDDDDD")
        if rr == 0: cell.set_facecolor(C_TITLE); cell.set_text_props(color="white", fontweight="bold")
        else: cell.set_facecolor(rcol[rr-1])
    tab.auto_set_column_width(range(len(headers)))
    thr = INDOOR_REF if indoor else RICHTWERT
    meas = [r for r in subset if not r.get("is_beleg")]
    exc  = sum(1 for r in meas if bewval(r) is not None and not np.isnan(bewval(r)) and bewval(r) > thr)
    teil = sum(1 for r in meas if r["status"])
    nbel = sum(1 for r in subset if r.get("is_beleg"))
    schwelle = "ASR A3.7-Orientierungswert" if indoor else "AVV-Richtwert"
    bel_txt = f" Zusätzlich {nbel} Beleg-Tag(e) lt. Bautagebuch/Video (hellblau, keine Dauermessung)." if nbel else ""
    fig.text(0.06, 0.03,
             f"{exc} von {len(meas)} eigengemessenen Tagen mit {MLABEL} > {thr:.0f} dB(A) ({schwelle}). "
             f"{teil} Tag(e) unvollständige Aufzeichnung (gelb) — fehlende Zeiten ≠ Ruhe.{bel_txt} "
             f"Rot = Überschreitung, Grün = darunter. PCE-323 Kl.2; Kalibrierung: Werkskalibrierung (bis 21.06.); ab 22.06. vor + nach Messung dokumentiert.",
             fontsize=7.5, color="#555555")
    pp.savefig(fig); plt.close(fig)

out_rows = sorted([r for r in rows_data if not r["indoor"]], key=lambda r: r["Datum"])
in_rows  = sorted([r for r in rows_data if r["indoor"]], key=lambda r: r["Datum"])

pp_a = PdfPages(OUT_AUSSEN)
bar_page(pp_a, out_rows,   f"Übersicht AUSSEN/Balkon – Schallpegelmessung {SITE_TITLE}", indoor=False)
table_page(pp_a, out_rows, f"Übersichtstabelle AUSSEN/Balkon – {SITE_TITLE}", indoor=False)
pp_a.close()
print("gespeichert:", OUT_AUSSEN)

pp_i = PdfPages(OUT_INNEN)
bar_page(pp_i, in_rows,    f"Übersicht INNENRAUM – Schallpegelmessung {SITE_TITLE}", indoor=True)
table_page(pp_i, in_rows,  f"Übersichtstabelle INNENRAUM – {SITE_TITLE}", indoor=True)
pp_i.close()
print("gespeichert:", OUT_INNEN)
def _bv_ok(r, thr): b = bewval(r); return b is not None and not np.isnan(b) and b > thr
n_out_exc = sum(1 for r in out_rows if not r.get("is_beleg") and _bv_ok(r, RICHTWERT))
n_in_exc  = sum(1 for r in in_rows if not r.get("is_beleg") and _bv_ok(r, INDOOR_REF))
n_out_m   = sum(1 for r in out_rows if not r.get("is_beleg"))
n_in_m    = sum(1 for r in in_rows if not r.get("is_beleg"))
n_teil    = sum(1 for r in rows_data if r["status"] and not r.get("is_beleg"))
n_beleg   = sum(1 for r in rows_data if r.get("is_beleg"))
print(f"  Aussen: {n_out_exc}/{n_out_m} > 55 dB | Innen: {n_in_exc}/{n_in_m} > 55 dB | "
      f"Teilmessungen: {n_teil} | Beleg-Tage: {n_beleg}")
