"""
DSM ASCII/LAZ -> LAZ-Kacheln (Grid-Shape, z.B. swissGRID 1km2) - Standalone ohne GUI
-------------------------------------------------------------------------------------
Nutzt exakt dieselbe Verarbeitung wie das GUI (_osgeo_runner._process), es gibt
keine zweite Implementierung. Konfiguration unten anpassen.

Anforderungen:
    OSGeo4W- oder QGIS-Python (osgeo/ogr) + pdal.exe (Teil von OSGeo4W/QGIS)

Verwendung:
    C:\\OSGeo4W\\bin\\python3.exe process_scripts\\01_convertDATA_dsm_to_laz_tiles.py
"""

import importlib.util
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Konfiguration – hier anpassen
# ---------------------------------------------------------------------------
CONFIG = {
    "input_dir":       r"C:\pfad\zum\input",            # Ordner mit .xyz/.txt/.asc/.csv oder .laz/.las
    "output_dir":      r"C:\pfad\zum\output",           # muss ein anderer Ordner als input_dir sein
    "grid_shape_path": str(SCRIPT_DIR.parent / "swissGRID_1km2_shp" / "chGRID_1km2.shp"),
    "format":          "ascii",      # "ascii" | "las"
    # nur ASCII: Spaltennamen (PDAL-Dimensionen), Trennzeichen, Kopfzeilen
    "columns":         "X Y Z",      # z.B. "X Y Z Intensity Classification"
    "separator":       "space",      # "space" | "tab" | "comma" | "semicolon"
    "skip":            0,
    # Ausgabename: <Basis>_<NAME>_LV95_LN02.laz (NAME aus der Attributtabelle des Grids).
    # Leer = Basis je Input-Datei automatisch (Name bis vor '_LV95'),
    # gesetzt = gilt fuer alle Dateien (EIN Kachelsatz). Hoehen immer LN02.
    "base_name":       "",
    "staging_dir":     "",           # leer = <output_dir>\_staging
    "num_workers":     4,
    "keep_staging":    False,
}


def _load_runner():
    spec = importlib.util.spec_from_file_location("_osgeo_runner", str(SCRIPT_DIR / "_osgeo_runner.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    try:
        _load_runner()._process(CONFIG)
    except Exception as exc:
        print(f"[FEHLER] {exc}", flush=True)
        sys.exit(1)
