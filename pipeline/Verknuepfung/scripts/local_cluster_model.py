# -*- coding: utf-8 -*-
"""Lokale Clusterbewertung fuer Baustellen- und Aussengeraeusche.

Das Modul arbeitet ausschliesslich mit den bereits berechneten 527
AudioSet-Wahrscheinlichkeiten des PANNs-Modells. Es verwendet weder dB(A)-Pegel
noch Ereignisdauer als Hinweis auf einen Tiefbohrer. Da sich kein Zug in der
Umgebung befindet, werden Train/Rail-Ausgaben als akustische Fehlaehnlichkeit
schwerer Baustellenmaschinen behandelt. Sie sind weder Verkehrs- noch
Tiefbohrernachweis.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

import numpy as np


CLASSIFIER_VERSION = "baustelle-clap-v3"
UNCLEAR = "Unklar/Mischgeraeusch"

# Gewichtete AudioSet-Merkmale. Ein Maximum plus kleine Beitraege weiterer
# Treffer verhindert, dass Kategorien nur wegen vieler Synonyme gewinnen.
CATEGORY_LABELS: dict[str, dict[str, float]] = {
    "Schlagen/Bohren": {
        "Jackhammer": 1.20,
        "Drill": 1.15,
        "Hammer": 1.00,
        "Power tool": 0.95,
        "Tools": 0.70,
    },
    "Saege": {
        "Sawing": 1.15,
        "Chainsaw": 1.10,
        "Filing (rasp)": 0.65,
    },
    "Motor/Diesel": {
        "Heavy engine (low frequency)": 1.15,
        "Medium engine (mid frequency)": 1.05,
        "Light engine (high frequency)": 0.90,
        "Engine": 0.95,
        "Idling": 1.00,
        "Engine starting": 0.95,
        "Engine knocking": 0.90,
        "Accelerating, revving, vroom": 0.85,
    },
    "Schweres Baugeraet/sonstige Maschine": {
        # Kontextkorrektur: Am Messort gibt es keinen Zug. Diese AudioSet-
        # Rohlabels beschreiben hier nur eine akustische Aehnlichkeit zu
        # rollenden, rotierenden oder metallisch quietschenden Baumaschinen.
        "Rail transport": 1.00,
        "Train": 1.00,
        "Railroad car, train wagon": 1.00,
        "Subway, metro, underground": 0.95,
        "Train wheels squealing": 0.95,
        "Squeal": 0.55,
        "Pump (liquid)": 0.75,
    },
    "Fahrzeug": {
        "Vehicle": 0.85,
        "Motor vehicle (road)": 1.10,
        "Car": 1.00,
        "Truck": 1.10,
        "Bus": 1.00,
        "Traffic noise, roadway noise": 1.10,
        "Car passing by": 1.05,
        "Air brake": 0.95,
        "Tire squeal": 0.90,
    },
    "Signal/Warnton": {
        "Reversing beeps": 1.20,
        "Beep, bleep": 0.95,
        "Buzzer": 0.90,
        "Vehicle horn, car horn, honking": 1.10,
        "Air horn, truck horn": 1.10,
        "Alarm": 0.90,
        "Siren": 1.00,
        "Fire engine, fire truck (siren)": 1.00,
        "Emergency vehicle": 0.90,
    },
    "Sprache": {
        "Speech": 1.00,
        "Male speech, man speaking": 1.00,
        "Female speech, woman speaking": 1.00,
        "Child speech, kid speaking": 1.00,
        "Conversation": 1.05,
        "Narration, monologue": 0.90,
        "Shout": 1.00,
        "Yell": 1.00,
        "Children shouting": 1.00,
        "Screaming": 0.95,
        "Babbling": 0.90,
    },
    "Umgebung/Sonstiges": {
        "Wind": 1.00,
        "Wind noise (microphone)": 1.10,
        "Rain": 1.00,
        "Thunder": 0.90,
        "Bird": 1.00,
        "Animal": 0.90,
        "Music": 1.00,
        "Silence": 1.10,
        "Rustling leaves": 1.00,
        "Air conditioning": 0.85,
        "Mechanical fan": 0.85,
        "Vacuum cleaner": 0.80,
        "Sliding door": 0.75,
        "Microwave oven": 0.65,
        "Sewing machine": 0.65,
        "Printer": 0.70,
        "Squawk": 0.90,
        "Insect": 0.90,
        "Owl": 0.90,
        "Cricket": 0.90,
        "Cupboard open or close": 0.70,
    },
}

CATEGORIES = [
    "Bohrgeraet/schweres Geraet",
    "Schweres Baugeraet/sonstige Maschine",
    "Motor/Diesel",
    "Schlagen/Bohren",
    "Saege",
    "Fahrzeug",
    "Signal/Warnton",
    "Sprache",
    "Umgebung/Sonstiges",
]


def _weighted_top(values: np.ndarray) -> np.ndarray:
    """Kombiniert die drei staerksten Belege ohne Synonym-Bias."""
    if values.shape[1] == 0:
        return np.zeros(values.shape[0], dtype=np.float32)
    ordered = np.sort(values, axis=1)[:, ::-1]
    result = ordered[:, 0].copy()
    if ordered.shape[1] > 1:
        result += 0.25 * ordered[:, 1]
    if ordered.shape[1] > 2:
        result += 0.10 * ordered[:, 2]
    return result.astype(np.float32)


def evidence_matrix(
    probabilities: np.ndarray,
    label_names: Sequence[str],
    *,
    category_labels: Mapping[str, Mapping[str, float]] = CATEGORY_LABELS,
) -> tuple[np.ndarray, list[str], np.ndarray]:
    """Berechnet robuste Kategoriebelege je Clip.

    Die dritte Rueckgabe ist die Abdeckung: Wie gut erklaert die beste bekannte
    Kategorie das staerkste AudioSet-Merkmal des Clips?
    """
    values = np.asarray(probabilities, dtype=np.float32)
    if values.ndim == 1:
        values = values[None, :]
    if values.ndim != 2 or values.shape[1] != len(label_names):
        raise ValueError("Wahrscheinlichkeiten und AudioSet-Labels passen nicht zusammen")

    lookup = {name: index for index, name in enumerate(label_names)}
    base_categories = list(category_labels)
    scores: dict[str, np.ndarray] = {}
    for category, mapping in category_labels.items():
        weighted = [
            values[:, lookup[label]] * float(weight)
            for label, weight in mapping.items()
            if label in lookup
        ]
        block = np.stack(weighted, axis=1) if weighted else np.empty((len(values), 0))
        scores[category] = _weighted_top(block)

    # Ein schweres Bohrgeraet wird nur bei gleichzeitigem Bohr-/Werkzeug- UND
    # Motorbeleg vorgeschlagen. Pegel oder Dauer gehen hier bewusst nicht ein.
    drill_indices = [
        lookup[label]
        for label in ("Drill", "Power tool", "Tools")
        if label in lookup
    ]
    engine_indices = [
        lookup[label]
        for label in (
            "Heavy engine (low frequency)",
            "Medium engine (mid frequency)",
            "Engine",
            "Idling",
        )
        if label in lookup
    ]
    if drill_indices and engine_indices:
        drill = values[:, drill_indices].max(axis=1)
        engine = values[:, engine_indices].max(axis=1)
        composite = np.sqrt(drill * engine) * 1.30
        composite[(drill < 0.06) | (engine < 0.06)] = 0.0
    else:
        composite = np.zeros(len(values), dtype=np.float32)
    scores["Bohrgeraet/schweres Geraet"] = composite.astype(np.float32)

    ordered_scores = np.stack([scores[category] for category in CATEGORIES], axis=1)
    strongest_raw = np.maximum(values.max(axis=1), 1e-6)
    coverage = np.clip(ordered_scores.max(axis=1) / strongest_raw, 0.0, 1.0)
    return ordered_scores, list(CATEGORIES), coverage.astype(np.float32)


def classify_cluster(
    probabilities: np.ndarray,
    label_names: Sequence[str],
    similarities: np.ndarray | None = None,
    *,
    min_confidence: float = 0.72,
    min_consensus: float = 0.60,
    min_similarity: float = 0.35,
) -> dict[str, Any]:
    """Bewertet einen akustischen Cluster und kalibriert eine Pruefentscheidung."""
    values = np.asarray(probabilities, dtype=np.float32)
    if values.ndim != 2 or not len(values):
        raise ValueError("Cluster enthaelt keine Wahrscheinlichkeiten")

    evidence, categories, coverage = evidence_matrix(values, label_names)
    totals = evidence.sum(axis=1, keepdims=True)
    shares = np.divide(
        evidence,
        totals,
        out=np.zeros_like(evidence),
        where=totals > 1e-9,
    )
    clip_winners = np.argmax(shares, axis=1)
    counts = Counter(int(index) for index in clip_winners)
    majority_index, majority_count = counts.most_common(1)[0]
    consensus = majority_count / len(values)

    mean_shares = shares.mean(axis=0)
    order = np.argsort(mean_shares)[::-1]
    candidate_index = int(order[0])
    candidate = categories[candidate_index]
    top_share = float(mean_shares[candidate_index])
    second_share = float(mean_shares[int(order[1])]) if len(order) > 1 else 0.0
    margin = max(0.0, top_share - second_share)
    strength = float(np.median(evidence.max(axis=1)))
    covered = float(np.median(coverage))

    if similarities is None:
        mean_similarity = 1.0
    else:
        similarity_values = np.asarray(similarities, dtype=np.float32)
        if len(similarity_values) != len(values):
            raise ValueError("Aehnlichkeiten und Clusterlaenge passen nicht zusammen")
        mean_similarity = float(similarity_values.mean())

    similarity_quality = float(np.clip((mean_similarity - 0.15) / 0.70, 0.0, 1.0))
    margin_quality = float(np.clip(margin / 0.45, 0.0, 1.0))
    strength_quality = float(np.clip(strength / 0.30, 0.0, 1.0))
    confidence = float(
        np.clip(
            0.30 * consensus
            + 0.25 * margin_quality
            + 0.20 * covered
            + 0.15 * strength_quality
            + 0.10 * similarity_quality,
            0.0,
            1.0,
        )
    )
    homogeneous = bool(
        consensus >= min_consensus
        and mean_similarity >= min_similarity
        and top_share >= 0.42
        and candidate_index == majority_index
    )
    automatic = bool(
        homogeneous
        and confidence >= min_confidence
        and covered >= 0.40
        and candidate != UNCLEAR
    )

    secondary = [
        categories[int(index)]
        for index in order[1:4]
        if mean_shares[int(index)] >= max(0.12, top_share * 0.30)
    ]
    mean_probs = values.mean(axis=0)
    top_audio_indices = np.argsort(mean_probs)[::-1][:5]
    top_audio = [
        f"{label_names[int(index)]} {float(mean_probs[int(index)]):.2f}"
        for index in top_audio_indices
    ]
    final_label = candidate if automatic else UNCLEAR
    reason = (
        f"Kandidat {candidate}; Konsens {consensus:.0%}, Abstand "
        f"{margin:.2f}, Merkmalsabdeckung {covered:.0%}, "
        f"Cluster-Aehnlichkeit {mean_similarity:.2f}. "
        "Die Einstufung verwendet keine Pegel- oder Dauerregel."
    )
    return {
        "label": final_label,
        "candidate": candidate,
        "confidence": round(confidence, 4),
        "homogeneous": homogeneous,
        "consensus": round(consensus, 4),
        "mean_similarity": round(mean_similarity, 4),
        "coverage": round(covered, 4),
        "margin": round(margin, 4),
        "secondary_sources": secondary,
        "top_audioset": top_audio,
        "summary": reason,
        "status": "automatisch" if automatic else "pruefen",
    }

