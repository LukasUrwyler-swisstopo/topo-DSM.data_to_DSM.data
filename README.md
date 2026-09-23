# DSM ASCII/LAZ → LAZ-Tiles

Wandelt alte DSM-Punktwolken (Lage bereits **LV95**) in GDWH-taugliche LAZ-Kacheln gemäss einem
Grid-Shape (z.B. swissGRID 1km²) um – optional ausgedünnt und mit DSM-Raster + Hillshade. Struktur
und Styling analog zu `topo-COGTIFFconverter` und `topo-DMCdataConverter`.

| Tab | Input | Output |
|---|---|---|
| **DSM.ascii → DSM.laz-Tiles** | ASCII (`.xyz` / `.txt` / `.asc` / `.csv`), typischerweise **gemergt pro Gebiet** | eine `.laz`/`.las` je Grid-Zelle, optional DSM + Hillshade |
| **DSM.laz → DSM.laz-Tiles** | LAZ / LAS, beliebiges Tiling oder ein merged.laz | eine `.laz`/`.las` je Grid-Zelle, optional DSM + Hillshade |

Beide Tabs haben dieselben Eingaben in derselben Reihenfolge – der ASCII-Tab zusätzlich das
**ASCII-Format**. Die CRS-Angabe der Quelle (LAZ: Tag, ASCII: Dateiname) darf falsch sein oder
fehlen: getaggt wird, was im GUI gewählt ist.

## Benennung

```
<Jahr>_<AREA>_TIN_DSM[_thin<NN>]_<NAME>_LV95_<LN02|LHN95>.<laz|las>
<Jahr>_<AREA>_DSM_<GSD>cm_LV95_<LN02|LHN95>.tif        (+ .tfw, optional)
<Jahr>_<AREA>_hillshade_<GSD>cm_LV95_<LN02|LHN95>.tif  (+ .tfw, optional)
```

- **NAME** = Attributfeld `NAME` des Grid-Shapes (z.B. `2612_1107`).
- `<NN>` = Thinning in Dezimetern, zweistellig (0.2 m → `thin02`, 1.5 m → `thin15`); ohne Thinning
  fällt der Teil weg.
- Die Endung `_LV95_<Höhe>` entspricht dem Muster des GDWH-Imports (`4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py`).
- Beispiel: `2021_DIABLONS_TIN_DSM_thin02_2612_1107_LV95_LN02.laz`

**Ein Kachelsatz je Lauf:** alle Dateien des Input-Ordners werden je Zelle zusammengeführt (z.B. die
Kacheln eines alten Tilings). Überlappen sich zwei Dateien flächig, bricht der Lauf ab (doppelte
Punkte) – z.B. merged.laz und Einzelkacheln desselben Gebiets im selben Ordner.

## GUI starten

```bash
python GUI_dsmAsciiToLaz.py
```

Beim ersten Start erkennt das GUI automatisch die OSGeo4W-/QGIS-Installation und die `pdal.exe`
daneben. Der Pfad kann über **Aendern…** gesetzt werden und wird in
`process_scripts/_dsm2laz_config.json` gespeichert (lokal, nicht im Repository).

---

## Bedienung

Das Formular folgt dem Arbeitsablauf; Log und Fortschritt sind für beide Tabs gemeinsam, während
eines Laufs sind beide Start-Buttons gesperrt.

| | Feld | Bedeutung |
|---|---|---|
| **1 Input** | Input-Ordner | alle Dateien des Tab-Formats (nicht rekursiv) = ein Kachelsatz |
| | Datei-Info | wird beim Wählen des Ordners gelesen, siehe unten |
| | ASCII-Format *(nur ASCII)* | Spalten als PDAL-Dimensionen (`X Y Z`, `X Y Z Intensity Classification` …), Trennzeichen, Kopfzeilen – aus der ersten Datei vorbelegt |
| **2 Projekt & Referenzsystem** | Jahr, AREA / AOI | Teil des Namens (Jahr vierstellig, AREA ohne Leerzeichen) |
| | CRS / SRS | `EPSG:2056 + 5728` (LV95 + LN02) oder `EPSG:2056 + 5729` (LV95 + LHN95) – nur Tag, **keine** Umrechnung |
| | Thinning | kein / 0.1 / 0.2 / 0.4 / 0.8 / 1 / 1.5 / 2 m – `filters.sample` (Mindestabstand) wie in `topo-DMCdataConverter`, je Kachel nach dem Merge |
| **3 Output** | Output-Ordner | muss ein anderer Ordner als der Input sein |
| | Format | `laz` (LASzip, GDWH-Standard) oder `las` (unkomprimiert) |
| | Grid-Shape | Standard `swissGRID_1km2_shp/chGRID_1km2.shp`, TileKey = Attribut `NAME` |
| | Benennung | Vorschau inkl. Raster-Namen |
| | Create DSM-Raster | blendet Raster-Output-Ordner und GSD (Default 0.5 m) ein |
| **4 Staging** | Staging-Ordner, CPU-Kerne | Zwischendateien (leer = `<Output>\_staging`), Parallelisierung |

### Datei-Info

| | LAZ / LAS | ASCII |
|---|---|---|
| Übersicht | Dateien, Version/PF → Ausgabe-PF, Punkte (Summe der Header), Extent, max. Kachelzahl | Dateien, Trennzeichen/Spalten/Kopfzeilen → Ausgabe-PF, Vorschau der ersten Zeilen |
| Metadaten 1. Datei | `pdal info --metadata` / `--schema`: Version/PF, Punkte, **Lage- und Höhen-CRS des Tags**, scale, offset, `global_encoding`, Software, VLRs, Dimensionen | Grösse, geschätzte Punktzahl, Lage (aus dem Wertebereich), **CRS-Angaben im Dateinamen** (LV95/LV03/LN02/LHN95/2056 …), 1. Zeile, Z-Bereich der ersten Zeilen |
| Höhen-Warnung | Höhen-Tag ≠ Auswahl | `LN02`/`LHN95` im Dateinamen ≠ Auswahl |

`pdal info --stats` wird bewusst nicht verwendet: es liest die ganze Punktwolke, bei einem grossen
merged.laz dauert das Minuten. Eine Höhen-Warnung bricht nichts ab – so lässt sich eine falsche
Angabe der Quelle korrigieren; sie passt sich an, wenn im Dropdown umgestellt wird.

---

## Ablauf

```
ASCII (gemergt, .asc …)                   LAZ/LAS (beliebiges Tiling / merged)
  │ [1] grosse Dateien an Zeilengrenzen           │
  │     in Teile zerlegt, readers.text → LAZ      │
  │     (parallel)                                 │
  └──────────────────────┬────────────────────────┘
                         ▼
  [2] Header: Punktzahl, LV95-Plausibilität, Z-Bereich, Höhenangabe der Quelle vs. Auswahl,
      Überlappung, Grid-Zellen im Datenbereich
                         ▼
  [3] pdal tile je Quelle → Kachelstücke t<E>_<N>.laz   (Streaming, parallel)
                         ▼
  [4] je Grid-Zelle Stücke mergen (+ Thinning) → Endkachel  (parallel)
      Zielformat · CRS-VLRs · Validierung · atomares Schreiben
                         ▼
  [5] Punktbilanz Input = Output
                         ▼
  [6] optional DSM + Hillshade aus den fertigen Kacheln, Staging aufräumen
```

**Warum `pdal tile`:** jede Quelle wird genau **einmal** gelesen (Streaming, wenig RAM) – ein
gemergtes Gebiet über viele km² wird nicht pro Kachel erneut eingelesen, und es entsteht nie ein
physisches merged.laz. Die Stücke heissen nach dem Zellindex `floor(X/1000)_floor(Y/1000)`; die
Zuordnung zum `NAME` erfolgt über die Geometrie des Grid-Shapes.

### Zielformat der Kacheln

Identisch zu SB_DSM_PUNKTWOLKE / swissSURFACE3D (`topo-importDATAtoGDWHandSTAC`) bzw. dem Tab
[LN02] in `topo-DMCdataConverter`:

| Eigenschaft | Wert |
|---|---|
| LAS-Version | 1.4, `header_size` 375 |
| Point Data Record Format | nach den Feldern der Quelle: PF0/1 → **PF6**, PF2/3 → **PF7** (RGB), PF6/7 bleiben, NIR (PF8/10) → PF8; ASCII: PF6, PF7 bei Spalten `Red/Green/Blue` |
| Kompression | LAZ (LASzip) oder wahlweise unkomprimiertes LAS |
| `global_encoding` | 17 – Bit 0 (Adjusted Standard GPS Time) + Bit 4 (WKT) |
| `scale_x/y/z` | 0.01 |
| `offset_x/y/z` | Kachelursprung aus dem Grid (`<E>*1000 / <N>*1000 / 0`) |
| CRS-Tag | LV95 + LN02 (EPSG:2056 + 5728): byte-exakte Referenz-VLRs (GeoTIFF-Keys 34735 + WKT 2112) aus swissSURFACE3D; LV95 + LHN95: davon abgeleitet (siehe [Koordinatensystem](#koordinatensystem)) |

Die VLR-Injektion ist unverändert aus `topo-DMCdataConverter` übernommen (Begründung dort im
README, „Warum die CRS-Tags byte-exakt injiziert werden“): PDAL schreibt bei `a_srs` einen nicht
byte-identischen WKT, keinen GeoTIFF-VLR 34735 und einen zweiten `liblas`-WKT-Zwilling ohne
Höhenbezug.

Weitere Dimensionen (`Intensity`, `Classification`, …) werden übernommen, wenn sie in der Quelle bzw.
als ASCII-Spalte vorhanden sind. Unbekannte ASCII-Spalten (`Col4` …) werden nicht geschrieben.

### DSM-Raster + Hillshade

Übernommen aus `topo-DMCdataConverter` (Create DSM-Raster), gerastert aus den **fertigen Kacheln**
(also ausgedünnt, falls Thinning gewählt):

- zellweise IDW (`writers.gdal`, 1-km-Arbeitszellen mit Puffer gegen Nähte) → VRT-Mosaik
- kleine Löcher bis 900 m² interpoliert, grössere bleiben NoData
- **DSM**: Float32, NoData **-3.4028235e+38** (GDWH-Konvention SB_DSM), CRS **LV95 + LN02/LHN95**
  (zusammengesetztes CRS im GeoTIFF, `gdalinfo` zeigt z.B. `CH1903+ / LV95 + LHN95 height`)
- **Hillshade**: Byte, NoData **255** (gültige Werte 1–254), CRS LV95
- kein AOI-Shape: NoData genau dort, wo keine Punkte liegen; der Hillshade ist exakt dort NoData,
  wo das DSM NoData ist
- Kontrolle: NoData-Werte, CRS beider Raster, deckungsgleiches Gitter

### Fachliche Absicherungen

- **Halb-offene Kachelgrenzen** `[E, E+1000)`: ein Punkt exakt auf einer Kilometerlinie (bei
  Raster-DSM im XYZ-Format der Normalfall) gehört genau **einer** Kachel.
- **Punktbilanz**: Summe Input = Summe Output (+ Punkte ausserhalb des Grids als Warnung); jede
  Abweichung ist ein Fehler. Mit Thinning wird sie *vor* dem Ausdünnen gezogen; das Log zeigt
  zusätzlich den Anteil nach dem Thinning.
- **Überlappung** (BBoxen zweier Input-Dateien überlappen > 1 %): Abbruch, weil beim Zusammenführen
  doppelte Punkte entstünden. Aneinanderstossende Kacheln sind kein Problem.
- **Pro Kachel validiert**: LAS 1.4 / PF, `header_size`, `global_encoding`, `scale`,
  Offset = Kachelursprung, Punktzahl = Summe der Stücke (mit Thinning: 1 … Summe), BBox im
  Kachelrahmen (±2 cm), Kompression, genau die zwei Referenz-VLRs LV95/<Höhe> (byte-genau verglichen).
- **Atomares Schreiben**: Temp-Datei im Output-Ordner, erst nach bestandener Validierung per
  `os.replace` an ihren Platz. Die Quelldateien werden nie verändert.
- **ASCII-Spaltenzahl**: PDAL überspringt unpassende Zeilen nur mit Warnung (Exit-Code 0) – passt die
  Spaltenzahl gar nicht, entstünde still eine leere Datei. Das Tool bricht dann ab und meldet
  ignorierte Einzelzeilen im Log.
- **ESRI-ASCII-Grid** (`.asc` mit `ncols`/`nrows`-Kopf, also Raster statt Punktliste) wird erkannt
  und mit Hinweis abgelehnt (`gdal_translate -of XYZ`).
- **Z-Plausibilität**: liegt der Z-Bereich ausserhalb 150–4900 m, erscheint eine Warnung im Log
  (NoData-Werte wie -9999 oder Ausreisser) – die Punkte werden unverändert übernommen.
- **Grid-Prüfung**: Feld `NAME`, CRS EPSG:2056, alle beteiligten Zellen achsparallele Quadrate auf
  dem Zellraster, keine doppelten `NAME`.
- **Pfadlänge**: `pdal.exe` ist nicht long-path-fähig – ab 260 Zeichen meldet es nur
  `file not found`. Input-, Staging- und Output-Pfade werden deshalb **vor** dem Lauf geprüft.
- **Einmal gerundet**: Zwischendateien liegen mit scale 0.01 / Offset 0 auf demselben
  Zentimeter-Gitter wie die Endkacheln – die Requantisierung im letzten Schritt ist eine Identität.

---

## Koordinatensystem

- **Lage: immer LV95 (EPSG:2056)** – nur getaggt, nie umgerechnet. Massgebend ist der
  **Koordinatenbereich**, nicht der Tag: eine Datei mit Tag `EPSG:4150` (CH1903+, das geographische
  Basis-CRS von LV95) oder ganz ohne Tag wird akzeptiert, wenn die Werte LV95-Meter sind.
- **LV03**-Koordinaten werden abgelehnt (bei ASCII schon vor dem Einlesen). PROJ fände für
  LV03 → LV95 ohne das CHENyx06-Gitter nur eine „Ballpark“-Transformation → zuerst mit
  **GeoSuite/REFRAME (FINELTRA)** transformieren.
- **Koordinaten in Grad** werden abgelehnt: aus CH1903+ (EPSG:4150) wäre die Projektion nach LV95
  zwar exakt, aus WGS84/ETRS89 aber eine Datumstransformation – und die Werte allein verraten nicht,
  welches von beiden vorliegt.
- **Höhe:** **LN02** oder **LHN95** nach GUI-Auswahl, nur getaggt. Eine abweichende Angabe der
  Quelle (Tag bzw. Dateiname) ergibt eine Warnung. Eine echte Umrechnung LHN95 ↔ LN02 macht
  ausschliesslich GeoSuite/REFRAME (HTRANS).
- **CRS-VLRs LV95/LHN95:** Es gibt keine verifizierte LHN95-Referenzkachel. Die zwei VLRs sind aus
  der LN02-Referenz abgeleitet: nur der `VERT_CS`-Block im WKT (zeichengleich mit der GDAL/PROJ-
  Definition von EPSG:5729) und der VerticalCSTypeGeoKey 4096 (5728 → 5729) sind ersetzt. Geprüft:
  `pdal info` liest LV95 + LHN95, der WKT ist laut GDAL gleichwertig zu `EPSG:2056+5729`.
  `gdalsrsinfo -o epsg` meldet für beide Varianten „EPSG:-1“ (70 %), weil es für ein
  zusammengesetztes CRS keinen eigenen EPSG-Code gibt – bei der swissSURFACE3D-Referenz identisch.
- **Grid-Shape** muss EPSG:2056 sein (wird geprüft).

---

## Performance

- `readers.text` liest einspurig: gemessen ca. **0.5 Mio Punkte/s**. Grosse ASCII-Dateien
  (> 256 MB) werden deshalb an Zeilengrenzen in so viele Teile zerlegt, wie **CPU-Kerne** gewählt
  sind, und parallel eingelesen – gemessen mit 4 Kernen **1.5 Mio Punkte/s** (4 Mio Punkte, 114 MB:
  7.9 s → 2.7 s für den ganzen Lauf). Kopfzeilen bleiben im ersten Teil.
- `pdal tile` streamt – der RAM-Bedarf hängt nicht von der Dateigrösse ab. Im letzten Schritt wird
  je Kachel nur deren Punktmenge im Speicher gehalten (bei knappem RAM CPU-Kerne reduzieren).
- Staging-Platzbedarf: vorübergehend die ASCII-Teile (werden nach dem Einlesen sofort gelöscht)
  plus ca. die Datenmenge als LAZ.

---

## Dateien

| Datei | Beschreibung |
|-------|-------------|
| `GUI_dsmAsciiToLaz.py` | Tkinter-GUI – startet mit Standard-Python (ab 3.6) |
| `process_scripts/_osgeo_runner.py` | Worker (Aktionen `info` / `process`) – via OSGeo4W/QGIS-Python als Subprocess, steuert `pdal.exe`; enthält auch die Benennungsregel (vom GUI für die Vorschau genutzt) |
| `process_scripts/01_convertDATA_dsm_to_laz_tiles.py` | Standalone-Script ohne GUI (gleiche Verarbeitung, Konfiguration im Script-Kopf) |
| `process_scripts/_dsm2laz_config.json` | GUI-Konfiguration (OSGeo4W-Python-Pfad), wird automatisch angelegt |
| `swissGRID_1km2_shp/` | Standard-Grid (swissGRID 1km², EPSG:2056, Feld `NAME`) |
| `test/test_functions.py` | Sanity-Checks ohne OSGeo4W/PDAL |

## Voraussetzungen

- **GUI:** Python ≥ 3.6, nur `tkinter`
- **Verarbeitung:** OSGeo4W- oder QGIS-Installation mit Python + `osgeo` und **`pdal.exe`**
  (getestet mit QGIS 3.44 / PDAL 2.10 / GDAL 3.13). Keine zusätzlichen Python-Pakete
  (kein `pdal`-Python-Binding, kein `laspy` nötig).

## Architektur

```
GUI_dsmAsciiToLaz.py                (Standard-Python, tkinter)
        │  JSON-Config (tempfile)
        ▼
process_scripts/_osgeo_runner.py    (OSGeo4W/QGIS-Python: OGR für das Grid)
        │  PDAL-Pipelines / pdal tile (Subprocess, parallel)
        ▼
    pdal.exe            stdout → live ins GUI-Log + logs/*.log
```

## Standalone-Script (ohne GUI)

```bash
C:\OSGeo4W\bin\python3.exe process_scripts\01_convertDATA_dsm_to_laz_tiles.py
```

Konfiguration im Script-Kopf (`CONFIG`) anpassen.

## Log-Ausgabe

Jeder Lauf schreibt eine Logdatei nach `logs/<Input-Ordner>_to_laz_<Zeitstempel>.log`.

## Tests

```bash
python -m pytest -q
```
