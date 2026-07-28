from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .sync import (
    RunLock,
    SyncStats,
    bootstrap_files,
    publish_outputs,
    sync_control_files,
    sync_raw_files,
    sync_tree,
)


def _expand_path(value: str, base: Path | None = None) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute() and base is not None:
        expanded = base / expanded
    return expanded.resolve()


def _read_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Konfiguration fehlt: {path}. Kopiere settings.example.json nach "
            "settings.local.json und trage den Cloud-Ordner ein."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Konfiguration muss ein JSON-Objekt sein: {path}")
    return data

_MOJIBAKE_MARKERS = ("\u00c3", "\u00c2", "\u00e2")


def _repair_text_tree(value: Any) -> Any:
    """Repair common UTF-8/Windows-1252 mojibake in nested config values."""
    if isinstance(value, dict):
        return {key: _repair_text_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_repair_text_tree(item) for item in value]
    if not isinstance(value, str) or not any(
        marker in value for marker in _MOJIBAKE_MARKERS
    ):
        return value

    text = value
    for _ in range(3):
        old_score = sum(text.count(marker) for marker in _MOJIBAKE_MARKERS)
        improved = False
        for encoding in ("cp1252", "latin-1"):
            try:
                candidate = text.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            new_score = sum(candidate.count(marker) for marker in _MOJIBAKE_MARKERS)
            if new_score < old_score:
                text = candidate
                improved = True
                break
        if not improved:
            break
    return text


def _fmt_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def _check_disk_space(cache_root: Path, required_bytes: int, reserve_gb: float) -> None:
    probe = cache_root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    reserve = int(max(0.0, reserve_gb) * 1024**3)
    if required_bytes + reserve > free:
        raise RuntimeError(
            "Nicht genug lokaler Speicherplatz: benötigt werden "
            f"{_fmt_bytes(required_bytes)} plus {_fmt_bytes(reserve)} Reserve; "
            f"frei sind {_fmt_bytes(free)} auf {probe.anchor}."
        )
    print(
        f"Speicherplatz: {_fmt_bytes(free)} frei; "
        f"voraussichtlicher Transfer {_fmt_bytes(required_bytes)}."
    )


def _print_stats(label: str, stats: SyncStats) -> None:
    print(
        f"{label}: {stats.copied} kopiert ({_fmt_bytes(stats.bytes_copied)}), "
        f"{stats.skipped} unveraendert, {stats.quarantined} quarantiniert"
    )
    for warning in stats.warnings:
        print(f"  WARNUNG: {warning}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Schallmessung v10: Cloud-Daten inkrementell lokal auswerten."
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, help="Pfad zu settings.local.json")
    parser.add_argument("--cloud-root", help="Cloud-/Google-Drive-Quellordner")
    parser.add_argument("--cache-root", help="Persistenter lokaler Arbeitsordner")
    parser.add_argument("--full", action="store_true", help="Alle Auswertungsschritte erzwingen")
    parser.add_argument(
        "--skip-audio",
        action="store_true",
        help="WAV-Auswertung verschieben; sie bleibt fuer den naechsten Lauf vorgemerkt",
    )
    parser.add_argument("--sync-only", action="store_true", help="Nur lokal synchronisieren")
    parser.add_argument("--no-sync-back", action="store_true", help="Ergebnisse nicht zurueckkopieren")
    parser.add_argument("--no-prune", action="store_true", help="Cloud-seitig entfernte Rohdaten lokal aktiv lassen")
    parser.add_argument("--dry-run", action="store_true", help="Aenderungen nur anzeigen")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    config_path = (args.config or (repo_root / "settings.local.json")).resolve()
    config = _read_config(config_path)

    cloud_value = args.cloud_root or config.get("cloud_root")
    cache_value = args.cache_root or config.get(
        "cache_root", r"%LOCALAPPDATA%\Baul-rm\workspace"
    )
    if not cloud_value:
        raise ValueError("cloud_root fehlt in Konfiguration und Kommandozeile.")

    cloud_root = _expand_path(str(cloud_value), config_path.parent)
    cache_root = _expand_path(str(cache_value), config_path.parent)
    runtime_root = cache_root / "runtime"
    pipeline_source = repo_root / "pipeline"

    if not cloud_root.is_dir():
        raise FileNotFoundError(f"Cloud-Quellordner nicht gefunden: {cloud_root}")
    if not pipeline_source.is_dir():
        raise FileNotFoundError(f"Pipeline-Quellcode fehlt: {pipeline_source}")
    try:
        cache_root.relative_to(cloud_root)
    except ValueError:
        pass
    else:
        raise ValueError("cache_root darf nicht innerhalb des Cloud-Ordners liegen.")

    report = _repair_text_tree(config.get("report", {}))
    if not isinstance(report, dict):
        raise ValueError("report muss in der Konfiguration ein JSON-Objekt sein.")
    output_prefix = str(report.get("output_prefix", "Schallmessung"))
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", output_prefix):
        raise ValueError(
            "report.output_prefix darf nur Buchstaben, Zahlen, Punkt, Minus und Unterstrich enthalten."
        )
    report_env = {
        "BAUL_RM_ADDRESS": str(report.get("address", "Messadresse")),
        "BAUL_RM_TENANT": str(report.get("tenant", "Auftraggeber")),
        "BAUL_RM_SITE_TITLE": str(report.get("site_title", "Messstandort")),
        "BAUL_RM_LEGAL_NOTE": str(report.get("legal_note", "Gebietscharakter lokal dokumentiert.")),
        "BAUL_RM_SETUP_GROUPS_JSON": json.dumps(
            report.get("setup_groups", []), ensure_ascii=False
        ),
        "BAUL_RM_OUTPUT_PREFIX": output_prefix,
    }

    print(f"Baul-rm v{__version__}")
    print(f"Cloud: {cloud_root}")
    print(f"Lokal: {runtime_root}")

    lock_path = cache_root / ".baul-rm" / "run.lock"
    if args.dry_run:
        lock_context = _NullContext()
    else:
        lock_context = RunLock(lock_path)

    with lock_context:
        runtime_preexisting = (
            runtime_root / "Verknuepfung" / "scripts" / "auto_pipeline_v10.py"
        ).is_file()
        if not args.dry_run:
            runtime_root.mkdir(parents=True, exist_ok=True)

        if not args.dry_run:
            preview_bootstrap = bootstrap_files(cloud_root, runtime_root, dry_run=True)
            preview_control = sync_control_files(cloud_root, runtime_root, dry_run=True)
            preview_raw = sync_raw_files(
                cloud_root,
                runtime_root,
                prune=not args.no_prune,
                dry_run=True,
            )
            required = (
                preview_bootstrap.bytes_copied
                + preview_control.bytes_copied
                + preview_raw.bytes_copied
            )
            _check_disk_space(
                cache_root,
                required,
                float(config.get("minimum_free_gb", 5.0)),
            )

        code_stats = sync_tree(pipeline_source, runtime_root, dry_run=args.dry_run)
        _print_stats("Code", code_stats)

        bootstrap_stats = bootstrap_files(
            cloud_root, runtime_root, dry_run=args.dry_run
        )
        _print_stats("Startbestand", bootstrap_stats)

        control_stats = sync_control_files(
            cloud_root, runtime_root, dry_run=args.dry_run
        )
        _print_stats("Steuerdateien", control_stats)

        raw_stats = sync_raw_files(
            cloud_root,
            runtime_root,
            prune=not args.no_prune,
            dry_run=args.dry_run,
        )
        _print_stats("Rohdaten", raw_stats)

        if args.dry_run or args.sync_only:
            print("Synchronisation abgeschlossen; Pipeline nicht gestartet.")
            return 0

        pipeline = runtime_root / "Verknuepfung" / "scripts" / "auto_pipeline_v10.py"
        command = [sys.executable, str(pipeline)]
        if args.full:
            command.append("--full")
        if args.skip_audio:
            command.append("--skip-audio")
        if code_stats.copied and runtime_preexisting:
            command.append("--code-changed")
        if control_stats.copied:
            command.append("--control-changed")

        print("\nLokale Auswertung startet ...", flush=True)
        proc = subprocess.run(
            command,
            cwd=runtime_root,
            env={**os.environ, **report_env, "PYTHONIOENCODING": "utf-8"},
        )
        success = proc.returncode == 0

        sync_back = bool(config.get("sync_back", True)) and not args.no_sync_back
        if sync_back:
            output_stats = publish_outputs(
                runtime_root,
                cloud_root,
                success=success,
            )
            _print_stats("Ruecktransfer", output_stats)
        else:
            print("Ruecktransfer deaktiviert.")

        if success:
            print(f"Fertig. Lokaler Arbeitsordner: {runtime_root}")
        else:
            print(f"Pipeline fehlgeschlagen (Code {proc.returncode}). Lokale Logs: {runtime_root}")
        return proc.returncode


class _NullContext:
    def __enter__(self) -> "_NullContext":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
