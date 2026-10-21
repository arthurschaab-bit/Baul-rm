"""Rückgewinnung reiner PCE-323-Sekunden aus Sicherungen und Alt-CSVs (siehe RECOVERY.md)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from baul_rm.recovery.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
