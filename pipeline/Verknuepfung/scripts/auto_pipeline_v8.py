# -*- coding: utf-8 -*-
"""
auto_pipeline_v8.py

Automatischer Lauf fuer die Schallmessungs-Auswertung (v8).

Neu gegenueber v7 (aus REVIEW_PERFORMANCE_WARTBARKEIT.md, A4):
- Tagesberichte (06_report_v2) werden nur noch fuer Tage mit neuen/geaenderten
  Rohdaten regeneriert (oder alle mit --full).
- Die faelligen Tagesberichte laufen parallel (bis zu 3 Prozesse).
- Gesamtbericht: 07_gesamtbericht_v9.py (gesamtbericht_lib_v3, mit Hash-Cache).
- auto_pipeline_v7.py bleibt unangetastet.

Ziele:
- neue CSV- und ZIP-Rohdaten erkennen
- bestehende Auswertungstabellen vor Veraenderungen sichern
- Tagesliste fuer den Gesamtbericht automatisch schreiben
- bestehende Pipeline in der richtigen Reihenfolge starten
- Tages-PDFs, Gesamtbericht, Manifest und Bautagebuch-Vorschlaege erzeugen

Die fachlichen Pegel-Formeln bleiben in den vorhandenen Auswertungsskripten.
Dieses Skript ist die Orchestrierungsschicht.
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
VK = HERE.parent
BASE = VK.parent
OUTDIR = BASE / "Aufbereit_v2"
LOGDIR = OUTDIR / "autolauf_v8"
STATE_PATH = OUTDIR / "automation_state_v8.json"
CONFIG_PATH = HERE / "pipeline_config_v9.json"

VERSION = "v8"
PY = sys.executable
BASELINE_REPORT_END = "2026-06-26"

CORE_BACKUP_FILES = [
    VK / "master_index.csv",
    VK / "relevante_ereignisse.csv",
    VK / "episoden.csv",
    VK / "dauerlaerm.csv",
    VK / "Laermquellen_Verknuepfung.xlsx",
    VK / "messpositionen.csv",
    CONFIG_PATH,
]

DEFAULT_CONFIG: dict[str, Any] = {
    "version": "v9",
    "version_str": "07_gesamtbericht v9 (2026-07) [gesamtbericht_lib_v3]",
    "gesamtbericht_pdf": "Gesamtbericht_Schallmessung_v9.pdf",
    "manifest_csv": "Rohdaten_Manifest_v9.csv",
    "video_audit_csv": "Videobelege_Pruefung_v9.csv",
    "relevant_threshold_dba": 60.0,
    "daily_report_script": "06_report_v2.py",
    "relevant_script": "03_relevant_v7.py",
    "dauerlaerm_script": "05_dauerlaerm_v7.py",
    "gesamtbericht_script": "07_gesamtbericht_v9.py",
    "coverage_valid": 0.90,
    "coverage_window": 0.70,
    "notes": [
        "Diese Datei wird von auto_pipeline_v8.py aktualisiert.",
        "v8 (Bericht + Pipeline v7) bleibt als Referenz unangetastet.",
    ],
}

# Aus dem bestehenden To-Do: Referenz-/Bauruhe-Tage, sobald CSVs vorhanden sind.
POSITION_DEFAULTS = {
    "2026-06-27": {
        "Umgebung": "Aussen",
        "Position": "SO-Balkon",
        "Hinweis": (
            "Referenz-/Bauruhe-Messung laut To-Do; CSV automatisch erkannt. "
            "Kein WAV-ZIP im Messordner gefunden."
        ),
    },
    "2026-06-28": {
        "Umgebung": "Aussen",
        "Position": "NW-Balkon",
        "Hinweis": (
            "Referenz-/Bauruhe-Messung laut To-Do; CSV automatisch erkannt. "
            "Kein WAV-ZIP im Messordner gefunden."
        ),
    },
}


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


RUN_ID = now_stamp()
RUN_LOG: list[dict[str, Any]] = []
WARNINGS: list[str] = []


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(BASE))
    except ValueError:
        return str(p)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def file_sig(path: Path, with_hash: bool = False) -> dict[str, Any]:
    st = path.stat()
    sig = {
        "path": rel(path),
        "name": path.name,
        "bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }
    if with_hash:
        sig["sha256"] = sha256(path)
    return sig


def parse_csv_day(path: Path) -> str | None:
    m = re.match(r"^(20\d{2}-\d{2}-\d{2})\s+.*\.csv$", path.name, re.I)
    return m.group(1) if m else None


def parse_zip_nominal_day(path: Path) -> str | None:
    m = re.match(r"^Laermprotokoll_(\d{2})\.(\d{2})\.(20\d{2}).*\.zip$", path.name, re.I)
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return f"{yyyy}-{mm}-{dd}"


def discover_inputs() -> dict[str, Any]:
    csv_by_day: dict[str, list[Path]] = {}
    zip_by_nominal_day: dict[str, list[Path]] = {}
    raw_files: list[Path] = []

    for path in sorted(BASE.glob("20??-??-?? *.csv")):
        day = parse_csv_day(path)
        if not day:
            continue
        csv_by_day.setdefault(day, []).append(path)
        raw_files.append(path)

    for path in sorted(BASE.glob("Laermprotokoll_*.zip")):
        day = parse_zip_nominal_day(path)
        if day:
            zip_by_nominal_day.setdefault(day, []).append(path)
        raw_files.append(path)

    days = sorted(set(csv_by_day) | set(zip_by_nominal_day))
    summary = []
    for day in days:
        summary.append(
            {
                "day": day,
                "csv_count": len(csv_by_day.get(day, [])),
                "zip_count_nominal": len(zip_by_nominal_day.get(day, [])),
                "csv_files": [p.name for p in csv_by_day.get(day, [])],
                "zip_files_nominal": [p.name for p in zip_by_nominal_day.get(day, [])],
            }
        )

    return {
        "csv_by_day": csv_by_day,
        "zip_by_nominal_day": zip_by_nominal_day,
        "days": days,
        "summary": summary,
        "raw_files": raw_files,
    }


def compare_state(raw_files: list[Path]) -> list[str]:
    prev = read_json(STATE_PATH, {})
    prev_files = {f.get("path"): f for f in prev.get("raw_files", [])}
    changed: list[str] = []
    for p in raw_files:
        sig = file_sig(p)
        old = prev_files.get(sig["path"])
        if not old or old.get("bytes") != sig["bytes"] or old.get("mtime_ns") != sig["mtime_ns"]:
            changed.append(sig["path"])
    old_paths = set(prev_files)
    new_paths = {rel(p) for p in raw_files}
    for missing in sorted(old_paths - new_paths):
        changed.append(f"ENTFERNT: {missing}")
    return changed


def save_state(discovery: dict[str, Any], outputs: dict[str, str],
               skip_audio_flag: bool = False, changed: list[str] | None = None) -> None:
    # Bei --skip-audio werden nur die JETZT NEUEN/GEAENDERTEN ZIPs NICHT im State vermerkt, damit
    # ein spaeterer normaler Lauf genau diese weiterhin als "geaendert" erkennt und die
    # uebersprungene WAV-Verarbeitung nachholt. Bereits frueher verarbeitete ZIPs bleiben im
    # State (sonst wuerde der naechste Normal-Lauf ALLE ZIPs erneut als neu behandeln).
    files = discovery["raw_files"]
    if skip_audio_flag:
        changed_zips = {c for c in (changed or []) if c.lower().endswith(".zip")}
        files = [p for p in files if not (str(rel(p)) in changed_zips)]
    data = {
        "version": VERSION,
        "last_run": dt.datetime.now().isoformat(timespec="seconds"),
        "raw_files": [file_sig(p) for p in files],
        "days": discovery["days"],
        "outputs": outputs,
        "warnings": WARNINGS,
    }
    write_json(STATE_PATH, data)


def backup_core_files() -> Path:
    backup_dir = OUTDIR / "archiv_v8" / RUN_ID
    backup_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in CORE_BACKUP_FILES:
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
            copied += 1
    RUN_LOG.append({"step": "backup", "ok": True, "detail": f"{copied} Dateien nach {rel(backup_dir)}"})
    return backup_dir


def read_positions() -> tuple[list[dict[str, str]], list[str]]:
    path = VK / "messpositionen.csv"
    if not path.exists():
        return [], ["Datum", "Umgebung", "Position", "Hinweis"]
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        rows = [dict(r) for r in reader]
        fields = reader.fieldnames or ["Datum", "Umgebung", "Position", "Hinweis"]
    return rows, fields


def ensure_positions(days: list[str]) -> list[str]:
    rows, fields = read_positions()
    for field in ["Datum", "Umgebung", "Position", "Hinweis"]:
        if field not in fields:
            fields.append(field)
    allowed = set(days)
    before = len(rows)
    rows = [
        r
        for r in rows
        if not (
            r.get("Datum") not in allowed
            and (r.get("Hinweis") or "").startswith("Automatisch erkannt; Messposition")
        )
    ]
    removed = before - len(rows)
    existing = {r.get("Datum") for r in rows}
    added: list[str] = []
    for day in days:
        if day in existing:
            continue
        default = POSITION_DEFAULTS.get(
            day,
            {
                "Umgebung": "Aussen",
                "Position": "Position noch zu dokumentieren",
                "Hinweis": "Automatisch erkannt; Messposition bitte pruefen/ergaenzen.",
            },
        )
        rows.append(
            {
                "Datum": day,
                "Umgebung": default["Umgebung"],
                "Position": default["Position"],
                "Hinweis": default["Hinweis"],
            }
        )
        added.append(day)
    if added:
        path = VK / "messpositionen.csv"
        if path.exists():
            shutil.copy2(path, VK / f"messpositionen.backup_v8_{RUN_ID}.csv")
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})
        detail = f"ergaenzt: {', '.join(added)}"
        if removed:
            detail += f"; bereinigt: {removed}"
        RUN_LOG.append({"step": "messpositionen", "ok": True, "detail": detail})
    else:
        detail = "keine Ergaenzung"
        if removed:
            path = VK / "messpositionen.csv"
            if path.exists():
                shutil.copy2(path, VK / f"messpositionen.backup_v8_{RUN_ID}.csv")
            with path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
                writer.writeheader()
                for row in rows:
                    writer.writerow({k: row.get(k, "") for k in fields})
            detail += f"; bereinigt: {removed}"
        RUN_LOG.append({"step": "messpositionen", "ok": True, "detail": detail})
    return added


def write_pipeline_config(discovery: dict[str, Any]) -> dict[str, Any]:
    report_days = sorted(discovery["csv_by_day"], reverse=True)
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        old = read_json(CONFIG_PATH, {})
        if isinstance(old, dict):
            config.update(old)
    config.update(
        {
            # "version" beschreibt die BERICHTS-Version (v9) und kommt aus der bestehenden
            # Config/DEFAULT_CONFIG; VERSION (v8) ist die Pipeline-Version.
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "report_days": report_days,
            "detected_inputs": discovery["summary"],
            "missing_nominal_zip_days": [
                d["day"]
                for d in discovery["summary"]
                if d["csv_count"] > 0 and d["zip_count_nominal"] == 0
            ],
        }
    )
    write_json(CONFIG_PATH, config)
    RUN_LOG.append({"step": "config", "ok": True, "detail": f"{len(report_days)} Berichtstage"})
    return config


_ZIP_DAY_RE = re.compile(r"Laermprotokoll_(\d{2})\.(\d{2})\.(\d{4})", re.I)

def changed_days_from_paths(changed: list[str]) -> set[str]:
    """Extrahiert die betroffenen Messtage aus geaenderten Rohdatei-Pfaden
    (CSV: '2026-06-30 ...' / ZIP: 'Laermprotokoll_30.06.2026...')."""
    days: set[str] = set()
    for pth in changed:
        # Entfernte Dateien werden als "ENTFERNT: <pfad>" gemeldet -> Prefix abstreifen,
        # damit auch der Tag einer geloeschten Rohdatei regeneriert wird.
        if pth.startswith("ENTFERNT: "):
            pth = pth[len("ENTFERNT: "):]
        name = Path(pth).name
        m = re.match(r"(20\d{2}-\d{2}-\d{2}) ", name)
        if m:
            days.add(m.group(1)); continue
        m = _ZIP_DAY_RE.search(name)
        if m:
            days.add(f"{m.group(3)}-{m.group(2)}-{m.group(1)}")
    return days


def should_update_audio(discovery: dict[str, Any], changed: list[str], force_full: bool) -> tuple[bool, str]:
    if force_full:
        return True, "Voll-Lauf per --full"
    changed_zips = [c for c in changed if c.lower().endswith(".zip")]
    if not changed_zips:
        return False, "keine geaenderten ZIPs"
    if not STATE_PATH.exists():
        new_zip_days = [
            day
            for day, paths in discovery["zip_by_nominal_day"].items()
            if day > BASELINE_REPORT_END and paths
        ]
        if new_zip_days:
            return True, "neue ZIP-Tage nach Berichtsschluss: " + ", ".join(sorted(new_zip_days))
        return False, "erster v8-Lauf: alte ZIPs als Bestand behandelt"
    return True, "geaenderte ZIP-Dateien erkannt"


def run_step(name: str, args: list[str], required: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [PY] + args
    started = dt.datetime.now()
    print(f"\n[{name}] {' '.join(Path(a).name if a.endswith('.py') else a for a in args)}", flush=True)
    proc = subprocess.run(
        cmd,
        cwd=str(BASE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
    )
    elapsed = (dt.datetime.now() - started).total_seconds()
    out = (proc.stdout or "") + (proc.stderr or "")
    if out.strip():
        print(out.strip())
    ok = proc.returncode == 0
    RUN_LOG.append(
        {
            "step": name,
            "ok": ok,
            "returncode": proc.returncode,
            "seconds": round(elapsed, 1),
            "command": " ".join(cmd),
            "output_tail": out[-4000:],
        }
    )
    if required and not ok:
        raise RuntimeError(f"Schritt fehlgeschlagen: {name} (Code {proc.returncode})")
    if not ok:
        WARNINGS.append(f"Optionaler Schritt fehlgeschlagen: {name} (Code {proc.returncode})")
    return proc


def read_csv_stats(day: str) -> dict[str, Any]:
    vals: list[float] = []
    first: dt.datetime | None = None
    last: dt.datetime | None = None
    for path in sorted(BASE.glob(f"{day} *.csv")):
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.reader(f):
                if len(row) >= 4 and row[0].strip().isdigit():
                    try:
                        stamp = dt.datetime.strptime(f"{row[1]} {row[2]}", "%m-%d-%Y %H:%M:%S")
                        val = float(row[3])
                    except Exception:
                        continue
                    vals.append(val)
                    first = stamp if first is None or stamp < first else first
                    last = stamp if last is None or stamp > last else last
    if not vals:
        return {"day": day, "samples": 0}
    # Energetischer Mittelwert ueber alle vorhandenen Sekunden, nur fuer Kurzstatus.
    import math

    leq = 10 * math.log10(sum(10 ** (v / 10) for v in vals) / len(vals))
    return {
        "day": day,
        "samples": len(vals),
        "start": first.strftime("%H:%M:%S") if first else "",
        "end": last.strftime("%H:%M:%S") if last else "",
        "leq_all": round(leq, 1),
        "lmax": round(max(vals), 1),
        "lmin": round(min(vals), 1),
    }


def write_bautagebuch_vorschlaege(discovery: dict[str, Any]) -> Path:
    positions, _ = read_positions()
    pos_by_day = {r.get("Datum"): r for r in positions}
    new_reference_days = [d for d in discovery["days"] if d > "2026-06-26"]
    out = OUTDIR / "Bautagebuch_Vorschlaege_v8.md"
    lines: list[str] = []
    lines.append("# Bautagebuch - Vorschlaege v8")
    lines.append("")
    lines.append(f"Automatisch erstellt: {dt.datetime.now().strftime('%d.%m.%Y %H:%M')}")
    lines.append("")
    lines.append("Prinzip: Das Bautagebuch soll weiterhin Erleben, Einschraenkungen und Beobachtungen festhalten.")
    lines.append("Messwerte und technische Details stehen im Schallbericht; hier werden sie nicht gedoppelt.")
    lines.append("")
    if not new_reference_days:
        lines.append("Keine neuen Messtage nach dem bisherigen Berichtsschluss 26.06.2026 erkannt.")
    else:
        lines.append("## Neue erkannte Messtage")
        lines.append("")
        for day in sorted(new_reference_days):
            stats = read_csv_stats(day)
            pos = pos_by_day.get(day, {})
            zip_count = len(discovery["zip_by_nominal_day"].get(day, []))
            weekday = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][dt.date.fromisoformat(day).weekday()]
            lines.append(f"### {dt.date.fromisoformat(day).strftime('%d.%m.%Y')} ({weekday})")
            lines.append("")
            lines.append(f"- Messposition: {pos.get('Position', 'noch zu pruefen')} ({pos.get('Umgebung', 'unbekannt')})")
            if stats.get("samples"):
                lines.append(f"- Erfassung: {stats['start']}-{stats['end']} Uhr, Details s. Schallbericht v9.")
            else:
                lines.append("- Keine auswertbaren CSV-Sekunden gefunden.")
            if zip_count == 0:
                lines.append("- Hinweis: Kein passendes WAV-ZIP gefunden; Quellenzuordnung bleibt fuer diesen Tag ohne WAV-Beleg.")
            lines.append("")
            if day in ("2026-06-27", "2026-06-28"):
                lines.append(
                    "> Vorschlag: Referenz-/Bauruhe-Tag dokumentieren. Bitte nur tatsaechliche Wahrnehmung ergaenzen: "
                    "Fensterzustand, Balkon-/Raumnutzung, Restgeraeusche, ob Baustellenbetrieb wahrnehmbar war oder nicht. "
                    "Pegel/Diagramm s. Schallbericht v9."
                )
            else:
                lines.append(
                    "> Vorschlag: Beobachtungen des Tages ergaenzen, insbesondere Art der Stoerung, betroffene Raeume, "
                    "Fensterzustand, Balkon-/Homeoffice-Nutzung. Pegel/Diagramm s. Schallbericht v9."
                )
            lines.append("")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    RUN_LOG.append({"step": "bautagebuch_vorschlaege", "ok": True, "detail": rel(out)})
    return out


def write_run_report(discovery: dict[str, Any], changed: list[str], outputs: dict[str, str], backup_dir: Path) -> Path:
    report = LOGDIR / f"autolauf_v8_{RUN_ID}.md"
    LOGDIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Autolauf v8 - {RUN_ID}")
    lines.append("")
    lines.append(f"Basisordner: `{BASE}`")
    lines.append(f"Sicherung: `{rel(backup_dir)}`")
    lines.append("")
    lines.append("## Erkannte Rohdaten")
    lines.append("")
    lines.append("| Datum | CSV | ZIP nominal | Hinweis |")
    lines.append("|---|---:|---:|---|")
    for row in discovery["summary"]:
        hint = ""
        if row["csv_count"] and not row["zip_count_nominal"]:
            hint = "CSV vorhanden, kein Tages-ZIP"
        lines.append(f"| {row['day']} | {row['csv_count']} | {row['zip_count_nominal']} | {hint} |")
    lines.append("")
    lines.append("## Neue/geaenderte Dateien seit letztem Lauf")
    lines.append("")
    if changed:
        for item in changed[:200]:
            lines.append(f"- `{item}`")
        if len(changed) > 200:
            lines.append(f"- ... {len(changed)-200} weitere")
    else:
        lines.append("- Keine Aenderung erkannt; Lauf trotzdem neu erzeugt.")
    lines.append("")
    lines.append("## Schritte")
    lines.append("")
    lines.append("| Schritt | Status | Sekunden | Detail |")
    lines.append("|---|---|---:|---|")
    for entry in RUN_LOG:
        status = "OK" if entry.get("ok") else "FEHLER"
        seconds = entry.get("seconds", "")
        detail = entry.get("detail") or entry.get("output_tail", "").splitlines()[-1:] or ""
        if isinstance(detail, list):
            detail = " ".join(detail)
        detail = str(detail).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {entry.get('step')} | {status} | {seconds} | {detail[:240]} |")
    lines.append("")
    lines.append("## Warnungen")
    lines.append("")
    if WARNINGS:
        for w in WARNINGS:
            lines.append(f"- {w}")
    else:
        lines.append("- Keine.")
    lines.append("")
    lines.append("## Ausgaben")
    lines.append("")
    for key, value in outputs.items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> int:
    force_full = "--full" in sys.argv[1:]
    skip_audio_flag = "--skip-audio" in sys.argv[1:]
    OUTDIR.mkdir(parents=True, exist_ok=True)
    LOGDIR.mkdir(parents=True, exist_ok=True)
    discovery = discover_inputs()
    changed = compare_state(discovery["raw_files"])
    for row in discovery["summary"]:
        if row["day"] > BASELINE_REPORT_END and row["csv_count"] > 0 and row["zip_count_nominal"] == 0:
            WARNINGS.append(f"{row['day']}: CSV vorhanden, aber kein Laermprotokoll-ZIP im Tagesnamen gefunden.")

    print(f"Schallmessung Autolauf {VERSION} - {RUN_ID}")
    print(f"Erkannte Tage: {len(discovery['days'])}; neue/geaenderte Rohdateien: {len(changed)}")

    backup_dir = backup_core_files()
    ensure_positions(sorted(discovery["csv_by_day"]))
    config = write_pipeline_config(discovery)
    threshold = str(config.get("relevant_threshold_dba", 60.0))
    if skip_audio_flag:
        update_audio, update_audio_reason = False, "--skip-audio gesetzt (WAV-Verarbeitung manuell uebersprungen)"
    else:
        update_audio, update_audio_reason = should_update_audio(discovery, changed, force_full)

    outputs: dict[str, str] = {}
    try:
        run_step("01_index", [str(HERE / "01_build_index.py")])
        run_step("meteo_dwd", [str(HERE / "meteo_dwd.py")], required=False)
        if update_audio:
            run_step("03_relevant", [str(HERE / config["relevant_script"]), threshold])
            run_step("07_reclassify", [str(HERE / "07_reclassify.py")], required=False)
        else:
            RUN_LOG.append({"step": "03_relevant", "ok": True, "detail": f"uebersprungen ({update_audio_reason})"})
            print(f"\n[03_relevant] uebersprungen: {update_audio_reason}")
        run_step("05_dauerlaerm", [str(HERE / config["dauerlaerm_script"])])
        run_step("08_tiefbohrer", [str(HERE / "08_tiefbohrer.py")])
        if (VK / "Laermquellen_Verknuepfung_backup.xlsx").exists():
            run_step("10_import_geprueft", [str(HERE / "10_import_geprueft.py")], required=False)
        run_step("04_excel", [str(HERE / "04_excel.py")])

        # v8: Tagesberichte nur fuer Tage mit neuen/geaenderten Rohdaten (oder --full /
        # erster Lauf ohne State), und die faelligen Tage parallel (Prozesse unabhaengig).
        _changed_days = changed_days_from_paths(changed)
        _first_run = not STATE_PATH.exists()
        days_to_run: list[str] = []
        for day in config["report_days"]:
            if not discovery["csv_by_day"].get(day):
                WARNINGS.append(f"{day}: Tagesbericht uebersprungen, keine CSV vorhanden.")
            elif force_full or _first_run or day in _changed_days:
                days_to_run.append(day)
            else:
                RUN_LOG.append({"step": f"06_report_{day}", "ok": True,
                                "detail": "uebersprungen (Rohdaten unveraendert)"})
        skipped_n = len(config["report_days"]) - len(days_to_run)
        print(f"\nTagesberichte: {len(days_to_run)} zu erneuern, {skipped_n} unveraendert uebersprungen.")
        if days_to_run:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=3) as ex:
                futs = {ex.submit(run_step, f"06_report_{day}",
                                   [str(HERE / config["daily_report_script"]), day],
                                   False): day for day in days_to_run}
                for fut in as_completed(futs):
                    fut.result()  # Ausgabe/Log erfolgt in run_step; Exceptions hochreichen

        run_step("09_uebersicht", [str(HERE / "09_uebersicht.py")], required=False)
        run_step("07_gesamtbericht_v9", [str(HERE / config["gesamtbericht_script"])])
        run_step("07_gesamtbericht_v9_ohne_wav", [str(HERE / "07_gesamtbericht_v9_ohne_wav.py")], required=False)
        bautagebuch_md = write_bautagebuch_vorschlaege(discovery)

        outputs = {
            "Gesamtbericht": rel(OUTDIR / config["gesamtbericht_pdf"]),
            "Rohdaten-Manifest": rel(OUTDIR / config["manifest_csv"]),
            "Excel-Arbeitsmappe": rel(VK / "Laermquellen_Verknuepfung.xlsx"),
            "Bautagebuch-Vorschlaege": rel(bautagebuch_md),
            "Konfiguration": rel(CONFIG_PATH),
        }
        save_state(discovery, outputs, skip_audio_flag, changed)
        report = write_run_report(discovery, changed, outputs, backup_dir)
        outputs["Laufbericht"] = rel(report)
        print("\nAutolauf fertig.")
        for k, v in outputs.items():
            print(f"  {k}: {v}")
        return 0
    except Exception as exc:
        WARNINGS.append(f"ABBRUCH: {exc}")
        RUN_LOG.append({"step": "abbruch", "ok": False, "detail": traceback.format_exc()})
        outputs = {
            "Konfiguration": rel(CONFIG_PATH),
            "Sicherung": rel(backup_dir),
        }
        report = write_run_report(discovery, changed, outputs, backup_dir)
        print("\nAutolauf abgebrochen.")
        print(exc)
        print(f"Laufbericht: {rel(report)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
