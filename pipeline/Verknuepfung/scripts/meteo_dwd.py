# -*- coding: utf-8 -*-
"""
meteo_dwd.py
Holt stündliche Wetterdaten der DWD-Station München-Stadt (03379) aus DWD Open Data
(opendata.dwd.de, kostenlos/amtlich) und aggregiert sie je Messtag aufs Tagzeitfenster
07–20 Uhr Ortszeit (CEST). Ausgabe: meteo_je_messtag.csv

Parameter: Wind (F m/s, Richtung D°), Lufttemperatur (TT_TU °C), Niederschlag (R1 mm).
DWD-Stundenwerte sind in UTC → +2 h auf Ortszeit (CEST, Mai–Juni).

Aufruf:  python meteo_dwd.py
"""
import os, sys, csv, io, zipfile, urllib.request, ssl, math, datetime, glob

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
VK   = os.path.abspath(os.path.join(HERE, ".."))
BASE = os.path.abspath(os.path.join(VK, ".."))
OUT  = os.path.join(HERE, "meteo_je_messtag.csv")

STATION = "03379"; STATION_NAME = "München-Stadt"
B = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly/"
SRC = {
    "wind":            B + f"wind/recent/stundenwerte_FF_{STATION}_akt.zip",
    "air_temperature": B + f"air_temperature/recent/stundenwerte_TU_{STATION}_akt.zip",
    "precipitation":   B + f"precipitation/recent/stundenwerte_RR_{STATION}_akt.zip",
}

def ctx():
    try:
        import certifi; return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        print("  (certifi fehlt — nutze ungeprüften SSL-Kontext)")
        return ssl._create_unverified_context()

def fetch_produkt(url, c):
    data = urllib.request.urlopen(url, timeout=60, context=c).read()
    zf = zipfile.ZipFile(io.BytesIO(data))
    name = [n for n in zf.namelist() if n.lower().startswith("produkt")][0]
    return zf.read(name).decode("latin-1")

def parse(txt):
    """produkt-txt (;-getrennt) → list of dict(zeile)."""
    rows = []
    rdr = csv.reader(io.StringIO(txt), delimiter=";")
    hdr = [h.strip() for h in next(rdr)]
    for r in rdr:
        if len(r) < len(hdr): continue
        rows.append({hdr[i]: r[i].strip() for i in range(len(hdr))})
    return rows

def to_local(mess_datum):
    # MESS_DATUM = YYYYMMDDHH (UTC) → lokale Zeit (CEST = UTC+2)
    dt = datetime.datetime.strptime(mess_datum, "%Y%m%d%H") + datetime.timedelta(hours=2)
    return dt

def fnum(v):
    try:
        x = float(v)
        return None if x <= -999 else x
    except Exception:
        return None

def measurement_days():
    days = set()
    p = os.path.join(VK, "messpositionen.csv")
    if os.path.exists(p):
        for row in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
            d = (row.get("Datum") or "").strip()
            if d: days.add(d)
    days |= {os.path.basename(c)[:10] for c in glob.glob(os.path.join(BASE, "2026-*.csv"))}
    return sorted(days)

COMPASS = ["N","NO","O","SO","S","SW","W","NW"]
def compass(deg):
    return COMPASS[int((deg % 360) / 45.0 + 0.5) % 8]

def main():
    c = ctx()
    print(f"Lade DWD-Stundenwerte Station {STATION} ({STATION_NAME})...")
    raw = {}
    for k, url in SRC.items():
        try:
            raw[k] = parse(fetch_produkt(url, c))
            print(f"  {k}: {len(raw[k])} Stundenwerte")
        except Exception as e:
            print(f"  {k}: FEHLER {type(e).__name__} {str(e)[:80]}")
            raw[k] = []

    # je (Datum 'YYYY-MM-DD') sammeln
    def collect(rows, *cols):
        out = {}
        for r in rows:
            md = r.get("MESS_DATUM")
            if not md: continue
            try: lt = to_local(md)
            except Exception: continue
            if not (7 <= lt.hour < 20): continue
            day = lt.strftime("%Y-%m-%d")
            out.setdefault(day, []).append(r)
        return out

    wind = collect(raw["wind"]); temp = collect(raw["air_temperature"]); prec = collect(raw["precipitation"])

    rows_out = []
    for day in measurement_days():
        w = wind.get(day, []); t = temp.get(day, []); p = prec.get(day, [])
        speeds = [fnum(r.get("   F")) or fnum(r.get("F")) for r in w]
        speeds = [s for s in speeds if s is not None]
        dirs = [fnum(r.get("   D")) or fnum(r.get("D")) for r in w]
        dirs = [d for d in dirs if d is not None and 0 <= d <= 360]
        temps = [fnum(r.get("TT_TU")) for r in t]; temps = [x for x in temps if x is not None]
        rains = [fnum(r.get("  R1")) or fnum(r.get("R1")) for r in p]; rains = [x for x in rains if x is not None]
        if dirs:
            sx = sum(math.sin(math.radians(d)) for d in dirs); cy = sum(math.cos(math.radians(d)) for d in dirs)
            ddeg = (math.degrees(math.atan2(sx, cy)) % 360)
            rich = f"{compass(ddeg)} ({ddeg:.0f}°)"
        else:
            rich = "—"
        rows_out.append({
            "Datum": day,
            "Wind_mittel_ms": f"{sum(speeds)/len(speeds):.1f}" if speeds else "—",
            "Wind_max_ms": f"{max(speeds):.1f}" if speeds else "—",
            "Windrichtung": rich,
            "Temp_min_C": f"{min(temps):.1f}" if temps else "—",
            "Temp_max_C": f"{max(temps):.1f}" if temps else "—",
            "Niederschlag_mm": f"{sum(rains):.1f}" if rains else "—",
            "Stunden_n": max(len(speeds), len(temps), len(rains)),
            "Station": f"DWD {STATION} {STATION_NAME}",
        })

    fields = ["Datum","Wind_mittel_ms","Wind_max_ms","Windrichtung","Temp_min_C","Temp_max_C",
              "Niederschlag_mm","Stunden_n","Station"]
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";"); w.writeheader(); w.writerows(rows_out)
    print(f"\nGeschrieben: {OUT} ({len(rows_out)} Messtage)")
    for r in rows_out:
        print(f"  {r['Datum']}: Wind {r['Wind_mittel_ms']}/{r['Wind_max_ms']} m/s {r['Windrichtung']}, "
              f"Temp {r['Temp_min_C']}–{r['Temp_max_C']}°C, Regen {r['Niederschlag_mm']} mm (n={r['Stunden_n']})")

if __name__ == "__main__":
    main()
