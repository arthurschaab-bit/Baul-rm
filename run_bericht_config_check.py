"""Validate private report configuration without printing its contents."""

import argparse
import re
from datetime import date
from pathlib import Path

import yaml


FIELDS = {
    "deckblatt.yaml": ("titel", "untertitel", "zeile", "namen"),
    "eingaben.yaml": ("quellen", "nur_lesen", "ausschluss_muster"),
    "forderung.yaml": ("phasen",),
    "vereinbarung.yaml": ("werktage", "feiertage_land", "mindestabdeckung", "tage_unter_abdeckung", "ende_anzahl_werktage", "ende_schwelle_db", "wiederaufleben_anzahl_werktage", "wiederaufleben_fenster_kalendertage", "wiederaufleben_schwelle_db", "rundung_db", "toleranzabzug"),
    "luecken.yaml": ("technischer_ausfall", "abwesenheit", "ruhetag_referenz_ebene1", "ruhetag_referenz_intern", "ungeklaerte_quelle"),
}
FORBIDDEN = ("tagebuch", "notiz", "chronik")


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
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
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
        if name == "forderung.yaml":
            phases = data.get("phasen")
            if not isinstance(phases, list) or len(phases) != 3:
                errors.append(f"{name}: genau drei Phasen erwartet")
            else:
                for index, phase in enumerate(phases, 1):
                    if not isinstance(phase, dict) or not phase.get("name") or not phase.get("beginn"):
                        errors.append(f"{name}: Phase {index} unvollständig")
                        continue
                    for key in ("beginn", "ende"):
                        value = phase.get(key)
                        if value is None and key == "ende" and index == 3:
                            continue
                        try:
                            date.fromisoformat(str(value))
                        except ValueError:
                            errors.append(f"{name}: Phase {index} {key} braucht JJJJ-MM-TT")
    pattern_file = pattern_file or root / "PRIVAT_MUSTER.txt"
    if not pattern_file.is_file():
        errors.append("PRIVAT_MUSTER.txt: fehlt")
    else:
        patterns = [line.strip() for line in pattern_file.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        if not patterns:
            errors.append("PRIVAT_MUSTER.txt: leer")
        for index, pattern in enumerate(patterns, 1):
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
