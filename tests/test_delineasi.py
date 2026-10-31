import datetime as dt
import json
import os
import zipfile

import pytest

from tamborafire import delineasi as D

ROOT = os.path.dirname(os.path.dirname(__file__))
TN = os.path.join(ROOT, "docs", "data", "batas_tn.geojson")
RESORT = os.path.join(ROOT, "docs", "data", "batas_resort.geojson")
CONTOH = os.path.join(ROOT, "contoh", "piong_9sep", "bekas_terbakar_9sep2026_dNBR")


@pytest.mark.parametrize("teks,hasil", [
    ("delineasi_2026-10-30", dt.date(2026, 10, 30)),
    ("20261030_piong", dt.date(2026, 10, 30)),
    ("areal terbakar 30-10-2026", dt.date(2026, 10, 30)),
    ("bekas_terbakar_9sep2026_dNBR", dt.date(2026, 9, 9)),
    ("Terbakar 5 Agustus 2026", dt.date(2026, 8, 5)),
    ("30102026", dt.date(2026, 10, 30)),
    ("piong", None),
])
def test_tanggal_dari_nama(teks, hasil):
    assert D.tanggal_dari_teks(teks) == hasil


def test_awal_musim():
    assert D.awal_musim(dt.date(2026, 10, 30), "06-01") == dt.date(2026, 6, 1)
    assert D.awal_musim(dt.date(2027, 2, 1), "06-01") == dt.date(2026, 6, 1)


def _geojson(path, kotak):
    x0, y0, x1, y1 = kotak
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    json.dump({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]}, open(path, "w"))


def test_gabung_tidak_menghitung_dua_kali(tmp_path):
    # Dua delineasi tumpang tindih di Resort Doro Ncanga (kotak derajat, WGS84)
    _geojson(tmp_path / "2026-09-01.geojson", (118.000, -8.360, 118.010, -8.350))
    _geojson(tmp_path / "2026-09-06.geojson", (118.005, -8.360, 118.015, -8.350))   # setengahnya sudah ada
    with zipfile.ZipFile(tmp_path / "Delineasi 9 Sep 2026.zip", "w") as z:            # SHP UTM dengan .prj
        for e in (".shp", ".shx", ".dbf", ".prj"):
            z.write(CONTOH + e, "piong" + e)
    hasil, gj = D.olah(D.kumpulan_lokal(str(tmp_path)), TN, RESORT)
    r = hasil["riwayat"]
    assert [x["tanggal"] for x in r] == ["2026-09-01", "2026-09-06", "2026-09-09"]
    assert r[1]["baru_ha"] == pytest.approx(r[0]["baru_ha"] / 2, rel=0.02)
    assert r[2]["baru_ha"] == pytest.approx(364.6, abs=0.5)                            # LUAS_HA di DBF contoh
    assert hasil["terbakar_ha"] == pytest.approx(sum(x["baru_ha"] for x in r), abs=0.2)
    per = {x["nama"]: x["terbakar_ha"] for x in hasil["resort"]}
    assert per["Resort Doro Ncanga"] == pytest.approx(r[0]["baru_ha"] * 1.5, rel=0.02)
    assert per["Resort Piong"] == pytest.approx(364.6, abs=0.5)
    assert {f["properties"]["tanggal"] for f in gj["features"]} == {"2026-09-01", "2026-09-06", "2026-09-09"}
    lon, lat = gj["features"][0]["geometry"]["coordinates"][0][0]
    assert 117.5 < lon < 118.5 and -8.6 < lat < -8.0


def test_mode_terbaru_memakai_koreksi(tmp_path):
    _geojson(tmp_path / "2026-09-01.geojson", (118.000, -8.360, 118.010, -8.350))
    _geojson(tmp_path / "2026-09-06.geojson", (118.000, -8.360, 118.005, -8.350))   # koreksi: lebih kecil
    hasil, _ = D.olah(D.kumpulan_lokal(str(tmp_path)), TN, RESORT, mode="terbaru")
    assert hasil["terbakar_ha"] == pytest.approx(hasil["riwayat"][0]["kumulatif_ha"] / 2, rel=0.02)


def test_folder_kosong(tmp_path):
    hasil, gj = D.olah(D.kumpulan_lokal(str(tmp_path)), TN, RESORT)
    assert hasil["jumlah_delineasi"] == 0 and hasil["terbakar_ha"] == 0 and gj["features"] == []
