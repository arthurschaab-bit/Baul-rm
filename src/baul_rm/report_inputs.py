"""Reproducible Noisecontrol CSV/SQLite normalization and provenance audit.

Inputs are never changed. Pure PCE CSV aggregates have priority; mixed
CSV seconds use separate PCE database records when available; all choices and failed database reads are recorded in the audit.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from .report_core import resolve_pce_seconds

TZ = ZoneInfo("Europe/Berlin")
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
        required = {"id", "at", "levelDb", "source"}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(level_samples)")}
        if not required <= columns:
            audit["database_days"].append({"day": day, "origin": origin,
                                           "raw_rows": 0, "fallback_unavailable": "level_samples columns/table missing"})
            return pd.DataFrame(columns=COLUMNS)
        try:
            samples = connection.execute(
                """SELECT id,at,levelDb FROM level_samples
                   WHERE at>=? AND at<? AND source='PCE_323' ORDER BY at,id""", (a,b)).fetchall()
        except sqlite3.DatabaseError as e:
            audit["database_days"].append({"day": day, "origin": origin,
                                           "raw_rows": 0, "fallback_error": str(e)})
            return pd.DataFrame(columns=COLUMNS)
        fallback_origin = origin + ":level_samples"
        audit["database_days"].append({"day": day, "origin": fallback_origin,
                                       "fallback": "level_samples", "raw_rows": len(samples)})
        if not samples:
            return pd.DataFrame(columns=COLUMNS)
        fallback = pd.DataFrame(samples, columns=["id", "ms", "level"])
        fallback["level"] = pd.to_numeric(fallback.level, errors="coerce")
        fallback = fallback[np.isfinite(fallback.level) & fallback.level.gt(0) & fallback.level.le(160)].copy()
        if fallback.empty:
            return pd.DataFrame(columns=COLUMNS)
        fallback["second"] = fallback.ms // 1000
        fallback["energy"] = 10**(fallback.level / 10)
        group = fallback.groupby("second", sort=True)
        out = pd.DataFrame({"leq": 10*np.log10(group["energy"].mean()),
                            "maximum": group["level"].max(), "raw": group["level"].last()})
        out["source"] = "PCE_323"
        out["weighting"] = ""
        out["time_weighting"] = ""
        out["origin"] = fallback_origin
        return out[COLUMNS]
    df = pd.DataFrame(rows, columns=["id","ms","level","weighting","flags","time_weighting","device"])
    df['level']=pd.to_numeric(df.level,errors='coerce')
    flags=pd.to_numeric(df['flags'],errors='coerce')
    # Missing/non-integral flags do not establish valid measurement status.
    known_flags=flags.notna() & flags.mod(1).eq(0) & flags.between(0,2**31-1)
    valid_flags=(flags.where(known_flags,11).astype('int64')&11)==0
    mask = np.isfinite(df.level) & (df.level > 0) & (df.level <= 160) & valid_flags
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


def read_session_export(path, audit):
    """Read one explicitly mapped old-app export as PCE measurement rows."""
    rows = []
    excluded = 0
    first = last = None
    total = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        required = {"Zeit", "Pegel_dB", "Bewertung", "Flags"}
        if not required <= set(reader.fieldnames or []):
            raise ValueError("Session-CSV has missing columns: " + path.name)
        for index, row in enumerate(reader):
            total += 1
            try:
                instant = dt.datetime.fromisoformat(row["Zeit"])
                second = stamp(row["Zeit"])
                flags = int(row["Flags"])
                level = numeric(row["Pegel_dB"])
                if flags < 0 or flags > 2**31-1 or flags & 11 or level is None:
                    raise ValueError("excluded row")
            except (KeyError, TypeError, ValueError):
                excluded += 1
                continue
            first = instant if first is None else min(first, instant)
            last = instant if last is None else max(last, instant)
            rows.append((second, instant.timestamp(), index, level, row.get("Bewertung", "") or "",
                         row.get("Zeitbewertung", "") or ""))
    audit.update({"rows": total, "excluded_rows": excluded,
                  "first_timestamp": first.astimezone(TZ).isoformat() if first is not None else None,
                  "last_timestamp": last.astimezone(TZ).isoformat() if last is not None else None})
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.DataFrame(rows, columns=["second", "instant", "position", "level", "weighting", "time_weighting"])
    frame = frame.sort_values(["second", "instant", "position"])
    frame["energy"] = 10**(frame.level / 10)
    group = frame.groupby("second", sort=True)
    out = pd.DataFrame({"leq": 10*np.log10(group["energy"].mean()),
                        "maximum": group["level"].max(), "raw": group["level"].last(),
                        "weighting": group["weighting"].first(), "time_weighting": group["time_weighting"].first()})
    out["source"] = "PCE_323"
    out["origin"] = "SESSION:" + path.name
    return out[COLUMNS]


def collect(root, start, end, databases, legacy_root=None, session_manifest=None):
    days = days_between(start,end)
    a = int(dt.datetime.combine(dt.date.fromisoformat(start),dt.time(),TZ).timestamp())
    b = int(dt.datetime.combine(dt.date.fromisoformat(end)+dt.timedelta(days=1),dt.time(),TZ).timestamp())
    audit = {k:0 for k in ("csv_rows","bad_rows","explicit_gap_rows","flagged_rows","empty_sample_rows","invalid_level_rows")}
    audit.update({"overlaps":[],"supplements":[],"database_days":[],"databases":[],"files":[],"sessions":[]})
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
    database_data = pd.DataFrame(columns=COLUMNS)
    for database in databases:
        database=Path(database).resolve()
        print('Sicherung: '+database.name,flush=True)
        with closing(sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)) as c:
            check=c.execute("pragma quick_check").fetchall()
            if check != [("ok",)]:
                raise ValueError('Database integrity check failed: '+str(check[:3]))
            audit["databases"].append({"path":str(database),"integrity":"ok"})
            origin = "DB:"+database.name
            for day in days:
                df = read_database_day(c,day,audit,origin)
                if not df.empty:
                    database_data=combine(database_data,df,audit,origin+":"+day)
        manifest.append({"type":"DB-Kopie","path":str(database),"bytes":database.stat().st_size,"sha256":file_hash(database)})
    if session_manifest is not None:
        mapping = Path(session_manifest).resolve()
        with mapping.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            if not {"datei", "sha256", "geraet_laut_app", "bemerkung"} <= set(reader.fieldnames or []):
                raise ValueError("Session manifest has missing columns")
            entries = list(reader)
        for entry in entries:
            path = Path(entry["datei"])
            if not path.is_absolute():
                path = mapping.parent / path
            path = path.resolve()
            if not path.is_file():
                raise FileNotFoundError("Session file missing: " + str(path))
            actual_hash = file_hash(path)
            expected_hash = (entry["sha256"] or "").lower()
            if actual_hash != expected_hash:
                raise ValueError("Session SHA-256 mismatch: " + str(path))
            record = {"path": str(path), "bytes": path.stat().st_size, "sha256": actual_hash}
            if entry["geraet_laut_app"] != "PCE-323":
                record["ignored"] = "geraet_laut_app is not PCE-323"
                audit["sessions"].append(record)
                continue
            df = read_session_export(path, record)
            df = df[(df.index >= a) & (df.index < b)]
            # Session manifests contribute only to the report's 07:00–20:00 day window.
            local_times = pd.to_datetime(df.index, unit="s", utc=True).tz_convert(TZ)
            in_window = (local_times.hour >= 7) & (local_times.hour < 20)
            record["outside_day_window_seconds"] = int((~in_window).sum())
            df = df.loc[in_window]
            record["used_seconds"] = len(df)
            audit["sessions"].append(record)
            manifest.append({"type": "SESSION-CSV", "path": str(path),
                             "bytes": path.stat().st_size, "sha256": actual_hash})
            if not df.empty:
                database_data = combine(database_data, df, audit, "SESSION:" + path.name)
    # Audit CSV/DB differences before replacing any original mixed aggregate.
    combine(data,database_data,audit,'CSV versus separate PCE database')
    selected,excluded,selection=resolve_pce_seconds(data,database_data)
    supplements=database_data.loc[~database_data.index.isin(data.index)]
    data=pd.concat([selected,supplements]).sort_index()
    audit['source_selection']=selection
    audit['excluded_source_seconds']=len(excluded)
    audit["valid_seconds"] = len(data)
    return data, audit, manifest

