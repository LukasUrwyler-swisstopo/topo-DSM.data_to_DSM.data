# CLAUDE.md – topo-DSMdata_to_DSMdata

Alte DSM-Punktwolken (ASCII-xyz, meist **gemergt pro Gebiet**, oder LAZ/LAS in beliebigem Tiling)
→ GDWH-taugliche LAZ-Kacheln gemäss swissGRID 1km², optional Thinning und DSM + Hillshade.
Details und Begründungen: `README.md`.

## Architektur
- `GUI_DSMdata_to_DSMdata.py` – Tkinter mit Standard-Python, **muss Python-3.6-kompatibel bleiben**
  (Firmen-Standard): kein Walrus, keine `list[int]`-Annotationen zur Laufzeit, `tk.Spinbox` statt
  `ttk.Spinbox`, `subprocess.run` ohne `capture_output`.
  Je Input-Format ein Tab (`TilesTab`, `fmt` = `ascii` / `las`); Log, Fortschritt, OSGeo4W-Python
  und Theme liegen gemeinsam in `DsmToLazApp`.
- `process_scripts/_osgeo_runner.py` – Aktionen `info` / `process`, läuft als Subprocess mit
  QGIS/OSGeo4W-Python (JSON-Config via tempfile, stdout = Log, `PROGRESS:x`-Zeilen). Beim Import nur
  Standardbibliothek (`osgeo` erst in den Funktionen): das GUI importiert den Runner für die
  Benennungsvorschau – `_las_tile_base` / `_raster_names` existieren nur dort.
- Punktwolken ausschliesslich via **`pdal.exe`** (Pipelines + `pdal tile`). Keine pip-Abhängigkeiten.
- `process_scripts/01_convertDATA_dsm_to_laz_tiles.py` – Standalone, ruft `_process()` des Runners
  auf (keine zweite Implementierung).
- Stil übernommen aus `../topo-COGTIFFconverter` (GUI) und `../topo-DMCdataConverter` (PDAL-Runner).

## Umgebung
- QGIS 3.44.12 unter `C:\QGIS 3.44.12`: `bin\python3.exe` (Python 3.12, GDAL 3.13) und
  `bin\pdal.exe` (PDAL 2.10). **Kein** `pdal`- oder `laspy`-Python-Binding vorhanden.
- Runner direkt starten: `PYTHONHOME=C:\QGIS 3.44.12\apps\Python312` und `PYTHONNOUSERSITE=1` setzen.
- System-Python 3.14 (mit pytest) für GUI und Tests.

## Fixe fachliche Entscheidungen (vom User bestätigt, 2026-09-10 / 2026-09-23)
Gilt für **beide Tabs** (ASCII und LAZ) – sie unterscheiden sich nur im Einlesen.
- Lage immer **LV95 (EPSG:2056)**, geprüft über den Koordinatenbereich (LV03 / Grad → Abbruch; ein
  Tag EPSG:4150 mit LV95-Werten ist ok). Höhe nach GUI-Auswahl **LN02 (5728) oder LHN95 (5729)**.
  Nichts wird umgerechnet, nur getaggt; Transformationen nur mit GeoSuite/REFRAME.
- CRS-Angabe der Quelle (LAZ: Tag, ASCII: Dateiname) wird ignoriert; Widerspruch zur Auswahl → nur
  Warnung (so lässt sich eine falsche Angabe korrigieren).
- Benennung `<Jahr>_<AREA>_TIN_DSM[_thinNN]_<NAME>_LV95_<LN02|LHN95>.<laz|las>`, NN = Thinning in dm.
  Die Endung ist das Muster des GDWH-Imports (`../topo-importDATAtoGDWHandSTAC`,
  `4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py`). Die frühere ASCII-Benennung aus dem Dateinamen
  (`<Basis>_<NAME>_LV95_LN02`, Basisname-Feld) ist abgeschafft.
- Alle Dateien eines Ordners = ein Kachelsatz; flächige Überlappung zweier Dateien → Abbruch.
- Zielformat wie SB_DSM_PUNKTWOLKE / swissSURFACE3D: LAS 1.4, header 375, global_encoding 17,
  scale 0.01, Offset = Kachelursprung, CRS-VLRs 34735 + 2112. PF nach Quelle: 0/1→6, 2/3→7, 6/7
  bleiben (NIR→8); ASCII PF6, PF7 bei RGB-Spalten. `laz` oder `las` wählbar.
  `_inject_reference_vlrs` und die LN02-Base64-Payloads stammen aus `../topo-DMCdataConverter` –
  Änderungen dort hier nachziehen.
- LHN95-VLRs aus der LN02-Referenz abgeleitet (nur VERT_CS + GeoKey 4096) – keine Referenzkachel
  vorhanden; liefert der User eine, byte-exakt übernehmen.
- Thinning = `filters.sample` (Mindestabstand) wie DMC, je Kachel nach dem Merge.
- Raster: aus den fertigen Kacheln, kein AOI-Shape, eigener Output-Ordner (Feld nur bei aktiver
  Option), DSM mit Compound-CRS 2056+5728/5729, Hillshade nur 2056, Hillshade-NoData = DSM-NoData.
  Code aus `../topo-DMCdataConverter` – Änderungen dort hier nachziehen.
- GUI-Reihenfolge in beiden Tabs: 1 Input (+ Datei-Info, ASCII-Format) → 2 Projekt & Referenzsystem
  → 3 Output (+ Raster) → 4 Staging.

## Bekannte Fallstricke (getestet, nicht annehmen)
- `pdal.exe` ist nicht long-path-fähig: Pfade > 259 Zeichen ergeben nur „file not found“.
  Staging-Namen deshalb kurz halten; `_check_path_lengths()` prüft vor dem Lauf.
- Defekte LAZ-Chunk-Tabelle (Offset falsch/abgeschnitten): `pdal tile` bricht erst beim Lesen ab
  („Invalid version N found in LAZ chunk table“) oder **hängt** (Version zufällig 0). Auch Offset -1
  (Tabelle am Dateiende) liest PDAL 2.10 nicht. `_las_integrity_error()` prüft das vorab (Info + Schritt 2).
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
- `writers.las compression` ist in PDAL 2.10 ein Schalter (Default `false`); `laszip` wirkt als true.
- `gdalsrsinfo -o epsg` meldet für jeden Compound-WKT (auch swissSURFACE3D) „EPSG:-1“ – zum Prüfen
  `osr.IsSame(..., EPSG:2056+57xx)` verwenden.
- GeoTIFF mit `EPSG:2056+5728/5729` übersteht `gdal.Warp` (srcSRS = dstSRS) samt Höhenteil.

## Tests
- `python -m pytest -q` – Unit-Tests ohne QGIS/PDAL (inkl. GUI-Instanz).
- Die End-to-End-Tests (synthetische Daten, echtes PDAL, zuletzt 47 Checks: beide Tabs,
  Raster, LHN95, Thinning, Überlappung, Grad, Höhen-Warnung, ASCII-Metadaten) liefen nur in Scratch-Sitzungen und sind **nicht** im
  Repo. Bei grösseren Änderungen neu aufsetzen: QGIS-Python, Punkte exakt auf km-Linien,
  Punktbilanz, `pdal info` auf jede Kachel, `gdalinfo` auf die Raster.
- **Keine Bildschirm-Screenshots** zur GUI-Prüfung – `CopyFromScreen` hat einmal ein fremdes
  Fenster (Outlook) erfasst.

## Offen
- Erster Lauf mit echten Daten (z.B. RHONE-ASCII) steht aus.
- Python-3.6-Kompatibilität des GUI nicht verifiziert (nur 3.12/3.14 installiert).
