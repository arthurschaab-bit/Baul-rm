"""Compatibility entry point for the metadata-driven ten-minute DWD downloader.

Explicit dates replace the old incomplete filename-based day discovery. The
paired report profile obtains its dates from its selected measurement seconds.
"""
from pathlib import Path
import runpy
import sys

if __name__=='__main__':
    checkout=Path(__file__).resolve().parents[3]
    launcher=checkout/'run_meteo.py'
    if not launcher.is_file():
        raise SystemExit('Use run_meteo.py in the public checkout or the configured private paired-report profile.')
    runpy.run_path(str(launcher),run_name='__main__')
