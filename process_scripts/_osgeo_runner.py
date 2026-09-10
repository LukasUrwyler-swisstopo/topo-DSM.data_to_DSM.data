"""
_osgeo_runner.py - Wird via OSGeo4W/QGIS Python aufgerufen (NICHT direkt starten).
Liest Parameter aus einer JSON-Datei. Punktwolken-Operationen laufen via PDAL-CLI
(pdal.exe als Subprocess), das Grid-Shape wird mit OGR gelesen.
Ausgabe geht auf stdout -> wird vom GUI live im Log angezeigt.

Aktionen:
    info    - Input-Ordner analysieren (Format, ASCII-Vorschau, Extent, CRS-Plausibilitaet),
              Ergebnis als JSON-Zeile auf stdout
    process - ASCII (xyz) oder LAZ/LAS (altes Tiling) -> LAZ-Kacheln gemaess Grid-Shape
              (Dateiname aus Attributfeld 'NAME'), siehe Kommentar ueber _process()
"""

import base64
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ASCII_EXTENSIONS = (".xyz", ".txt", ".asc", ".csv")
LAS_EXTENSIONS   = (".laz", ".las")

# Trennzeichen: GUI-Schluessel -> Zeichen fuer readers.text
SEPARATORS = {"space": " ", "tab": "\t", "comma": ",", "semicolon": ";"}

# ─── Zielformat der LAZ-Kacheln ────────────────────────────────────────────────
# Identisch zu SB_DSM_PUNKTWOLKE / swissSURFACE3D (topo-importDATAtoGDWHandSTAC,
# 4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py) und topo-DMCdataConverter, damit die Kacheln
# ohne weiteren Upgrade-Schritt GDWH-tauglich sind.
OUT_MINOR_VERSION    = 4
OUT_POINT_FORMAT     = 6      # PF6 (ohne Farbe) - Standard fuer DSM aus xyz
OUT_POINT_FORMAT_RGB = 7      # PF7 = PF6 + RGB, nur wenn die Quelle Farbe fuehrt
OUT_GLOBAL_ENCODING  = 17     # Bit 0 (Adjusted Standard GPS Time) + Bit 4 (WKT)
OUT_SCALE            = 0.01   # Schweizer Konvention
FRAME_TOLERANCE_M    = 0.02   # Rundungsrauschen am Kachelrand nach Requantisierung

PC_FORMATS_WITH_RGB = (2, 3, 5, 7, 8, 10)

# ─── Benennung der Kacheln ─────────────────────────────────────────────────────
# <Basis>_<NAME>_LV95_LN02.laz. Basis = Input-Dateiname bis vor '_LV95' (der Rest wie
# '_CIR_low_raw' faellt weg), ein alter TileKey am Ende der Basis wird entfernt:
#   2015_RHONE_DSM_1m_LV95_LN02_CIR_low_raw.asc -> 2015_RHONE_DSM_1m_2600_1200_LV95_LN02.laz
# Die Endung entspricht dem Muster des GDWH-Imports (4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py).
# Dateien mit gleicher Basis ergeben EINEN Kachelsatz (je Zelle zusammengefuehrt).
OUT_NAME_SUFFIX = "_LV95_LN02"
_LV95_MARKER    = re.compile(r"_LV95(?=[_.\s-]|$)", re.IGNORECASE)
# Alter TileKey am Ende der Basis ('_2600_1200', '_1091-44', '_600_200'). Die erste Zahl
# muss LV95-km (2480-2840), LK25-Blatt (1011-1374) oder LV03-km (480-840) sein, damit
# z.B. ein Jahr ('_2015_1') nicht faelschlich als TileKey gilt.
_OLD_TILEKEY    = re.compile(r"[_-](\d{3,4})[_-]\d{1,4}$")

# Hoehen sind immer LN02: getaggt wird ausschliesslich mit den byte-exakten GDWH-
# Referenz-VLRs (_inject_reference_vlrs), LHN95-Quellen werden abgelehnt.
PLAUSIBLE_Z = (150.0, 4900.0)   # Schweiz: tiefster Punkt 193 m, hoechster 4634 m (+ Reserve)

# Grosse ASCII-Dateien (gemergte Gebiete) werden fuer paralleles Einlesen an Zeilen-
# grenzen in Teile zerlegt - readers.text liest einspurig (gemessen ~0.5 Mio Punkte/s).
ASCII_CHUNK_MB = 256

# Plausible Koordinatenbereiche (m) mit Rand, zur Erkennung LV95 vs. LV03
LV95_RANGE = ((2400000.0, 2900000.0), (1000000.0, 1400000.0))
LV03_RANGE = ((400000.0, 900000.0), (0.0, 400000.0))
NOT_LV95_HINT = ("LV03-Daten zuerst mit GeoSuite/REFRAME (FINELTRA) nach LV95 transformieren - "
                 "dieses Tool transformiert bewusst nicht (PROJ haette ohne CHENyx06-Gitter nur "
                 "eine Ballpark-Transformation).")

# ─── Byte-exakte CRS-VLRs LV95/LN02 ────────────────────────────────────────────
# Unveraendert uebernommen aus topo-DMCdataConverter (dort aus der verifizierten
# swissSURFACE3D-Referenzkachel 2655_1272.laz). Begruendung, warum nicht PDAL den
# WKT schreiben darf: siehe README Abschnitt "CRS-Tags".
REFERENCE_VLR_DESCRIPTION = "by LAStools of rapidlasso GmbH"
CRS_VLR_USER_IDS   = ("LASF_Projection", "liblas")
CRS_VLR_RECORD_IDS = (2111, 2112, 34735, 34736, 34737)
REFERENCE_VLR_34735_B64 = (
    "AQABAAAABQAABAAAAQABAAAMAAABAAgIBAwAAAEAKSMDEAAAAQApIwAQAAABAGAW"
)
REFERENCE_VLR_2112_B64 = (
    "Q09NUE9VTkRDUlNbIlByb2plY3RlZCBjb29yZGluYXRlIHN5c3RlbSB3aXRoIGVsZXZhdGlvbiIsUFJPSkNTWyJDSDE5MDMrIC8gTFY5"
    "NSIsR0VPR0NTWyJDSDE5MDMrIixEQVRVTVsiQ0gxOTAzKyIsU1BIRVJPSURbIkJlc3NlbCAxODQxIiw2Mzc3Mzk3LjE1NSwyOTkuMTUy"
    "ODEyOCxBVVRIT1JJVFlbIkVQU0ciLCI3MDA0Il1dLEFVVEhPUklUWVsiRVBTRyIsIjYxNTAiXV0sUFJJTUVNWyJHcmVlbndpY2giLDAs"
    "QVVUSE9SSVRZWyJFUFNHIiwiODkwMSJdXSxVTklUWyJkZWdyZWUiLDAuMDE3NDUzMjkyNTE5OTQzMyxBVVRIT1JJVFlbIkVQU0ciLCI5"
    "MTIyIl1dLEFVVEhPUklUWVsiRVBTRyIsIjQxNTAiXV0sUFJPSkVDVElPTlsiSG90aW5lX09ibGlxdWVfTWVyY2F0b3JfQXppbXV0aF9D"
    "ZW50ZXIiXSxQQVJBTUVURVJbImxhdGl0dWRlX29mX2NlbnRlciIsNDYuOTUyNDA1NTU1NTU1Nl0sUEFSQU1FVEVSWyJsb25naXR1ZGVf"
    "b2ZfY2VudGVyIiw3LjQzOTU4MzMzMzMzMzMzXSxQQVJBTUVURVJbImF6aW11dGgiLDkwXSxQQVJBTUVURVJbInJlY3RpZmllZF9ncmlk"
    "X2FuZ2xlIiw5MF0sUEFSQU1FVEVSWyJzY2FsZV9mYWN0b3IiLDFdLFBBUkFNRVRFUlsiZmFsc2VfZWFzdGluZyIsMjYwMDAwMF0sUEFS"
    "QU1FVEVSWyJmYWxzZV9ub3J0aGluZyIsMTIwMDAwMF0sVU5JVFsibWV0cmUiLDEsQVVUSE9SSVRZWyJFUFNHIiwiOTAwMSJdXSxBWElT"
    "WyJFYXN0aW5nIixFQVNUXSxBWElTWyJOb3J0aGluZyIsTk9SVEhdLEFVVEhPUklUWVsiRVBTRyIsIjIwNTYiXV0sVkVSVF9DU1siTE4w"
    "MiBoZWlnaHQiLFZFUlRfREFUVU1bIkxhbmRlc25pdmVsbGVtZW50IDE5MDIiLDIwMDUsQVVUSE9SSVRZWyJFUFNHIiwiNTEyNyJdXSxV"
    "TklUWyJtZXRyZSIsMSxBVVRIT1JJVFlbIkVQU0ciLCI5MDAxIl1dLEFYSVNbIkdyYXZpdHktcmVsYXRlZCBoZWlnaHQiLFVQXSxBVVRI"
    "T1JJVFlbIkVQU0ciLCI1NzI4Il1dXQA="
)

# Staging-Namen bewusst kurz: pdal.exe ist nicht long-path-aware (Windows MAX_PATH).
# Getestet (PDAL 2.10): Pipeline-Datei mit 262 Zeichen -> "PDAL: file not found".
PIECE_NAME_PATTERN = re.compile(r"^t(-?\d+)_(-?\d+)\.laz$", re.IGNORECASE)
PDAL_MAX_PATH      = 259     # MAX_PATH 260 inkl. Nullbyte
STAGING_NAME_RESERVE = 24    # laengster Staging-Name unter run_dir: "\p\00000\t2600_1200.laz"
NAME_FIELD_RESERVE   = 15    # Feldbreite 'NAME' in swissGRID


def _check_path_lengths(files: list, run_dir: Path, output_dir: str, max_base_len: int) -> None:
    """Bricht vor der Verarbeitung ab, wenn pdal.exe einen Pfad nicht oeffnen koennte
    (sonst scheitert der Lauf erst mittendrin mit einem irrefuehrenden 'file not found')."""
    checks = [("Input-Datei", max(len(f) for f in files)),
              ("Staging-Datei", len(str(run_dir)) + STAGING_NAME_RESERVE),
              ("Output-Kachel", len(output_dir) + 1 + max_base_len + 1 + NAME_FIELD_RESERVE
               + len(OUT_NAME_SUFFIX) + 4)]
    too_long = [f"{label}: bis {n} Zeichen" for label, n in checks if n > PDAL_MAX_PATH]
    if too_long:
        raise ValueError("Pfad zu lang fuer pdal.exe (Windows MAX_PATH = 260 Zeichen, pdal.exe ist nicht "
                         "long-path-faehig):\n  " + "\n  ".join(too_long)
                         + "\nKuerzeren Input-/Output-/Staging-Ordner waehlen (z.B. D:\\staging).")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _progress(fraction: float) -> None:
    print(f"PROGRESS:{max(0.0, min(1.0, fraction)):.6f}", flush=True)


def _fmt_count(n) -> str:
    return f"{int(n):,}".replace(",", "'")


# ─── LAS-Header (reines Python, ohne Punktdaten/Dekompression) ─────────────────
def _read_las_header(path: str) -> dict:
    """Liest die fuer Tiling/Validierung noetigen Felder des LAS-Public-Headers
    (ASPRS LAS 1.4 R15, Tabelle 3). Funktioniert auch fuer LAZ, da der Header dort
    unkomprimiert ist - schnell auch bei tausenden Dateien (kein pdal-Aufruf)."""
    with open(path, "rb") as f:
        b = f.read(375)
    if len(b) < 227 or b[:4] != b"LASF":
        raise ValueError(f"Keine gueltige LAS/LAZ-Datei (Signatur 'LASF' fehlt): {path}")
    minor = b[25]
    header_size, offset_to_point_data, n_vlr = struct.unpack_from("<HII", b, 94)
    point_format = b[104] & 0x3F          # Bits 6/7 = LASzip-Kompressionsflag
    global_encoding, = struct.unpack_from("<H", b, 6)
    count, = struct.unpack_from("<I", b, 107)
    if minor >= 4 and len(b) >= 255:
        count64, = struct.unpack_from("<Q", b, 247)
        count = count64 or count
    sx, sy, sz, ox, oy, oz = struct.unpack_from("<6d", b, 131)
    maxx, minx, maxy, miny, maxz, minz = struct.unpack_from("<6d", b, 179)
    return {
        "minor_version": minor, "header_size": header_size,
        "offset_to_point_data": offset_to_point_data, "n_vlr": n_vlr,
        "point_format": point_format, "compressed": bool(b[104] & 0xC0),
        "global_encoding": global_encoding, "count": int(count),
        "scale": (sx, sy, sz), "offset": (ox, oy, oz),
        "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy, "minz": minz, "maxz": maxz,
    }


def _read_vlr_ids(path: str, header: dict) -> list:
    """(user_id, record_id) aller VLRs - fuer die CRS-Tag-Kontrolle der Ausgabe."""
    ids = []
    with open(path, "rb") as f:
        f.seek(header["header_size"])
        for _ in range(header["n_vlr"]):
            rec = f.read(54)
            if len(rec) < 54:
                break
            _, uid, rid, rlen, _ = struct.unpack("<H16sHH32s", rec)
            ids.append((uid.split(b"\x00")[0].decode("ascii", "replace"), rid))
            f.seek(rlen, 1)
    return ids


def _classify_crs(minx: float, miny: float, maxx: float, maxy: float) -> str:
    """'LV95' | 'LV03' | 'unbekannt' anhand des Koordinatenbereichs (Plausibilitaet)."""
    def _inside(rng):
        (x0, x1), (y0, y1) = rng
        return x0 <= minx <= maxx <= x1 and y0 <= miny <= maxy <= y1
    if _inside(LV95_RANGE):
        return "LV95"
    if _inside(LV03_RANGE):
        return "LV03"
    return "unbekannt"


# ─── PDAL-Aufrufe ──────────────────────────────────────────────────────────────
def _resolve_pdal_exe(cfg: dict) -> str:
    """pdal.exe aus der GUI-Config, sonst neben dem laufenden (OSGeo4W/QGIS-)Python."""
    candidates = [cfg.get("pdal_exe") or "",
                  str(Path(sys.executable).parent / "pdal.exe"),
                  shutil.which("pdal") or ""]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("pdal.exe nicht gefunden (weder in der Config noch neben "
                            f"{sys.executable} oder im PATH).")


def _pdal_env(pdal_exe: str) -> dict:
    env = os.environ.copy()
    env["PATH"] = os.path.dirname(pdal_exe) + os.pathsep + env.get("PATH", "")
    return env


def _run_pdal(pdal_exe: str, args: list) -> str:
    """Fuehrt pdal.exe aus, gibt stdout+stderr zurueck, wirft bei Exit-Code != 0."""
    result = subprocess.run([pdal_exe] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, env=_pdal_env(pdal_exe))
    out = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        hint = "" if out else (" - kein stdout/stderr: deutet auf einen Prozessabsturz hin "
                               "(z.B. zu wenig RAM bei vielen parallelen pdal.exe).")
        raise RuntimeError(f"pdal {args[0]} beendet mit Exit-Code {result.returncode}{hint}"
                           + (f": {out[:800]}" if out else ""))
    return out


def _run_pipeline(pdal_exe: str, stages: list, pipeline_path: Path) -> str:
    with open(pipeline_path, "w", encoding="utf-8") as f:
        json.dump({"pipeline": stages}, f, indent=1)
    return _run_pdal(pdal_exe, ["pipeline", str(pipeline_path)])


def _pdal_srs_name(pdal_exe: str, path: str) -> str:
    """Name des CRS-Tags einer LAS/LAZ-Datei ('pdal info --metadata', nur Header)."""
    try:
        md = json.loads(_run_pdal(pdal_exe, ["info", "--metadata", path]))["metadata"]
    except Exception as e:
        return f"(nicht lesbar: {e})"
    srs = md.get("srs") or {}
    wkt = srs.get("compoundwkt") or srs.get("wkt") or ""
    if not wkt:
        return "– (kein CRS-Tag in der Datei)"
    m = re.search(r'^(?:COMPD_CS|COMPOUNDCRS|PROJCS|PROJCRS|GEOGCS)\["([^"]+)"', wkt)
    label = m.group(1) if m else wkt[:60]
    for token in ("LN02", "LHN95"):
        if token in wkt:
            return f"{label}  [{token}]"
    return label


def _discard(path) -> None:
    try:
        if path and os.path.isfile(str(path)):
            os.remove(str(path))
    except OSError:
        pass


# ─── Byte-exakte CRS-VLR-Injektion (aus topo-DMCdataConverter) ─────────────────
def _is_crs_vlr(user_id: str, record_id: int) -> bool:
    """True, wenn dieser VLR einen Raumbezug deklariert."""
    if user_id == "LASF_Projection":
        return True
    return user_id in CRS_VLR_USER_IDS and record_id in CRS_VLR_RECORD_IDS


def _build_vlr_record(user_id: str, record_id: int, description: str, payload: bytes) -> bytes:
    """Baut einen kompletten LAS-VLR (54-Byte-Header + Payload)."""
    header = struct.pack("<H16sHH32s", 0, user_id.encode("ascii").ljust(16, b"\x00"),
                         record_id, len(payload), description.encode("ascii").ljust(32, b"\x00"))
    return header + payload


def _inject_reference_vlrs(las_path: str) -> int:
    """Ersetzt alle CRS-VLRs durch die zwei byte-exakten LV95/LN02-Referenz-VLRs
    (GeoTIFF-KeyDirectory 34735 + OGC-WKT 2112). In-place, nur auf Temp-Dateien
    aufrufen. Bei LAZ wird zusaetzlich die LASzip-Chunk-Table-Position korrigiert
    (int64 am Anfang des Punktbereichs), sonst bricht jede Dekompression ab.
    Gibt die Anzahl entfernter CRS-VLRs zurueck."""
    with open(las_path, "rb") as f:
        head = f.read(512)
        header_size, offset_to_point_data, n_vlr = struct.unpack_from("<HII", head, 94)
        f.seek(0)
        data = f.read(offset_to_point_data)

    existing = data[header_size:offset_to_point_data]
    is_laszip, n_stripped, kept, pos = False, 0, [], 0
    for _ in range(n_vlr):
        _, uid_raw, record_id, record_len, _ = struct.unpack_from("<H16sHH32s", existing, pos)
        user_id = uid_raw.split(b"\x00")[0].decode("ascii", "replace")
        vlr_len = 54 + record_len
        if _is_crs_vlr(user_id, record_id):
            n_stripped += 1
        else:
            kept.append(existing[pos:pos + vlr_len])
        if user_id == "laszip encoded" and record_id == 22204:
            is_laszip = True
        pos += vlr_len

    vlr1 = _build_vlr_record("LASF_Projection", 34735, REFERENCE_VLR_DESCRIPTION,
                             base64.b64decode(REFERENCE_VLR_34735_B64))
    vlr2 = _build_vlr_record("LASF_Projection", 2112, REFERENCE_VLR_DESCRIPTION,
                             base64.b64decode(REFERENCE_VLR_2112_B64))
    new_vlr_block = b"".join(kept) + vlr1 + vlr2
    new_offset = header_size + len(new_vlr_block)
    shift = new_offset - offset_to_point_data

    new_header = bytearray(data[:header_size])
    struct.pack_into("<I", new_header, 96, new_offset)
    struct.pack_into("<I", new_header, 100, n_vlr - n_stripped + 2)
    ge, = struct.unpack_from("<H", new_header, 6)
    struct.pack_into("<H", new_header, 6, ge | 0x10)  # WKT-Bit setzen

    tmp_out = las_path + ".vlrtmp"
    try:
        with open(las_path, "rb") as fin, open(tmp_out, "wb") as fout:
            fout.write(new_header)
            fout.write(new_vlr_block)
            fin.seek(offset_to_point_data)
            if is_laszip:
                first8 = fin.read(8)
                if len(first8) == 8:
                    chunk_table_pos, = struct.unpack("<q", first8)
                    if chunk_table_pos != -1:
                        first8 = struct.pack("<q", chunk_table_pos + shift)
                fout.write(first8)
            shutil.copyfileobj(fin, fout, 8 * 1024 * 1024)
        os.replace(tmp_out, las_path)
        tmp_out = None
    finally:
        _discard(tmp_out)
    return n_stripped


# ─── ASCII-Analyse ─────────────────────────────────────────────────────────────
# Spaltennamen aus einer Kopfzeile -> PDAL-Dimensionsnamen (Gross-/Kleinschreibung egal)
_KNOWN_DIMS = {"x": "X", "y": "Y", "z": "Z", "h": "Z", "intensity": "Intensity",
               "classification": "Classification", "class": "Classification",
               "red": "Red", "green": "Green", "blue": "Blue"}


def _is_number(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def _split_fields(line: str, sep_key: str) -> list:
    if sep_key == "space":
        return line.split()
    return [t.strip() for t in line.split(SEPARATORS[sep_key])]


def _sniff_ascii(path: str, max_lines: int = 50) -> dict:
    """Erkennt Kopfzeilen, Trennzeichen und Spaltenzahl aus den ersten Zeilen einer
    ASCII-Datei und schaetzt das Koordinatensystem aus dem Wertebereich."""
    lines = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for _ in range(max_lines):
            line = f.readline()
            if not line:
                break
            if line.strip():
                lines.append(line.rstrip("\r\n"))
    if not lines:
        raise ValueError(f"Datei ist leer: {path}")
    if lines[0].split()[0].lower() in ("ncols", "nrows", "xllcorner", "xllcenter"):
        raise ValueError(f"{os.path.basename(path)} ist ein ESRI-ASCII-Grid (Raster mit ncols/nrows-Kopf), "
                         "keine XYZ-Punktliste - z.B. mit 'gdal_translate -of XYZ' umwandeln.")

    skip = 0
    for line in lines:
        if _is_number(re.split(r"[\s,;]+", line.strip())[0]):
            break
        skip += 1
    data = lines[skip:]
    if not data:
        raise ValueError(f"Keine numerische Datenzeile in den ersten {max_lines} Zeilen: {path}")

    sample = data[0]
    if "\t" in sample:
        sep = "tab"
    elif ";" in sample:
        sep = "semicolon"
    elif "," in sample:
        sep = "comma"
    else:
        sep = "space"
    fields = _split_fields(sample, sep)
    if len(fields) < 3 or not all(_is_number(t) for t in fields[:3]):
        raise ValueError(f"Erste Datenzeile hat keine 3 numerischen Spalten (X Y Z): '{sample[:80]}'")

    ncols = len(fields)
    columns = ["X", "Y", "Z"] + [f"Col{i}" for i in range(4, ncols + 1)]
    if skip:
        tokens = _split_fields(lines[skip - 1], sep)
        if len(tokens) == ncols:
            mapped = [_KNOWN_DIMS.get(t.strip().strip('"').lower()) for t in tokens]
            columns = [m or columns[i] for i, m in enumerate(mapped)]

    xs, ys = [], []
    for line in data:
        parts = _split_fields(line, sep)
        if len(parts) >= 2 and _is_number(parts[0]) and _is_number(parts[1]):
            xs.append(float(parts[0]))
            ys.append(float(parts[1]))
    return {
        "separator": sep, "skip": skip, "ncols": ncols, "columns": " ".join(columns),
        "preview": lines[:4], "first_xy": [xs[0], ys[0]],
        "crs_guess": _classify_crs(min(xs), min(ys), max(xs), max(ys)),
    }


def _list_inputs(input_dir: str, fmt: str) -> list:
    exts = ASCII_EXTENSIONS if fmt == "ascii" else LAS_EXTENSIONS
    return sorted(str(p) for p in Path(input_dir).iterdir()
                  if p.is_file() and p.suffix.lower() in exts)


def _output_base(filename: str) -> str:
    """Basis des Ausgabenamens <Basis>_<NAME>_LV95_LN02.laz aus einem Input-Dateinamen."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = _LV95_MARKER.search(stem)
    base = stem[:m.start()] if m else stem
    k = _OLD_TILEKEY.search(base)
    if k and (480 <= int(k.group(1)) <= 840 or 1011 <= int(k.group(1)) <= 1374
              or 2480 <= int(k.group(1)) <= 2840):
        base = base[:k.start()]
    return base.rstrip("_- ") or stem


def _group_by_base(files: list, base_override: str = "") -> dict:
    """{basis: [dateien]} - Dateien mit gleicher Basis ergeben EINEN Kachelsatz.
    Ein gesetzter base_override gilt fuer alle Dateien."""
    groups = {}
    for f in files:
        groups.setdefault(base_override or _output_base(f), []).append(f)
    return groups


def _overlapping_pairs(boxes: dict, min_share: float = 0.01) -> list:
    """Paare, deren BBoxen sich flaechig ueberlappen (> min_share der kleineren Flaeche).
    Aneinanderstossende Kacheln (gemeinsame Kante) zaehlen nicht."""
    items = sorted(boxes.items())
    pairs = []
    for i, (a, ba) in enumerate(items):
        for b, bb in items[i + 1:]:
            w = min(ba[2], bb[2]) - max(ba[0], bb[0])
            h = min(ba[3], bb[3]) - max(ba[1], bb[1])
            smaller = min((ba[2] - ba[0]) * (ba[3] - ba[1]), (bb[2] - bb[0]) * (bb[3] - bb[1]))
            if w > 0 and h > 0 and w * h > min_share * max(smaller, 1e-9):
                pairs.append((a, b))
    return pairs


def _lhn95_tagged(path: str) -> bool:
    """True, wenn die CRS-VLRs einer LAS/LAZ-Datei LHN95 (EPSG:5729) deklarieren -
    WKT (2111/2112) oder VerticalCSTypeGeoKey (4096) im GeoTIFF-KeyDirectory (34735)."""
    h = _read_las_header(path)
    with open(path, "rb") as f:
        f.seek(h["header_size"])
        for _ in range(h["n_vlr"]):
            rec = f.read(54)
            if len(rec) < 54:
                break
            _, _, record_id, record_len, _ = struct.unpack("<H16sHH32s", rec)
            payload = f.read(record_len)
            if record_id in (2111, 2112) and (b"LHN95" in payload or b'"5729"' in payload):
                return True
            if record_id == 34735 and len(payload) >= 8:
                n_keys, = struct.unpack_from("<H", payload, 6)
                for k in range(n_keys):
                    if 16 + 8 * k > len(payload):
                        break
                    key_id, location, _, value = struct.unpack_from("<4H", payload, 8 + 8 * k)
                    if key_id == 4096 and location == 0 and value == 5729:
                        return True
    return False


def _split_ascii(src: str, chunk_dir: Path, n_parts: int) -> list:
    """Zerlegt eine grosse ASCII-Datei an Zeilengrenzen in bis zu n_parts Teildateien
    (binaere Kopie, kein Parsen), damit readers.text die Teile parallel liest.
    Kopfzeilen bleiben im ersten Teil."""
    size = os.path.getsize(src)
    cuts = [0]
    chunk_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    with open(src, "rb") as f:
        for k in range(1, n_parts):
            f.seek(size * k // n_parts)
            f.readline()                      # bis zum naechsten Zeilenende
            if cuts[-1] < f.tell() < size:
                cuts.append(f.tell())
        cuts.append(size)
        for i in range(len(cuts) - 1):
            part = chunk_dir / f"c{i:02d}.txt"
            f.seek(cuts[i])
            remaining = cuts[i + 1] - cuts[i]
            with open(part, "wb") as out:
                while remaining > 0:
                    buf = f.read(min(remaining, 16 * 1024 * 1024))
                    if not buf:
                        break
                    out.write(buf)
                    remaining -= len(buf)
            parts.append(str(part))
    return parts


def _cells_of_bbox(minx, miny, maxx, maxy, size: float) -> set:
    """Grid-Zellen (ix, iy), die eine BBox beruehrt - halb-offene Intervalle [a, a+size)
    wie bei 'pdal tile'."""
    import math
    return {(ix, iy)
            for ix in range(math.floor(minx / size), math.floor(maxx / size) + 1)
            for iy in range(math.floor(miny / size), math.floor(maxy / size) + 1)}


def _info(cfg: dict) -> None:
    """Analysiert den Input-Ordner und gibt das Ergebnis als JSON-Zeile auf stdout aus."""
    input_dir = cfg["input_dir"]
    grid_size = float(cfg.get("grid_size") or 1000.0)
    if not os.path.isdir(input_dir):
        raise FileNotFoundError(f"Input-Ordner nicht gefunden: {input_dir}")

    ascii_files = _list_inputs(input_dir, "ascii")
    las_files   = _list_inputs(input_dir, "las")
    fmt = cfg.get("format") or ""
    if fmt not in ("ascii", "las"):
        fmt = "las" if len(las_files) > len(ascii_files) else "ascii"
    files = ascii_files if fmt == "ascii" else las_files

    result = {"format": fmt, "n_ascii": len(ascii_files), "n_las": len(las_files),
              "n_files": len(files), "sample": os.path.basename(files[0]) if files else "",
              "size_mb": round(sum(os.path.getsize(p) for p in files) / 1024 ** 2, 1)}
    if not files:
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return

    if fmt == "ascii":
        result.update(_sniff_ascii(files[0]))
    else:
        headers = [_read_las_header(p) for p in files]
        minx = min(h["minx"] for h in headers)
        miny = min(h["miny"] for h in headers)
        maxx = max(h["maxx"] for h in headers)
        maxy = max(h["maxy"] for h in headers)
        cells = set()
        for h in headers:
            cells |= _cells_of_bbox(h["minx"], h["miny"], h["maxx"], h["maxy"], grid_size)
        h0 = headers[0]
        pdal_exe = ""
        try:
            pdal_exe = _resolve_pdal_exe(cfg)
        except FileNotFoundError:
            pass
        result.update({
            "version": f"LAS 1.{h0['minor_version']}  /  PF{h0['point_format']}"
                       + ("  (LAZ)" if h0["compressed"] else "  (LAS)"),
            "count_total": sum(h["count"] for h in headers),
            "extent": [minx, miny, maxx, maxy],
            "zrange": [min(h["minz"] for h in headers), max(h["maxz"] for h in headers)],
            "has_rgb": any(h["point_format"] in PC_FORMATS_WITH_RGB for h in headers),
            "crs_guess": _classify_crs(minx, miny, maxx, maxy),
            "crs_tag": _pdal_srs_name(pdal_exe, files[0]) if pdal_exe else "(pdal.exe nicht gefunden)",
            "n_cells_max": len(cells),
        })
    print(json.dumps(result, ensure_ascii=False), flush=True)


# ─── Grid-Shape ────────────────────────────────────────────────────────────────
def _load_grid_cells(grid_shape_path: str, bbox: tuple) -> tuple:
    """Liest die Grid-Zellen, die die Daten-BBox beruehren.

    Erwartet ein regelmaessiges, achsparalleles Quadratraster in EPSG:2056, dessen
    Zellen auf Vielfachen der Zellgroesse liegen (wie swissGRID 1km2). Nur dann
    entspricht die Kachelung von 'pdal tile' (Ursprung 0/0, halb-offene Intervalle)
    exakt den Grid-Zellen - das wird hier fuer jede beteiligte Zelle geprueft.
    Rueckgabe: (zellgroesse_m, {(ix, iy): NAME})."""
    from collections import Counter
    from osgeo import ogr, osr
    ogr.UseExceptions()
    osr.UseExceptions()

    ds = ogr.Open(grid_shape_path, 0)
    if ds is None:
        raise FileNotFoundError(f"OGR konnte das Grid-Shape nicht oeffnen: {grid_shape_path}")
    layer = ds.GetLayer()
    defn = layer.GetLayerDefn()
    if defn.GetFieldIndex("NAME") < 0:
        fields = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
        raise ValueError(f"Grid-Shape enthaelt kein Feld 'NAME' - vorhandene Felder: {fields}")

    srs = layer.GetSpatialRef()
    target = osr.SpatialReference()
    target.ImportFromEPSG(2056)
    if srs is None:
        _log("  WARNUNG       : Grid-Shape hat kein CRS (.prj fehlt) - wird als EPSG:2056 angenommen")
    elif srs.GetAuthorityCode(None) != "2056" and not srs.IsSame(target):
        raise ValueError(f"Grid-Shape ist nicht in EPSG:2056 (LV95), sondern '{srs.GetName()}'. "
                         "Das Tiling rechnet mit LV95-Kilometerzellen - bitte ein LV95-Grid verwenden.")
    else:
        _log("  Grid-Shape CRS: EPSG:2056 (passend)")

    layer.SetSpatialFilterRect(*bbox)
    size, cells, irregular = None, {}, []
    for feat in layer:
        name = feat.GetField("NAME")
        geom = feat.GetGeometryRef()
        if name is None or not str(name).strip() or geom is None:
            continue
        x0, x1, y0, y1 = geom.GetEnvelope()
        if size is None:
            size = round(x1 - x0, 6)
        if (abs((x1 - x0) - size) > 1e-6 or abs((y1 - y0) - size) > 1e-6
                or abs(geom.GetArea() - size * size) > 1e-3
                or abs(x0 / size - round(x0 / size)) > 1e-9
                or abs(y0 / size - round(y0 / size)) > 1e-9):
            irregular.append(str(name))
            continue
        cells[(int(round(x0 / size)), int(round(y0 / size)))] = str(name).strip()
    ds = None

    if size is None or not cells:
        raise ValueError("Grid-Shape hat keine Zelle im Bereich der Daten - Extent/CRS pruefen.")
    if irregular:
        raise ValueError(f"{len(irregular)} Grid-Zelle(n) sind keine achsparallelen {size:g} m-Quadrate "
                         f"auf dem {size:g} m-Raster (z.B. {irregular[:3]}) - dieses Tool erwartet "
                         "ein regelmaessiges Grid wie swissGRID 1km2.")
    dupes = [n for n, c in Counter(cells.values()).items() if c > 1]
    if dupes:
        raise ValueError(f"Doppelte NAME-Werte im Grid-Shape (Ausgaben wuerden sich ueberschreiben): {dupes[:5]}")
    return size, cells


# ─── Worker (laufen parallel in Threads, die Arbeit macht pdal.exe) ────────────
def _las_writer(filename: str, point_format: int, offset=(0.0, 0.0, 0.0), srs="EPSG:2056",
                global_encoding=None) -> dict:
    writer = {"type": "writers.las", "filename": filename,
              # Kompression explizit, nicht ueber die Dateiendung erraten lassen
              "compression": "laszip",
              "minor_version": OUT_MINOR_VERSION, "dataformat_id": point_format,
              "scale_x": OUT_SCALE, "scale_y": OUT_SCALE, "scale_z": OUT_SCALE,
              "offset_x": offset[0], "offset_y": offset[1], "offset_z": offset[2],
              "a_srs": srs}
    if global_encoding is not None:
        writer["global_encoding"] = global_encoding
    return writer


def _ascii_to_laz_worker(args) -> tuple:
    """ASCII -> Zwischen-LAZ (scale 0.01, offset 0 -> dasselbe Zentimeter-Gitter wie die
    Endkacheln mit Offset = Kachelursprung, die Requantisierung dort ist eine Identitaet).
    Rueckgabe: (quelle, ziel, punktzahl, anzahl_ignorierter_zeilen)."""
    src, dst, pipeline_path, pdal_exe, columns, sep_key, skip, point_format = args
    sep = SEPARATORS[sep_key]
    reader = {"type": "readers.text", "filename": src, "separator": sep,
              "header": sep.join(columns), "skip": int(skip)}
    try:
        out = _run_pipeline(pdal_exe, [reader, _las_writer(dst, point_format)], pipeline_path)
    finally:
        _discard(pipeline_path)
    # readers.text ueberspringt unpassende Zeilen nur mit Warnung (Exit-Code 0) -
    # passt die Spaltenzahl gar nicht, entsteht still eine leere Datei.
    ignored = out.count("Ignoring")
    count = _read_las_header(dst)["count"] if os.path.isfile(dst) else 0
    if count == 0:
        first = out.splitlines()[0] if out else ""
        raise ValueError(f"0 Punkte gelesen - Spalten '{' '.join(columns)}' / Trennzeichen passen "
                         f"nicht zur Datei. PDAL: {first[:300]}")
    return src, dst, count, ignored


def _split_worker(args) -> tuple:
    """Zerlegt EINE Quelldatei mit 'pdal tile' (Streaming, ein Lesedurchlauf) in
    Kachelstuecke 't<ix>_<iy>.laz' - ix/iy = floor(koordinate / zellgroesse),
    also halb-offene Intervalle: ein Punkt auf der Kilometerlinie gehoert genau
    einer Kachel. Rueckgabe: (quelle, [((ix, iy), pfad, punktzahl), ...])."""
    src, piece_dir, pdal_exe, size, point_format = args
    Path(piece_dir).mkdir(parents=True, exist_ok=True)
    _run_pdal(pdal_exe, [
        "tile", src, str(Path(piece_dir) / "t#.laz"),
        "--length", f"{size:g}", "--origin_x", "0", "--origin_y", "0",
        "--writers.las.compression=laszip",
        f"--writers.las.minor_version={OUT_MINOR_VERSION}",
        f"--writers.las.dataformat_id={point_format}",
        f"--writers.las.scale_x={OUT_SCALE}", f"--writers.las.scale_y={OUT_SCALE}",
        f"--writers.las.scale_z={OUT_SCALE}",
        "--writers.las.offset_x=0", "--writers.las.offset_y=0", "--writers.las.offset_z=0",
    ])
    pieces = []
    for p in sorted(Path(piece_dir).iterdir()):
        m = PIECE_NAME_PATTERN.match(p.name)
        if m:
            pieces.append(((int(m.group(1)), int(m.group(2))), str(p),
                           _read_las_header(str(p))["count"]))
    return src, pieces


def _validate_tile(path: str, h: dict, origin: tuple, size: float, point_format: int,
                   expected_count: int) -> list:
    """Kontrolle statt Annahme: Header-Zielwerte, Punktbilanz, Kachelrahmen, CRS-VLRs."""
    problems = []
    if h["minor_version"] != OUT_MINOR_VERSION or h["point_format"] != point_format:
        problems.append(f"LAS 1.{h['minor_version']}/PF{h['point_format']}, erwartet "
                        f"LAS 1.{OUT_MINOR_VERSION}/PF{point_format}")
    if h["header_size"] != 375:
        problems.append(f"header_size={h['header_size']}, erwartet 375")
    if h["global_encoding"] != OUT_GLOBAL_ENCODING:
        problems.append(f"global_encoding={h['global_encoding']}, erwartet {OUT_GLOBAL_ENCODING}")
    if any(abs(s - OUT_SCALE) > 1e-12 for s in h["scale"]):
        problems.append(f"scale={h['scale']}, erwartet {OUT_SCALE}")
    if abs(h["offset"][0] - origin[0]) > 1e-6 or abs(h["offset"][1] - origin[1]) > 1e-6:
        problems.append(f"offset={h['offset'][:2]}, erwartet Kachelursprung {origin}")
    if h["count"] != expected_count:
        problems.append(f"Punktzahl {h['count']} statt {expected_count} (Summe der Kachelstuecke)")
    eps = FRAME_TOLERANCE_M
    if (h["minx"] < origin[0] - eps or h["maxx"] > origin[0] + size + eps or
            h["miny"] < origin[1] - eps or h["maxy"] > origin[1] + size + eps):
        problems.append(f"Punkte ausserhalb des Kachelrahmens (BBox X {h['minx']:.2f}-{h['maxx']:.2f}, "
                        f"Y {h['miny']:.2f}-{h['maxy']:.2f})")
    crs_vlrs = sorted(v for v in _read_vlr_ids(path, h) if _is_crs_vlr(*v))
    if crs_vlrs != [("LASF_Projection", 2112), ("LASF_Projection", 34735)]:
        problems.append(f"CRS-VLRs {crs_vlrs}, erwartet genau die LV95/LN02-Referenz (2112 + 34735)")
    return problems


def _tile_worker(args) -> tuple:
    """Fuegt die Kachelstuecke EINER Grid-Zelle eines Kachelsatzes zusammen und schreibt
    die Endkachel im Zielformat. Atomar: Temp-Datei im Output-Ordner, erst nach
    bestandener Validierung per os.replace an ihren Platz.
    Rueckgabe: (status, dateiname, punktzahl, fehler, ueberschrieben)."""
    key, out_path, pieces, expected_count, pipeline_path, pdal_exe, size, point_format = args
    origin = (key[0] * size, key[1] * size)
    out_name = os.path.basename(out_path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".laz", dir=os.path.dirname(out_path))
    os.close(fd)
    os.remove(tmp_path)  # writers.las legt die Datei selbst an

    stages = [{"type": "readers.las", "filename": p, "tag": f"r{i}"} for i, p in enumerate(pieces)]
    if len(stages) > 1:
        stages.append({"type": "filters.merge", "inputs": [s["tag"] for s in stages]})
    # PDAL schreibt vorerst nur LV95 - die autoritativen LV95/LN02-VLRs kommen danach per Byte-Patch
    stages.append(_las_writer(tmp_path, point_format, (origin[0], origin[1], 0.0), "EPSG:2056",
                              OUT_GLOBAL_ENCODING))
    try:
        _run_pipeline(pdal_exe, stages, Path(pipeline_path))
        if not os.path.isfile(tmp_path) or _read_las_header(tmp_path)["count"] == 0:
            return ("empty", out_name, 0, None, False)
        _inject_reference_vlrs(tmp_path)
        h = _read_las_header(tmp_path)
        problems = _validate_tile(tmp_path, h, origin, size, point_format, expected_count)
        if problems:
            return ("error", out_name, 0, "; ".join(problems), False)
        existed = os.path.isfile(out_path)
        os.replace(tmp_path, out_path)
        tmp_path = None
        return ("written", out_name, h["count"], None, existed)
    except Exception as e:
        return ("error", out_name, 0, str(e), False)
    finally:
        _discard(tmp_path)
        _discard(pipeline_path)


# ─── Hauptablauf (Aktion 'process') ────────────────────────────────────────────
#   1) nur ASCII: jede Datei -> Zwischen-LAZ (readers.text, parallel; grosse gemergte
#      Dateien vorher an Zeilengrenzen in Teile zerlegt)
#   2) Header: Punktzahl, LV95-/Z-Plausibilitaet, Ueberlappung innerhalb eines
#      Kachelsatzes, Grid-Zellen im Datenbereich laden
#   3) jede Quelle mit 'pdal tile' in Kachelstuecke zerlegen (parallel, Streaming)
#   4) je Kachelsatz und Grid-Zelle alle Stuecke mergen -> <Basis>_<NAME>_LV95_LN02.laz
#      im Zielformat, validiert und atomar geschrieben (parallel)
#   5) Punktbilanz Input = Output (+ ausserhalb Grid), Staging aufraeumen
# Jede Quelldatei wird genau einmal gelesen - auch ein grosses gemergtes Gebiet wird
# nicht pro Kachel erneut eingelesen.
def _process(cfg: dict) -> None:
    t_start     = time.time()
    input_dir   = cfg["input_dir"]
    output_dir  = cfg["output_dir"]
    grid_path   = cfg["grid_shape_path"]
    fmt         = cfg.get("format", "ascii")
    columns     = (cfg.get("columns") or "X Y Z").split()
    sep_key     = cfg.get("separator", "space")
    skip        = int(cfg.get("skip") or 0)
    base_name   = (cfg.get("base_name") or "").strip()
    workers     = max(1, int(cfg.get("num_workers") or 4))
    keep        = bool(cfg.get("keep_staging"))
    chunk_bytes = int(float(cfg.get("ascii_chunk_mb") or ASCII_CHUNK_MB) * 1024 ** 2)
    staging_dir = cfg.get("staging_dir") or os.path.join(output_dir, "_staging")
    pdal_exe    = _resolve_pdal_exe(cfg)

    # --- Eingaben pruefen, bevor irgendetwas geschrieben wird ---
    if fmt not in ("ascii", "las"):
        raise ValueError(f"Unbekanntes Input-Format '{fmt}' (ascii | las)")
    if fmt == "ascii":
        if sep_key not in SEPARATORS:
            raise ValueError(f"Trennzeichen '{sep_key}' ungueltig ({' | '.join(SEPARATORS)})")
        if not {"X", "Y", "Z"} <= set(columns):
            raise ValueError(f"Spalten '{' '.join(columns)}' muessen X, Y und Z enthalten")
    if not os.path.isdir(input_dir):
        raise FileNotFoundError(f"Input-Ordner nicht gefunden: {input_dir}")
    if os.path.normcase(os.path.abspath(input_dir)) == os.path.normcase(os.path.abspath(output_dir)):
        raise ValueError("Output-Ordner muss sich vom Input-Ordner unterscheiden "
                         "(gleiche Namenskonvention -> Quelldateien koennten ueberschrieben werden).")
    if re.search(r'[<>:"/\\|?*]', base_name):
        raise ValueError(f"Basisname enthaelt unzulaessige Zeichen: '{base_name}'")
    files = _list_inputs(input_dir, fmt)
    if not files:
        raise FileNotFoundError(f"Keine {fmt.upper()}-Dateien gefunden in: {input_dir}")
    lhn95 = [os.path.basename(f) for f in files
             if "LHN95" in os.path.basename(f).upper() or (fmt == "las" and _lhn95_tagged(f))]
    if lhn95:
        raise ValueError(f"Quelle ist als LHN95 bezeichnet/getaggt: {lhn95[:10]}\nDieses Tool erwartet "
                         "LN02-Hoehen und rechnet nicht um - LHN95 -> LN02 mit GeoSuite/REFRAME (HTRANS).")
    if fmt == "ascii":
        # LV03 frueh erkennen (erste Zeilen je Datei), bevor alles eingelesen wird.
        # Massgebend bleibt die Header-Pruefung in Schritt 2.
        lv03 = [os.path.basename(f) for f in files if _sniff_ascii(f)["crs_guess"] == "LV03"]
        if lv03:
            raise ValueError(f"Quelldaten in LV03 statt LV95: {lv03[:10]}\n{NOT_LV95_HINT}")
    groups   = _group_by_base(files, base_name)
    group_of = {f: b for b, members in groups.items() for f in members}

    point_format = OUT_POINT_FORMAT
    if fmt == "ascii" and {"Red", "Green", "Blue"} & set(columns):
        point_format = OUT_POINT_FORMAT_RGB

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    run_dir = Path(staging_dir) / f"d2l_{time.strftime('%y%m%d_%H%M%S')}"
    _check_path_lengths(files, run_dir, output_dir, max(len(b) for b in groups))
    run_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Input-Ordner   : {input_dir}")
    _log(f"Input-Format   : {fmt.upper()}  ({len(files)} Datei(en))")
    if fmt == "ascii":
        _log(f"  Spalten      : {' '.join(columns)}  |  Trennzeichen: {sep_key}  |  Kopfzeilen: {skip}")
    _log(f"Output-Ordner  : {output_dir}")
    _log(f"Grid-Shape     : {grid_path}")
    _log("Referenzsystem : EPSG:2056 + 5728 (LV95 / LN02) - nur CRS-Tag, keine Umrechnung")
    _log(f"pdal.exe       : {pdal_exe}")
    _log(f"Parallel       : {workers} Prozess(e)  |  Staging: {run_dir}")
    _log(f"Kachelsaetze   : {len(groups)}")
    for b, members in groups.items():
        _log(f"  {b}_<NAME>{OUT_NAME_SUFFIX}.laz  <-  " + (os.path.basename(members[0]) if len(members) == 1
                                                            else f"{len(members)} Dateien"))

    names = {f: os.path.basename(f) for f in files}
    origin_of = {f: f for f in files}   # Quelle (Zwischen-LAZ bzw. Input-LAZ) -> Input-Datei
    try:
        # 1) ASCII -> Zwischen-LAZ (grosse Dateien in Teilen parallel)
        if fmt == "ascii":
            _log("\n[1/4] ASCII -> LAZ (readers.text) ...")
            tmp_dir = run_dir / "a"
            tmp_dir.mkdir()
            jobs = []   # (teil_oder_datei, input_datei, kopfzeilen)
            for i, f in enumerate(files):
                size_b = os.path.getsize(f)
                n_parts = min(workers, -(-size_b // chunk_bytes)) if workers > 1 else 1
                if n_parts > 1:
                    parts = _split_ascii(f, run_dir / "c" / f"{i:05d}", n_parts)
                    _log(f"  {names[f]}: {size_b / 1024 ** 2:,.0f} MB -> {len(parts)} Teile (paralleles Einlesen)")
                    jobs += [(p, f, skip if k == 0 else 0) for k, p in enumerate(parts)]
                else:
                    jobs.append((f, f, skip))
            sources, failed = [], []
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_ascii_to_laz_worker,
                                  (src, str(tmp_dir / f"s{j:05d}.laz"), tmp_dir / f"p{j:05d}.json",
                                   pdal_exe, columns, sep_key, sk, point_format)): (src, orig)
                        for j, (src, orig, sk) in enumerate(jobs)}
                for done, fut in enumerate(as_completed(futs), 1):
                    src, orig = futs[fut]
                    label = names[orig] + (f" [{os.path.basename(src)}]" if src != orig else "")
                    try:
                        _, dst, count, ignored = fut.result()
                        sources.append(dst)
                        origin_of[dst] = orig
                        _log(f"  [{done}/{len(jobs)}] {label}: {_fmt_count(count)} Punkte"
                             + (f"  - WARNUNG: {ignored} Zeile(n) ignoriert (Spaltenzahl)" if ignored else ""))
                    except Exception as e:
                        failed.append(label)
                        _log(f"  [{done}/{len(jobs)}] {label}: FEHLER - {e}")
                    finally:
                        if src != orig:
                            _discard(src)   # Teildatei sofort freigeben (Platz im Staging)
                    _progress(0.3 * done / len(jobs))
            if failed:
                raise RuntimeError(f"{len(failed)} ASCII-Datei(en)/Teil(e) nicht lesbar - Abbruch vor dem Tiling.")
            sources.sort()
            p0, pspan = 0.3, 0.7
        else:
            sources, p0, pspan = list(files), 0.0, 1.0

        # 2) Header, Koordinaten-/Hoehenplausibilitaet, Ueberlappungen
        _log("\n[2/4] Quell-Header pruefen ...")
        headers = {s: _read_las_header(s) for s in sources}
        not_lv95 = set()
        for s, h in headers.items():
            crs = _classify_crs(h["minx"], h["miny"], h["maxx"], h["maxy"])
            if crs != "LV95":
                not_lv95.add(f"{names[origin_of[s]]}: {crs}  (X {h['minx']:.0f}-{h['maxx']:.0f}, "
                             f"Y {h['miny']:.0f}-{h['maxy']:.0f})")
        if not_lv95:
            raise ValueError("Quelldaten nicht in LV95 (EPSG:2056):\n  " + "\n  ".join(sorted(not_lv95)[:10])
                             + "\n" + NOT_LV95_HINT)
        if fmt == "las":
            _log(f"  CRS-Tag (1. Datei): {_pdal_srs_name(pdal_exe, sources[0])}")
            if any(h["point_format"] in PC_FORMATS_WITH_RGB for h in headers.values()):
                point_format = OUT_POINT_FORMAT_RGB
                _log("  Quelle fuehrt Farbe (RGB) -> Ausgabe als PF7 statt PF6")

        # Ueberlappen sich zwei Input-Dateien desselben Kachelsatzes, entstuenden beim
        # Zusammenfuehren doppelte Punkte (z.B. Kachel-Buffer oder zwei Varianten eines Gebiets)
        file_box = {}
        for s, h in headers.items():
            o, box = origin_of[s], (h["minx"], h["miny"], h["maxx"], h["maxy"])
            old = file_box.get(o, box)
            file_box[o] = (min(old[0], box[0]), min(old[1], box[1]), max(old[2], box[2]), max(old[3], box[3]))
        clashes = []
        for members in groups.values():
            clashes += _overlapping_pairs({m: file_box[m] for m in members})
        if clashes:
            raise ValueError("Input-Dateien desselben Kachelsatzes ueberlappen raeumlich - beim Zusammenfuehren "
                             "entstuenden doppelte Punkte:\n  "
                             + "\n  ".join(f"{names[a]}  <->  {names[b]}" for a, b in clashes[:10])
                             + "\nUeberlappende Kachelraender (Buffer) oder zwei Varianten desselben Gebiets? "
                               "Dateien getrennt verarbeiten bzw. Basisname-Feld leer lassen.")

        total_in = sum(h["count"] for h in headers.values())
        bbox = (min(h["minx"] for h in headers.values()), min(h["miny"] for h in headers.values()),
                max(h["maxx"] for h in headers.values()), max(h["maxy"] for h in headers.values()))
        zmin = min(h["minz"] for h in headers.values())
        zmax = max(h["maxz"] for h in headers.values())
        _log(f"  Koordinaten   : LV95 plausibel  |  Extent X {bbox[0]:.1f}-{bbox[2]:.1f}, "
             f"Y {bbox[1]:.1f}-{bbox[3]:.1f}")
        _log(f"  Z-Bereich     : {zmin:.2f} - {zmax:.2f} m")
        if zmin < PLAUSIBLE_Z[0] or zmax > PLAUSIBLE_Z[1]:
            _log(f"  WARNUNG       : Z ausserhalb des fuer die Schweiz plausiblen Bereichs "
                 f"({PLAUSIBLE_Z[0]:g}-{PLAUSIBLE_Z[1]:g} m) - NoData-Werte (z.B. -9999) oder Ausreisser "
                 "in der Quelle? Die Punkte werden unveraendert uebernommen.")
        _log(f"  Punkte Input  : {_fmt_count(total_in)}")
        size, cells = _load_grid_cells(grid_path, bbox)
        _log(f"  Grid          : {len(cells)} Zelle(n) im Datenbereich, Zellgroesse {size:g} m")
        _log(f"  Zielformat    : LAS 1.{OUT_MINOR_VERSION} / PF{point_format} / LAZ, scale {OUT_SCALE}, "
             f"Offset = Kachelursprung, global_encoding {OUT_GLOBAL_ENCODING}")

        # 3) Zerlegen in Kachelstuecke
        _log("\n[3/4] Quellen in Kachelstuecke zerlegen (pdal tile) ...")
        by_cell, failed = {}, []   # (basis, ix, iy) -> [(pfad, punktzahl)]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_split_worker, (s, str(run_dir / "p" / f"{i:05d}"), pdal_exe,
                                              size, point_format)): s
                    for i, s in enumerate(sources)}
            for done, fut in enumerate(as_completed(futs), 1):
                s = futs[fut]
                label = names[origin_of[s]]
                try:
                    _, pieces = fut.result()
                    for (ix, iy), path, count in pieces:
                        by_cell.setdefault((group_of[origin_of[s]], ix, iy), []).append((path, count))
                    _log(f"  [{done}/{len(sources)}] {label}: {len(pieces)} Kachelstueck(e)")
                except Exception as e:
                    failed.append(label)
                    _log(f"  [{done}/{len(sources)}] {label}: FEHLER - {e}")
                _progress(p0 + pspan * 0.4 * done / len(sources))
        if failed:
            raise RuntimeError(f"{len(failed)} Quelle(n) konnten nicht zerlegt werden - Abbruch.")

        outside = sorted(k for k in by_cell if k[1:] not in cells)
        outside_points = sum(c for k in outside for _, c in by_cell[k])
        if outside:
            _log(f"  WARNUNG: {_fmt_count(outside_points)} Punkt(e) in {len(outside)} Zelle(n) ausserhalb "
                 f"des Grid-Shapes - werden nicht geschrieben (z.B. {[k[1:] for k in outside[:3]]})")

        # 4) Endkacheln: je Kachelsatz (Basis) und Grid-Zelle
        jobs = []
        for j, key in enumerate(sorted(k for k in by_cell if k[1:] in cells)):
            b, ix, iy = key
            expected = sum(c for _, c in by_cell[key])
            if expected == 0:
                continue
            out_path = os.path.join(output_dir, f"{b}_{cells[(ix, iy)]}{OUT_NAME_SUFFIX}.laz")
            jobs.append(((ix, iy), out_path, [p for p, _ in by_cell[key]], expected,
                         run_dir / f"t{j:05d}.json", pdal_exe, size, point_format))
        _log(f"\n[4/4] {len(jobs)} Kachel(n) schreiben ...")
        written = empty = overwritten = total_out = 0
        errors = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_tile_worker, job) for job in jobs]
            for done, fut in enumerate(as_completed(futs), 1):
                status, out_name, count, err, existed = fut.result()
                prefix_log = f"  [{done}/{len(jobs)}] {out_name}"
                if status == "written":
                    written += 1
                    total_out += count
                    overwritten += int(existed)
                    _log(f"{prefix_log}  ({_fmt_count(count)} Punkte)" + ("  - ueberschrieben" if existed else ""))
                elif status == "empty":
                    empty += 1
                    _log(f"{prefix_log}  - leer, nicht geschrieben")
                else:
                    errors.append(f"{out_name}: {err}")
                    _log(f"{prefix_log}  - FEHLER: {err}")
                _progress(p0 + pspan * (0.4 + 0.6 * done / len(jobs)))

        # 5) Zusammenfassung + Punktbilanz
        balance = total_in - total_out - outside_points
        dt = time.time() - t_start
        _log("\nZusammenfassung:")
        _log(f"  Kacheln geschrieben : {written}" + (f"  (davon {overwritten} ueberschrieben)" if overwritten else ""))
        if empty:
            _log(f"  leere Kacheln       : {empty}")
        _log(f"  Punkte Input        : {_fmt_count(total_in)}")
        _log(f"  Punkte Output       : {_fmt_count(total_out)}")
        if outside_points:
            _log(f"  ausserhalb Grid     : {_fmt_count(outside_points)}")
        _log(f"  Punktbilanz         : " + ("OK (kein Punkt verloren/doppelt)" if balance == 0
                                            else f"DIFFERENZ {_fmt_count(balance)}"))
        _log(f"  Laufzeit            : {int(dt // 60)}m {int(dt % 60):02d}s")
        if errors:
            raise RuntimeError(f"{len(errors)} Kachel(n) fehlgeschlagen:\n  " + "\n  ".join(errors[:20]))
        if written == 0:
            raise RuntimeError("Keine Kachel geschrieben - Grid-Shape und Input pruefen.")
        if balance != 0:
            raise RuntimeError("Punktbilanz stimmt nicht - Ergebnis pruefen (siehe Log).")
        _log("Fertig.")
    finally:
        if keep:
            _log(f"\nStaging-Dateien behalten: {run_dir}")
        else:
            shutil.rmtree(run_dir, ignore_errors=True)
            try:
                os.rmdir(staging_dir)  # nur falls leer (Standard-Unterordner '_staging')
            except OSError:
                pass


def main() -> None:
    if len(sys.argv) < 2:
        print("[FEHLER] Kein Konfigurationspfad uebergeben.", flush=True)
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        cfg = json.load(f)

    action = cfg.get("action", "")
    try:
        if action == "info":
            _info(cfg)
        elif action == "process":
            _process(cfg)
        else:
            print(f"[FEHLER] Unbekannte Aktion: '{action}'", flush=True)
            sys.exit(1)
    except Exception as e:
        print(f"\n[FEHLER] {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
