"""Portable Cache-Erweiterung fuer die verifizierte Berichtsbibliothek v3."""
from __future__ import annotations

import json
import os
from pathlib import Path

import gesamtbericht_lib_v3 as _base


def _cache_key(path: str) -> str:
    absolute = os.path.abspath(path)
    try:
        return os.path.relpath(absolute, _base.BASE).replace("\\", "/")
    except ValueError:
        # Windows definiert keine relativen Pfade zwischen zwei Laufwerken.
        # Manifest-Rohdaten liegen flach und besitzen eindeutige Dateinamen.
        return Path(absolute).name


def _load_hash_cache() -> dict:
    if _base._HASH_CACHE is not None:
        return _base._HASH_CACHE
    _base._HASH_CACHE = {}
    if (
        os.environ.get("SCHALLBERICHT_REHASH") == "1"
        or not os.path.exists(_base._HASH_CACHE_PATH)
    ):
        return _base._HASH_CACHE
    try:
        with open(_base._HASH_CACHE_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return _base._HASH_CACHE
        for old_key, value in data.items():
            if not isinstance(value, dict):
                continue
            key_path = Path(old_key)
            if key_path.is_absolute():
                # Die Manifest-Dateien liegen alle direkt unter BASE. So bleiben
                # bestehende Drive-Hashes nach dem Umzug in den Cache nutzbar.
                new_key = key_path.name
            else:
                new_key = str(old_key).replace("\\", "/")
            _base._HASH_CACHE[new_key] = value
    except Exception as exc:
        print(f"[lib_v4] Hash-Cache nicht lesbar (wird neu aufgebaut): {exc}")
    return _base._HASH_CACHE


def _sha256(path: str, chunk: int = 1 << 20) -> str:
    cache = _load_hash_cache()
    try:
        stat = os.stat(path)
        key = _cache_key(path)
        entry = cache.get(key)
        if (
            entry
            and entry.get("bytes") == stat.st_size
            and entry.get("mtime_ns") == stat.st_mtime_ns
        ):
            return str(entry["sha256"])
        digest = _base._sha256_raw(path, chunk)
        cache[key] = {
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": digest,
        }
        return digest
    except OSError:
        return _base._sha256_raw(path, chunk)


_base._load_hash_cache = _load_hash_cache
_base._sha256 = _sha256


def main() -> None:
    _base.main()
