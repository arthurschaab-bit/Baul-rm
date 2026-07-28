# -*- coding: utf-8 -*-
"""
05_dauerlaerm_v7.py

Dauerlaerm-Erkennung fuer v7.

Unterschied zu 05_dauerlaerm.py:
- keine erneute PANNs-Inferenz pro Phase
- vorhandene Quellenzuordnungen aus relevante_ereignisse.csv und alte
  dauerlaerm.csv werden erhalten
- reine CSV-Tage ohne WAV-ZIP koennen trotzdem Dauerlaerm-Phasen bekommen
"""
from __future__ import annotations

import csv
import datetime
import glob
import math
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VK = os.path.join(BASE, "Verknuepfung")

CRITERIA = [(60, 5), (60, 10), (65, 5), (70, 3)]
GAP = 20
MAX_CLIPS_PER_PHASE = 6
ALIAS = {"Tiefbohrer": "Bohrgeraet/schweres Geraet"}


def csv_series(day: str) -> list[tuple[int, float]]:
    pts: dict[int, float] = {}
    for c in glob.glob(os.path.join(BASE, f"{day} *.csv")):
        with open(c, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.reader(f):
                if len(row) >= 4 and row[0].strip().isdigit():
                    try:
                        h, mi, s = (int(x) for x in row[2].split(":"))
                        pts[h * 3600 + mi * 60 + s] = float(row[3])
                    except Exception:
                        pass
    return sorted(pts.items())


def detect(series: list[tuple[int, float]], threshold: float, minutes: float) -> list[tuple[int, int]]:
    above = [s for s, dba in series if dba >= threshold]
    if not above:
        return []
    phases = []
    start = prev = above[0]
    for s in above[1:]:
        if s - prev <= GAP:
            prev = s
        else:
            phases.append((start, prev))
            start = prev = s
    phases.append((start, prev))
    return [(a, b) for a, b in phases if (b - a) >= minutes * 60]


def leq(vals: list[float]) -> float | None:
    return 10 * math.log10(sum(10 ** (v / 10) for v in vals) / len(vals)) if vals else None


def read_master_events() -> dict[str, list[dict[str, object]]]:
    wav_by_day: dict[str, list[dict[str, object]]] = {}
    path = os.path.join(VK, "master_index.csv")
    if not os.path.exists(path):
        return wav_by_day
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                h, mi, s = (int(x) for x in r["Uhrzeit"].split(":"))
                dba = float(r["dBA_Dauermessung"]) if r.get("dBA_Dauermessung") else None
                ts = int(r["Timestamp_ms"]) if r.get("Timestamp_ms") else None
            except Exception:
                continue
            wav_by_day.setdefault(r["Datum"], []).append(
                {
                    "sec": h * 3600 + mi * 60 + s,
                    "ts": ts,
                    "dba": dba,
                    "wav": r.get("WAV", ""),
                    "zip": r.get("ZIP", ""),
                }
            )
    return wav_by_day


def source_of_event(r: dict[str, str]) -> str:
    src = (r.get("Laermquelle_geprueft") or "").strip()
    if not src:
        src = (r.get("Laermquelle_KI") or r.get("Laermquelle_Auto") or "").strip()
    return ALIAS.get(src, src)


def read_relevant_sources() -> dict[str, dict[str, str]]:
    path = os.path.join(VK, "relevante_ereignisse.csv")
    if not os.path.exists(path):
        return {}
    out: dict[str, dict[str, str]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            wav = r.get("WAV")
            if wav:
                out[wav] = r
    return out


def read_old_dauerlaerm() -> dict[tuple[str, str, str, str], dict[str, str]]:
    path = os.path.join(VK, "dauerlaerm.csv")
    if not os.path.exists(path):
        return {}
    out: dict[tuple[str, str, str, str], dict[str, str]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = (r.get("Kriterium", ""), r.get("Datum", ""), r.get("Start", ""), r.get("Ende", ""))
            out[key] = r
    return out


def dominant_source(sample: list[dict[str, object]], relevant_by_wav: dict[str, dict[str, str]]) -> tuple[str, str, str, str]:
    weights: dict[str, float] = {}
    repr_clip = ""
    repr_path = ""
    for i, event in enumerate(sample):
        wav = str(event.get("wav") or "")
        src_row = relevant_by_wav.get(wav)
        label = source_of_event(src_row) if src_row else ""
        if label:
            weights[label] = weights.get(label, 0.0) + float(event.get("dba") or 1.0)
        if i == 0 and wav:
            repr_clip = wav
            repr_path = os.path.join("relevante_wavs", str(event.get("day") or ""), wav)
    if not weights:
        return "—", "", repr_clip, repr_path
    total = sum(weights.values()) or 1.0
    dom = max(weights, key=weights.get)
    detail = ", ".join(f"{k} {round(100*v/total)}%" for k, v in sorted(weights.items(), key=lambda kv: -kv[1]))
    return dom, detail, repr_clip, repr_path


def main() -> int:
    wav_by_day = read_master_events()
    relevant_by_wav = read_relevant_sources()
    old_by_key = read_old_dauerlaerm()
    csv_days = {os.path.basename(c)[:10] for c in glob.glob(os.path.join(BASE, "2026-*.csv"))}
    days = sorted(set(wav_by_day) | csv_days)

    rows: list[dict[str, object]] = []
    reused_source = 0
    for day in days:
        series = csv_series(day)
        if not series:
            continue
        for threshold, minutes in CRITERIA:
            for a, b in detect(series, threshold, minutes):
                seg = [dba for s, dba in series if a <= s <= b]
                cov = 100.0 * sum(1 for dba in seg if dba >= threshold) / len(seg)
                t0 = datetime.datetime.strptime(day, "%Y-%m-%d") + datetime.timedelta(seconds=a)
                t1 = datetime.datetime.strptime(day, "%Y-%m-%d") + datetime.timedelta(seconds=b)
                criterion = f">={threshold}dB/>={minutes}min"
                key = (criterion, day, t0.strftime("%H:%M:%S"), t1.strftime("%H:%M:%S"))
                old = old_by_key.get(key, {})

                evs = [dict(e, day=day) for e in wav_by_day.get(day, []) if a <= int(e["sec"]) <= b]
                evs_sorted = sorted(evs, key=lambda e: (e.get("dba") or 0), reverse=True)
                sample = evs_sorted[:MAX_CLIPS_PER_PHASE]

                dom = old.get("Laermquelle_Auto", "")
                checked = old.get("Laermquelle_geprueft", "")
                detail = old.get("Quellen_Detail", "")
                repr_clip = old.get("repr_Clip", "")
                repr_path = old.get("repr_Clip_Pfad", "")
                if old:
                    reused_source += 1
                if not dom:
                    dom, detail, repr_clip, repr_path = dominant_source(sample, relevant_by_wav)

                rows.append(
                    {
                        "Kriterium": criterion,
                        "Datum": day,
                        "Start": t0.strftime("%H:%M:%S"),
                        "Ende": t1.strftime("%H:%M:%S"),
                        "Dauer_min": round((b - a) / 60.0, 1),
                        "Leq_dBA": round(leq(seg), 1),
                        "Lmax_dBA": round(max(seg), 1),
                        "Lmin_dBA": round(min(seg), 1),
                        "Abdeckung_%": round(cov),
                        "Anzahl_WAV": len(evs),
                        "Laermquelle_Auto": dom or "—",
                        "Laermquelle_geprueft": checked,
                        "Quellen_Detail": detail,
                        "repr_Clip": repr_clip,
                        "repr_Clip_Pfad": repr_path,
                    }
                )

    rows.sort(key=lambda r: (str(r["Datum"]), str(r["Start"]), str(r["Kriterium"])))
    fields = [
        "Kriterium",
        "Datum",
        "Start",
        "Ende",
        "Dauer_min",
        "Leq_dBA",
        "Lmax_dBA",
        "Lmin_dBA",
        "Abdeckung_%",
        "Anzahl_WAV",
        "Laermquelle_Auto",
        "Laermquelle_geprueft",
        "Quellen_Detail",
        "repr_Clip",
        "repr_Clip_Pfad",
    ]
    with open(os.path.join(VK, "dauerlaerm.csv"), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} Dauerlaerm-Phasen erkannt")
    print(f"  Quellen aus alter dauerlaerm.csv wiederverwendet: {reused_source}")
    for threshold, minutes in CRITERIA:
        crit = f">={threshold}dB/>={minutes}min"
        subset = [r for r in rows if r["Kriterium"] == crit]
        total = sum(float(r["Dauer_min"]) for r in subset)
        print(f"  {crit}: {len(subset)} Phasen, gesamt {total:.0f} min")
    print("Quellen (dominant):", dict(Counter(str(r["Laermquelle_Auto"]) for r in rows)))
    print("-> dauerlaerm.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
