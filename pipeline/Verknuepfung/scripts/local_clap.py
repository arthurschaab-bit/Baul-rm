# -*- coding: utf-8 -*-
"""Lokale CLAP-Zero-Shot-Klassifikation fuer repraesentative WAVs."""
from __future__ import annotations

import json
import os
import wave
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from scipy.signal import resample_poly


DEFAULT_MODEL = "laion/clap-htsat-unfused"
PROMPT_VERSION = "baustelle-clap-v1"

PROMPTS: dict[str, list[str]] = {
    "Bagger": [
        "an excavator digging and moving earth on a construction site",
        "a tracked excavator or backhoe operating nearby",
        "a construction excavator bucket scraping soil and rubble",
    ],
    "Bohrgeraet/schweres Geraet": [
        "a deep drilling rig drilling foundations on a construction site",
        "a large rotary construction drilling machine operating",
        "heavy foundation drilling equipment with engine and drill noise",
    ],
    "Motor/Diesel": [
        "a diesel engine or electric generator running continuously",
        "an idling construction machine engine",
        "a low frequency industrial motor running",
    ],
    "Schlagen/Bohren": [
        "hammering drilling or jackhammer work on a construction site",
        "a power drill and impact tools hitting concrete",
        "construction workers using a jackhammer or hammer",
    ],
    "Saege": [
        "a circular saw cutting wood or construction material",
        "a power saw operating on a construction site",
        "sawing and cutting material with a loud machine",
    ],
    "Fahrzeug": [
        "road traffic with cars trucks buses or a passing train",
        "a truck or car driving past outdoors",
        "urban traffic and heavy vehicles on a street",
    ],
    "Signal/Warnton": [
        "a reversing alarm warning beep horn or siren",
        "construction vehicle backup beeps",
        "a loud warning signal or vehicle horn",
    ],
    "Sprache": [
        "people speaking shouting or having a conversation outdoors",
        "human voices and speech outside",
        "construction workers talking or calling to each other",
    ],
    "Umgebung/Sonstiges": [
        "wind birds rain and ordinary outdoor ambience",
        "quiet environmental background noise outside",
        "non construction outdoor sounds such as birds or wind",
    ],
}


def load_wav(path: Path, *, target_rate: int = 48000, max_seconds: float = 10.0) -> np.ndarray:
    """Laedt PCM-WAV robust als Mono-float32 fuer CLAP."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(
            min(handle.getnframes(), int(handle.getframerate() * max_seconds))
        )
    dtype_by_width = {1: np.uint8, 2: np.int16, 4: np.int32}
    if width not in dtype_by_width:
        raise ValueError(f"Nicht unterstuetzte PCM-Breite {width}: {path}")
    raw = np.frombuffer(frames, dtype=dtype_by_width[width])
    if width == 1:
        audio = (raw.astype(np.float32) - 128.0) / 128.0
    else:
        audio = raw.astype(np.float32) / float(2 ** (8 * width - 1))
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != target_rate:
        audio = resample_poly(audio, target_rate, rate).astype(np.float32)
    return np.asarray(audio, dtype=np.float32)


class ClapZeroShot:
    """Laedt CLAP einmal und klassifiziert WAVs in Batches."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        import torch
        from transformers import ClapModel, ClapProcessor

        self.torch = torch
        self.model_name = model_name
        self.processor = ClapProcessor.from_pretrained(model_name)
        self.model = ClapModel.from_pretrained(model_name)
        self.model.eval()
        self.categories = list(PROMPTS)

        prompts: list[str] = []
        prompt_categories: list[int] = []
        for category_index, category in enumerate(self.categories):
            for prompt in PROMPTS[category]:
                prompts.append(prompt)
                prompt_categories.append(category_index)
        encoded = self.processor(text=prompts, return_tensors="pt", padding=True)
        with torch.inference_mode():
            text_output = self.model.get_text_features(**encoded)
            text_features = getattr(text_output, "pooler_output", text_output)
        text_features = torch.nn.functional.normalize(text_features, dim=-1)
        class_features = []
        for category_index in range(len(self.categories)):
            indices = [
                index
                for index, value in enumerate(prompt_categories)
                if value == category_index
            ]
            vector = text_features[indices].mean(dim=0)
            class_features.append(torch.nn.functional.normalize(vector, dim=-1))
        self.class_features = torch.stack(class_features)
        self.logit_scale = float(self.model.logit_scale_a.exp().detach().cpu())

    def classify_arrays(
        self,
        arrays: Sequence[np.ndarray],
        *,
        batch_size: int = 8,
    ) -> np.ndarray:
        blocks: list[np.ndarray] = []
        for start in range(0, len(arrays), batch_size):
            batch = list(arrays[start : start + batch_size])
            inputs = self.processor(
                audio=batch,
                sampling_rate=48000,
                return_tensors="pt",
                padding=True,
            )
            with self.torch.inference_mode():
                audio_output = self.model.get_audio_features(**inputs)
                audio_features = getattr(audio_output, "pooler_output", audio_output)
                audio_features = self.torch.nn.functional.normalize(
                    audio_features, dim=-1
                )
                logits = self.logit_scale * audio_features @ self.class_features.T
                scores = self.torch.softmax(logits, dim=-1)
            blocks.append(scores.detach().cpu().numpy().astype(np.float32))
        if not blocks:
            return np.empty((0, len(self.categories)), dtype=np.float32)
        return np.vstack(blocks)

    def classify_paths(
        self,
        paths: Sequence[Path],
        *,
        batch_size: int = 8,
    ) -> np.ndarray:
        arrays = [load_wav(path) for path in paths]
        return self.classify_arrays(arrays, batch_size=batch_size)


def aggregate_scores(
    scores: np.ndarray,
    categories: Sequence[str],
    *,
    panns_candidate: str = "",
    min_confidence: float = 0.72,
    min_consensus: float = 0.60,
) -> dict[str, Any]:
    """Verdichtet die CLAP-Ergebnisse mehrerer Clustervertreter."""
    values = np.asarray(scores, dtype=np.float32)
    if values.ndim != 2 or not len(values) or values.shape[1] != len(categories):
        raise ValueError("Ungueltige CLAP-Scorematrix")
    mean_scores = values.mean(axis=0)
    order = np.argsort(mean_scores)[::-1]
    winner = int(order[0])
    candidate = str(categories[winner])
    top_score = float(mean_scores[winner])
    second_score = float(mean_scores[int(order[1])]) if len(order) > 1 else 0.0
    margin = max(0.0, top_score - second_score)
    votes = np.argmax(values, axis=1)
    consensus = Counter(int(value) for value in votes).most_common(1)[0][1] / len(values)
    agreement = candidate == panns_candidate

    confidence = float(
        np.clip(
            0.40 * min(1.0, top_score / 0.50)
            + 0.30 * consensus
            + 0.20 * min(1.0, margin / 0.20)
            + 0.10 * float(agreement),
            0.0,
            1.0,
        )
    )
    automatic = bool(
        confidence >= min_confidence
        and consensus >= min_consensus
        and top_score >= 0.24
        and margin >= 0.035
    )
    # Die spezifischste und folgenreichste Maschinenklasse braucht eine
    # deutlichere akustische Trennung als allgemeine Kategorien.
    if candidate == "Bohrgeraet/schweres Geraet":
        automatic = bool(
            automatic
            and confidence >= max(min_confidence, 0.80)
            and top_score >= 0.41
            and margin >= 0.15
            and consensus >= 0.999
        )

    secondary = [
        str(categories[int(index)])
        for index in order[1:4]
        if mean_scores[int(index)] >= max(0.10, top_score * 0.45)
    ]
    sample_labels = [str(categories[int(index)]) for index in votes]
    return {
        "label": candidate if automatic else "Unklar/Mischgeraeusch",
        "candidate": candidate,
        "confidence": round(confidence, 4),
        "status": "automatisch" if automatic else "pruefen",
        "homogeneous": consensus >= min_consensus,
        "consensus": round(consensus, 4),
        "margin": round(margin, 4),
        "clap_score": round(top_score, 4),
        "secondary_sources": secondary,
        "sample_labels": sample_labels,
        "score_detail": {
            str(categories[index]): round(float(mean_scores[index]), 4)
            for index in range(len(categories))
        },
        "summary": (
            f"CLAP-Kandidat {candidate}; Modellanteil {top_score:.0%}, "
            f"Abstand {margin:.0%}, Vertreter-Konsens {consensus:.0%}; "
            f"PANNs-Vergleich {panns_candidate or 'ohne'}"
            f"{' (gleich)' if agreement else ''}. Pegel und Dauer unbenutzt."
        ),
    }


def load_score_cache(
    matrix_path: Path,
    index_path: Path,
    meta_path: Path,
    *,
    model_name: str,
) -> dict[str, np.ndarray]:
    if not matrix_path.is_file() or not index_path.is_file() or not meta_path.is_file():
        return {}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if (
        meta.get("model") != model_name
        or meta.get("prompt_version") != PROMPT_VERSION
        or meta.get("categories") != list(PROMPTS)
    ):
        return {}
    names = json.loads(index_path.read_text(encoding="utf-8"))
    matrix = np.load(matrix_path, allow_pickle=False)
    if (
        not isinstance(names, list)
        or matrix.ndim != 2
        or matrix.shape != (len(names), len(PROMPTS))
    ):
        raise ValueError("CLAP-Cache ist inkonsistent")
    return {
        str(name): np.asarray(matrix[index], dtype=np.float32)
        for index, name in enumerate(names)
    }


def save_score_cache(
    scores: dict[str, np.ndarray],
    matrix_path: Path,
    index_path: Path,
    meta_path: Path,
    *,
    model_name: str,
) -> None:
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    names = list(scores)
    matrix = (
        np.vstack([scores[name] for name in names]).astype(np.float32)
        if names
        else np.empty((0, len(PROMPTS)), dtype=np.float32)
    )
    suffix = f".tmp-{os.getpid()}"
    matrix_tmp = matrix_path.with_name(matrix_path.name + suffix)
    index_tmp = index_path.with_name(index_path.name + suffix)
    meta_tmp = meta_path.with_name(meta_path.name + suffix)
    try:
        with matrix_tmp.open("wb") as handle:
            np.save(handle, matrix, allow_pickle=False)
        index_tmp.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
        meta_tmp.write_text(
            json.dumps(
                {
                    "model": model_name,
                    "prompt_version": PROMPT_VERSION,
                    "categories": list(PROMPTS),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(matrix_tmp, matrix_path)
        os.replace(index_tmp, index_path)
        os.replace(meta_tmp, meta_path)
    finally:
        matrix_tmp.unlink(missing_ok=True)
        index_tmp.unlink(missing_ok=True)
        meta_tmp.unlink(missing_ok=True)
