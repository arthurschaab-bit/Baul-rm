# -*- coding: utf-8 -*-
"""Abhaengigkeitsgesteuerter Autolauf fuer die Schallmessung (v10).

Die fachlichen Berechnungen bleiben in den bewaehrten Einzelskripten. Dieses
Modul entscheidet nur, welche Schritte aufgrund geaenderter Eingaben wirklich
erneut laufen muessen.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import auto_pipeline_v8 as core


sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
VK = HERE.parent
BASE = VK.parent
OUTDIR = BASE / "Aufbereit_v2"
LOGDIR = OUTDIR / "autolauf_v10"
STATE_PATH = OUTDIR / "automation_state_v10.json"
FALLBACK_STATE_PATH = OUTDIR / "automation_state_v8.json"
TEMPLATE_CONFIG_PATH = HERE / "pipeline_config_v10.json"
CONFIG_PATH = OUTDIR / "pipeline_config_v10.runtime.json"
VERSION = "v10.2.1"
RUN_ID = core.now_stamp()

RUN_LOG = core.RUN_LOG
WARNINGS = core.WARNINGS
RUN_LOG.clear()
WARNINGS.clear()
core.RUN_ID = RUN_ID

CONTROL_FILES = [
    VK / "messpositionen.csv",
    VK / "belege.csv",
    VK / "Laermquellen_Verknuepfung_backup.xlsx",
]

CORE_BACKUP_FILES = [
    VK / "master_index.csv",
    VK / "relevante_ereignisse.csv",
    VK / "episoden.csv",
    VK / "dauerlaerm.csv",
    VK / "Laermquellen_Verknuepfung.xlsx",
    VK / "messpositionen.csv",
    CONFIG_PATH,
]

OUTPUT_PREFIX = os.environ.get("BAUL_RM_OUTPUT_PREFIX", "Schallmessung")

DEFAULT_CONFIG: dict[str, Any] = {
    "version": "v10.2.1",
    "version_str": "07_gesamtbericht v10.2.1 (2026-07) [gesamtbericht_lib_v4]",
    "gesamtbericht_pdf": f"Gesamtbericht_{OUTPUT_PREFIX}_v10.pdf",
    "gesamtbericht_pdf_ohne_wav": f"Gesamtbericht_{OUTPUT_PREFIX}_v10_ohne_WAV.pdf",
    "manifest_csv": "Rohdaten_Manifest_v10.csv",
    "video_audit_csv": "Videobelege_Pruefung_v10.csv",
    "relevant_threshold_dba": 60.0,
    "daily_report_script": "06_report_v2.py",
    "relevant_script": "03_relevant_v7.py",
    "dauerlaerm_script": "05_dauerlaerm_v7.py",
    "gesamtbericht_script": "07_gesamtbericht_v10.py",
    "openai_clusters": {
        "enabled": False,
        "from": "2026-07-08",
        "model": "gpt-audio-1.5",
    },
    "coverage_valid": 0.90,
    "coverage_window": 0.70,
    "daily_workers": 3,
    "notes": [
        "Pipeline v10: lokaler Arbeitscache, inkrementelle Stufen und portable Hashes.",
        "Die fachlichen Pegel- und Bewertungsformeln entsprechen der verifizierten v9-Basis.",
    ],
}


def rel(path: Path) -> str:
    return core.rel(path)


def read_json(path: Path, default: Any) -> Any:
    return core.read_json(path, default)


def previous_state() -> tuple[dict[str, Any], bool]:
    if STATE_PATH.exists():
        state = read_json(STATE_PATH, {})
        return (state if isinstance(state, dict) else {}), False
    if FALLBACK_STATE_PATH.exists():
        state = read_json(FALLBACK_STATE_PATH, {})
        return (state if isinstance(state, dict) else {}), True
    return {}, False


def compare_files(
    paths: list[Path],
    state: dict[str, Any],
    key: str,
    *,
    migrated: bool,
) -> list[str]:
    previous = state.get(key)
    if not isinstance(previous, list):
        if migrated and state.get("last_run"):
            try:
                last_run = dt.datetime.fromisoformat(str(state["last_run"]))
                return [
                    rel(path)
                    for path in paths
                    if path.exists()
                    and dt.datetime.fromtimestamp(path.stat().st_mtime) > last_run
                ]
            except (TypeError, ValueError):
                return []
        return [rel(path) for path in paths if path.exists()]

    old = {
        item.get("path"): item
        for item in previous
        if isinstance(item, dict) and item.get("path")
    }
    changed: list[str] = []
    current_paths: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        signature = core.file_sig(path)
        current_paths.add(signature["path"])
        prior = old.get(signature["path"])
        if (
            not prior
            or prior.get("bytes") != signature["bytes"]
            or prior.get("mtime_ns") != signature["mtime_ns"]
        ):
            changed.append(signature["path"])
    for missing in sorted(set(old) - current_paths):
        changed.append(f"ENTFERNT: {missing}")
    return changed


def _path_name(change: str) -> str:
    return change.removeprefix("ENTFERNT: ")


def audio_trigger_changes(
    discovery: dict[str, Any],
    raw_changes: list[str],
) -> list[str]:
    zip_days = set(discovery["zip_by_nominal_day"])
    trigger: list[str] = []
    for change in raw_changes:
        clean = _path_name(change)
        lower = clean.lower()
        if lower.endswith(".zip"):
            trigger.append(change)
            continue
        if lower.endswith(".csv"):
            day = core.parse_csv_day(Path(clean))
            if day in zip_days:
                trigger.append(change)
    return trigger


def backup_core_files() -> Path:
    backup_dir = OUTDIR / "archiv_v10" / RUN_ID
    backup_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in CORE_BACKUP_FILES:
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
            copied += 1
    RUN_LOG.append(
        {
            "step": "backup",
            "ok": True,
            "detail": f"{copied} Dateien nach {rel(backup_dir)}",
        }
    )
    return backup_dir


def write_pipeline_config(discovery: dict[str, Any]) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    for source in (TEMPLATE_CONFIG_PATH, CONFIG_PATH):
        old = read_json(source, {})
        if isinstance(old, dict):
            for key, value in old.items():
                if key not in {"report_days", "detected_inputs", "missing_nominal_zip_days"}:
                    config[key] = value
    cluster_config = config.get("openai_clusters", {})
    cluster_config = dict(cluster_config) if isinstance(cluster_config, dict) else {}
    cluster_config.update(
        {
            "enabled": os.environ.get("BAUL_RM_OPENAI_CLUSTER_ENABLED", "0") == "1",
            "from": os.environ.get("BAUL_RM_OPENAI_CLUSTER_FROM", "2026-07-08"),
            "model": os.environ.get("BAUL_RM_OPENAI_AUDIO_MODEL", "gpt-audio-1.5"),
        }
    )
    config["openai_clusters"] = cluster_config
    config["version"] = DEFAULT_CONFIG["version"]
    config["version_str"] = DEFAULT_CONFIG["version_str"]
    config["gesamtbericht_pdf"] = DEFAULT_CONFIG["gesamtbericht_pdf"]
    config["gesamtbericht_pdf_ohne_wav"] = DEFAULT_CONFIG["gesamtbericht_pdf_ohne_wav"]
    config.update(
        {
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "report_days": sorted(discovery["csv_by_day"], reverse=True),
            "detected_inputs": discovery["summary"],
            "missing_nominal_zip_days": [
                row["day"]
                for row in discovery["summary"]
                if row["csv_count"] and not row["zip_count_nominal"]
            ],
        }
    )
    core.write_json(CONFIG_PATH, config)
    RUN_LOG.append(
        {
            "step": "config",
            "ok": True,
            "detail": f"{len(config['report_days'])} Berichtstage",
        }
    )
    return config


def save_state(
    discovery: dict[str, Any],
    outputs: dict[str, str],
    pending_audio: list[str],
) -> None:
    data = {
        "version": VERSION,
        "last_run": dt.datetime.now().isoformat(timespec="seconds"),
        "raw_files": [core.file_sig(path) for path in discovery["raw_files"]],
        "control_files": [
            core.file_sig(path) for path in CONTROL_FILES if path.exists()
        ],
        "days": discovery["days"],
        "outputs": outputs,
        "pending_audio": sorted(set(pending_audio)),
        "warnings": WARNINGS,
    }
    core.write_json(STATE_PATH, data)


def write_bautagebuch_vorschlaege(discovery: dict[str, Any]) -> Path:
    positions, _ = core.read_positions()
    pos_by_day = {row.get("Datum"): row for row in positions}
    out = OUTDIR / "Bautagebuch_Vorschlaege_v10.md"
    lines = [
        "# Bautagebuch - Vorschlaege v10",
        "",
        f"Automatisch erstellt: {dt.datetime.now().strftime('%d.%m.%Y %H:%M')}",
        "",
        "Messwerte und technische Details stehen im Schallbericht. "
        "Hier werden nur Beobachtungshinweise vorgeschlagen.",
        "",
    ]
    for day in sorted((d for d in discovery["days"] if d > "2026-06-26"), reverse=True):
        stats = core.read_csv_stats(day)
        position = pos_by_day.get(day, {})
        lines.extend(
            [
                f"## {dt.date.fromisoformat(day).strftime('%d.%m.%Y')}",
                "",
                f"- Messposition: {position.get('Position', 'noch zu pruefen')} "
                f"({position.get('Umgebung', 'unbekannt')})",
            ]
        )
        if stats.get("samples"):
            lines.append(f"- Erfassung: {stats['start']}-{stats['end']} Uhr.")
        if not discovery["zip_by_nominal_day"].get(day):
            lines.append("- Kein nominal passendes WAV-ZIP vorhanden.")
        lines.extend(
            [
                "- Bitte Wahrnehmung, Fensterzustand, betroffene Raeume und Nutzung ergaenzen.",
                "",
            ]
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    RUN_LOG.append(
        {"step": "bautagebuch_vorschlaege", "ok": True, "detail": rel(out)}
    )
    return out


def write_run_report(
    discovery: dict[str, Any],
    raw_changes: list[str],
    control_changes: list[str],
    outputs: dict[str, str],
    backup_dir: Path,
) -> Path:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    report = LOGDIR / f"autolauf_v10_{RUN_ID}.md"
    lines = [
        f"# Autolauf v10 - {RUN_ID}",
        "",
        f"Lokaler Arbeitsordner: `{BASE}`",
        f"Sicherung: `{rel(backup_dir)}`",
        "",
        "## Aenderungen",
        "",
    ]
    changes = raw_changes + [f"STEUERDATEI: {item}" for item in control_changes]
    lines.extend([f"- `{item}`" for item in changes] or ["- Keine."])
    lines.extend(
        [
            "",
            "## Schritte",
            "",
            "| Schritt | Status | Sekunden | Detail |",
            "|---|---|---:|---|",
        ]
    )
    for entry in RUN_LOG:
        detail = entry.get("detail") or entry.get("output_tail", "")
        if isinstance(detail, list):
            detail = " ".join(str(item) for item in detail)
        detail = str(detail).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {entry.get('step')} | {'OK' if entry.get('ok') else 'FEHLER'} | "
            f"{entry.get('seconds', '')} | {detail[:240]} |"
        )
    lines.extend(["", "## Warnungen", ""])
    lines.extend([f"- {item}" for item in WARNINGS] or ["- Keine."])
    lines.extend(["", "## Ausgaben", ""])
    lines.extend([f"- {key}: `{value}`" for key, value in outputs.items()])
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def mark_skipped(step: str, reason: str) -> None:
    RUN_LOG.append({"step": step, "ok": True, "detail": f"uebersprungen ({reason})"})
    print(f"\n[{step}] uebersprungen: {reason}")


def main() -> int:
    args = set(sys.argv[1:])
    force_full = "--full" in args
    reports_only = "--reports-only" in args
    skip_audio = "--skip-audio" in args
    code_changed = "--code-changed" in args
    external_control_changed = "--control-changed" in args
    force_openai = "--openai-clusters" in args

    OUTDIR.mkdir(parents=True, exist_ok=True)
    LOGDIR.mkdir(parents=True, exist_ok=True)
    discovery = core.discover_inputs()
    state, migrated = previous_state()
    raw_changes = compare_files(
        discovery["raw_files"], state, "raw_files", migrated=migrated
    )
    control_changes = compare_files(
        CONTROL_FILES, state, "control_files", migrated=migrated
    )
    if external_control_changed and not control_changes:
        control_changes = ["externer Synchronisationshinweis"]
    pending_before = [
        str(item) for item in state.get("pending_audio", []) if str(item)
    ]

    any_change = bool(
        force_full
        or reports_only
        or code_changed
        or raw_changes
        or control_changes
        or pending_before
        or force_openai
    )
    print(f"Schallmessung Autolauf {VERSION} - {RUN_ID}")
    print(
        f"Erkannte Tage: {len(discovery['days'])}; "
        f"Rohdaten-Aenderungen: {len(raw_changes)}; "
        f"Steuerdateien: {len(control_changes)}"
    )
    if not any_change:
        print("Keine Aenderung erkannt. Alle Auswertungsschritte bleiben unveraendert.")
        return 0

    for row in discovery["summary"]:
        if row["day"] > core.BASELINE_REPORT_END and row["csv_count"] and not row["zip_count_nominal"]:
            WARNINGS.append(
                f"{row['day']}: CSV vorhanden, aber kein nominal passendes WAV-ZIP."
            )

    backup_dir = backup_core_files()
    core.ensure_positions(sorted(discovery["csv_by_day"]))
    config = write_pipeline_config(discovery)
    threshold = str(config.get("relevant_threshold_dba", 60.0))

    raw_days = core.changed_days_from_paths(raw_changes)
    audio_changes = sorted(
        set(pending_before + audio_trigger_changes(discovery, raw_changes))
    )
    audio_needed = bool(force_full or audio_changes)
    data_needed = bool(force_full or code_changed or raw_changes)
    reports_all = bool(
        force_full or code_changed or control_changes or force_openai or reports_only
    )

    outputs: dict[str, str] = {}
    pending_after: list[str] = pending_before
    try:
        if data_needed or not (VK / "master_index.csv").exists():
            core.run_step("01_index", [str(HERE / "01_build_index.py")])
        else:
            mark_skipped("01_index", "Rohdaten unveraendert")

        if raw_changes or force_full:
            core.run_step("meteo_dwd", [str(HERE / "meteo_dwd.py")], required=False)
        else:
            mark_skipped("meteo_dwd", "keine neuen Messtage")

        if skip_audio and audio_needed:
            pending_after = sorted(set(audio_changes or pending_before or ["Voll-Lauf ausstehend"]))
            mark_skipped("03_relevant", "--skip-audio; fuer naechsten Lauf vorgemerkt")
        elif audio_needed:
            core.run_step(
                "03_relevant",
                [str(HERE / config["relevant_script"]), threshold],
            )
            core.run_step(
                "07_reclassify",
                [str(HERE / "07_reclassify.py")],
                required=False,
            )
            pending_after = []
        else:
            mark_skipped("03_relevant", "keine Audio-relevante Aenderung")

        openai_config = config.get("openai_clusters", {})
        openai_enabled = bool(openai_config.get("enabled", False))
        openai_from = str(openai_config.get("from", "2026-07-08"))
        openai_model = str(openai_config.get("model", "gpt-audio-1.5"))
        openai_package = OUTDIR / f"OpenAI_Cluster_ab_{openai_from.replace('-', '')}"
        openai_needed = (
            openai_enabled
            and (
                force_full
                or code_changed
                or audio_needed
                or force_openai
                or not (openai_package / "laufinfo.json").exists()
            )
        )
        if openai_needed:
            core.run_step(
                "09_openai_clusters",
                [str(HERE / "09_openai_clusters.py"), "--from-date", openai_from, "--model", openai_model],
                required=False,
            )
        elif openai_enabled:
            mark_skipped("09_openai_clusters", "Clusterpaket bereits aktuell")

        downstream_needed = bool(data_needed or control_changes or audio_needed or openai_needed)
        if (VK / "Laermquellen_Verknuepfung_backup.xlsx").exists() and downstream_needed:
            core.run_step(
                "10_import_geprueft",
                [str(HERE / "10_import_geprueft.py")],
                required=False,
            )

        if downstream_needed:
            core.run_step(
                "05_dauerlaerm",
                [str(HERE / config["dauerlaerm_script"])],
            )
            core.run_step("08_tiefbohrer", [str(HERE / "08_tiefbohrer.py")])
            core.run_step("04_excel", [str(HERE / "04_excel.py")])
        elif reports_only:
            mark_skipped("05_dauerlaerm", "--reports-only")
            mark_skipped("08_tiefbohrer", "--reports-only")
            core.run_step("04_excel", [str(HERE / "04_excel.py")])
        else:
            mark_skipped("05_dauerlaerm", "Daten unveraendert")
            mark_skipped("08_tiefbohrer", "Daten unveraendert")
            mark_skipped("04_excel", "Daten unveraendert")

        days_to_run: list[str] = []
        for day in config["report_days"]:
            if not discovery["csv_by_day"].get(day):
                continue
            if reports_only:
                mark_skipped(f"06_report_{day}", "--reports-only")
                continue
            if reports_all or day in raw_days:
                days_to_run.append(day)
            else:
                mark_skipped(f"06_report_{day}", "Eingaben unveraendert")
        print(
            f"\nTagesberichte: {len(days_to_run)} zu erneuern, "
            f"{len(config['report_days']) - len(days_to_run)} uebersprungen."
        )
        workers = max(1, min(6, int(config.get("daily_workers", 3))))
        if days_to_run:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        core.run_step,
                        f"06_report_{day}",
                        [str(HERE / config["daily_report_script"]), day],
                        False,
                    ): day
                    for day in days_to_run
                }
                for future in as_completed(futures):
                    future.result()

        core.run_step(
            "09_uebersicht",
            [str(HERE / "09_uebersicht.py")],
            required=False,
        )
        core.run_step(
            "07_gesamtbericht_v10",
            [str(HERE / config["gesamtbericht_script"])],
            env={"SCHALLBERICHT_CONFIG": str(CONFIG_PATH)},
        )
        core.run_step(
            "07_gesamtbericht_v10_ohne_wav",
            [str(HERE / "07_gesamtbericht_v10_ohne_wav.py")],
            required=False,
            env={"SCHALLBERICHT_CONFIG": str(CONFIG_PATH)},
        )
        bautagebuch = write_bautagebuch_vorschlaege(discovery)

        outputs = {
            "Gesamtbericht": rel(OUTDIR / config["gesamtbericht_pdf"]),
            "Gesamtbericht ohne WAV": rel(
                OUTDIR / config["gesamtbericht_pdf_ohne_wav"]
            ),
            "Rohdaten-Manifest": rel(OUTDIR / config["manifest_csv"]),
            "Excel-Arbeitsmappe": rel(VK / "Laermquellen_Verknuepfung.xlsx"),
            "Bautagebuch-Vorschlaege": rel(bautagebuch),
            "Konfiguration": rel(CONFIG_PATH),
        }
        if openai_enabled:
            outputs["OpenAI-Audio-Cluster"] = rel(openai_package)
        save_state(discovery, outputs, pending_after)
        report = write_run_report(
            discovery, raw_changes, control_changes, outputs, backup_dir
        )
        outputs["Laufbericht"] = rel(report)
        print("\nAutolauf fertig.")
        for key, value in outputs.items():
            print(f"  {key}: {value}")
        return 0
    except Exception as exc:
        WARNINGS.append(f"ABBRUCH: {exc}")
        RUN_LOG.append(
            {"step": "abbruch", "ok": False, "detail": traceback.format_exc()}
        )
        outputs = {
            "Konfiguration": rel(CONFIG_PATH),
            "Sicherung": rel(backup_dir),
        }
        report = write_run_report(
            discovery, raw_changes, control_changes, outputs, backup_dir
        )
        print(f"\nAutolauf abgebrochen: {exc}")
        print(f"Laufbericht: {rel(report)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
