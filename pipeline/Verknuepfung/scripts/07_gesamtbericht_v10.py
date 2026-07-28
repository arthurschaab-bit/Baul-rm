"""Gesamtbericht v10 mit portablem Hash-Cache."""
import os

os.environ.setdefault(
    "SCHALLBERICHT_CONFIG",
    os.path.join(os.path.dirname(__file__), "pipeline_config_v10.json"),
)

import gesamtbericht_lib_v4 as lib


if __name__ == "__main__":
    lib.main()
