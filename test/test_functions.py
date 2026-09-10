"""Leichtgewichtige Import-/Sanity-Checks - laufen ohne OSGeo4W/PDAL."""
import importlib.util
import os
import struct

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_module_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gui():
    return load_module_from_path("gui_module", os.path.join(PROJECT_ROOT, "GUI_dsmAsciiToLaz.py"))


def _runner():
    return load_module_from_path("runner_module",
                                 os.path.join(PROJECT_ROOT, "process_scripts", "_osgeo_runner.py"))


def _las_bytes(n_vlr=0, vlr=b""):
    """Minimaler LAS-1.4-Header (375 Byte) + optionaler VLR-Block, ohne Punkte."""
    b = bytearray(375)
    b[0:4] = b"LASF"
    struct.pack_into("<H", b, 6, 17)
    b[24], b[25] = 1, 4
    struct.pack_into("<HII", b, 94, 375, 375 + len(vlr), n_vlr)
    b[104] = 6 | 0x80
    struct.pack_into("<6d", b, 131, 0.01, 0.01, 0.01, 2600000.0, 1200000.0, 0.0)
    struct.pack_into("<6d", b, 179, 2600999.0, 2600000.0, 1200999.0, 1200000.0, 600.0, 400.0)
    struct.pack_into("<Q", b, 247, 12345)
    return bytes(b) + vlr


def test_constants():
    gui = _gui()
    assert os.path.basename(gui.RUNNER_SCRIPT) == "_osgeo_runner.py"
    assert gui.CONFIG_FILE.endswith("_dsm2laz_config.json")
    assert os.path.isfile(gui.DEFAULT_GRID_SHAPE)
    assert set(gui.SEPARATOR_LABELS) == set(_runner().SEPARATORS)


def test_detect_python_home_returns_string():
    gui = _gui()
    assert isinstance(gui._detect_python_home(os.path.join("C:", "OSGeo4W", "bin", "python3.exe")), str)


def test_output_base():
    f = _runner()._output_base
    assert f("2015_RHONE_DSM_1m_LV95_LN02_CIR_low_raw.asc") == "2015_RHONE_DSM_1m"
    assert f("2015_RHONE_DSM_1m_2600_1200_LV95_LN02.laz") == "2015_RHONE_DSM_1m"   # alter 1km-Key
    assert f("SURFACE_2010_1091-44_LV95_LN02.laz") == "SURFACE_2010"               # LK25-Blatt
    assert f("dom_600_200.xyz") == "dom"                                             # LV03-km
    assert f("dsm_2015_1.xyz") == "dsm_2015_1"                                       # Jahr ist kein Key
    assert f("gebiet.xyz") == "gebiet"


def test_group_by_base_and_preview():
    r = _runner()
    files = [os.path.join("x", "2015_RHONE_DSM_1m_LV95_LN02_CIR_low_raw.asc"),
             os.path.join("x", "2016_AARE_DSM_1m_LV95_LN02_CIR_low_raw.asc")]
    assert sorted(r._group_by_base(files)) == ["2015_RHONE_DSM_1m", "2016_AARE_DSM_1m"]
    assert list(r._group_by_base(files, "MEIN_NAME")) == ["MEIN_NAME"]
    preview = _gui()._naming_preview(files[:1])
    assert "2015_RHONE_DSM_1m_2600_1200_LV95_LN02.laz" in preview


def test_overlapping_pairs():
    r = _runner()
    touching = {"a": (0, 0, 1000, 1000), "b": (1000, 0, 2000, 1000)}
    assert r._overlapping_pairs(touching) == []
    overlapping = {"a": (0, 0, 1000, 1000), "b": (500, 0, 1500, 1000)}
    assert r._overlapping_pairs(overlapping) == [("a", "b")]


def test_classify_crs_and_cells():
    r = _runner()
    assert r._classify_crs(2600000, 1200000, 2601000, 1201000) == "LV95"
    assert r._classify_crs(600000, 200000, 601000, 201000) == "LV03"
    assert r._classify_crs(0, 0, 10, 10) == "unbekannt"
    # halb-offene Intervalle: BBox endet exakt auf der Kilometerlinie -> Nachbarzelle zaehlt mit
    assert r._cells_of_bbox(2600000, 1200000, 2600999.99, 1200999.99, 1000) == {(2600, 1200)}
    assert len(r._cells_of_bbox(2600000, 1200000, 2601000, 1200500, 1000)) == 2


def test_sniff_ascii_variants(tmp_path):
    r = _runner()
    p1 = tmp_path / "a.xyz"
    p1.write_text("2600000.00   1200000.00  512.34\r\n2600002.00 1200000.00 512.40\r\n")
    s1 = r._sniff_ascii(str(p1))
    assert (s1["separator"], s1["skip"], s1["ncols"], s1["columns"], s1["crs_guess"]) == \
        ("space", 0, 3, "X Y Z", "LV95")

    p2 = tmp_path / "b.csv"
    p2.write_text("x;y;z;class\n600000.1;200000.2;500.0;2\n")
    s2 = r._sniff_ascii(str(p2))
    assert (s2["separator"], s2["skip"], s2["columns"], s2["crs_guess"]) == \
        ("semicolon", 1, "X Y Z Classification", "LV03")

    p3 = tmp_path / "grid.asc"
    p3.write_text("ncols 3\nnrows 1\nxllcorner 2600000\nyllcorner 1200000\ncellsize 1\n500 501 502\n")
    with pytest.raises(ValueError, match="ESRI"):
        r._sniff_ascii(str(p3))


def test_split_ascii_keeps_all_lines(tmp_path):
    r = _runner()
    src = tmp_path / "big.xyz"
    content = "X Y Z\n" + "".join("%d 1200000 500\n" % (2600000 + i) for i in range(1000))
    src.write_bytes(content.encode())
    parts = r._split_ascii(str(src), tmp_path / "c", 4)
    data = [open(p, "rb").read() for p in parts]
    assert len(parts) == 4
    assert b"".join(data) == content.encode()
    assert all(d.endswith(b"\n") for d in data)
    assert data[0].startswith(b"X Y Z") and not any(d.startswith(b"X") for d in data[1:])


def test_read_las_header_and_lhn95_tag(tmp_path):
    r = _runner()
    p = tmp_path / "x.laz"
    p.write_bytes(_las_bytes())
    h = r._read_las_header(str(p))
    assert (h["minor_version"], h["point_format"], h["compressed"], h["count"]) == (4, 6, True, 12345)
    assert (h["minx"], h["maxy"], h["global_encoding"]) == (2600000.0, 1200999.0, 17)
    assert r._lhn95_tagged(str(p)) is False

    for wkt, expected in ((b'VERT_CS["LHN95 height",AUTHORITY["EPSG","5729"]]', True),
                          (b'VERT_CS["LN02 height",AUTHORITY["EPSG","5728"]]', False)):
        vlr = struct.pack("<H16sHH32s", 0, b"LASF_Projection", 2112, len(wkt), b"") + wkt
        p.write_bytes(_las_bytes(1, vlr))
        assert r._lhn95_tagged(str(p)) is expected


def test_process_action_available():
    r = _runner()
    assert callable(r._process) and callable(r._info)


def test_format_ui_toggle():
    gui = _gui()
    app = gui.DsmToLazApp()
    try:
        app._fmt_var.set("las")
        app._update_format_ui()
        assert not app._ascii_sec.winfo_manager()
        app._fmt_var.set("ascii")
        app._update_format_ui()
        assert app._ascii_sec.winfo_manager() == "pack"
        assert app._base_var.get() == ""
    finally:
        app.destroy()
