"""Validate private report configuration without printing its contents."""

import argparse
import re
from datetime import date
from pathlib import Path

import yaml


FIELDS = {
    "deckblatt.yaml": ("titel", "untertitel", "zeile", "namen", "adresse"),
    "eingaben.yaml": ("quellen", "nur_lesen", "ausschluss_muster"),
    "forderung.yaml": ("phasen",),
    "vereinbarung.yaml": ("werktage", "feiertage_land", "mindestabdeckung", "tage_unter_abdeckung", "ende_anzahl_werktage", "ende_schwelle_db", "wiederaufleben_anzahl_werktage", "wiederaufleben_fenster_kalendertage", "wiederaufleben_schwelle_db", "rundung_db", "toleranzabzug"),
    "luecken.yaml": ("technischer_ausfall", "abwesenheit", "ruhetag_referenz_ebene1", "ruhetag_referenz_intern", "ungeklaerte_quelle"),
}
FORBIDDEN = ("tagebuch", "notiz", "chronik")
PLACEHOLDER = re.compile(r"^<[^<>]+>$")


def read_text(path: Path) -> str:
    """Read UTF-8 (with or without BOM) or UTF-16 text; Windows editors write all three."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def load_patterns(path: Path) -> list[str]:
    """Return the private search patterns: one regular expression per line, comments with #."""
    return [line.strip() for line in read_text(path).splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def validate(root: Path, pattern_file: Path | None = None) -> list[str]:
    errors = []
    root = root.resolve()
    repo = Path(__file__).resolve().parent
    if root == repo or repo in root.parents:
        return ["Privater Arbeitsordner liegt im Repository."]
    config = root / "config"
    for name, required in FIELDS.items():
        path = config / name
        if not path.is_file():
            errors.append(f"{name}: fehlt")
            continue
        try:
            data = yaml.safe_load(read_text(path))
        except (OSError, UnicodeError, yaml.YAMLError):
            errors.append(f"{name}: YAML-Formatfehler")
            continue
        if not isinstance(data, dict):
            errors.append(f"{name}: Objekt erwartet")
            continue
        for key in required:
            value = data.get(key)
            if value is None or value == "" or isinstance(value, str) and value.startswith("<"):
                errors.append(f"{name}: {key} fehlt oder ist Platzhalter")
        if name == "eingaben.yaml":
            sources = data.get("quellen")
            needed = ("csv_tagesordner", "datenbank", "beweissicherung", "kalibrierfotos_drive_ordner_id", "bautaetigkeit", "kalibrierung_lokal")
            if not isinstance(sources, dict):
                errors.append(f"{name}: quellen muss ein Objekt sein")
            else:
                for key in needed:
                    if not sources.get(key) or isinstance(sources.get(key), str) and sources[key].startswith("<"):
                        errors.append(f"{name}: quellen.{key} fehlt")
                    value = sources.get(key)
                    values = value if isinstance(value, list) else [value]
                    if any(isinstance(item, str) and any(token in item.lower() for token in FORBIDDEN) for item in values):
                        errors.append(f"{name}: quellen.{key} trifft Ausschlussmuster")
            if not isinstance(data.get("ausschluss_muster"), list):
                errors.append(f"{name}: ausschluss_muster muss eine Liste sein")
        if name == "luecken.yaml":
            reference = data.get("ruhetag_referenz_ebene1")
            if reference is not None and not (isinstance(reference, str) and reference.startswith("<")):
                try:
                    date.fromisoformat(str(reference))
                except ValueError:
                    errors.append(f"{name}: ruhetag_referenz_ebene1 braucht JJJJ-MM-TT")
        if name == "forderung.yaml":
            phases = data.get("phasen")
            if not isinstance(phases, list) or len(phases) != 3:
                errors.append(f"{name}: genau drei Phasen erwartet")
            else:
                spans = []
                for index, phase in enumerate(phases, 1):
                    if not isinstance(phase, dict) or not phase.get("name") or not phase.get("beginn"):
                        errors.append(f"{name}: Phase {index} unvollständig")
                        spans.append(None)
                        continue
                    parsed = {}
                    for key in ("beginn", "ende"):
                        value = phase.get(key)
                        if value is None and key == "ende" and index == 3:
                            parsed[key] = None
                            continue
                        try:
                            parsed[key] = date.fromisoformat(str(value))
                        except ValueError:
                            parsed[key] = None
                            errors.append(f"{name}: Phase {index} {key} braucht JJJJ-MM-TT")
                    spans.append(parsed)
                    if parsed["beginn"] and parsed["ende"] and parsed["ende"] < parsed["beginn"]:
                        errors.append(f"{name}: Phase {index} endet vor ihrem Beginn")
                for index, (before, after) in enumerate(zip(spans, spans[1:]), 1):
                    if before and after and before["ende"] and after["beginn"] and after["beginn"] <= before["ende"]:
                        errors.append(f"{name}: Phase {index + 1} beginnt nicht nach dem Ende von Phase {index}")
    pattern_file = pattern_file or root / "PRIVAT_MUSTER.txt"
    if not pattern_file.is_file():
        errors.append("PRIVAT_MUSTER.txt: fehlt")
    else:
        try:
            patterns = load_patterns(pattern_file)
        except (OSError, UnicodeError):
            errors.append("PRIVAT_MUSTER.txt: nicht lesbar (UTF-8 oder UTF-16 erwartet)")
            patterns = []
        else:
            if not patterns:
                errors.append("PRIVAT_MUSTER.txt: leer")
        for index, pattern in enumerate(patterns, 1):
            if PLACEHOLDER.match(pattern):
                errors.append(f"PRIVAT_MUSTER.txt: Zeile {index} ist ein Platzhalter")
                continue
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error:
                errors.append(f"PRIVAT_MUSTER.txt: Regex {index} ungültig")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--privat-root", type=Path, required=True)
    parser.add_argument("--privat-muster", type=Path)
    args = parser.parse_args()
    errors = validate(args.privat_root, args.privat_muster)
    for error in errors:
        print(error)
    print("Konfiguration gültig" if not errors else f"Konfiguration unvollständig: {len(errors)} Fehler")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
