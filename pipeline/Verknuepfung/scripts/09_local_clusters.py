# -*- coding: utf-8 -*-
"""Vollstaendig lokale WAV-Clustererkennung fuer Baustellenlaerm.

Die Stufe verwendet den vorhandenen PANNs-Cache, gruppiert akustisch aehnliche
WAVs ab dem konfigurierten Startdatum und bewertet jede Gruppe gemeinsam.
Uneindeutige Gruppen werden als Unklar/Mischgeraeusch markiert und in einer
kompakten Pruefliste ausgegeben. Es werden keine Daten an externe Dienste
gesendet.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from local_clap import (
    DEFAULT_MODEL as DEFAULT_CLAP_MODEL,
    PROMPTS as CLAP_PROMPTS,
    ClapZeroShot,
    aggregate_scores,
    load_score_cache,
    save_score_cache,
)
from local_cluster_model import CLASSIFIER_VERSION, classify_cluster

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
VK = HERE.parent
BASE = VK.parent
EVENTS = VK / "relevante_ereignisse.csv"
DEFAULT_FROM = "2026-07-08"
MODEL_NAME = "CLAP Zero-Shot + PANNs CNN14 (lokal)"

_spec = importlib.util.spec_from_file_location(
    "audio_cluster_helpers", HERE / "09_openai_clusters.py"
)
_helpers = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_helpers)


def apply_local_results(
    *,
    event_path: Path,
    from_date: str,
    wav_to_cluster: dict[str, str],
    results: dict[str, dict[str, Any]],
) -> tuple[int, int, bool]:
    """Schreibt lokale Ergebnisse idempotent und respektiert manuelle Labels."""
    with event_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]
        fields = list(reader.fieldnames or [])

    new_columns = [
        "Laermquelle_Cluster",
        "Cluster_ID",
        "Cluster_Verifikation",
        "Cluster_Kandidat",
        "Cluster_Konfidenz",
        "Cluster_Status",
        "Cluster_AudioSet",
    ]
    for column in new_columns:
        if column not in fields:
            fields.append(column)

    applied = conflicts = 0
    changed = False
    for row in rows:
        if (row.get("Datum") or "") < from_date:
            continue
        cluster_id = wav_to_cluster.get(row.get("WAV", ""))
        result = results.get(cluster_id or "")
        if not cluster_id or not result:
            continue

        candidate = str(result["candidate"])
        manual = (row.get("Laermquelle_geprueft") or "").strip()
        if manual:
            if manual != candidate:
                conflicts += 1
            desired_label = ""
        else:
            desired_label = str(result["label"])
            applied += 1

        desired = {
            "Laermquelle_Cluster": desired_label,
            "Cluster_ID": cluster_id,
            "Cluster_Verifikation": (
                f"Lokales Audio-Cluster ({cluster_id}; {CLASSIFIER_VERSION}; "
                f"Konfidenz {float(result['confidence']):.2f}; "
                f"Status {result['status']}; kein Pegel-/Dauernachweis)"
            ),
            "Cluster_Kandidat": candidate,
            "Cluster_Konfidenz": f"{float(result['confidence']):.4f}",
            "Cluster_Status": str(result["status"]),
            "Cluster_AudioSet": ", ".join(result.get("top_audioset", [])),
        }
        for column, value in desired.items():
            if row.get(column, "") != value:
                row[column] = value
                changed = True

    if not changed:
        return applied, conflicts, False

    backup = event_path.with_name(
        f"{event_path.stem}.backup_local_cluster_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    )
    shutil.copy2(event_path, backup)
    temporary = event_path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    os.replace(temporary, event_path)
    return applied, conflicts, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-date",
        default=os.environ.get("BAUL_RM_LOCAL_CLUSTER_FROM", DEFAULT_FROM),
    )
    parser.add_argument(
        "--k",
        type=int,
        default=int(os.environ.get("BAUL_RM_LOCAL_CLUSTER_COUNT", "120")),
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=float(os.environ.get("BAUL_RM_LOCAL_CLUSTER_CONFIDENCE", "0.72")),
    )
    parser.add_argument(
        "--min-consensus",
        type=float,
        default=float(os.environ.get("BAUL_RM_LOCAL_CLUSTER_CONSENSUS", "0.60")),
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=float(os.environ.get("BAUL_RM_LOCAL_CLUSTER_SIMILARITY", "0.35")),
    )
    parser.add_argument(
        "--clap-model",
        default=os.environ.get("BAUL_RM_LOCAL_CLAP_MODEL", DEFAULT_CLAP_MODEL),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("BAUL_RM_LOCAL_CLAP_BATCH_SIZE", "8")),
    )
    parser.add_argument("--max-clusters", type=int, default=0)
    parser.add_argument("--no-apply", action="store_true")
    args = parser.parse_args(argv)

    rows = _helpers.read_event_rows(EVENTS, args.from_date)
    names, probabilities = _helpers.load_selected_probabilities(rows)
    print(
        f"Lokale Audio-Cluster: {len(names)} WAVs ab einschliesslich "
        f"{args.from_date}; keine Cloud/API"
    )
    features = _helpers.build_cluster_features(probabilities)
    _, labels, similarities = _helpers.cluster_features(features, k=args.k)

    from panns_inference.config import labels as audioset_labels

    by_wav: dict[str, dict[str, str]] = {}
    for row in rows:
        by_wav.setdefault(row["WAV"], row)

    output = BASE / "Aufbereit_v2" / f"Local_Cluster_ab_{args.from_date.replace('-', '')}"
    output.mkdir(parents=True, exist_ok=True)
    assignment_rows: list[dict[str, Any]] = []
    overview_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    wav_to_cluster: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}

    cluster_ids = sorted(set(int(value) for value in labels))
    if args.max_clusters > 0:
        cluster_ids = cluster_ids[: args.max_clusters]

    cluster_data: list[dict[str, Any]] = []
    representative_names: list[str] = []
    for cluster in cluster_ids:
        members = np.where(labels == cluster)[0]
        core, _ = _helpers.representative_indices(
            labels, similarities, cluster, first_round=3, second_round=0
        )
        panns_result = classify_cluster(
            probabilities[members],
            audioset_labels,
            similarities[members],
            min_confidence=args.min_confidence,
            min_consensus=args.min_consensus,
            min_similarity=args.min_similarity,
        )
        sample_names = [names[index] for index in core]
        cluster_data.append(
            {
                "number": cluster,
                "members": members,
                "sample_names": sample_names,
                "panns": panns_result,
            }
        )
        representative_names.extend(sample_names)

    clap_cache = output / "clap_cache"
    score_matrix_path = clap_cache / "scores.npy"
    score_index_path = clap_cache / "scores_index.json"
    score_meta_path = clap_cache / "scores_meta.json"
    scores_by_wav = load_score_cache(
        score_matrix_path,
        score_index_path,
        score_meta_path,
        model_name=args.clap_model,
    )
    pending_names = [
        name for name in dict.fromkeys(representative_names) if name not in scores_by_wav
    ]
    if pending_names:
        print(
            f"CLAP: {len(pending_names)} neue Vertreter; Modell {args.clap_model} "
            "wird lokal geladen.",
            flush=True,
        )
        classifier = ClapZeroShot(args.clap_model)
        wav_cache = output / "_wav_cache"
        batch_size = max(1, args.batch_size)
        for start in range(0, len(pending_names), batch_size):
            batch_names = pending_names[start : start + batch_size]
            paths = [
                _helpers.resolve_wav_path(by_wav[name], wav_cache)
                for name in batch_names
            ]
            batch_scores = classifier.classify_paths(paths, batch_size=batch_size)
            for name, score in zip(batch_names, batch_scores):
                scores_by_wav[name] = score
            save_score_cache(
                scores_by_wav,
                score_matrix_path,
                score_index_path,
                score_meta_path,
                model_name=args.clap_model,
            )
            print(
                f"  CLAP-Vertreter: {min(start + len(batch_names), len(pending_names))}/"
                f"{len(pending_names)}",
                flush=True,
            )
    else:
        print(f"CLAP: alle {len(representative_names)} Vertreter aus lokalem Cache")

    for ordinal, item in enumerate(cluster_data, 1):
        cluster = int(item["number"])
        cluster_id = f"LC{cluster:03d}"
        members = item["members"]
        sample_names = item["sample_names"]
        panns_result = item["panns"]
        clap_matrix = np.vstack([scores_by_wav[name] for name in sample_names])
        result = aggregate_scores(
            clap_matrix,
            list(CLAP_PROMPTS),
            panns_candidate=str(panns_result["candidate"]),
            min_confidence=args.min_confidence,
            min_consensus=args.min_consensus,
        )
        result.update(
            {
                "mean_similarity": panns_result["mean_similarity"],
                "coverage": panns_result["coverage"],
                "top_audioset": panns_result["top_audioset"],
                "panns_candidate": panns_result["candidate"],
            }
        )
        if float(result["mean_similarity"]) < args.min_similarity:
            result["label"] = "Unklar/Mischgeraeusch"
            result["status"] = "pruefen"
            result["homogeneous"] = False
            result["summary"] += "; PANNs-Cluster ist akustisch zu inhomogen."
        results[cluster_id] = result
        row = {
            "Cluster": cluster_id,
            "Ereignisse": len(members),
            "Label": result["label"],
            "Kandidat": result["candidate"],
            "Konfidenz": f"{float(result['confidence']):.4f}",
            "Status": result["status"],
            "Konsens": f"{float(result['consensus']):.1%}",
            "Homogen": result["homogeneous"],
            "Aehnlichkeit": f"{float(result['mean_similarity']):.4f}",
            "Merkmalsabdeckung": f"{float(result['coverage']):.1%}",
            "CLAP_Score": f"{float(result['clap_score']):.4f}",
            "CLAP_Vertreter": ", ".join(result["sample_labels"]),
            "PANNs_Kandidat": result["panns_candidate"],
            "Nebenquellen": ", ".join(result["secondary_sources"]),
            "AudioSet_Top": ", ".join(result["top_audioset"]),
            "Stichproben": ", ".join(sample_names),
            "Kurzbegruendung": result["summary"],
        }
        overview_rows.append(row)
        if result["status"] != "automatisch":
            review_rows.append(row)

        for index in members:
            wav = names[index]
            wav_to_cluster[wav] = cluster_id
            assignment_rows.append(
                {
                    "WAV": wav,
                    "Datum": by_wav.get(wav, {}).get("Datum", ""),
                    "Cluster": cluster_id,
                    "Aehnlichkeit": f"{float(similarities[index]):.4f}",
                    "Label": result["label"],
                    "Kandidat": result["candidate"],
                    "Status": result["status"],
                }
            )
        print(
            f"  {ordinal:03d}/{len(cluster_ids):03d} {cluster_id}: "
            f"{len(members)} Clips, {result['status']} -> {result['label']} "
            f"(Kandidat {result['candidate']}, {float(result['confidence']):.2f})",
            flush=True,
        )
    assignment_fields = [
        "WAV",
        "Datum",
        "Cluster",
        "Aehnlichkeit",
        "Label",
        "Kandidat",
        "Status",
    ]
    overview_fields = [
        "Cluster",
        "Ereignisse",
        "Label",
        "Kandidat",
        "Konfidenz",
        "Status",
        "Konsens",
        "Homogen",
        "Aehnlichkeit",
        "Merkmalsabdeckung",
        "CLAP_Score",
        "CLAP_Vertreter",
        "PANNs_Kandidat",
        "Nebenquellen",
        "AudioSet_Top",
        "Stichproben",
        "Kurzbegruendung",
    ]
    _helpers.write_semicolon_csv(
        output / "cluster_zuordnung.csv", assignment_rows, assignment_fields
    )
    _helpers.write_semicolon_csv(
        output / "cluster_uebersicht.csv", overview_rows, overview_fields
    )
    _helpers.write_semicolon_csv(
        output / "pruefliste.csv", review_rows, overview_fields
    )
    _helpers.atomic_json(
        output / "laufinfo.json",
        {
            "from": args.from_date,
            "model": MODEL_NAME,
            "classifier_version": CLASSIFIER_VERSION,
            "clips": len(names),
            "clusters": len(cluster_ids),
            "configuration": {
                "cluster_count": args.k,
                "min_confidence": args.min_confidence,
                "min_consensus": args.min_consensus,
                "min_similarity": args.min_similarity,
                "clap_model": args.clap_model,
            },
            "status_counts": dict(
                Counter(result["status"] for result in results.values())
            ),
            "label_counts": dict(Counter(result["label"] for result in results.values())),
            "external_requests": 0,
            "events_applied": not args.no_apply,
        },
    )

    if args.no_apply:
        applied = conflicts = 0
        changed = False
        print("Pilotmodus: Ereignistabelle bleibt unveraendert.")
    else:
        applied, conflicts, changed = apply_local_results(
            event_path=EVENTS,
            from_date=args.from_date,
            wav_to_cluster=wav_to_cluster,
            results=results,
        )
    print(f"Lokales Clusterpaket: {output}")
    print(
        f"In Ereignistabelle uebernommen: {applied}; "
        f"manuelle Labels respektiert: {conflicts}; "
        f"Datei geaendert: {'ja' if changed else 'nein'}"
    )
    print(f"Pruefliste: {len(review_rows)} von {len(overview_rows)} Clustern")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
