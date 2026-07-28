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
import os, csv, wave, json
import numpy as np
import importlib.util

HERE = os.path.dirname(__file__)
VK   = os.path.abspath(os.path.join(HERE, ".."))
CACHE_NPY = os.path.join(VK, "panns_probs.npy")
CACHE_IDX = os.path.join(VK, "panns_probs_index.json")

spec = importlib.util.spec_from_file_location("pc", os.path.join(HERE, "panns_classify.py"))
pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)

# ---- Prob-Cache laden
probs_by_wav = {}
if os.path.exists(CACHE_NPY) and os.path.exists(CACHE_IDX):
    names = json.load(open(CACHE_IDX, encoding="utf-8"))
    mat = np.load(CACHE_NPY)
    probs_by_wav = {n: mat[i] for i, n in enumerate(names)}
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
todo = [r for r in rows if r["WAV"] not in probs_by_wav]
print(f"{len(rows)} Ereignisse, {len(todo)} brauchen neue Inferenz, "
      f"{len(rows)-len(todo)} aus Cache")
for k, r in enumerate(todo, 1):
    wp = os.path.join(VK, r["WAV_Pfad"])
    try:
        w = wave.open(wp, "rb"); sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)/32768.0
        w.close()
        probs_by_wav[r["WAV"]] = pc.infer_probs(x, sr).astype(np.float32)
    except Exception as e:
        probs_by_wav[r["WAV"]] = np.zeros(527, dtype=np.float32)
        print("  FEHLER", r["WAV"], e)
    if k % 200 == 0:
        print(f"  ... {k}/{len(todo)}", flush=True)

# ---- Cache speichern (nur Clips, die in den Ereignissen vorkommen)
wavs = [r["WAV"] for r in rows]
mat = np.vstack([probs_by_wav[w] for w in wavs]).astype(np.float32)
np.save(CACHE_NPY, mat)
json.dump(wavs, open(CACHE_IDX, "w", encoding="utf-8"))

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
