# -*- coding: utf-8 -*-
"""OpenAI-gestuetzte WAV-Clustererkennung fuer Baustellenlaerm.

Die lokalen PANNs-Wahrscheinlichkeiten bilden akustisch aehnliche Gruppen. Nur
wenige repraesentative WAVs je Gruppe werden anschliessend von einem Audio-Modell
klassifiziert. API-Antworten werden pro Cluster gecacht. Der fachlich festgelegte
Startdatum ist standardmaessig der 08.07.2026 (einschliesslich).
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import wave
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
VK = HERE.parent
BASE = VK.parent
EVENTS = VK / "relevante_ereignisse.csv"
PROB_NPY = VK / "panns_probs.npy"
PROB_IDX = VK / "panns_probs_index.json"
DEFAULT_FROM = "2026-07-08"
DEFAULT_MODEL = "gpt-audio-1.5"
PROMPT_VERSION = "baustelle-mischgeraeusche-v2"
CATEGORIES = [
    "Bagger",
    "Bohrgeraet/schweres Geraet",
    "Schweres Baugeraet/sonstige Maschine",
    "Motor/Diesel",
    "Schlagen/Bohren",
    "Saege",
    "Fahrzeug",
    "Signal/Warnton",
    "Sprache",
    "Umgebung/Sonstiges",
    "Unklar/Mischgeraeusch",
]
CATEGORY_ALIASES = {
    "Bohrgerät/schweres Gerät": "Bohrgeraet/schweres Geraet",
    "Bohrgerät / schweres Gerät": "Bohrgeraet/schweres Geraet",
    "Schweres Baugerät": "Schweres Baugeraet/sonstige Maschine",
    "Sonstige Baumaschine": "Schweres Baugeraet/sonstige Maschine",
    "Säge": "Saege",
    "Unklar": "Unklar/Mischgeraeusch",
    "Mischgeräusch": "Unklar/Mischgeraeusch",
}


def read_event_rows(path: Path, from_date: str) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [
            dict(row)
            for row in csv.DictReader(handle)
            if (row.get("Datum") or "") >= from_date and row.get("WAV")
        ]


def load_selected_probabilities(
    rows: list[dict[str, str]],
    npy_path: Path = PROB_NPY,
    index_path: Path = PROB_IDX,
) -> tuple[list[str], np.ndarray]:
    names_all = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(names_all, list):
        raise ValueError(f"Ungueltiger PANNs-Index: {index_path}")
    matrix = np.load(npy_path, mmap_mode="r")
    if matrix.ndim != 2 or matrix.shape[0] != len(names_all):
        raise ValueError("PANNs-Cache und Index sind inkonsistent")
    wanted = list(dict.fromkeys(row["WAV"] for row in rows))
    lookup = {name: i for i, name in enumerate(names_all)}
    missing = [name for name in wanted if name not in lookup]
    if missing:
        print(
            f"WARNUNG: {len(missing)} WAVs ab dem Startdatum fehlen im PANNs-Cache; "
            "sie werden nicht geclustert."
        )
    names = [name for name in wanted if name in lookup]
    indices = np.asarray([lookup[name] for name in names], dtype=np.int64)
    return names, np.asarray(matrix[indices], dtype=np.float32)


def build_cluster_features(
    probabilities: np.ndarray,
    *,
    projection_dim: int = 64,
    seed: int = 42,
    chunk_size: int = 4096,
) -> np.ndarray:
    """Robuste, kompakte Signaturen aus 527 PANNs-Ausgaben."""
    if probabilities.ndim != 2 or len(probabilities) == 0:
        raise ValueError("Keine PANNs-Wahrscheinlichkeiten zum Clustern")
    transformed = np.log1p(np.maximum(probabilities, 0.0) * 100.0)
    mean = transformed.mean(axis=0, dtype=np.float64).astype(np.float32)
    std = transformed.std(axis=0, dtype=np.float64).astype(np.float32)
    active = std > 1e-5
    if not np.any(active):
        raise ValueError("PANNs-Cache enthaelt keine variierenden Merkmale")
    dim = min(projection_dim, int(active.sum()))
    rng = np.random.default_rng(seed)
    projection = rng.normal(
        0.0, 1.0 / np.sqrt(float(active.sum())), (int(active.sum()), dim)
    ).astype(np.float32)
    features = np.empty((len(transformed), dim), dtype=np.float32)
    for start in range(0, len(transformed), chunk_size):
        stop = min(len(transformed), start + chunk_size)
        z = (transformed[start:stop, active] - mean[active]) / std[active]
        block = z @ projection
        block /= np.linalg.norm(block, axis=1, keepdims=True) + 1e-9
        features[start:stop] = block
    return features


def _kmeans_pp(sample: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    centers = np.empty((k, sample.shape[1]), dtype=np.float32)
    centers[0] = sample[int(rng.integers(len(sample)))]
    best = np.maximum(0.0, 1.0 - sample @ centers[0])
    for index in range(1, k):
        total = float(best.sum())
        if total <= 1e-12:
            centers[index] = sample[int(rng.integers(len(sample)))]
        else:
            centers[index] = sample[int(rng.choice(len(sample), p=best / total))]
        best = np.minimum(best, np.maximum(0.0, 1.0 - sample @ centers[index]))
    return centers


def cluster_features(
    features: np.ndarray,
    *,
    k: int = 120,
    seed: int = 42,
    fit_limit: int = 12000,
    iterations: int = 25,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Deterministische Uebersegmentierung auf einer repraesentativen Teilmenge."""
    if len(features) < 2:
        raise ValueError("Zu wenige Clips zum Clustern")
    k = max(2, min(k, len(features)))
    rng = np.random.default_rng(seed)
    if len(features) > fit_limit:
        fit_indices = np.sort(rng.choice(len(features), fit_limit, replace=False))
        sample = features[fit_indices]
    else:
        sample = features
    centers = _kmeans_pp(sample, k, rng)
    previous: np.ndarray | None = None
    for _ in range(iterations):
        labels = np.argmax(sample @ centers.T, axis=1).astype(np.int32)
        if previous is not None and np.array_equal(labels, previous):
            break
        previous = labels
        similarity = sample @ centers.T
        for cluster in range(k):
            members = sample[labels == cluster]
            if len(members):
                center = members.mean(axis=0)
                centers[cluster] = center / (np.linalg.norm(center) + 1e-9)
            else:
                own = similarity[np.arange(len(sample)), labels]
                centers[cluster] = sample[int(np.argmin(own))]
    labels_all = np.argmax(features @ centers.T, axis=1).astype(np.int32)
    own_similarity = np.einsum("ij,ij->i", features, centers[labels_all])
    return centers, labels_all, own_similarity


def representative_indices(
    labels: np.ndarray,
    similarity: np.ndarray,
    cluster: int,
    *,
    first_round: int = 3,
    second_round: int = 3,
) -> tuple[list[int], list[int]]:
    members = np.where(labels == cluster)[0]
    order = members[np.argsort(similarity[members])[::-1]]
    if len(order) == 0:
        return [], []

    def picks(percentiles: list[float]) -> list[int]:
        positions = {
            min(len(order) - 1, int(round(p * (len(order) - 1)))) for p in percentiles
        }
        return [int(order[pos]) for pos in sorted(positions)]

    core = picks([0.0, 0.10, 0.25])[:first_round]
    more = [i for i in picks([0.40, 0.65, 0.85]) if i not in core][:second_round]
    return core, more


def canonical_wav_bytes(path: Path, max_seconds: float = 8.0) -> bytes:
    """Begrenzt API-Audio auf maximal acht Sekunden und erhaelt PCM-Parameter."""
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.readframes(min(source.getnframes(), int(rate * max_seconds)))
    out = io.BytesIO()
    with wave.open(out, "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(width)
        target.setframerate(rate)
        target.writeframes(frames)
    return out.getvalue()


def resolve_wav_path(
    row: dict[str, str],
    cache_dir: Path,
    *,
    working_dir: Path = VK,
    zip_root: Path = BASE,
) -> Path:
    """Liefert eine WAV-Datei direkt oder stellt sie aus dem Tages-ZIP bereit."""
    relative = row.get("WAV_Pfad", "")
    candidate = working_dir / relative if relative else Path()
    if relative and candidate.is_file():
        return candidate

    wav_name = (row.get("WAV") or "").strip()
    day = (row.get("Datum") or "").strip()
    if not wav_name or len(day) != 10:
        raise FileNotFoundError(f"WAV-Pfad unvollstaendig: {wav_name or '?'}")
    zip_path = zip_root / f"Laermprotokoll_{day[8:10]}.{day[5:7]}.{day[:4]}.zip"
    if not zip_path.is_file():
        raise FileNotFoundError(f"Tages-ZIP fehlt fuer {wav_name}: {zip_path}")

    target = cache_dir / day / wav_name
    if target.is_file():
        return target
    with zipfile.ZipFile(zip_path) as archive:
        members = [name for name in archive.namelist() if Path(name).name == wav_name]
        if len(members) != 1:
            raise FileNotFoundError(
                f"{wav_name} im Tages-ZIP nicht eindeutig gefunden ({len(members)} Treffer)"
            )
        payload = archive.read(members[0])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, target)
    return target


def system_prompt() -> str:
    categories = ", ".join(CATEGORIES)
    return f"""Du klassifizierst kurze Aussenaufnahmen einer Schallmessung.
Das Mikrofon zeigt in Richtung einer aktiven Baustelle. Typische Baustellengeraeusche
koennen gleichzeitig mit allgemeinen Aussengeraeuschen wie Strassenverkehr, Wind,
Voegeln oder Sprache vorkommen. Beurteile deshalb jede Stichprobe als moegliches
Mischgeraeusch und trenne primaere und parallele Quellen soweit akustisch moeglich.
Am Messort gibt es keinen Zug. Train/Rail-aehnliche Klangmerkmale sind daher als
moegliche schwere, rollende, rotierende oder metallische Baustellenmaschine zu
deuten und nicht als reale Zugquelle.
Hoher dB(A)-Pegel, lange Dauer oder tieffrequentes Rumpeln allein sind KEIN Beleg
fuer einen Tiefbohrer. Waehle Bohrgeraet/schweres Geraet nur bei passenden
akustischen Merkmalen. Erlaubte Hauptlabels: {categories}.
Antworte ausschliesslich als JSON-Objekt mit: label, confidence (0..1), homogeneous
(boolean), sample_labels (Liste je Stichprobe), secondary_sources (Liste), summary
(kurze deutsche Begruendung ohne Rechtsbewertung)."""


def _normalise_result(data: dict[str, Any], sample_count: int) -> dict[str, Any]:
    label = CATEGORY_ALIASES.get(str(data.get("label", "")).strip(), str(data.get("label", "")).strip())
    if label not in CATEGORIES:
        label = "Unklar/Mischgeraeusch"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    sample_labels = data.get("sample_labels")
    if not isinstance(sample_labels, list):
        sample_labels = []
    sample_labels = [
        CATEGORY_ALIASES.get(str(item).strip(), str(item).strip()) for item in sample_labels
    ][:sample_count]
    secondary = data.get("secondary_sources")
    if not isinstance(secondary, list):
        secondary = []
    return {
        "label": label,
        "confidence": confidence,
        "homogeneous": bool(data.get("homogeneous", False)),
        "sample_labels": sample_labels,
        "secondary_sources": [str(item)[:80] for item in secondary[:6]],
        "summary": str(data.get("summary", ""))[:600],
    }


def _parse_json_content(content: str, sample_count: int) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:].lstrip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Audio-Modell lieferte kein JSON-Objekt")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Audio-Modell lieferte kein JSON-Objekt")
    return _normalise_result(data, sample_count)


def openai_audio_request(
    *,
    api_key: str,
    model: str,
    prompt: str,
    samples: list[tuple[dict[str, str], Path]],
    timeout: int = 180,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for number, (row, path) in enumerate(samples, 1):
        content.append(
            {
                "type": "text",
                "text": (
                    f"Stichprobe {number}: {row.get('Datum', '')} {row.get('Uhrzeit', '')}, "
                    f"Pegel {row.get('dBA', '?')} dB(A)."
                ),
            }
        )
        content.append(
            {
                "type": "input_audio",
                "input_audio": {
                    "data": base64.b64encode(canonical_wav_bytes(path)).decode("ascii"),
                    "format": "wav",
                },
            }
        )
    payload = {
        "model": model,
        "modalities": ["text"],
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": content},
        ],
        "max_completion_tokens": 900,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            text = body["choices"][0]["message"]["content"]
            return _parse_json_content(text, len(samples))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")[:1000]
            last_error = RuntimeError(f"OpenAI HTTP {exc.code}: {details}")
            if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                break
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < 3:
            time.sleep(2**attempt)
    raise RuntimeError(f"OpenAI-Audioanfrage fehlgeschlagen: {last_error}")

def safe_openai_audio_request(**kwargs: Any) -> dict[str, Any]:
    """Laesst einen einzelnen API-Fehler nicht die restliche Clusterloop stoppen."""
    try:
        return openai_audio_request(**kwargs)
    except Exception as exc:
        return {
            "label": "Unklar/Mischgeraeusch",
            "confidence": 0.0,
            "homogeneous": False,
            "sample_labels": [],
            "secondary_sources": [],
            "summary": f"API-Fehler: {exc}"[:600],
            "api_error": str(exc)[:1000],
        }


def cluster_signature(model: str, from_date: str, names: list[str]) -> str:
    payload = json.dumps(
        {"model": model, "from": from_date, "prompt": PROMPT_VERSION, "samples": names},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def write_semicolon_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    os.replace(temporary, path)


def apply_cluster_results(
    *,
    event_path: Path,
    from_date: str,
    model: str,
    wav_to_cluster: dict[str, str],
    results: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    with event_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]
        fields = list(reader.fieldnames or [])
    new_columns = ["Laermquelle_Cluster", "Cluster_ID", "Cluster_Verifikation"]
    for column in new_columns:
        if column not in fields:
            fields.append(column)
    applied = conflicts = 0
    for row in rows:
        if (row.get("Datum") or "") < from_date:
            continue
        cluster = wav_to_cluster.get(row.get("WAV", ""))
        result = results.get(cluster or "")
        if not cluster or not result or result.get("status") != "ki_bestaetigt":
            continue
        label = str(result["label"])
        manual = (row.get("Laermquelle_geprueft") or "").strip()
        if manual and manual != label:
            conflicts += 1
            continue
        row["Laermquelle_Cluster"] = label
        row["Cluster_ID"] = cluster
        row["Cluster_Verifikation"] = (
            f"OpenAI-Audio-Cluster ({cluster}; {model}; Konfidenz "
            f"{float(result['confidence']):.2f}; automatisch, nicht manuell geprueft)"
        )
        applied += 1
    if not applied:
        return 0, conflicts
    backup = event_path.with_name(
        f"{event_path.stem}.backup_openai_cluster_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    )
    shutil.copy2(event_path, backup)
    temporary = event_path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    os.replace(temporary, event_path)
    return applied, conflicts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-date",
        default=os.environ.get("BAUL_RM_OPENAI_CLUSTER_FROM", DEFAULT_FROM),
    )
    parser.add_argument("--model", default=os.environ.get("BAUL_RM_OPENAI_AUDIO_MODEL", DEFAULT_MODEL))
    parser.add_argument("--k", type=int, default=120)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--max-clusters", type=int, default=0)
    parser.add_argument("--min-confidence", type=float, default=0.72)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)

    rows = read_event_rows(EVENTS, args.from_date)
    names, probabilities = load_selected_probabilities(rows)
    print(f"OpenAI-Cluster: {len(names)} WAVs ab einschliesslich {args.from_date}")
    features = build_cluster_features(probabilities)
    _, labels, similarity = cluster_features(features, k=args.k)
    by_wav: dict[str, dict[str, str]] = {}
    for row in rows:
        by_wav.setdefault(row["WAV"], row)

    output = BASE / "Aufbereit_v2" / f"OpenAI_Cluster_ab_{args.from_date.replace('-', '')}"
    cache_dir = output / "api_cache"
    wav_cache = output / "_wav_cache"
    output.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    use_api = bool(api_key) and not args.prepare_only
    if not use_api:
        reason = "--prepare-only" if args.prepare_only else "OPENAI_API_KEY fehlt"
        print(f"API-Loop wird vorbereitet, aber nicht ausgefuehrt: {reason}")

    assignment_rows: list[dict[str, Any]] = []
    overview_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    wav_to_cluster: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}
    cluster_ids = sorted(set(int(value) for value in labels))
    if args.max_clusters > 0:
        cluster_ids = cluster_ids[: args.max_clusters]

    for ordinal, cluster in enumerate(cluster_ids, 1):
        cluster_id = f"C{cluster:03d}"
        members = np.where(labels == cluster)[0]
        core, more = representative_indices(
            labels,
            similarity,
            cluster,
            first_round=args.samples,
            second_round=args.samples,
        )
        sample_indices = core + more
        sample_names = [names[index] for index in sample_indices]
        signature = cluster_signature(args.model, args.from_date, sample_names)
        cache_path = cache_dir / f"{cluster_id}.json"
        cached: dict[str, Any] | None = None
        if cache_path.is_file():
            try:
                candidate = json.loads(cache_path.read_text(encoding="utf-8"))
                if (
                    candidate.get("signature") == signature
                    and not (candidate.get("result") or {}).get("api_error")
                ):
                    cached = candidate
            except (OSError, ValueError, json.JSONDecodeError):
                cached = None

        result: dict[str, Any]
        if cached is not None and cached.get("result"):
            result = dict(cached["result"])
        elif use_api:
            first_samples = [
                (by_wav[names[index]], resolve_wav_path(by_wav[names[index]], wav_cache))
                for index in core
                if names[index] in by_wav
            ]
            result = safe_openai_audio_request(
                api_key=api_key,
                model=args.model,
                prompt=(
                    f"Analysiere Cluster {cluster_id} mit {len(members)} aehnlichen Clips. "
                    "Die folgenden kernnahen Stichproben sollen gemeinsam beurteilt werden."
                ),
                samples=first_samples,
            )
            rounds = 1
            if (
                more
                and (not result["homogeneous"] or result["confidence"] < args.min_confidence)
            ):
                second_samples = [
                    (by_wav[names[index]], resolve_wav_path(by_wav[names[index]], wav_cache))
                    for index in core + more
                    if names[index] in by_wav
                ]
                result = safe_openai_audio_request(
                    api_key=api_key,
                    model=args.model,
                    prompt=(
                        f"Zweite Pruefrunde fuer Cluster {cluster_id}. Das erste Urteil war: "
                        f"{json.dumps(result, ensure_ascii=False)}. Beurteile jetzt auch mittlere "
                        "und randnahe Stichproben; markiere Mischcluster konsequent als inhomogen."
                    ),
                    samples=second_samples,
                )
                rounds = 2
            result["rounds"] = rounds
            result["status"] = (
                "ki_bestaetigt"
                if result["homogeneous"]
                and result["confidence"] >= args.min_confidence
                and result["label"] != "Unklar/Mischgeraeusch"
                else "offen"
            )
            atomic_json(
                cache_path,
                {
                    "signature": signature,
                    "model": args.model,
                    "from": args.from_date,
                    "prompt_version": PROMPT_VERSION,
                    "samples": sample_names,
                    "result": result,
                },
            )
        else:
            result = {
                "label": "",
                "confidence": 0.0,
                "homogeneous": False,
                "sample_labels": [],
                "secondary_sources": [],
                "summary": "",
                "rounds": 0,
                "status": "wartet_auf_api",
            }
        results[cluster_id] = result

        ki = Counter((by_wav[names[index]].get("Laermquelle_KI") or "?") for index in members)
        majority, majority_count = ki.most_common(1)[0] if ki else ("?", 0)
        overview_rows.append(
            {
                "Cluster": cluster_id,
                "Ereignisse": len(members),
                "Homogenitaet_lokal": f"{float(similarity[members].mean()):.3f}",
                "PANNs_Mehrheit": majority,
                "PANNs_Anteil": f"{100 * majority_count / max(1, len(members)):.0f}%",
                "OpenAI_Label": result.get("label", ""),
                "OpenAI_Konfidenz": f"{float(result.get('confidence', 0.0)):.2f}",
                "OpenAI_Homogen": result.get("homogeneous", False),
                "Status": result.get("status", ""),
                "Runden": result.get("rounds", 0),
                "Stichproben": ", ".join(sample_names),
                "Nebenquellen": ", ".join(result.get("secondary_sources", [])),
                "Kurzbegruendung": result.get("summary", ""),
            }
        )
        review_rows.append(overview_rows[-1])
        for index in members:
            wav_to_cluster[names[index]] = cluster_id
            assignment_rows.append(
                {
                    "WAV": names[index],
                    "Datum": by_wav[names[index]].get("Datum", ""),
                    "Cluster": cluster_id,
                    "Aehnlichkeit": f"{float(similarity[index]):.4f}",
                }
            )
        print(
            f"  {ordinal:03d}/{len(cluster_ids):03d} {cluster_id}: {len(members)} Clips, "
            f"{result.get('status')} {result.get('label', '')}",
            flush=True,
        )

    write_semicolon_csv(
        output / "cluster_zuordnung.csv",
        assignment_rows,
        ["WAV", "Datum", "Cluster", "Aehnlichkeit"],
    )
    fields = [
        "Cluster",
        "Ereignisse",
        "Homogenitaet_lokal",
        "PANNs_Mehrheit",
        "PANNs_Anteil",
        "OpenAI_Label",
        "OpenAI_Konfidenz",
        "OpenAI_Homogen",
        "Status",
        "Runden",
        "Stichproben",
        "Nebenquellen",
        "Kurzbegruendung",
    ]
    write_semicolon_csv(output / "cluster_uebersicht.csv", overview_rows, fields)
    write_semicolon_csv(output / "pruefliste.csv", review_rows, fields)
    atomic_json(
        output / "laufinfo.json",
        {
            "from": args.from_date,
            "model": args.model,
            "prompt_version": PROMPT_VERSION,
            "clips": len(names),
            "clusters": len(cluster_ids),
            "api_executed": use_api,
            "status_counts": dict(Counter(result.get("status", "") for result in results.values())),
        },
    )

    applied, conflicts = apply_cluster_results(
        event_path=EVENTS,
        from_date=args.from_date,
        model=args.model,
        wav_to_cluster=wav_to_cluster,
        results=results,
    )
    print(f"Clusterpaket: {output}")
    print(f"In Ereignistabelle uebernommen: {applied}; manuelle Konflikte: {conflicts}")
    if not use_api:
        print(
            "Naechster Schritt: OPENAI_API_KEY lokal setzen und dieses Skript erneut starten; "
            "das Startdatum bleibt unveraendert."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
