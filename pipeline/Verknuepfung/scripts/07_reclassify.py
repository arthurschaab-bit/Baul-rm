# -*- coding: utf-8 -*-
"""
07_reclassify.py
Klassifiziert die relevanten Ereignisse mit PANNs CNN14 (AudioSet) und schreibt
  Laermquelle_KI, KI_Konfidenz, KI_AudioSet
in relevante_ereignisse.csv. Heuristik (Laermquelle_Auto) und manuelle Korrekturen
(Laermquelle_geprueft) bleiben erhalten.

Die rohen 527er-Wahrscheinlichkeiten werden gecacht (panns_probs.npy + .json).
=> Inferenz läuft nur einmal je Clip; bei geänderten Mapping/Kategorien in
   panns_classify.py genügt ein erneuter Lauf (liest Cache, KEINE neue Inferenz).
"""
import os, csv, wave
import numpy as np
import importlib.util
from panns_cache import (
    load_probability_cache,
    save_probability_cache,
    unique_pending_rows,
)

HERE = os.path.dirname(__file__)
VK   = os.path.abspath(os.path.join(HERE, ".."))
CACHE_NPY = os.path.join(VK, "panns_probs.npy")
CACHE_IDX = os.path.join(VK, "panns_probs_index.json")

spec = importlib.util.spec_from_file_location("pc", os.path.join(HERE, "panns_classify.py"))
pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)

# ---- Prob-Cache laden
try:
    probs_by_wav = load_probability_cache(CACHE_NPY, CACHE_IDX)
except (OSError, ValueError, TypeError) as exc:
    print(f"Warnung: Prob-Cache unbrauchbar, starte leer: {exc}")
    probs_by_wav = {}
if probs_by_wav:
    print(f"Prob-Cache: {len(probs_by_wav)} Clips geladen")

path = os.path.join(VK, "relevante_ereignisse.csv")
rows = list(csv.DictReader(open(path, newline='', encoding='utf-8-sig')))
fields = list(rows[0].keys())
for c in ["Laermquelle_KI", "KI_Konfidenz", "KI_AudioSet"]:
    if c not in fields:
        i = fields.index("Laermquelle_Auto")+1 if "Laermquelle_Auto" in fields else len(fields)
        fields.insert(i, c)

miss = pc.mapping_coverage()
if miss: print("Hinweis: Mapping-Keys ohne AudioSet-Treffer:", miss)

# ---- fehlende Inferenzen ergänzen
# Ein WAV kann in relevante_ereignisse.csv mehrfach vorkommen. Die alte
# Aufgabenliste enthielt dann denselben Clip mehrfach, weil sie vor der ersten
# Inferenz komplett aufgebaut wurde. Hier wird bewusst je WAV dedupliziert.
todo = unique_pending_rows(rows, probs_by_wav)
unique_wavs = list(dict.fromkeys(r["WAV"] for r in rows if r.get("WAV")))
print(
    f"{len(rows)} Ereignisse, {len(unique_wavs)} eindeutige WAVs, "
    f"{len(todo)} brauchen neue Inferenz, "
    f"{len(unique_wavs)-len(todo)} aus Cache"
)
BATCH_SIZE = max(1, int(os.environ.get("BAUL_RM_PANNS_BATCH_SIZE", "8")))
CHECKPOINT_EVERY = max(
    1, int(os.environ.get("BAUL_RM_PANNS_CHECKPOINT_EVERY", "2000"))
)
groups = {}
processed = 0
for r in todo:
    wp = os.path.join(VK, r["WAV_Pfad"])
    try:
        with wave.open(wp, "rb") as handle:
            shape = (
                handle.getframerate(),
                handle.getnframes(),
                handle.getnchannels(),
                handle.getsampwidth(),
            )
        groups.setdefault(shape, []).append(r)
    except Exception as exc:
        probs_by_wav[r["WAV"]] = np.zeros(527, dtype=np.float32)
        processed += 1
        print("  FEHLER", r["WAV"], exc)

checkpoint_at = CHECKPOINT_EVERY
for shape, group_rows in groups.items():
    sr = shape[0]
    for start in range(0, len(group_rows), BATCH_SIZE):
        batch_rows = group_rows[start:start + BATCH_SIZE]
        loaded = []
        for r in batch_rows:
            wp = os.path.join(VK, r["WAV_Pfad"])
            try:
                with wave.open(wp, "rb") as handle:
                    samples = np.frombuffer(
                        handle.readframes(handle.getnframes()),
                        dtype=np.int16,
                    ).astype(np.float32) / 32768.0
                loaded.append((r, samples))
            except Exception as exc:
                probs_by_wav[r["WAV"]] = np.zeros(527, dtype=np.float32)
                print("  FEHLER", r["WAV"], exc)

        if loaded:
            try:
                batch_probs = pc.infer_probs_batch(
                    [samples for _, samples in loaded],
                    sr,
                )
                for (r, _), probabilities in zip(loaded, batch_probs):
                    probs_by_wav[r["WAV"]] = probabilities
            except Exception as batch_exc:
                print(f"  Batch-Fallback ({len(loaded)} Clips): {batch_exc}")
                for r, samples in loaded:
                    try:
                        probs_by_wav[r["WAV"]] = pc.infer_probs(samples, sr).astype(np.float32)
                    except Exception as exc:
                        probs_by_wav[r["WAV"]] = np.zeros(527, dtype=np.float32)
                        print("  FEHLER", r["WAV"], exc)

        processed += len(batch_rows)
        if processed >= checkpoint_at:
            saved = save_probability_cache(probs_by_wav, CACHE_NPY, CACHE_IDX)
            print(f"  Zwischenstand: {saved} Clips sicher im Cache", flush=True)
            print(f"  ... {processed}/{len(todo)}", flush=True)
            while checkpoint_at <= processed:
                checkpoint_at += CHECKPOINT_EVERY

# ---- Cache speichern (nur eindeutige Clips, die aktuell vorkommen)
save_probability_cache(
    probs_by_wav,
    CACHE_NPY,
    CACHE_IDX,
    keep_names=unique_wavs,
)

# ---- Mapping anwenden
for r in rows:
    cat, conf, top, _ = pc.classify_probs(probs_by_wav[r["WAV"]])
    r["Laermquelle_KI"] = cat
    r["KI_Konfidenz"]   = conf
    r["KI_AudioSet"]    = top

with open(path, "w", newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in fields})

from collections import Counter
print("\nKI-Verteilung:", dict(Counter(r["Laermquelle_KI"] for r in rows)))
print("-> aktualisiert:", path)
