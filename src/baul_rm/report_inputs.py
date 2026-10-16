"""Reproducible Noisecontrol CSV/SQLite import and measurement report.

Inputs are never changed. Pure PCE CSV aggregates have priority; mixed
CSV seconds use separate PCE database records when available; all choices and failed database reads are recorded in the audit.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TZ = ZoneInfo("Europe/Berlin")
COLORS = {"GEMISCHT": "#2466ae", "PCE_323": "#228747", "MIKROFON": "#888888", "UNBEKANNT": "#ae7b28"}
COLUMNS = ["leq", "maximum", "raw", "source", "weighting", "time_weighting", "origin"]


def numeric(value):
    try:
        x = float(str(value).replace(",", "."))
        return x if math.isfinite(x) and 0 < x <= 160 else None
    except (TypeError, ValueError):
        return None


def stamp(value):
    t = dt.datetime.fromisoformat(value)
    if t.tzinfo is None:
        raise ValueError("Zeitstempel ohne Zeitzone")
    return int(t.timestamp())


def days_between(start, end):
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if b < a:
        raise ValueError("Enddatum liegt vor Startdatum")
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b-a).days+1)]


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def import_csv(path, audit):
    """Read Drive aggregates, raw session CSV or legacy meter CSV."""
    rows = []
    with path.open(encoding="utf-8-sig", newline="", errors="strict") as f:
        first = f.readline()
        f.seek(0)
        if first.startswith('"FileName"'):
            for r in csv.reader(f):
                if len(r) >= 4 and r[0].strip().isdigit():
                    try:
                        t = dt.datetime.strptime(r[1]+" "+r[2], "%m-%d-%Y %H:%M:%S").replace(tzinfo=TZ)
                        v = numeric(r[3])
                        if v is not None:
                            rows.append((int(t.timestamp()), v, v, v, "PCE_323", "A", "FAST", str(path)))
                    except ValueError:
                        audit["bad_rows"] += 1
        else:
            reader = csv.DictReader(f, delimiter=";")
            aggregate = "LAeq_dB" in (reader.fieldnames or [])
            for r in reader:
                audit["csv_rows"] += 1
                try:
                    t = stamp(r["Zeit"])
                except (KeyError, ValueError, TypeError):
                    audit["bad_rows"] += 1
                    continue
                source = r.get("Quelle", "UNBEKANNT") or "UNBEKANNT"
                if source == "KEINE_VERBINDUNG":
                    audit["explicit_gap_rows"] += 1
                    continue
                try:
                    if int(r.get("Flags", "0") or 0) & 11:
                        audit["flagged_rows"] += 1
                        continue
                    if aggregate and int(r.get("Samples", "1") or 0) <= 0:
                        audit["empty_sample_rows"] += 1
                        continue
                except ValueError:
                    audit["bad_rows"] += 1
                    continue
                leq = numeric(r.get("LAeq_dB")) if aggregate else numeric(r.get("Pegel_dB"))
                if leq is None:
                    audit["invalid_level_rows"] += 1
                    continue
                maximum = numeric(r.get("LAFmax_dB")) or leq
                raw = numeric(r.get("Pegel_dB")) or leq
                rows.append((t, leq, maximum, raw, source, r.get("Bewertung", ""), r.get("Zeitbewertung", ""), str(path)))
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows, columns=["second"]+COLUMNS)
    if df.second.duplicated().any():
        # Raw session records: multiple samples in the same second are energy averaged.
        df["energy"] = 10**(df.leq/10)
        group = df.groupby("second", sort=True)
        out = group.first()[COLUMNS]
        out["leq"] = 10*np.log10(group.energy.mean())
        out["maximum"] = group.maximum.max()
        out["source"] = group.source.agg(lambda x: x.iloc[0] if x.nunique()==1 else "GEMISCHT")
        return out
    return df.set_index("second")[COLUMNS]


def read_database_day(connection, day, audit, origin):
    start = dt.datetime.combine(dt.date.fromisoformat(day), dt.time(), TZ)
    a, b = int(start.timestamp()*1000), int((start+dt.timedelta(days=1)).timestamp()*1000)
    query = """SELECT m.id,m.timestamp,m.levelDb,m.weighting,m.flags,m.timeWeighting,s.deviceName
        FROM measurements m JOIN sessions s ON s.id=m.sessionId
        WHERE m.timestamp>=? AND m.timestamp<? AND s.deviceName='PCE-323' ORDER BY m.timestamp,m.id"""
    try:
        rows = connection.execute(query, (a,b)).fetchall()
    except (sqlite3.DatabaseError, ValueError) as e:
        audit["database_days"].append({"day": day, "origin": origin, "error": str(e)})
        return pd.DataFrame(columns=COLUMNS)
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows, columns=["id","ms","level","weighting","flags","time_weighting","device"])
    mask = np.isfinite(df.level) & (df.level > 0) & (df.level <= 160) & ((df['flags'].astype(int)&11)==0)
    audit["database_days"].append({"day":day, "origin":origin, "raw_rows":len(df), "excluded":int((~mask).sum())})
    df = df[mask].copy()
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df["second"] = df.ms//1000
    df["energy"] = 10**(df.level/10)
    df["source"] = df.device.map(lambda x: "PCE_323" if x=="PCE-323" else "MIKROFON" if x=="Smartphone-Mikrofon" else "UNBEKANNT")
    group = df.groupby("second", sort=True)
    out = pd.DataFrame({"leq":10*np.log10(group['energy'].mean()), "maximum":group['level'].max(), "raw":group['level'].last()})
    for field in ("source", "weighting", "time_weighting"):
        values=group[field].first().fillna('')
        uniform=(group[field].nunique()==1)&(group[field].count()==group.size())
        out[field]=values.where(uniform,'GEMISCHT' if field=='source' else '')
    out["origin"] = origin
    return out[COLUMNS]


def combine(primary, secondary, audit, label):
    overlap = primary.index.intersection(secondary.index)
    if len(overlap):
        same = primary.loc[overlap,"source"].to_numpy()==secondary.loc[overlap,"source"].to_numpy()
        difference = np.abs(primary.loc[overlap,"leq"].astype(float).to_numpy()-secondary.loc[overlap,"leq"].astype(float).to_numpy())
        audit["overlaps"].append({"origin":label,"seconds":len(overlap),"same_source":int(same.sum()),"within_0_15db":int((difference<=0.15).sum()),"conflicting_seconds":int((difference>0.15).sum()),"max_difference":round(float(difference.max()),3)})
    missing = secondary.loc[~secondary.index.isin(primary.index)]
    if len(missing) and len(primary):
        audit["supplements"].append({"origin":label,"seconds":len(missing)})
    return pd.concat([primary,missing]).sort_index()


def measurement_files(root, legacy_root=None):
    """Only live daily exports and flat legacy recordings, never evidence copies."""
    found = list(root.glob("laermprotokoll_*.csv"))
    for folder in root.iterdir():
        if folder.is_dir() and re.fullmatch(r"20\d\d-\d\d-\d\d", folder.name):
            found.extend(folder.glob("laermprotokoll_*.csv"))
            found.extend((folder / "Schallmessung").glob("laermprotokoll_*.csv"))
            found.extend(folder.glob("Session_*.csv"))
    if legacy_root is not None:
        found.extend(legacy_root.glob("20??-??-?? *.csv"))
    return sorted(found, key=lambda p: (0 if p.name.startswith("laermprotokoll_") else 1,
                                         -p.stat().st_size, str(p)))


def collect(root, start, end, databases, legacy_root=None):
    days = days_between(start,end)
    a = int(dt.datetime.combine(dt.date.fromisoformat(start),dt.time(),TZ).timestamp())
    b = int(dt.datetime.combine(dt.date.fromisoformat(end)+dt.timedelta(days=1),dt.time(),TZ).timestamp())
    audit = {k:0 for k in ("csv_rows","bad_rows","explicit_gap_rows","flagged_rows","empty_sample_rows","invalid_level_rows")}
    audit.update({"overlaps":[],"supplements":[],"database_days":[],"databases":[],"files":[]})
    frames = []
    manifest = []
    # Prefer the complete canonical export over copies; CSV rows always win over backup rows.
    files = measurement_files(root, legacy_root)
    for p in files:
        print('CSV: '+p.name,flush=True)
        with p.open(encoding="utf-8-sig", newline="") as handle:
            first=handle.readline().strip()
        schema="legacy-meter" if first.startswith('"FileName"') else "app-aggregate" if "LAeq_dB" in first else "app-session" if "Pegel_dB" in first else "unknown"
        df = import_csv(p,audit)
        df = df[(df.index>=a)&(df.index<b)]
        audit["files"].append({"path":str(p),"schema":schema,"header":first,"valid_seconds_in_period":len(df),"first_second":int(df.index.min()) if len(df) else None,"last_second":int(df.index.max()) if len(df) else None})
        if not df.empty:
            frames.append(df)
            manifest.append({"type":"CSV","path":str(p),"bytes":p.stat().st_size,"sha256":file_hash(p)})
    data = pd.DataFrame(columns=COLUMNS)
    for df in frames:
        data = combine(data,df,audit,str(df.origin.iloc[0]))
    for database in databases:
        print('Sicherung: '+database.name,flush=True)
        c = sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)
        try:
            check = c.execute("pragma quick_check").fetchall()
            integrity = "ok" if check==[("ok",)] else str(check[:3])
        except sqlite3.DatabaseError as e:
            integrity = str(e)
        audit["databases"].append({"path":str(database),"integrity":integrity})
        origin = "DB:"+database.name
        for day in days:
            df = read_database_day(c,day,audit,origin)
            if not df.empty:
                from .report_core import resolve_pce_seconds
                replacement,_,selection=resolve_pce_seconds(data,df)
                # Keep unselected originals until all database files were tried.
                source=data.get('source_export',data.source)
                replace_idx=data.index[source.eq('GEMISCHT')].intersection(df.index)
                data=data.drop(index=replace_idx)
                data=pd.concat([data,df.loc[replace_idx]]).sort_index()
                data = combine(data,df,audit,origin+":"+day)
                print(day+': '+str(len(df))+' lesbare Sekunden',flush=True)
        c.close()
        manifest.append({"type":"DB-Kopie","path":str(database),"bytes":database.stat().st_size,"sha256":file_hash(database)})
    from .report_core import resolve_pce_seconds
    empty=pd.DataFrame(columns=COLUMNS)
    data,excluded,selection=resolve_pce_seconds(data,empty)
    audit['source_selection']=selection
    audit['excluded_source_seconds']=len(excluded)
    audit["valid_seconds"] = len(data)
    return data, audit, manifest

