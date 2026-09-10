# CLAUDE.md – topo-DSMascii_to_DSMlaz

Alte DSM-Punktwolken (ASCII-xyz, meist **gemergt pro Gebiet**, oder LAZ im alten Tiling) →
GDWH-taugliche LAZ-Kacheln gemäss swissGRID 1km². Details und Begründungen: `README.md`.

## Architektur
- `GUI_dsmAsciiToLaz.py` – Tkinter mit Standard-Python, **muss Python-3.6-kompatibel bleiben**
  (Firmen-Standard): kein Walrus, keine `list[int]`-Annotationen zur Laufzeit, `tk.Spinbox` statt
  `ttk.Spinbox`, `subprocess.run` ohne `capture_output`.
- `process_scripts/_osgeo_runner.py` – Aktionen `info` / `process`, läuft als Subprocess mit
  QGIS/OSGeo4W-Python (JSON-Config via tempfile, stdout = Log, `PROGRESS:x`-Zeilen). Beim Import nur
  Standardbibliothek (`osgeo` erst in den Funktionen): das GUI importiert den Runner für die
  Benennungsvorschau – `_output_base` / `_group_by_base` existieren nur dort.
- Punktwolken ausschliesslich via **`pdal.exe`** (Pipelines + `pdal tile`). Keine pip-Abhängigkeiten.
- `process_scripts/01_convertDATA_dsm_to_laz_tiles.py` – Standalone, ruft `_process()` des Runners
  auf (keine zweite Implementierung).
- Stil übernommen aus `../topo-COGTIFFconverter` (GUI) und `../topo-DMCdataConverter` (PDAL-Runner).

## Umgebung
- QGIS 3.44.12 unter `C:\QGIS 3.44.12`: `bin\python3.exe` (Python 3.12, GDAL 3.13) und
  `bin\pdal.exe` (PDAL 2.10). **Kein** `pdal`- oder `laspy`-Python-Binding vorhanden.
- Runner direkt starten: `PYTHONHOME=C:\QGIS 3.44.12\apps\Python312` und `PYTHONNOUSERSITE=1` setzen.
- System-Python 3.14 (mit pytest) für GUI und Tests.

## Fixe fachliche Entscheidungen (vom User bestätigt, 2026-09-10)
- Input ist immer **LV95 (EPSG:2056) / LN02 (EPSG:5728)** – nichts wird umgerechnet, nur getaggt.
  LV03 und LHN95 (Tag oder Dateiname) → Abbruch; Transformationen nur mit GeoSuite/REFRAME.
- Benennung `<Basis>_<NAME>_LV95_LN02.laz`: Basis = Dateiname bis vor `_LV95` (Rest wie
  `_CIR_low_raw` fällt weg), ein alter TileKey am Ende wird entfernt.
  `2015_RHONE_DSM_1m_LV95_LN02_CIR_low_raw.asc` → `2015_RHONE_DSM_1m_2600_1200_LV95_LN02.laz`.
  Die Endung ist das Muster des GDWH-Imports (`../topo-importDATAtoGDWHandSTAC`,
  `4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py`).
- Gleiche Basis = ein Kachelsatz (je Zelle zusammengeführt); flächige Überlappung zweier Dateien
  im selben Kachelsatz → Abbruch (doppelte Punkte).
- Zielformat wie SB_DSM_PUNKTWOLKE / swissSURFACE3D: LAS 1.4, PF6 (PF7 bei RGB), header 375,
  global_encoding 17, scale 0.01, Offset = Kachelursprung, byte-exakte LV95/LN02-VLRs (34735 + 2112).
  `_inject_reference_vlrs` und die Base64-Payloads stammen aus `../topo-DMCdataConverter` –
  Änderungen dort hier nachziehen.

## Bekannte Fallstricke (getestet, nicht annehmen)
- `pdal.exe` ist nicht long-path-fähig: Pfade > 259 Zeichen ergeben nur „file not found“.
  Staging-Namen deshalb kurz halten; `_check_path_lengths()` prüft vor dem Lauf.
- `readers.text`: passt die Spaltenzahl nicht, wird jede Zeile nur mit Warnung übersprungen –
  Exit-Code 0, leere Datei. Punktzahl immer prüfen. Der `header`-String muss das Trennzeichen der
  Datei verwenden (Tab: `X\tY\tZ`).
- `pdal tile` erkennt `.xyz`/`.asc` nicht (Reader per Dateiendung) → zuerst ASCII → LAZ.
- `pdal tile` und `filters.range [a:b)` sind halb-offen (Punkt auf der km-Linie → genau eine
  Kachel); `filters.crop` wäre inklusiv und erzeugt doppelte Randpunkte.
- Mehrere `readers.las` in einer Pipeline brauchen `filters.merge` mit allen Tags, sonst
  „multiple leaf nodes“ und nur der erste Zweig läuft.
- Tkinter nie aus Worker-Threads ansprechen (Python 3.14: RuntimeError) → `_call_in_ui()`
  (Queue, abgearbeitet in `_poll_log`).
- Runner-Ausgabe im GUI als UTF-8 dekodieren (`encoding="utf-8"`), sonst Mojibake.

## Tests
- `python -m pytest -q` – Unit-Tests ohne QGIS/PDAL (inkl. GUI-Instanz).
- Der End-to-End-Test (synthetische Daten, echtes PDAL, 29 Checks) lief nur in einer
  Scratch-Sitzung und ist **nicht** im Repo. Bei grösseren Änderungen neu aufsetzen: QGIS-Python,
  Punkte exakt auf km-Linien, Punktbilanz, `pdal info --stats` auf jede Kachel.
- **Keine Bildschirm-Screenshots** zur GUI-Prüfung – `CopyFromScreen` hat einmal ein fremdes
  Fenster (Outlook) erfasst.

## Offen
- Erster Lauf mit echten Daten (z.B. RHONE-ASCII) steht aus.
- Python-3.6-Kompatibilität des GUI nicht verifiziert (nur 3.12/3.14 installiert).
