# -*- coding: utf-8 -*-
"""
panns_classify.py -- Lärmquellen-Klassifikation mit vortrainiertem AudioSet-Modell
(PANNs CNN14).

CNN14 liefert je Clip Wahrscheinlichkeiten für 527 AudioSet-Klassen. Wir summieren
die Wahrscheinlichkeit verwandter Klassen je Baustellen-Kategorie und nehmen die
stärkste. Die rohen Top-Labels werden zur Kontrolle mitgeführt.

WICHTIG (Standort mit Tiefbohrer): Der dominante Klang ist ein
kontinuierliches tieffrequentes Rumpeln. AudioSet hat keine Baustellen-Klasse dafür
und ordnet es als "Train/Rail/Subway" ein. Dieser Cluster wird hier als
"Bohrgeraet/schweres Geraet" gewertet (die dominante Maschine vor Ort).
Die zu breite Oberklasse "Vehicle" wird bewusst NICHT verwendet.
"""
import os
import numpy as np
from scipy.signal import resample_poly

CAT_MAP = {
    # Schlagen / Bohren / Abbruch / Elektrowerkzeug (impulsiv)
    "Jackhammer": "Schlagen/Bohren", "Power tool": "Schlagen/Bohren",
    "Drill": "Schlagen/Bohren", "Hammer": "Schlagen/Bohren", "Tools": "Schlagen/Bohren",
    # Säge
    "Sawing": "Saege", "Chainsaw": "Saege", "Filing (rasp)": "Saege",
    # Motor / Diesel (Aggregat, Motorgeräusch)
    "Engine": "Motor/Diesel", "Light engine (high frequency)": "Motor/Diesel",
    "Medium engine (mid frequency)": "Motor/Diesel",
    "Heavy engine (low frequency)": "Motor/Diesel", "Idling": "Motor/Diesel",
    "Engine starting": "Motor/Diesel", "Engine knocking": "Motor/Diesel",
    "Accelerating, revving, vroom": "Motor/Diesel",
    # Bohrgerät / schweres Gerät: kontinuierliches Rumpeln (AudioSet: Zug/Gleis/U-Bahn)
    "Train": "Bohrgeraet/schweres Geraet", "Rail transport": "Bohrgeraet/schweres Geraet",
    "Railroad car, train wagon": "Bohrgeraet/schweres Geraet",
    "Subway, metro, underground": "Bohrgeraet/schweres Geraet",
    "Train wheels squealing": "Bohrgeraet/schweres Geraet",
    # Fahrzeug / LKW / Verkehr (spezifisch, NICHT die Oberklasse "Vehicle")
    "Truck": "Fahrzeug", "Bus": "Fahrzeug", "Car": "Fahrzeug",
    "Motor vehicle (road)": "Fahrzeug", "Air brake": "Fahrzeug",
    "Car passing by": "Fahrzeug", "Traffic noise, roadway noise": "Fahrzeug",
    "Tire squeal": "Fahrzeug",
    # Signal / Warnton (Rückfahrwarner, Hupe, Sirene)
    "Reversing beeps": "Signal/Warnton", "Beep, bleep": "Signal/Warnton",
    "Buzzer": "Signal/Warnton", "Vehicle horn, car horn, honking": "Signal/Warnton",
    "Air horn, truck horn": "Signal/Warnton", "Alarm": "Signal/Warnton",
    "Siren": "Signal/Warnton", "Fire engine, fire truck (siren)": "Signal/Warnton",
    "Emergency vehicle": "Signal/Warnton",
    # Sprache (nicht baustellenrelevant)
    "Speech": "Sprache", "Male speech, man speaking": "Sprache",
    "Female speech, woman speaking": "Sprache", "Child speech, kid speaking": "Sprache",
    "Conversation": "Sprache", "Narration, monologue": "Sprache",
    "Shout": "Sprache", "Yell": "Sprache", "Children shouting": "Sprache",
    "Screaming": "Sprache", "Babbling": "Sprache",
    # Umgebung / Sonstiges (nicht baustellenrelevant)
    "Wind": "Umgebung/Sonstiges", "Wind noise (microphone)": "Umgebung/Sonstiges",
    "Rain": "Umgebung/Sonstiges", "Bird": "Umgebung/Sonstiges",
    "Music": "Umgebung/Sonstiges", "Silence": "Umgebung/Sonstiges",
    "Animal": "Umgebung/Sonstiges", "Rustling leaves": "Umgebung/Sonstiges",
    "Vacuum cleaner": "Umgebung/Sonstiges", "Air conditioning": "Umgebung/Sonstiges",
    "Mechanical fan": "Umgebung/Sonstiges",
}
BAU_RELEVANT = ["Bohrgeraet/schweres Geraet", "Motor/Diesel", "Schlagen/Bohren",
                "Saege", "Fahrzeug", "Signal/Warnton"]
ALL_CATS = ["Bohrgeraet/schweres Geraet", "Motor/Diesel", "Schlagen/Bohren", "Saege",
            "Fahrzeug", "Signal/Warnton", "Sprache", "Umgebung/Sonstiges"]

_AT = None
_LABELS = None
_CAT_IDX = None

def _load_labels():
    """Lädt nur die AudioSet-Labelliste + baut CAT_IDX (OHNE schweres Modell)."""
    global _LABELS, _CAT_IDX
    if _LABELS is None:
        from panns_inference.config import labels
        _LABELS = labels
        _CAT_IDX = {}
        for i, l in enumerate(labels):
            c = CAT_MAP.get(l)
            if c:
                _CAT_IDX.setdefault(c, []).append(i)
    return _LABELS

def _ensure_model():
    global _AT
    if _AT is None:
        from panns_inference import AudioTagging
        ckpt = os.path.expanduser("~/panns_data/Cnn14_mAP=0.431.pth")
        _AT = AudioTagging(checkpoint_path=ckpt, device="cpu")
    _load_labels()
    return _AT

def mapping_coverage():
    _load_labels()
    have = set(_LABELS)
    return [k for k in CAT_MAP if k not in have]

def infer_probs(x, sr):
    """Roh-Wahrscheinlichkeiten (527,) für einen Clip."""
    _ensure_model()
    if sr != 32000:
        x = resample_poly(x, 32000, sr).astype(np.float32)
    else:
        x = np.asarray(x, dtype=np.float32)
    out, _ = _AT.inference(x[None, :])
    return out[0]

def infer_probs_batch(clips, sr):
    """Roh-Wahrscheinlichkeiten fuer gleich lange Clips als Stapel."""
    _ensure_model()
    prepared = []
    for clip in clips:
        if sr != 32000:
            clip = resample_poly(clip, 32000, sr).astype(np.float32)
        else:
            clip = np.asarray(clip, dtype=np.float32)
        prepared.append(clip)
    if not prepared:
        return np.empty((0, 527), dtype=np.float32)
    lengths = {len(clip) for clip in prepared}
    if len(lengths) != 1:
        raise ValueError("Batch enthaelt unterschiedlich lange Audio-Clips")
    out, _ = _AT.inference(np.stack(prepared))
    return np.asarray(out, dtype=np.float32)

def classify_probs(probs):
    """Aus Roh-Wahrscheinlichkeiten -> (kategorie, konfidenz, top_audioset, scores)."""
    _load_labels()
    scores = {c: float(np.asarray(probs)[ix].sum()) for c, ix in _CAT_IDX.items()}
    for c in ALL_CATS:
        scores.setdefault(c, 0.0)
    cat = max(scores, key=scores.get)
    if scores[cat] < 0.02:
        cat = "Umgebung/Sonstiges"
    top = np.argsort(probs)[::-1][:3]
    toplab = ", ".join(f"{_LABELS[i]} {probs[i]:.2f}" for i in top)
    return cat, round(float(scores[cat]), 3), toplab, scores

def classify_array(x, sr):
    return classify_probs(infer_probs(x, sr))


if __name__ == "__main__":
    miss = mapping_coverage()
    print("Mapping-Keys ohne AudioSet-Treffer:", miss if miss else "keine")
