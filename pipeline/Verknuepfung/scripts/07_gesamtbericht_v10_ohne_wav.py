"""Gesamtbericht v10 ohne WAV-/KI-basierte Quellenzuordnung."""
import os

os.environ.setdefault(
    "SCHALLBERICHT_CONFIG",
    os.path.join(os.path.dirname(__file__), "pipeline_config_v10.json"),
)
os.environ["SCHALLBERICHT_NO_AUDIO"] = "1"
_output_prefix = os.environ.get("BAUL_RM_OUTPUT_PREFIX", "Schallmessung")
os.environ["SCHALLBERICHT_OUTPUT_OVERRIDE"] = (
    f"Gesamtbericht_{_output_prefix}_v10_ohne_WAV.pdf"
)

import gesamtbericht_lib_v4 as lib


if __name__ == "__main__":
    lib.main()
