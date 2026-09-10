# DSM ASCII/LAZ → LAZ-Tiles

Wandelt alte DSM-Punktwolken (bereits **LV95 / LN02**) in GDWH-taugliche LAZ-Kacheln gemäss einem
Grid-Shape (z.B. swissGRID 1km²) um. Struktur und Styling analog zu `topo-COGTIFFconverter` und
`topo-DMCdataConverter`.

| Input | Verarbeitung | Output |
|---|---|---|
| **ASCII** (`.xyz` / `.txt` / `.asc` / `.csv`), typischerweise **gemergt pro Gebiet** | ASCII → LAZ, dann Tiling | eine `.laz` je Grid-Zelle mit Daten |
| **LAZ / LAS** (altes Tiling) | nur neues Tiling | eine `.laz` je Grid-Zelle mit Daten |

## Benennung

```
<Basis>_<NAME>_LV95_LN02.laz
```

- **Basis** = Input-Dateiname bis vor `_LV95` – der Rest (z.B. `_CIR_low_raw`) fällt weg.
- Endet die Basis auf einen **alten TileKey** (`_2600_1200`, `_1091-44`, `_600_200`), wird er entfernt.
  Als TileKey gilt nur eine erste Zahl im Bereich LV95-km (2480–2840), LK25-Blatt (1011–1374) oder
  LV03-km (480–840) – ein Jahr wie `_2015_1` bleibt stehen.
- **NAME** = Attributfeld `NAME` des Grid-Shapes (z.B. `2600_1200`).
- Die Endung `_LV95_LN02` entspricht dem Muster des GDWH-Imports (`4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py`).

| Input | Output (Beispiel-Zelle 2600_1200) |
|---|---|
| `2015_RHONE_DSM_1m_LV95_LN02_CIR_low_raw.asc` (gemergt) | `2015_RHONE_DSM_1m_2600_1200_LV95_LN02.laz` |
| `2015_RHONE_DSM_1m_1291-11_LV95_LN02.laz` (altes Tiling) | `2015_RHONE_DSM_1m_2600_1200_LV95_LN02.laz` |

**Kachelsätze:** Dateien mit gleicher Basis ergeben **einen** Kachelsatz (ihre Punkte werden je
Zelle zusammengeführt – z.B. die Kacheln eines alten Tilings). Dateien mit unterschiedlicher Basis
(z.B. `2015_RHONE_…` und `2016_AARE_…` im selben Ordner) werden als **getrennte** Kachelsätze
verarbeitet, auch wenn sie sich räumlich überlappen.

Optional überschreibt das Feld **Basisname** die Automatik für alle Dateien (alles wird zu einem
Kachelsatz). Die GUI zeigt die Zuordnung Input → Output als Vorschau.

## GUI starten

```bash
python GUI_dsmAsciiToLaz.py
```

Beim ersten Start erkennt das GUI automatisch die OSGeo4W-/QGIS-Installation und die `pdal.exe`
daneben. Der Pfad kann über **Aendern…** gesetzt werden und wird in
`process_scripts/_dsm2laz_config.json` gespeichert (lokal, nicht im Repository).

---

## Bedienung

1. **Input-Ordner** wählen – das **Input-Format** (ASCII oder LAZ/LAS) wird aus den Dateiendungen
   vorgewählt. Verarbeitet werden alle passenden Dateien im Ordner (nicht rekursiv).
2. **Output-Ordner** – muss ein anderer Ordner als der Input sein.
3. **Grid-Shape** – Standard `swissGRID_1km2_shp/chGRID_1km2.shp`.
4. **Datei-Info** prüft den Ordner:
   - ASCII: Vorschau der ersten Zeilen, Trennzeichen, Spaltenzahl, Kopfzeilen, Koordinatenbereich →
     füllt die Sektion **ASCII-Format** vor
   - LAZ/LAS: Version/Punktformat, Punktzahl (Summe der Header), Extent, CRS-Tag, max. Kachelzahl
5. **ASCII-Format** (nur bei ASCII): Spalten als PDAL-Dimensionen (`X Y Z`, oder z.B.
   `X Y Z Intensity Classification`), Trennzeichen, Kopfzeilen überspringen.
6. **Benennung** in der Vorschau prüfen, optional **Basisname** setzen.
7. **Staging & Parallelisierung** – Zwischendateien (leer = `<Output>\_staging`), CPU-Kerne.
8. **LAZ-TILES ERSTELLEN** starten.

---

## Ablauf

```
ASCII (gemergt, .asc …)                   LAZ/LAS (altes Tiling)
  │ [1] grosse Dateien an Zeilengrenzen           │
  │     in Teile zerlegt, readers.text → LAZ      │
  │     (parallel)                                 │
  └──────────────────────┬────────────────────────┘
                         ▼
  [2] Header: Punktzahl, LV95-Plausibilität, Z-Bereich, Überlappung je Kachelsatz,
      Grid-Zellen im Datenbereich
                         ▼
  [3] pdal tile je Quelle → Kachelstücke t<E>_<N>.laz   (Streaming, parallel)
                         ▼
  [4] je Kachelsatz + Grid-Zelle Stücke mergen → <Basis>_<NAME>_LV95_LN02.laz  (parallel)
      Zielformat · CRS-VLRs · Validierung · atomares Schreiben
                         ▼
  [5] Punktbilanz Input = Output, Staging aufräumen
```

**Warum `pdal tile`:** jede Quelle wird genau **einmal** gelesen (Streaming, wenig RAM) – ein
gemergtes Gebiet über viele km² wird nicht pro Kachel erneut eingelesen. Die Stücke heissen nach
dem Zellindex `floor(X/1000)_floor(Y/1000)`; die Zuordnung zum `NAME` erfolgt über die Geometrie des
Grid-Shapes.

### Zielformat der Kacheln

Identisch zu SB_DSM_PUNKTWOLKE / swissSURFACE3D (`topo-importDATAtoGDWHandSTAC`) bzw. dem Tab
[LN02] in `topo-DMCdataConverter`:

| Eigenschaft | Wert |
|---|---|
| LAS-Version | 1.4, `header_size` 375 |
| Point Data Record Format | **6** (PF7 = PF6 + RGB, nur wenn die Quelle Farbe führt bzw. ASCII-Spalten `Red/Green/Blue`) |
| Kompression | LAZ (LASzip) |
| `global_encoding` | 17 – Bit 0 (Adjusted Standard GPS Time) + Bit 4 (WKT) |
| `scale_x/y/z` | 0.01 |
| `offset_x/y/z` | Kachelursprung aus dem Grid (`<E>*1000 / <N>*1000 / 0`) |
| CRS-Tag | LV95 + LN02 (EPSG:2056 + 5728): byte-exakte Referenz-VLRs (GeoTIFF-Keys 34735 + WKT 2112) aus swissSURFACE3D |

Die VLR-Injektion ist unverändert aus `topo-DMCdataConverter` übernommen (Begründung dort im
README, „Warum die CRS-Tags byte-exakt injiziert werden“): PDAL schreibt bei `a_srs` einen nicht
byte-identischen WKT, keinen GeoTIFF-VLR 34735 und einen zweiten `liblas`-WKT-Zwilling ohne
Höhenbezug.

Weitere Dimensionen (`Intensity`, `Classification`, …) werden übernommen, wenn sie in der Quelle bzw.
als ASCII-Spalte vorhanden sind. Unbekannte ASCII-Spalten (`Col4` …) werden nicht geschrieben.

### Fachliche Absicherungen

- **Halb-offene Kachelgrenzen** `[E, E+1000)`: ein Punkt exakt auf einer Kilometerlinie (bei
  Raster-DSM im XYZ-Format der Normalfall) gehört genau **einer** Kachel.
- **Punktbilanz**: Summe Input = Summe Output (+ Punkte ausserhalb des Grids als Warnung); jede
  Abweichung ist ein Fehler.
- **Überlappung innerhalb eines Kachelsatzes** (BBoxen zweier Input-Dateien mit gleicher Basis
  überlappen > 1 %): Abbruch, weil beim Zusammenführen doppelte Punkte entstünden (Kachel-Buffer
  oder zwei Varianten desselben Gebiets). Aneinanderstossende Kacheln sind kein Problem.
- **Pro Kachel validiert**: LAS 1.4 / PF, `header_size`, `global_encoding`, `scale`,
  Offset = Kachelursprung, Punktzahl = Summe der Stücke, BBox im Kachelrahmen (±2 cm), genau die
  zwei LV95/LN02-Referenz-VLRs.
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

- **Lage: LV95 (EPSG:2056)**, **Höhe: LN02 (EPSG:5728)** – beides wird vorausgesetzt und nur
  getaggt, nie umgerechnet.
- **LV03**-Koordinaten werden abgelehnt (bei ASCII schon vor dem Einlesen). PROJ fände für
  LV03 → LV95 ohne das CHENyx06-Gitter nur eine „Ballpark“-Transformation → zuerst mit
  **GeoSuite/REFRAME (FINELTRA)** transformieren.
- **LHN95** wird abgelehnt: LAZ mit LHN95-Tag (WKT oder GeoTIFF-Key 5729) oder Dateiname mit
  `LHN95`. LHN95 → LN02 ausschliesslich mit GeoSuite/REFRAME (HTRANS).
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
