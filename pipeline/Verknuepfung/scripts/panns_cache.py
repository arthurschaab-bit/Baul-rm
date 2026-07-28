# -*- coding: utf-8 -*-
"""Robuste Hilfsfunktionen fuer den PANNs-Wahrscheinlichkeitscache."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np


PROBABILITY_COUNT = 527


def unique_pending_rows(
    rows: Iterable[dict[str, str]],
    cached_names: Iterable[str],
) -> list[dict[str, str]]:
    """Liefert hoechstens eine noch nicht gecachte Zeile je WAV-Datei."""
    cached = set(cached_names)
    pending: dict[str, dict[str, str]] = {}
    for row in rows:
        name = (row.get("WAV") or "").strip()
        if name and name not in cached:
            pending.setdefault(name, row)
    return list(pending.values())


def load_probability_cache(
    npy_path: str | os.PathLike[str],
    index_path: str | os.PathLike[str],
) -> dict[str, np.ndarray]:
    """Laedt nur einen vollstaendigen, konsistenten Cache."""
    npy = Path(npy_path)
    index = Path(index_path)
    if not npy.exists() or not index.exists():
        return {}

    names = json.loads(index.read_text(encoding="utf-8"))
    matrix = np.load(npy, allow_pickle=False)
    if not isinstance(names, list):
        raise ValueError("Cache-Index ist keine Liste")
    if matrix.ndim != 2 or matrix.shape[1] != PROBABILITY_COUNT:
        raise ValueError(f"Ungueltige Cache-Matrix: {matrix.shape}")
    if len(names) != matrix.shape[0]:
        raise ValueError(
            f"Cache-Index und Matrix passen nicht zusammen: "
            f"{len(names)} != {matrix.shape[0]}"
        )

    return {
        str(name): np.asarray(matrix[pos], dtype=np.float32)
        for pos, name in enumerate(names)
    }


def save_probability_cache(
    probabilities: Mapping[str, np.ndarray],
    npy_path: str | os.PathLike[str],
    index_path: str | os.PathLike[str],
    *,
    keep_names: Sequence[str] | None = None,
) -> int:
    """Schreibt Matrix und Index ueber temporaere Dateien und benennt atomar um."""
    npy = Path(npy_path)
    index = Path(index_path)
    npy.parent.mkdir(parents=True, exist_ok=True)
    index.parent.mkdir(parents=True, exist_ok=True)

    source_names = keep_names if keep_names is not None else list(probabilities)
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in source_names:
        name = str(raw_name)
        if name in probabilities and name not in seen:
            names.append(name)
            seen.add(name)

    if names:
        matrix = np.vstack([probabilities[name] for name in names]).astype(np.float32)
    else:
        matrix = np.empty((0, PROBABILITY_COUNT), dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != PROBABILITY_COUNT:
        raise ValueError(f"Ungueltige Wahrscheinlichkeitsmatrix: {matrix.shape}")

    suffix = f".tmp-{os.getpid()}"
    npy_tmp = npy.with_name(npy.name + suffix)
    index_tmp = index.with_name(index.name + suffix)
    try:
        with npy_tmp.open("wb") as handle:
            np.save(handle, matrix, allow_pickle=False)
        index_tmp.write_text(
            json.dumps(names, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(npy_tmp, npy)
        os.replace(index_tmp, index)
    finally:
        npy_tmp.unlink(missing_ok=True)
        index_tmp.unlink(missing_ok=True)
    return len(names)
