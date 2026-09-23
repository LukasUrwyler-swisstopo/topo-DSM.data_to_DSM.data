"""
GUI_dsmAsciiToLaz.py  –  DSM ASCII/LAZ → LAZ-Tiles GUI
Tkinter-Oberflaeche fuer die Umwandlung alter DSM-Punktwolken, je Input-Format ein Tab:
  - DSM.ascii → DSM.laz-Tiles:  ASCII (xyz)                        → LAZ/LAS-Kacheln
  - DSM.laz   → DSM.laz-Tiles:  LAZ/LAS (beliebiges Tiling/merged) → LAZ/LAS-Kacheln
Beide Tabs: Tiling gemaess Grid-Shape, CRS-Tag LV95 + LN02/LHN95 nach Auswahl (nie umgerechnet),
optional Thinning und DSM-Raster + Hillshade.
Dateiname je Kachel: <Jahr>_<AREA>_TIN_DSM[_thinNN]_<NAME>_LV95_<LN02|LHN95>.<laz|las>
(NAME aus der Attributtabelle des Grids).
Styling analog zu topo-COGTIFFconverter / topo-DMCdataConverter.

Das GUI laeuft mit Standard-Python (kein osgeo erforderlich, kompatibel ab 3.6).
Die Verarbeitung laeuft via process_scripts/_osgeo_runner.py als Subprocess
(OSGeo4W/QGIS-Python), die Punktwolken-Operationen dort via pdal.exe.
"""

import ctypes
import datetime
import time
import glob as _glob
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
from pathlib import Path
from typing import List, Dict

# ─── Pfade ────────────────────────────────────────────────────────────────────
SCRIPT_DIR          = os.path.dirname(os.path.abspath(__file__))
PROCESS_SCRIPTS_DIR = os.path.join(SCRIPT_DIR, "process_scripts")
RUNNER_SCRIPT       = os.path.join(PROCESS_SCRIPTS_DIR, "_osgeo_runner.py")
CONFIG_FILE         = os.path.join(PROCESS_SCRIPTS_DIR, "_dsm2laz_config.json")
DEFAULT_GRID_SHAPE  = os.path.join(SCRIPT_DIR, "swissGRID_1km2_shp", "chGRID_1km2.shp")

# ─── Auswahl-Listen ───────────────────────────────────────────────────────────
ASCII_EXTENSIONS = (".xyz", ".txt", ".asc", ".csv")
LAS_EXTENSIONS   = (".laz", ".las")
# Ein Tab je Input-Format (Schluessel = 'format' im Runner)
TAB_LABELS = {
    "ascii": "DSM.ascii → DSM.laz-Tiles",
    "las":   "DSM.laz → DSM.laz-Tiles",
}
FORMAT_NAMES = {"ascii": "ASCII", "las": "LAZ/LAS"}
# Schluessel 1:1 wie _osgeo_runner.SEPARATORS
SEPARATOR_LABELS = {
    "space":     "Leerzeichen",
    "tab":       "Tabulator",
    "comma":     "Komma  ( , )",
    "semicolon": "Semikolon  ( ; )",
}
EXAMPLE_TILE_NAME = "2600_1200"   # Beispiel-NAME fuer die Benennungs-Vorschau

# ── nur LAZ-Tab ── Schluessel 1:1 wie _osgeo_runner.HEIGHT_REFS / THIN_OPTIONS_M / OUT_FORMATS
HEIGHT_LABELS = {
    "LN02":  "EPSG:2056 + 5728   (CH1903+ / LV95 + LN02)",
    "LHN95": "EPSG:2056 + 5729   (CH1903+ / LV95 + LHN95)",
}
THIN_LABELS = [("kein Thinning", 0.0), ("0.1 m", 0.1), ("0.2 m", 0.2), ("0.4 m", 0.4),
               ("0.8 m", 0.8), ("1 m", 1.0), ("1.5 m", 1.5), ("2 m", 2.0)]
OUT_FORMAT_CHOICES = ("laz", "las")
DEFAULT_GSD = "0.5"


# ─── OSGeo4W Python / pdal.exe Erkennung ──────────────────────────────────────
def _detect_osgeo_python() -> str:
    """Gibt den Pfad zum OSGeo4W/QGIS-Python zurueck (aus Config, System-Python oder bekannten Pfaden)."""
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                path = json.load(f).get("osgeo_python", "")
            if path and os.path.isfile(path):
                return path
        except Exception:
            pass

    try:
        if importlib.util.find_spec("osgeo") is not None:
            return sys.executable
    except Exception:
        pass

    kandidaten = []  # type: List[str]
    osgeo_root = os.environ.get("OSGEO4W_ROOT")
    if osgeo_root:
        kandidaten.append(str(Path(osgeo_root) / "bin" / "python3.exe"))
    kandidaten += [r"C:\OSGeo4W\bin\python3.exe", r"C:\OSGeo4W64\bin\python3.exe"]
    for pat in [r"C:\Program Files\QGIS*\bin\python3.exe",
                r"C:\Program Files (x86)\QGIS*\bin\python3.exe",
                r"C:\QGIS*\bin\python3.exe"]:
        kandidaten.extend(sorted(_glob.glob(pat), reverse=True))
    return next((p for p in kandidaten if Path(p).is_file()), "")


def _detect_pdal_exe(osgeo_python: str = "") -> str:
    """Gibt den Pfad zur pdal.exe zurueck (neben dem OSGeo4W-Python, PATH, bekannte Installationen)."""
    import shutil as _shutil
    kandidaten = []  # type: List[str]
    if osgeo_python and os.path.isfile(osgeo_python):
        kandidaten.append(os.path.join(os.path.dirname(osgeo_python), "pdal.exe"))
    found = _shutil.which("pdal")
    if found:
        kandidaten.append(found)
    osgeo_root = os.environ.get("OSGEO4W_ROOT")
    if osgeo_root:
        kandidaten.append(str(Path(osgeo_root) / "bin" / "pdal.exe"))
    kandidaten += [r"C:\OSGeo4W\bin\pdal.exe", r"C:\OSGeo4W64\bin\pdal.exe"]
    for pat in [r"C:\Program Files\QGIS*\bin\pdal.exe", r"C:\QGIS*\bin\pdal.exe"]:
        kandidaten.extend(sorted(_glob.glob(pat), reverse=True))
    return next((p for p in kandidaten if Path(p).is_file()), "")


def _detect_python_home(python_exe: str) -> str:
    """Leitet PYTHONHOME vom Python-Executable ab (QGIS: apps\\PythonXXX, OSGeo4W: root)."""
    bin_dir  = os.path.dirname(python_exe)
    root_dir = os.path.dirname(bin_dir)
    apps_dir = os.path.join(root_dir, "apps")
    if os.path.isdir(apps_dir):
        for name in sorted(os.listdir(apps_dir), reverse=True):
            if name.lower().startswith("python"):
                candidate = os.path.join(apps_dir, name)
                if os.path.isdir(candidate):
                    return candidate
    return root_dir


def _save_osgeo_config(path: str) -> None:
    try:
        cfg = {}  # type: Dict
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        cfg["osgeo_python"] = path
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ─── Benennung (Regel liegt im Runner, hier nur fuer die Vorschau genutzt) ────
_RUNNER_MODULE = None


def _runner_module():
    """Laedt _osgeo_runner.py als Modul. Beim Import braucht der Runner nur die
    Standardbibliothek (osgeo erst in den Funktionen) - so existiert die
    Benennungsregel nur an einer Stelle."""
    global _RUNNER_MODULE
    if _RUNNER_MODULE is None:
        spec = importlib.util.spec_from_file_location("_osgeo_runner", RUNNER_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _RUNNER_MODULE = module
    return _RUNNER_MODULE


def _naming_preview(jahr: str, area: str, thin_m: float, height_ref: str, out_format: str,
                    raster_gsd=None) -> str:
    """Benennungs-Vorschau (Regel im Runner) inkl. optionaler Raster-Namen."""
    runner = _runner_module()
    jahr, area = jahr.strip() or "<Jahr>", area.strip() or "<AREA>"
    lines = ["{}_{}{}.{}".format(runner._las_tile_base(jahr, area, thin_m), EXAMPLE_TILE_NAME,
                                 runner._height_suffix(height_ref), out_format)]
    if raster_gsd:
        lines += list(runner._raster_names(jahr, area, raster_gsd, height_ref))
    return "\n".join(lines)


def _list_input_files(input_dir: str, fmt: str) -> List[str]:
    exts = ASCII_EXTENSIONS if fmt == "ascii" else LAS_EXTENSIONS
    try:
        return sorted(os.path.join(input_dir, n) for n in os.listdir(input_dir)
                      if os.path.splitext(n)[1].lower() in exts
                      and os.path.isfile(os.path.join(input_dir, n)))
    except OSError:
        return []


# ─── Farbpaletten (identisch zu topo-COGTIFFconverter) ────────────────────────
LIGHT = {
    "root":      "#f0f0f0",
    "panel":     "#f5f5f5",
    "input":     "#ffffff",
    "fg":        "#1a1a1a",
    "fg_dim":    "#666666",
    "accent":    "#0063b1",
    "hdr_bg":    "#1a3a5c",
    "hdr_fg":    "#ffffff",
    "btn":       "#e1e1e1",
    "btn_hover": "#c8c8c8",
    "list":      "#ffffff",
    "log_bg":    "#1e1e1e",
    "log_fg":    "#d4d4d4",
    "sep":       "#c0c0c0",
    "sel_bg":    "#0078d4",
    "sel_fg":    "#ffffff",
    "ok":        "#2e7d32",
    "err":       "#c62828",
    "hint":      "#8a6f2e",
}

DARK = {
    "root":      "#1e1e1e",
    "panel":     "#252526",
    "input":     "#3c3c3c",
    "fg":        "#cccccc",
    "fg_dim":    "#7a7a7a",
    "accent":    "#4fc3f7",
    "hdr_bg":    "#1a1a1a",
    "hdr_fg":    "#cccccc",
    "btn":       "#3c3c3c",
    "btn_hover": "#505050",
    "list":      "#2d2d30",
    "log_bg":    "#1e1e1e",
    "log_fg":    "#d4d4d4",
    "sep":       "#3c3c3c",
    "sel_bg":    "#094771",
    "sel_fg":    "#cccccc",
    "ok":        "#66bb6a",
    "err":       "#ef5350",
    "hint":      "#c9a84c",
}


# ─── Tab 'DSM → LAZ-Tiles' (ein Input-Format je Tab) ──────────────────────────
class TilesTab:
    """Formular + Start fuer genau ein Input-Format ('ascii' oder 'las').
    Log, Fortschritt, OSGeo4W-Python und Theme teilen sich alle Tabs ueber die App."""

    def __init__(self, app, parent, fmt: str):
        self.app = app
        self.fmt = fmt
        self._ascii_sec = None
        self._last_info = None
        self._build(parent)

    # ── UI Aufbau ──────────────────────────────────────────────────────────────
    # Reihenfolge = Arbeitsablauf: 1 Input lesen und pruefen -> 2 Projekt/CRS festlegen ->
    # 3 Output (Ziel, Kachelung, Benennung, Raster) -> 4 Staging/Performance -> Start
    def _build(self, parent):
        sf = self._build_scrollable(parent)

        self.app._build_group_header(sf, "1   Input")
        self._build_input(sf)
        self._build_dateiinfo(sf)
        if self.fmt == "ascii":
            self._build_ascii_options(sf)

        self.app._build_group_header(sf, "2   Projekt & Referenzsystem")
        self._build_projekt(sf)

        self.app._build_group_header(sf, "3   Output")
        self._build_output(sf)
        self._build_raster_options(sf)

        self.app._build_group_header(sf, "4   Staging & Parallelisierung")
        self._build_staging(sf)

        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x", pady=(6, 0))
        self._start_btn = ttk.Button(btn_row, text="▶   LAZ-TILES ERSTELLEN",
                                      command=self._start)
        self._start_btn.pack(side="right", ipadx=22, ipady=7)

        for var in (self._jahr_var, self._area_var, self._thin_label_var, self._out_fmt_var,
                    self._gsd_var):
            var.trace_add("write", lambda *_: self._update_name_preview())
        self._update_name_preview()

    def _build_scrollable(self, parent):
        """Scrollbarer Formular-Bereich (Canvas + vertikale Scrollbar)."""
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        sf     = ttk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=sf, anchor="nw")
        sf.bind("<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win_id, width=e.width))
        self._canvas = canvas
        self._sf     = sf
        return sf

    def _section(self, parent, title: str):
        sec = ttk.LabelFrame(parent, text=title, padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(1, weight=1)
        return sec

    def _hint(self, parent, text: str, row: int, column: int = 1, columnspan: int = 1, pady=(0, 0)):
        h = ttk.Label(parent, text=text, font=("", 8), justify="left", wraplength=560)
        h.grid(row=row, column=column, columnspan=columnspan, sticky="w",
               padx=(8, 0) if column else (0, 0), pady=pady)
        self.app._dim_labels.append(h)
        return h

    @staticmethod
    def _bold(parent, text: str, row: int, pady=3):
        ttk.Label(parent, text=text, font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="w", pady=pady)

    # ── 1 Input ────────────────────────────────────────────────────────────────
    def _build_input(self, parent):
        sec = self._section(parent, "Input-Daten")
        self._bold(sec, "Input-Ordner:", 0)
        self._in_var = tk.StringVar()
        ttk.Entry(sec, textvariable=self._in_var).grid(row=0, column=1, sticky="ew", padx=(8, 4), pady=3)
        ttk.Button(sec, text="Ordner…", command=self._browse_input).grid(row=0, column=2, pady=3)
        exts = ASCII_EXTENSIONS if self.fmt == "ascii" else LAS_EXTENSIONS
        hint = "Alle {} im Ordner (nicht rekursiv) = EIN Kachelsatz  |  ".format("/".join(exts))
        hint += ("typischerweise gemergt pro Gebiet" if self.fmt == "ascii"
                 else "beliebiges Tiling oder ein merged.laz")
        self._hint(sec, hint, 1)

    def _build_dateiinfo(self, parent):
        app = self.app
        sec = self._section(parent, "Datei-Info  (aus dem Input-Ordner gelesen)")
        fields = [
            ("Dateien:",          "_info_files"),
            ("Format / Version:", "_info_version"),
            ("Punkte:",           "_info_count"),
            ("Extent (X / Y):",   "_info_extent"),
            ("Koordinatensys.:",  "_info_crs"),
            ("Kacheln (max.):",   "_info_cells"),
        ]
        for row, (label, attr) in enumerate(fields):
            ttk.Label(sec, text=label, font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="nw", pady=1)
            val = ttk.Label(sec, text="–", font=("Segoe UI", 9), wraplength=540, justify="left")
            val.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=1)
            setattr(self, attr, val)
            app._accent_labels.append(val)

        r = len(fields)
        ttk.Label(sec, text="Metadaten\n1. Datei:", font=("Segoe UI", 9, "bold"), justify="left"
                  ).grid(row=r, column=0, sticky="nw", pady=(4, 1))
        self._info_meta = ttk.Label(sec, text="–", font=("Courier New", 8), justify="left", wraplength=560)
        self._info_meta.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=(4, 1))
        app._dim_labels.append(self._info_meta)
        r += 1
        if self.fmt == "ascii":
            ttk.Label(sec, text="Vorschau:", font=("Segoe UI", 9, "bold")).grid(row=r, column=0, sticky="nw", pady=(4, 1))
            self._info_preview = ttk.Label(sec, text="–", font=("Courier New", 8), justify="left")
            self._info_preview.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=(4, 1))
            app._dim_labels.append(self._info_preview)
            r += 1

        self._info_warn = ttk.Label(sec, text="", font=("Segoe UI", 8, "italic"), wraplength=560, justify="left")
        self._info_warn.grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._info_warn.grid_remove()
        app._hint_labels.append(self._info_warn)

        ttk.Button(sec, text="Datei-Info aktualisieren", command=self._refresh_info
                   ).grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_ascii_options(self, parent):
        self._ascii_sec = sec = self._section(parent, "ASCII-Format  (wie die Datei gelesen wird)")
        self._bold(sec, "Spalten:", 0)
        self._cols_var = tk.StringVar(value="X Y Z")
        ttk.Entry(sec, textvariable=self._cols_var, width=40).grid(row=0, column=1, sticky="w", padx=(8, 0), pady=3)
        self._hint(sec, "PDAL-Dimensionen in Datei-Reihenfolge, z.B.  X Y Z  oder  X Y Z Intensity "
                        "Classification\nUnbekannte Namen (Col4 …) werden gelesen, aber nicht geschrieben", 1)
        self._bold(sec, "Trennzeichen:", 2, pady=(8, 3))
        self._sep_label_var = tk.StringVar(value=SEPARATOR_LABELS["space"])
        ttk.Combobox(sec, textvariable=self._sep_label_var, values=list(SEPARATOR_LABELS.values()),
                     state="readonly", width=18).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 3))
        self._bold(sec, "Kopfzeilen ueberspringen:", 3)
        self._skip_var = tk.StringVar(value="0")
        tk.Spinbox(sec, from_=0, to=50, textvariable=self._skip_var, width=6
                   ).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=3)
        self._hint(sec, "Wird aus der ersten Datei vorbelegt (Datei-Info)", 4, column=0, columnspan=2, pady=(6, 0))

    # ── 2 Projekt & Referenzsystem ────────────────────────────────────────────
    def _build_projekt(self, parent):
        sec = self._section(parent, "Projekt  (Benennung + CRS-Tag)")
        self._bold(sec, "Jahr:", 0)
        self._jahr_var = tk.StringVar()
        ttk.Entry(sec, textvariable=self._jahr_var, width=8).grid(row=0, column=1, sticky="w", padx=(8, 0), pady=3)
        self._bold(sec, "AREA / AOI:", 1)
        self._area_var = tk.StringVar()
        ttk.Entry(sec, textvariable=self._area_var, width=28).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=3)

        self._bold(sec, "CRS / SRS:", 2, pady=(8, 3))
        self._height_label_var = tk.StringVar(value=HEIGHT_LABELS["LN02"])
        cb = ttk.Combobox(sec, textvariable=self._height_label_var, values=list(HEIGHT_LABELS.values()),
                          state="readonly", width=44)
        cb.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 3))
        cb.bind("<<ComboboxSelected>>", lambda _: self._on_height_changed())
        quelle = "die CRS-Angabe im Dateinamen" if self.fmt == "ascii" else "der CRS-Tag der Quelle"
        self._hint(sec, "Wird als CRS-Tag gesetzt (Kachel-Header, DSM-Raster) - {} wird ignoriert, eine falsche "
                        "Angabe so korrigiert.\nKoordinaten und Hoehen werden NICHT umgerechnet: die Quelle muss "
                        "bereits in LV95 und im gewaehlten Hoehenbezug vorliegen.".format(quelle), 3)

        self._bold(sec, "Thinning:", 4, pady=(8, 3))
        self._thin_label_var = tk.StringVar(value=THIN_LABELS[0][0])
        ttk.Combobox(sec, textvariable=self._thin_label_var, values=[l for l, _ in THIN_LABELS],
                     state="readonly", width=16).grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(8, 3))
        self._hint(sec, "Mindestabstand zwischen den Punkten (filters.sample, wie topo-DMCdataConverter) - "
                        "im Namen als _thin<dm>, z.B. 0.2 m -> _thin02", 5)

    # ── 3 Output ───────────────────────────────────────────────────────────────
    def _build_output(self, parent):
        app = self.app
        sec = self._section(parent, "Kachel-Output")
        self._bold(sec, "Output-Ordner:", 0)
        self._out_var = tk.StringVar()
        ttk.Entry(sec, textvariable=self._out_var).grid(row=0, column=1, sticky="ew", padx=(8, 4), pady=3)
        ttk.Button(sec, text="Ordner…", command=self._browse_output).grid(row=0, column=2, pady=3)
        self._hint(sec, "Je Grid-Zelle mit Daten eine Kachel  |  muss ein anderer Ordner als der Input sein", 1)

        self._bold(sec, "Format:", 2, pady=(8, 3))
        self._out_fmt_var = tk.StringVar(value=OUT_FORMAT_CHOICES[0])
        ttk.Combobox(sec, textvariable=self._out_fmt_var, values=list(OUT_FORMAT_CHOICES),
                     state="readonly", width=6).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 3))
        self._hint(sec, "laz = LASzip-komprimiert (GDWH-Standard)  |  las = unkomprimiert", 3)

        self._bold(sec, "Grid-Shape (.shp):", 4, pady=(8, 3))
        self._grid_var = tk.StringVar(value=DEFAULT_GRID_SHAPE if os.path.isfile(DEFAULT_GRID_SHAPE) else "")
        ttk.Entry(sec, textvariable=self._grid_var).grid(row=4, column=1, sticky="ew", padx=(8, 4), pady=(8, 3))
        ttk.Button(sec, text="Datei…", command=self._browse_grid_shape).grid(row=4, column=2, pady=(8, 3))
        self._hint(sec, "Attributfeld 'NAME' = TileKey  |  regelmaessiges LV95-Grid (EPSG:2056), z.B. swissGRID 1km²", 5)

        ttk.Label(sec, text="Benennung:", font=("Segoe UI", 9, "bold")).grid(row=6, column=0, sticky="nw", pady=(10, 3))
        self._name_preview_lbl = ttk.Label(sec, text="–", font=("Courier New", 9), justify="left")
        self._name_preview_lbl.grid(row=6, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(10, 3))
        app._accent_labels.append(self._name_preview_lbl)
        self._hint(sec, "<Jahr>_<AREA>_TIN_DSM[_thinNN]_<NAME>_LV95_<LN02|LHN95>  (Beispiel NAME = {})"
                   .format(EXAMPLE_TILE_NAME), 7)
        pf = ("PF6 (PF7 bei Spalten Red/Green/Blue)" if self.fmt == "ascii"
              else "PF6 (PF7 bei RGB, PF8 bei NIR - nach den Feldern der Quelle)")
        self._hint(sec, "Zielformat (GDWH, wie swissSURFACE3D): LAS 1.4 · {} · scale 0.01 · Offset = "
                        "Kachelursprung · global_encoding 17 · CRS-VLRs 34735 + 2112".format(pf),
                   8, column=0, columnspan=3, pady=(8, 0))

    def _build_raster_options(self, parent):
        sec = self._section(parent, "DSM-Raster  (optional)")
        self._raster_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sec, text="Create DSM-Raster  (+ Hillshade) aus den neuen Kacheln",
                        variable=self._raster_var, command=self._update_raster_ui
                        ).grid(row=0, column=0, columnspan=3, sticky="w")

        # Nur sichtbar, wenn die Option aktiv ist
        self._raster_frame = rf = ttk.Frame(sec)
        rf.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        rf.columnconfigure(1, weight=1)
        self._bold(rf, "Raster-Output-Ordner:", 0)
        self._raster_dir_var = tk.StringVar()
        ttk.Entry(rf, textvariable=self._raster_dir_var).grid(row=0, column=1, sticky="ew", padx=(8, 4), pady=3)
        ttk.Button(rf, text="Ordner…", command=self._browse_raster_dir).grid(row=0, column=2, pady=3)
        self._bold(rf, "Aufloesung (GSD) [m]:", 1)
        self._gsd_var = tk.StringVar(value=DEFAULT_GSD)
        ttk.Entry(rf, textvariable=self._gsd_var, width=8).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=3)
        self._hint(rf, "IDW je Zelle, kleine Loecher (<= 900 m²) interpoliert, NoData wo keine Punkte liegen\n"
                       "DSM: Float32, NoData -3.4028235e+38, CRS LV95 + Hoehe  |  Hillshade: Byte, NoData 255, "
                       "CRS LV95  |  je .tif + .tfw", 2, column=0, columnspan=3, pady=(4, 0))
        self._update_raster_ui()

    def _update_raster_ui(self):
        if self._raster_var.get():
            self._raster_frame.grid()
        else:
            self._raster_frame.grid_remove()
        self._update_name_preview()

    def _browse_raster_dir(self):
        path = filedialog.askdirectory(title="Output-Ordner fuer DSM-Raster + Hillshade auswaehlen")
        if path:
            self._raster_dir_var.set(path.replace("/", "\\"))

    def _height_ref(self) -> str:
        return next((k for k, v in HEIGHT_LABELS.items() if v == self._height_label_var.get()), "LN02")

    def _thin_m(self) -> float:
        return next((v for l, v in THIN_LABELS if l == self._thin_label_var.get()), 0.0)

    def _on_height_changed(self):
        self._update_name_preview()
        if self._last_info:
            self._show_info(self._last_info)   # Warnung zur Hoehenangabe gegen die neue Auswahl pruefen

    # ── 4 Staging & Parallelisierung ──────────────────────────────────────────
    def _build_staging(self, parent):
        sec = self._section(parent, "Staging & Parallelisierung")
        self._bold(sec, "Staging-Ordner:", 0)
        self._staging_var = tk.StringVar()
        ttk.Entry(sec, textvariable=self._staging_var).grid(row=0, column=1, sticky="ew", padx=(8, 4), pady=3)
        ttk.Button(sec, text="Ordner…", command=self._browse_staging).grid(row=0, column=2, pady=3)
        inhalt = "ASCII→LAZ, Kachelstuecke, Raster-Zellen" if self.fmt == "ascii" else "Kachelstuecke, Raster-Zellen"
        self._hint(sec, "Leer = <Output-Ordner>\\_staging  |  Zwischendateien ({}), Platzbedarf etwa "
                        "Datenmenge als LAZ".format(inhalt), 1)

        self._bold(sec, "CPU-Kerne:", 2, pady=(8, 3))
        cpu_max = max(1, os.cpu_count() or 4)
        self._workers_var = tk.StringVar(value=str(min(4, cpu_max)))
        tk.Spinbox(sec, from_=1, to=cpu_max, textvariable=self._workers_var, width=6
                   ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 3))

        self._keep_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sec, text="Staging-Dateien nach Abschluss behalten (nicht loeschen)",
                        variable=self._keep_var).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

    # ── Benennung ─────────────────────────────────────────────────────────────
    def _update_name_preview(self):
        if getattr(self, "_name_preview_lbl", None) is None:
            return
        gsd = None
        if self._raster_var.get():
            try:
                gsd = float(self._gsd_var.get().replace(",", "."))
            except ValueError:
                gsd = None
        try:
            text = _naming_preview(self._jahr_var.get(), self._area_var.get(), self._thin_m(),
                                   self._height_ref(), self._out_fmt_var.get(),
                                   gsd if gsd and gsd > 0 else None)
        except Exception as e:
            text = "(Vorschau nicht moeglich: {})".format(e)
        self._name_preview_lbl.config(text=text)

    # ── Browse-Helfer ─────────────────────────────────────────────────────────
    def _browse_input(self):
        path = filedialog.askdirectory(title="Input-Ordner ({}) auswaehlen".format(FORMAT_NAMES[self.fmt]))
        if not path:
            return
        path = path.replace("/", "\\")
        self._in_var.set(path)
        if not self._out_var.get().strip():
            p = Path(path)
            self._out_var.set(str(p.parent / (p.name + "_LAZ_tiles")))
        self._update_name_preview()
        self.app._clear_log()
        self._refresh_info()

    def _browse_output(self):
        path = filedialog.askdirectory(title="Output-Ordner (LAZ-Kacheln) auswaehlen")
        if path:
            self._out_var.set(path.replace("/", "\\"))

    def _browse_grid_shape(self):
        current   = self._grid_var.get().strip()
        start_dir = os.path.dirname(current) if current and os.path.isfile(current) \
                    else os.path.dirname(DEFAULT_GRID_SHAPE)
        kwargs = {"title": "Grid-Shape auswaehlen",
                  "filetypes": [("Shapefile", "*.shp"), ("Alle Dateien", "*.*")]}
        if os.path.isdir(start_dir):
            kwargs["initialdir"] = start_dir
        path = filedialog.askopenfilename(**kwargs)
        if path:
            self._grid_var.set(path.replace("/", "\\"))

    def _browse_staging(self):
        path = filedialog.askdirectory(title="Staging-Ordner auswaehlen")
        if path:
            self._staging_var.set(path.replace("/", "\\"))

    # ── Datei-Info via Runner ──────────────────────────────────────────────────
    def _reset_info(self):
        for attr in ("_info_files", "_info_version", "_info_count", "_info_extent",
                     "_info_crs", "_info_cells", "_info_preview", "_info_meta"):
            if getattr(self, attr, None) is not None:
                getattr(self, attr).config(text="–")
        self._info_warn.grid_remove()
        self._last_info = None

    def _refresh_info(self):
        src = self._in_var.get().strip()
        self._reset_info()
        if not src or not os.path.isdir(src):
            return
        osgeo_python = self.app._osgeo_python
        if not osgeo_python or not os.path.isfile(osgeo_python):
            self._info_files.config(text="OSGeo4W Python nicht gefunden – bitte Pfad setzen")
            return

        cfg = {"action": "info", "input_dir": src, "format": self.fmt,
               "pdal_exe": self.app._pdal_exe, "grid_size": 1000}
        self._info_files.config(text="wird gelesen…")

        def ui_error(msg):
            try:
                from tkinter import messagebox
                messagebox.showerror("Datei-Info Fehler", msg[-2000:], parent=self.app)
            except Exception:
                pass
            self._reset_info()

        def ui_info(info):
            try:
                self._show_info(info)
            except Exception:
                ui_error("Fehler beim Darstellen der Datei-Info:\n" + traceback.format_exc())

        self.app._fetch_info_async(cfg, ui_info, ui_error)

    def _show_info(self, info: dict):
        T = DARK if self.app._dark else LIGHT
        other = "las" if self.fmt == "ascii" else "ascii"
        n = info.get("n_files", 0)
        n_other = info.get("n_" + other, 0)
        if not n:
            text = "(keine {}-Dateien im Ordner)".format(FORMAT_NAMES[self.fmt])
            if n_other:
                text += "  –  {} {}-Datei(en) gefunden: Tab '{}' verwenden".format(
                    n_other, FORMAT_NAMES[other], TAB_LABELS[other])
            self._info_files.config(text=text)
            return
        self._info_files.config(text="{} Datei(en), {:.1f} MB  |  z.B. {}".format(
            n, info.get("size_mb", 0.0), info.get("sample", "")))

        warnings = []
        if n_other:
            warnings.append("ℹ  Ordner enthaelt auch {} {}-Datei(en) – dieser Tab verarbeitet nur die "
                            "{}-Dateien.".format(n_other, FORMAT_NAMES[other], FORMAT_NAMES[self.fmt]))
        first_show = self._last_info is not info   # Neu-Anzeige nach CRS-Wechsel: Eingaben nicht ueberschreiben
        self._last_info = info
        if self.fmt == "ascii":
            sep = info.get("separator", "space")
            self._info_version.config(text="ASCII  |  Trennzeichen: {}  |  {} Spalten  |  {} Kopfzeile(n)  →  "
                                           "Ausgabe LAS 1.4 / PF{}".format(SEPARATOR_LABELS.get(sep, sep),
                                                                           info.get("ncols"), info.get("skip"),
                                                                           info.get("target_pf", "?")))
            self._info_count.config(text="(1. Datei) " + str((info.get("first_meta") or {}).get("count", "–"))
                                    + "  |  exakt erst nach dem Einlesen")
            x, y = info.get("first_xy", [0, 0])
            self._info_extent.config(text="1. Datenzeile:  {:.2f} / {:.2f}".format(x, y))
            self._info_cells.config(text="– (erst nach dem Einlesen bekannt)")
            self._info_preview.config(text="\n".join(line[:90] for line in info.get("preview", [])))
            if first_show:
                self._cols_var.set(info.get("columns", "X Y Z"))
                self._sep_label_var.set(SEPARATOR_LABELS.get(sep, SEPARATOR_LABELS["space"]))
                self._skip_var.set(str(info.get("skip", 0)))
            where, missing = "im Dateinamen als", "ohne Hoehenangabe im Dateinamen"
        else:
            self._info_version.config(text="{}  →  Ausgabe LAS 1.4 / PF{}".format(
                info.get("version", "–"), info.get("target_pf", "?")))
            self._info_count.config(text="{:,}".format(info.get("count_total", 0)).replace(",", "'")
                                    + "  (Summe aller Header)")
            e = info.get("extent", [0, 0, 0, 0])
            self._info_extent.config(text="{:.1f} – {:.1f}  /  {:.1f} – {:.1f}".format(e[0], e[2], e[1], e[3]))
            self._info_cells.config(text="≤ {}  (aus den Header-BBoxen, leere Zellen fallen weg)".format(
                info.get("n_cells_max", 0)))
            where, missing = "als", "ohne Hoehen-Tag"
        self._info_meta.config(text=self._format_meta(info.get("first_meta") or {}))

        # Hoehenangabe der Quelle (LAS: CRS-Tag, ASCII: Dateiname) gegen die Auswahl - nur Warnung
        chosen = self._height_ref()
        for tag, count in sorted((info.get("vertical_tags") or {}).items()):
            if tag == "ohne":
                warnings.append("ℹ  {} Datei(en) {} → werden als {} getaggt.".format(count, missing, chosen))
            elif tag != chosen:
                warnings.append("⚠  {} Datei(en) sind {} {} bezeichnet/getaggt, gewaehlt ist {} – die Hoehen werden "
                                "NICHT umgerechnet, nur als {} getaggt. Hoehenbezug der Quelle pruefen!".format(
                                    count, where, tag, chosen, chosen))

        crs = info.get("crs_guess", "unbekannt")
        crs_text = {"LV95": "LV95 (EPSG:2056) – Koordinatenbereich plausibel",
                    "LV03": "LV03 (EPSG:21781) – NICHT unterstuetzt",
                    "GRAD": "Grad (geographisch) – NICHT unterstuetzt",
                    }.get(crs, "unbekannt – Koordinaten weder LV95 noch LV03")
        if self.fmt == "las":
            crs_text += "  |  Tag 1. Datei: {}".format(info.get("crs_tag", "–"))
        self._info_crs.config(text=crs_text, foreground=T["accent"] if crs == "LV95" else T["err"])
        if crs == "LV03":
            warnings.append("⚠  Koordinaten liegen in LV03 – zuerst mit GeoSuite/REFRAME (FINELTRA) "
                            "nach LV95 transformieren; dieses Tool transformiert bewusst nicht.")
        elif crs == "GRAD":
            warnings.append("⚠  Koordinaten in Grad (z.B. CH1903+ EPSG:4150 oder WGS84) – zuerst nach LV95 "
                            "projizieren; ein reiner Tag EPSG:2056 waere falsch.")
        elif crs != "LV95":
            hint = "Spalten-Reihenfolge / Trennzeichen pruefen." if self.fmt == "ascii" \
                   else "Header-BBox der Dateien pruefen."
            warnings.append("⚠  Koordinaten weder LV95 noch LV03 – " + hint)

        if warnings:
            self._info_warn.config(text="\n".join(warnings))
            self._info_warn.grid()
        else:
            self._info_warn.grid_remove()

    # Zeilen der Metadaten-Anzeige: LAS/LAZ aus 'pdal info --metadata', ASCII aus der Stichprobe
    # (fehlende Schluessel werden ausgelassen)
    _META_ROWS = [("file", "Datei"), ("size", "Groesse"), ("version", "Version"), ("count", "Punkte"),
                  ("crs_horizontal", "Lage-CRS"), ("crs_vertical", "Hoehen-CRS"), ("name_hints", "Dateiname"),
                  ("first_line", "1. Zeile"), ("zrange", "Z-Bereich"), ("scale", "scale"), ("offset", "offset"),
                  ("global_encoding", "global_enc."), ("software", "Software"), ("vlrs", "VLRs"),
                  ("dims", "Dimensionen")]

    @classmethod
    def _format_meta(cls, meta: dict) -> str:
        """Metadaten der ersten Input-Datei als Textblock."""
        if not meta:
            return "–"
        if meta.get("error"):
            return "{}: nicht lesbar – {}".format(meta.get("file", ""), meta["error"][:300])
        rows = []
        for key, label in cls._META_ROWS:
            if key not in meta:
                continue
            v = meta[key]
            if key in ("scale", "offset"):
                v = " / ".join("{:.10g}".format(x) if isinstance(x, (int, float)) else str(x) for x in v)
            elif key == "count" and isinstance(v, (int, float)):
                v = "{:,}".format(int(v)).replace(",", "'")
            elif key == "vlrs":
                v = ", ".join(v) or "–"
            elif key == "dims":
                v = " ".join(v) or "–"
            elif key == "software" and meta.get("created"):
                v = "{}  |  erstellt {}".format(v, meta["created"])
            rows.append("{:<12}: {}".format(label, v))
        return "\n".join(rows)

    # ── Validierung ───────────────────────────────────────────────────────────
    def _validate(self) -> bool:
        app  = self.app
        errors = []
        inp  = self._in_var.get().strip()
        out  = self._out_var.get().strip()
        grid = self._grid_var.get().strip()

        if not app._osgeo_python or not os.path.isfile(app._osgeo_python):
            errors.append("OSGeo4W Python nicht gefunden.\n"
                          "Bitte Pfad via 'Aendern…' festlegen  (z.B. C:\\OSGeo4W\\bin\\python3.exe).")
        if not app._pdal_exe or not os.path.isfile(app._pdal_exe):
            errors.append("pdal.exe nicht gefunden (Teil von OSGeo4W/QGIS) - erwartet neben dem "
                          "OSGeo4W Python oder im PATH.")
        if not inp:
            errors.append("Input-Ordner fehlt.")
        elif not os.path.isdir(inp):
            errors.append("Input-Ordner nicht gefunden:\n  {}".format(inp))
        elif not _list_input_files(inp, self.fmt):
            errors.append("Keine {}-Dateien im Input-Ordner:\n  {}".format(FORMAT_NAMES[self.fmt], inp))
        if not out:
            errors.append("Output-Ordner fehlt.")
        elif inp and os.path.normcase(os.path.abspath(inp)) == os.path.normcase(os.path.abspath(out)):
            errors.append("Output-Ordner muss sich vom Input-Ordner unterscheiden.")
        if not grid:
            errors.append("Grid-Shape fehlt.")
        elif not os.path.isfile(grid):
            errors.append("Grid-Shape nicht gefunden:\n  {}".format(grid))
        if self.fmt == "ascii":
            cols = self._cols_var.get().split()
            if not {"X", "Y", "Z"} <= set(cols):
                errors.append("Spalten muessen X, Y und Z enthalten (z.B.  X Y Z).")
            if not self._skip_var.get().strip().isdigit():
                errors.append("Kopfzeilen ueberspringen: ganze Zahl >= 0 erwartet.")
        if not re.fullmatch(r"\d{4}", self._jahr_var.get().strip()):
            errors.append("Jahr: vierstellig erwartet (z.B. 2021).")
        area = self._area_var.get().strip()
        if not area:
            errors.append("AREA / AOI fehlt.")
        elif re.search(r'[<>:"/\\|?*\s]', area):
            errors.append('AREA enthaelt Leerzeichen oder unzulaessige Zeichen  (< > : " / \\ | ? *).')
        if self._raster_var.get():
            try:
                if float(self._gsd_var.get().replace(",", ".")) <= 0:
                    raise ValueError
            except ValueError:
                errors.append("Raster-Aufloesung (GSD): Zahl > 0 erwartet (z.B. 0.5).")
            if not self._raster_dir_var.get().strip():
                errors.append("Raster-Output-Ordner fehlt (Option 'Create DSM-Raster').")
        try:
            if int(self._workers_var.get()) < 1:
                raise ValueError
        except Exception:
            errors.append("CPU-Kerne ungueltig.")

        from tkinter import messagebox
        if errors:
            messagebox.showerror("Eingabe-Fehler", "\n\n".join("• " + e for e in errors), parent=app)
            return False

        existing = _list_input_files(out, "las") if os.path.isdir(out) else []
        if existing and not messagebox.askyesno(
                "Output-Ordner nicht leer",
                "Der Output-Ordner enthaelt bereits {} LAZ/LAS-Datei(en).\n"
                "Gleichnamige Kacheln werden ueberschrieben.\n\nFortfahren?".format(len(existing)),
                parent=app):
            return False
        return True

    # ── Verarbeitung starten ──────────────────────────────────────────────────
    def _start(self):
        if self.app._running or not self._validate():
            return

        inp = self._in_var.get().strip()
        cfg = {
            "action":          "process",
            "input_dir":       inp,
            "output_dir":      self._out_var.get().strip(),
            "grid_shape_path": self._grid_var.get().strip(),
            "format":          self.fmt,
            "jahr":            self._jahr_var.get().strip(),
            "area":            self._area_var.get().strip(),
            "height_ref":      self._height_ref(),
            "thin_m":          self._thin_m(),
            "out_format":      self._out_fmt_var.get(),
            "create_raster":   bool(self._raster_var.get()),
            "raster_dir":      self._raster_dir_var.get().strip(),
            "gsd":             float(self._gsd_var.get().replace(",", ".") or DEFAULT_GSD)
                               if self._raster_var.get() else float(DEFAULT_GSD),
            "staging_dir":     self._staging_var.get().strip(),
            "num_workers":     int(self._workers_var.get()),
            "keep_staging":    bool(self._keep_var.get()),
            "pdal_exe":        self.app._pdal_exe,
        }
        if self.fmt == "ascii":
            sep_key = next((k for k, v in SEPARATOR_LABELS.items() if v == self._sep_label_var.get()), "space")
            cfg.update({
                "columns":   " ".join(self._cols_var.get().split()),
                "separator": sep_key,
                "skip":      int(self._skip_var.get().strip() or 0),
            })

        self.app._start_run(cfg, "{}_to_laz".format(Path(inp).name), TAB_LABELS[self.fmt])


# ─── Haupt-App ─────────────────────────────────────────────────────────────────
class DsmToLazApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("DSM ASCII/LAZ → LAZ-Tiles")
        screen_h = self.winfo_screenheight()
        win_h    = min(880, screen_h - 80)
        self.geometry(f"820x{win_h}")
        self.minsize(680, min(760, win_h))
        self.resizable(True, True)

        self._dark    = False
        self._running = False
        self._log_q   = queue.Queue()
        self._ui_q    = queue.Queue()   # UI-Aufrufe aus Worker-Threads, abgearbeitet in _poll_log
        self._progress_start = None

        self._dim_labels    = []
        self._accent_labels = []
        self._hint_labels   = []
        self._tabs          = {}   # type: Dict[str, TilesTab]

        self._osgeo_python = _detect_osgeo_python()
        self._osgeo_lbl    = None
        self._osgeo_status = None
        self._pdal_exe     = _detect_pdal_exe(self._osgeo_python)

        self._build_ui()
        self._apply_theme(True)   # Dark Mode als Standard
        self.after(100, self._poll_log)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    # ── UI Aufbau ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        self._hdr = tk.Frame(self, height=52)
        self._hdr.pack(fill="x")
        self._hdr.pack_propagate(False)
        self._hdr_lbl = tk.Label(self._hdr, text="DSM ASCII/LAZ → LAZ-Tiles",
                                  font=("Segoe UI", 15, "bold"))
        self._hdr_lbl.pack(side="left", padx=16, pady=12)
        self._theme_btn = tk.Button(self._hdr, text="Dark",
                                     command=self._toggle_theme,
                                     relief="flat", borderwidth=0,
                                     font=("", 9), cursor="hand2",
                                     padx=10, pady=4)
        self._theme_btn.pack(side="right", padx=12)

        # OSGeo4W Python Zeile
        self._osgeo_frame = ttk.Frame(self)
        self._osgeo_frame.pack(fill="x", padx=12, pady=(6, 0))
        osgeo_lbl_static = ttk.Label(self._osgeo_frame, text="OSGeo4W Python:",
                                      font=("Segoe UI", 9))
        osgeo_lbl_static.pack(side="left")
        self._dim_labels.append(osgeo_lbl_static)
        self._osgeo_lbl = ttk.Label(self._osgeo_frame, font=("Courier New", 8),
                                     text=self._osgeo_python or "(nicht gefunden)")
        self._osgeo_lbl.pack(side="left", padx=(6, 0))
        self._osgeo_status = ttk.Label(self._osgeo_frame, font=("Segoe UI", 8, "bold"))
        self._osgeo_status.pack(side="left", padx=(6, 0))
        ttk.Button(self._osgeo_frame, text="Aendern…",
                    command=self._set_osgeo_python).pack(side="right")

        # Tabs: je Input-Format einer
        # Mausrad soll ueberall im Formular scrollen, nicht nur ueber der leeren Canvas-Flaeche
        self.bind_class("TCombobox", "<MouseWheel>", self._fwd_wheel)
        self.bind_all("<MouseWheel>", self._fwd_wheel)
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=12, pady=6)
        for fmt, label in TAB_LABELS.items():
            frame = ttk.Frame(self._notebook)
            self._notebook.add(frame, text=label)
            self._tabs[fmt] = TilesTab(self, frame, fmt)

        # Log
        ttk.Separator(self).pack(fill="x", padx=12, pady=4)
        log_frame = ttk.LabelFrame(self, text="Log-Ausgabe", padding=4,
                                    style="Section.TLabelframe")
        log_frame.pack(fill="x", padx=12, pady=(0, 4))
        self._log_box = scrolledtext.ScrolledText(
            log_frame, height=10, wrap="word", state="disabled",
            font=("Courier New", 9))
        self._log_box.pack(fill="both", expand=True)

        # Fortschrittsbalken (versteckt bis Verarbeitung laeuft)
        self._progress_frame = ttk.Frame(self)
        self._progress_bar   = ttk.Progressbar(self._progress_frame, mode="indeterminate")
        self._progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._progress_lbl = ttk.Label(self._progress_frame,
                                        text="Verarbeitung laeuft…", font=("", 9))
        self._progress_lbl.pack(side="left")

        # Buttons
        self._btn_row = ttk.Frame(self)
        self._btn_row.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Button(self._btn_row, text="Log loeschen",
                    command=self._clear_log).pack(side="right")

    def _build_group_header(self, parent, text):
        """Visueller Zwischentitel zur thematischen Gruppierung."""
        lbl = ttk.Label(parent, text=text, font=("Segoe UI", 10, "bold"))
        lbl.pack(fill="x", pady=(10, 2), anchor="w")
        self._accent_labels.append(lbl)
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(0, 6))

    # ── Datei-Info via Runner ──────────────────────────────────────────────────
    def _fetch_info_async(self, cfg: dict, on_info, on_error) -> None:
        """Ruft _osgeo_runner.py (Aktion 'info') als Subprocess auf; Ergebnis ueber
        on_info(dict) / on_error(msg) im UI-Thread."""
        def worker():
            tmp_name = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
                    json.dump(cfg, tmp, ensure_ascii=False)
                    tmp_name = tmp.name
                result = subprocess.run([self._osgeo_python, RUNNER_SCRIPT, tmp_name],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        universal_newlines=True, encoding="utf-8", errors="replace",
                                        env=self._osgeo_env())
                if result.returncode != 0:
                    self._call_in_ui(on_error, ((result.stdout or "") + "\n" + (result.stderr or "")).strip())
                    return
                lines = (result.stdout or "").strip().splitlines()
                self._call_in_ui(on_info, json.loads(lines[-1] if lines else "{}"))
            except Exception as e:
                self._call_in_ui(on_error, str(e))
            finally:
                try:
                    if tmp_name and os.path.exists(tmp_name):
                        os.unlink(tmp_name)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _osgeo_env(self) -> dict:
        env = os.environ.copy()
        env["PYTHONHOME"] = _detect_python_home(self._osgeo_python)
        # Per-User site-packages ausschliessen: verhindert NumPy-ABI-Konflikte mit der QGIS-GDAL
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    # ── Hilfsfunktionen ────────────────────────────────────────────────────────
    def _fwd_wheel(self, event):
        """Mausrad ueber beliebigen Formular-Widgets scrollt das Formular des jeweiligen Tabs
        (nicht Combobox-Werte)."""
        w = event.widget
        while w is not None and not isinstance(w, str):
            for tab in self._tabs.values():
                if w in (tab._canvas, tab._sf):
                    tab._canvas.yview_scroll(-1 * (event.delta // 120), "units")
                    return "break"
            w = getattr(w, "master", None)
        return "break"

    # ── OSGeo4W Python Verwaltung ──────────────────────────────────────────────
    def _update_osgeo_label(self):
        T = DARK if self._dark else LIGHT
        if not self._osgeo_python or not os.path.isfile(self._osgeo_python):
            self._osgeo_lbl.config(text=self._osgeo_python or "(nicht gefunden)")
            self._osgeo_status.config(text="✗ nicht gefunden", foreground=T["err"])
        elif not self._pdal_exe or not os.path.isfile(self._pdal_exe):
            self._osgeo_lbl.config(text=self._osgeo_python)
            self._osgeo_status.config(text="✓  |  ✗ pdal.exe fehlt", foreground=T["err"])
        else:
            self._osgeo_lbl.config(text=self._osgeo_python)
            self._osgeo_status.config(text="✓  (+ pdal.exe)", foreground=T["ok"])

    def _set_osgeo_python(self):
        init_dir = os.path.dirname(self._osgeo_python) if self._osgeo_python else r"C:\OSGeo4W\bin"
        if not os.path.isdir(init_dir):
            init_dir = "C:\\"
        path = filedialog.askopenfilename(
            title="OSGeo4W / QGIS Python auswaehlen",
            initialdir=init_dir,
            filetypes=[("Python", "python*.exe"), ("Executable", "*.exe"), ("Alle", "*.*")],
        )
        if path:
            path = path.replace("/", "\\")
            self._osgeo_python = path
            self._pdal_exe = _detect_pdal_exe(path)
            _save_osgeo_config(path)
            self._update_osgeo_label()

    # ── Theme ──────────────────────────────────────────────────────────────────
    def _toggle_theme(self):
        self._apply_theme(not self._dark)

    def _apply_theme(self, dark: bool):
        self._dark = dark
        T = DARK if dark else LIGHT
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".",
            background=T["panel"], foreground=T["fg"],
            fieldbackground=T["input"],
            selectbackground=T["sel_bg"], selectforeground=T["sel_fg"],
            bordercolor=T["sep"], lightcolor=T["panel"], darkcolor=T["sep"],
            insertcolor=T["fg"], troughcolor=T["root"],
        )
        s.configure("TFrame",      background=T["panel"])
        s.configure("TLabelframe", background=T["panel"], bordercolor=T["sep"])
        s.configure("TLabelframe.Label",
                    background=T["panel"], foreground=T["fg"], font=("Segoe UI", 9, "bold"))
        s.configure("Section.TLabelframe", background=T["panel"], bordercolor=T["sep"])
        s.configure("Section.TLabelframe.Label",
                    background=T["panel"], foreground=T["accent"], font=("Segoe UI", 10, "bold"))
        s.configure("TLabel",  background=T["panel"], foreground=T["fg"])
        s.configure("TCheckbutton", background=T["panel"], foreground=T["fg"])
        s.map("TCheckbutton", background=[("active", T["panel"])])
        s.configure("TButton",
            background=T["btn"], foreground=T["fg"],
            bordercolor=T["sep"], relief="flat",
            padding=(8, 4), focuscolor=T["panel"])
        s.map("TButton",
            background=[("active", T["btn_hover"]), ("pressed", T["sep"])],
            foreground=[("active", T["fg"])],
            relief=[("pressed", "flat")])
        s.configure("TCombobox",
            fieldbackground=T["input"], background=T["btn"],
            foreground=T["fg"], arrowcolor=T["fg"],
            selectbackground=T["sel_bg"], selectforeground=T["sel_fg"],
            bordercolor=T["sep"], insertcolor=T["fg"])
        s.map("TCombobox",
            fieldbackground=[("readonly", T["input"]), ("disabled", T["panel"])],
            selectbackground=[("readonly", T["input"])],
            selectforeground=[("readonly", T["fg"])],
            foreground=[("readonly", T["fg"]), ("disabled", T["fg_dim"])],
            background=[("active", T["btn_hover"])])
        s.configure("TEntry",
            fieldbackground=T["input"], foreground=T["fg"],
            bordercolor=T["sep"], insertcolor=T["fg"],
            selectbackground=T["sel_bg"], selectforeground=T["sel_fg"])
        s.configure("Vertical.TScrollbar",
            background=T["btn"], troughcolor=T["root"],
            bordercolor=T["sep"], arrowcolor=T["fg"])
        s.configure("TSeparator",  background=T["sep"])
        s.configure("TProgressbar",
            background=T["accent"], troughcolor=T["root"], bordercolor=T["sep"])
        s.configure("TNotebook", background=T["root"], bordercolor=T["sep"])
        s.configure("TNotebook.Tab",
            background=T["btn"], foreground=T["fg"], bordercolor=T["sep"], padding=(10, 4))
        s.map("TNotebook.Tab",
            background=[("selected", T["panel"]), ("active", T["btn_hover"])],
            foreground=[("selected", T["accent"])],
            padding=[("selected", (16, 8))])

        self.option_add("*TCombobox*Listbox.background",       T["list"])
        self.option_add("*TCombobox*Listbox.foreground",       T["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", T["sel_bg"])
        self.option_add("*TCombobox*Listbox.selectForeground", T["sel_fg"])

        self.configure(bg=T["root"])
        for tab in self._tabs.values():
            tab._canvas.configure(bg=T["panel"], highlightbackground=T["sep"])

        self._hdr.configure(bg=T["hdr_bg"])
        self._hdr_lbl.configure(bg=T["hdr_bg"], fg=T["hdr_fg"])
        self._theme_btn.configure(
            bg=T["hdr_bg"], fg=T["hdr_fg"],
            activebackground=T["btn"], activeforeground=T["fg"],
            text="Hell" if dark else "Dark")

        self._log_box.configure(bg=T["log_bg"], fg=T["log_fg"], insertbackground=T["log_fg"])

        for lbl in self._dim_labels:
            try: lbl.configure(foreground=T["fg_dim"])
            except tk.TclError: pass
        for lbl in self._accent_labels:
            try: lbl.configure(foreground=T["accent"])
            except tk.TclError: pass
        for lbl in self._hint_labels:
            try: lbl.configure(foreground=T["hint"])
            except tk.TclError: pass

        if self._osgeo_lbl is not None:
            self._update_osgeo_label()
        self._set_titlebar_dark(dark)

    def _set_titlebar_dark(self, dark: bool):
        if not self.winfo_ismapped():
            self.after(50, lambda: self._set_titlebar_dark(dark))
            return
        try:
            hwnd  = int(self.wm_frame(), 16)
            value = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except Exception:
            pass

    # ── Log / Fortschritt ─────────────────────────────────────────────────────
    def _log(self, text: str):
        self._log_box.config(state="normal")
        self._log_box.insert("end", text)
        self._log_box.see("end")
        self._log_box.config(state="disabled")

    def _clear_log(self):
        self._log_box.config(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.config(state="disabled")

    def _call_in_ui(self, fn, *args):
        """Tkinter ist nicht thread-sicher (Python 3.14 lehnt 'after' aus Threads ohne
        laufenden mainloop ab): Worker-Threads reichen UI-Aufrufe ueber eine Queue weiter."""
        self._ui_q.put((fn, args))

    def _poll_log(self):
        try:
            while True:
                self._log(self._log_q.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                fn, args = self._ui_q.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    self._log("\n[GUI-FEHLER]\n" + traceback.format_exc())
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _update_progress(self, fraction: float):
        """Fortschrittsbalken + Restzeit, fraction in [0 .. 1]."""
        try:
            if self._progress_start is None:
                self._progress_start = time.time()
                self._progress_bar.stop()
                self._progress_bar.config(mode="determinate", maximum=100)
            pct = max(0.0, min(1.0, fraction))
            self._progress_bar["value"] = pct * 100.0
            eta = "--:--"
            if pct > 0:
                remaining = (time.time() - self._progress_start) * (1.0 - pct) / pct
                eta = "{:d}m {:02d}s".format(int(remaining // 60), int(remaining % 60))
            self._progress_lbl.config(text="{:5.1f}% — verbleibend: {}".format(pct * 100, eta))
        except Exception:
            pass

    def _set_start_buttons(self, state: str):
        # Log und Fortschritt sind gemeinsam -> waehrend eines Laufs alle Tabs sperren
        for tab in self._tabs.values():
            tab._start_btn.config(state=state)

    def _on_done(self, success: bool):
        self._running = False
        self._set_start_buttons("normal")
        self._progress_bar.stop()
        self._progress_frame.pack_forget()
        self._log("\n✔  LAZ-Kacheln erfolgreich erstellt.\n" if success
                  else "\n✘  Verarbeitung fehlgeschlagen.\n")
        from tkinter import messagebox
        if success:
            messagebox.showinfo("LAZ-Tiles abgeschlossen",
                                "LAZ-Kacheln erfolgreich erstellt.", parent=self)
        else:
            messagebox.showerror("LAZ-Tiles fehlgeschlagen",
                                 "Verarbeitung ist fehlgeschlagen.\nDetails siehe Log-Ausgabe.", parent=self)

    # ── Verarbeitung starten ──────────────────────────────────────────────────
    def _start_run(self, cfg: dict, log_stem: str, title: str):
        self._running = True
        self._progress_start = None
        self._set_start_buttons("disabled")
        self._progress_frame.pack(fill="x", padx=12, pady=(0, 4), before=self._btn_row)
        self._progress_bar.config(mode="indeterminate")
        self._progress_bar.start(10)
        self._clear_log()
        self._log("=== {} gestartet ===\n\n".format(title))
        threading.Thread(target=self._run_thread, args=(cfg, log_stem), daemon=True).start()

    def _run_thread(self, cfg: dict, log_stem: str):
        try:
            self._run_osgeo_subprocess(cfg, log_stem)
            self._call_in_ui(self._on_done, True)
        except Exception as e:
            self._log_q.put("\n[FEHLER] {}\n".format(e))
            self._log_q.put(traceback.format_exc())
            self._call_in_ui(self._on_done, False)

    def _run_osgeo_subprocess(self, cfg: dict, log_stem: str) -> None:
        """Startet _osgeo_runner.py als Subprocess; Log-Ausgabe + Fortschritt live im GUI."""
        logs_dir = Path(SCRIPT_DIR) / "logs"
        logs_dir.mkdir(exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        log_path  = logs_dir / "{}_{}.log".format(log_stem, timestamp)

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump(cfg, tmp, ensure_ascii=False, indent=2)
            tmp_name = tmp.name
        try:
            header = "[Subprocess] {}\n\n".format(self._osgeo_python)
            self._log_q.put(header)
            proc = subprocess.Popen(
                [self._osgeo_python, RUNNER_SCRIPT, tmp_name],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, encoding="utf-8", errors="replace",
                env=self._osgeo_env(),
            )
            with open(str(log_path), "w", encoding="utf-8") as lf:
                lf.write(header)
                for line in proc.stdout:
                    stripped = line.strip()
                    if stripped.startswith("PROGRESS:"):
                        try:
                            self._call_in_ui(self._update_progress, float(stripped.split(":", 1)[1]))
                        except ValueError:
                            pass
                        continue   # Fortschritt nur im Balken, nicht im Log
                    self._log_q.put(line)
                    lf.write(line)
            proc.wait()
            self._log_q.put("\nLog gespeichert: {}\n".format(log_path))
            if proc.returncode != 0:
                raise RuntimeError("OSGeo4W Subprocess beendet mit Exit-Code {}".format(proc.returncode))
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = DsmToLazApp()
    app.mainloop()
