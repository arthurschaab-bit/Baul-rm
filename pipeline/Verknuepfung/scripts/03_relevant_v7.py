# -*- coding: utf-8 -*-
"""
03_relevant_v7.py

Robuste Variante von 03_relevant.py:
- verwendet master_index.csv als kanonische Quelle fuer Datum/Uhrzeit/Timestamp/dBA
- repariert wiederverwendete Altzeilen mit leerem Timestamp_ms
- erhaelt zusaetzliche Spalten wie KI_AudioSet, KI_Konfidenz, Laermquelle_KI
- schreibt keine Zeilen mit ungueltigem Timestamp
"""
from __future__ import annotations

import csv
import datetime
import importlib.util
import os
import sys
import zipfile
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VK = os.path.join(BASE, "Verknuepfung")
HERE = os.path.dirname(__file__)

spec = importlib.util.spec_from_file_location("feat", os.path.join(HERE, "02_features.py"))
feat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feat)

THRESHOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
GAP_S = 120


def canonical_from_master(r: dict[str, str]) -> dict[str, object]:
    dba = float(r["dBA_Dauermessung"])
    amp = float(r["Amplitude"]) if r.get("Amplitude") else None
    return {
        "Datum": r["Datum"],
        "Uhrzeit": r["Uhrzeit"],
        "Timestamp_ms": r["Timestamp_ms"],
        "dBA": round(dba, 1),
        "Amplitude": amp,
        "WAV": r["WAV"],
        "WAV_Pfad": os.path.join("relevante_wavs", r["Datum"], r["WAV"]),
    }


def read_master() -> list[dict[str, str]]:
    rel = []
    with open(os.path.join(VK, "master_index.csv"), newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            dba = r.get("dBA_Dauermessung")
            if dba and float(dba) >= THRESHOLD and r.get("Timestamp_ms"):
                rel.append(r)
    rel.sort(key=lambda r: (r["Datum"], int(r["Timestamp_ms"])))
    return rel


def read_existing() -> tuple[dict[str, dict[str, str]], list[str]]:
    path = os.path.join(VK, "relevante_ereignisse.csv")
    if not os.path.exists(path):
        return {}, []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    existing = {}
    for r in rows:
        wav = r.get("WAV")
        if wav:
            existing[wav] = r
    return existing, fields


zcache: dict[str, zipfile.ZipFile] = {}


def zf(name: str) -> zipfile.ZipFile:
    if name not in zcache:
        zcache[name] = zipfile.ZipFile(os.path.join(BASE, name))
    return zcache[name]


def read_wav_from_zip(zip_name: str, wav: str) -> bytes:
    z = zf(zip_name)
    for attempt in range(4):
        try:
            return z.read(wav)
        except KeyError:
            alt = os.path.join(BASE, os.path.splitext(zip_name)[0], wav)
            with open(alt, "rb") as f:
                return f.read()
        except OSError:
            zcache.pop(zip_name, None)
            z = zf(zip_name)
            if attempt == 3:
                raise
    raise RuntimeError(f"WAV nicht lesbar: {zip_name}/{wav}")


def merge_existing(old: dict[str, str], master: dict[str, str]) -> dict[str, object]:
    row: dict[str, object] = dict(old)
    row.update(canonical_from_master(master))
    return row


def classify_new(master: dict[str, str]) -> dict[str, object]:
    wav = master["WAV"]
    data = read_wav_from_zip(master["ZIP"], wav)
    x, sr = feat.load_wav_bytes(data)
    fobj = feat.features(x, sr)
    label, conf, reason = feat.classify(fobj)

    dday = os.path.join(VK, "relevante_wavs", master["Datum"])
    os.makedirs(dday, exist_ok=True)
    dst = os.path.join(dday, wav)
    if not os.path.exists(dst):
        with open(dst, "wb") as o:
            o.write(data)

    fo = fobj or {}
    row = canonical_from_master(master)
    row.update(
        {
            "Laermquelle_Auto": label,
            "Konfidenz": conf,
            "Laermquelle_geprueft": "",
            "centroid_Hz": round(fo.get("centroid", 0)),
            "dom_Hz": round(fo.get("dom_freq", 0)),
            "e_tief_<250": round(fo.get("e_sub", 0), 2),
            "e_hoch_>2k": round(fo.get("e_high", 0) + fo.get("e_vhigh", 0), 2),
            "impuls_crest": round(fo.get("crest", 0), 1),
            "silbentakt": round(fo.get("syllabic", 0), 2),
            "grundton": round(fo.get("pitch_strength", 0), 2),
            "scores": reason,
        }
    )
    return row


def build_episodes(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    rows.sort(key=lambda r: (str(r["Datum"]), int(str(r["Timestamp_ms"]))))
    episodes = []
    cur = None
    for r in rows:
        ts = int(str(r["Timestamp_ms"])) / 1000.0
        if cur and r["Datum"] == cur["Datum"] and ts - cur["_last_ts"] <= GAP_S:
            cur["events"].append(r)
            cur["_last_ts"] = ts
        else:
            if cur:
                episodes.append(cur)
            cur = {"Datum": r["Datum"], "events": [r], "_last_ts": ts}
    if cur:
        episodes.append(cur)

    ep_rows = []
    for i, ep in enumerate(episodes, 1):
        evs = ep["events"]
        for e in evs:
            e["Episode"] = i
        dbas = [float(e.get("dBA") or 0) for e in evs]
        wlab: dict[str, float] = {}
        for e in evs:
            lab = str(e.get("Laermquelle_Auto") or e.get("Laermquelle_KI") or "")
            wlab[lab] = wlab.get(lab, 0.0) + float(e.get("dBA") or 0)
        dom = max(wlab, key=wlab.get) if wlab else ""
        loudest = max(evs, key=lambda e: float(e.get("dBA") or 0))
        t0 = datetime.datetime.fromtimestamp(int(str(evs[0]["Timestamp_ms"])) / 1000.0)
        t1 = datetime.datetime.fromtimestamp(int(str(evs[-1]["Timestamp_ms"])) / 1000.0)
        ep_rows.append(
            {
                "Episode": i,
                "Datum": ep["Datum"],
                "Start": t0.strftime("%H:%M:%S"),
                "Ende": t1.strftime("%H:%M:%S"),
                "Dauer_min": round((t1 - t0).total_seconds() / 60.0, 1),
                "Ereignisse": len(evs),
                "dBA_Spitze": round(max(dbas), 1),
                "dBA_Mittel": round(sum(dbas) / len(dbas), 1),
                "Laermquelle_Auto": dom,
                "Laermquelle_geprueft": "",
                "lautester_Clip": loudest.get("WAV", ""),
                "lautester_Clip_Pfad": loudest.get("WAV_Pfad", ""),
            }
        )
    return ep_rows


def main() -> int:
    rel = read_master()
    print(f"Schwelle >= {THRESHOLD} dBA -> {len(rel)} relevante Ereignisse")
    existing, existing_fields = read_existing()
    print(f"  {len(existing)} vorhandene Ereignisse -> werden kanonisch wiederverwendet")

    out_rows: list[dict[str, object]] = []
    repaired = 0
    added = 0
    skipped_invalid = 0
    for i, r in enumerate(rel, 1):
        wav = r["WAV"]
        old = existing.get(wav)
        if old:
            if not (old.get("Timestamp_ms") or "").strip():
                repaired += 1
            out_rows.append(merge_existing(old, r))
        else:
            out_rows.append(classify_new(r))
            added += 1
        if i % 1000 == 0:
            print(f"  ... {i}/{len(rel)} verarbeitet")

    valid_rows = []
    for r in out_rows:
        try:
            int(str(r.get("Timestamp_ms", "")))
            valid_rows.append(r)
        except ValueError:
            skipped_invalid += 1

    base_fields = [
        "Datum",
        "Uhrzeit",
        "Timestamp_ms",
        "dBA",
        "Amplitude",
        "Laermquelle_Auto",
        "KI_AudioSet",
        "KI_Konfidenz",
        "Laermquelle_KI",
        "Konfidenz",
        "Laermquelle_geprueft",
        "Episode",
        "WAV",
        "WAV_Pfad",
        "centroid_Hz",
        "dom_Hz",
        "e_tief_<250",
        "e_hoch_>2k",
        "impuls_crest",
        "silbentakt",
        "grundton",
        "scores",
        "Dauerbetrieb_Regel",
    ]
    fields = []
    for f in base_fields + existing_fields:
        if f not in fields:
            fields.append(f)

    ep_rows = build_episodes(valid_rows)

    with open(os.path.join(VK, "relevante_ereignisse.csv"), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in valid_rows:
            writer.writerow({k: r.get(k, "") for k in fields})

    ep_fields = [
        "Episode",
        "Datum",
        "Start",
        "Ende",
        "Dauer_min",
        "Ereignisse",
        "dBA_Spitze",
        "dBA_Mittel",
        "Laermquelle_Auto",
        "Laermquelle_geprueft",
        "lautester_Clip",
        "lautester_Clip_Pfad",
    ]
    with open(os.path.join(VK, "episoden.csv"), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=ep_fields)
        writer.writeheader()
        writer.writerows(ep_rows)

    print(f"\n{len(valid_rows)} Ereignisse, {len(ep_rows)} Episoden")
    print(f"  neu klassifiziert: {added}")
    print(f"  Timestamp aus Master repariert: {repaired}")
    print(f"  ungueltige Zeilen uebersprungen: {skipped_invalid}")
    print("Auto-Verteilung:", dict(Counter(str(r.get("Laermquelle_Auto") or "") for r in valid_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
