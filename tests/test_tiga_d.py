import gzip
import json

import numpy as np
import pytest

from tamborafire import tiga_d as T


def _grid(ny=600, nx=800, res=10.0):
    yy, xx = np.mgrid[0:ny, 0:nx]
    elev = 500 + 2.0 * xx + 1.0 * yy
    arr = np.full((ny, nx), np.nan)
    r = np.hypot(xx - 400, yy - 300) * res       # api melingkar dari tengah, 10 m/menit
    arr[r <= 800] = r[r <= 800] / 10.0
    ros = np.where(np.isfinite(arr), 10.0, 0.0)
    flame = np.where(np.isfinite(arr), 1.5, 0.0)
    fli = np.where(np.isfinite(arr), 900.0, 0.0)
    crown = np.where(np.isfinite(arr), 1, 0).astype(np.uint8)
    fuel = np.full((ny, nx), 31, np.int32)
    return elev, arr, ros, flame, fli, crown, fuel


def test_bangun_dan_baca_bolak_balik():
    elev, arr, ros, flame, fli, crown, fuel = _grid()
    west, north, res = 600000.0, 9090000.0, 10.0
    ign = (west + 400.5 * res, north - 300.5 * res)
    meta = dict(simulasi=dict(mode="farsite", start="2026-10-30 13:57:00"), jam=[dict(t="2026-10-30T13:00", ws=12, wd=90)])
    data = T.bangun(arr, ros, flame, fli, crown, elev, fuel, west, north, res, "EPSG:32750", ign, meta)
    assert data[:2] == b"\x1f\x8b"
    h, a = T.baca(data)
    # dipotong ke area terbakar (r = 800 m) + tepi 1,2 km per sisi = +-2 km, bukan seluruh 8 x 6 km
    assert h["kolom"] == pytest.approx(402, abs=3) and h["baris"] == pytest.approx(402, abs=3)
    assert h["sel_m"] == 10.0 and h["perkecil"] == 1
    ok = np.isfinite(a["tiba"])
    assert h["luas_terbakar_ha"] == pytest.approx(np.isfinite(arr).sum() * 0.01, abs=0.06)
    assert np.nanmax(a["tiba"]) == pytest.approx(80.0, abs=0.06)
    assert a["ros"][ok].min() == pytest.approx(10.0, abs=0.01)
    assert a["fli"][ok].max() == pytest.approx(900.0, abs=1)
    assert set(np.unique(a["fuel"])) == {31}
    # titik api tetap di sel yang sama
    c, r = h["titik_api"]
    assert a["tiba"][int(r), int(c)] == pytest.approx(0.0, abs=0.2)
    # elevasi dan georeferensi
    x0 = int((h["utm"]["barat"] - west) / res)
    y0 = int((north - h["utm"]["utara"]) / res)
    assert a["elev"][0, 0] == pytest.approx(elev[y0, x0], abs=0.06)
    A = h["piksel_ke_lonlat"]
    lon, lat = A[0] * c + A[1] * r + A[2], A[3] * c + A[4] * r + A[5]
    B = h["lonlat_ke_piksel"]
    assert B[0] * lon + B[1] * lat + B[2] == pytest.approx(c, abs=0.05)
    assert -9 < lat < -7 and 117 < lon < 119
    assert h["jam"][0]["ws"] == 12 and h["simulasi"]["mode"] == "farsite"


def test_perkecil_jika_terlalu_besar():
    elev, arr, ros, flame, fli, crown, fuel = _grid()
    data = T.bangun(arr, ros, flame, fli, crown, elev, fuel, 600000.0, 9090000.0, 10.0, "EPSG:32750",
                    (604005.0, 9086995.0), {}, maks=100)
    h, a = T.baca(data)
    assert h["perkecil"] >= 2 and max(h["kolom"], h["baris"]) <= 100
    assert h["sel_m"] == 10.0 * h["perkecil"]
    # waktu tiba blok = minimum, jadi luas terbakar tidak hilang
    assert np.isfinite(a["tiba"]).sum() * h["sel_m"] ** 2 >= np.isfinite(arr).sum() * 100 * 0.95


def test_tanpa_area_terbakar():
    elev, arr, ros, flame, fli, crown, fuel = _grid()
    arr[:] = np.nan
    data = T.bangun(arr, ros * 0, flame * 0, fli * 0, crown * 0, elev, fuel, 600000.0, 9090000.0, 10.0,
                    "EPSG:32750", (604005.0, 9086995.0), {})
    h, a = T.baca(data)
    assert h["luas_terbakar_ha"] == 0 and not np.isfinite(a["tiba"]).any()
    assert h["kolom"] >= 300 and h["baris"] >= 300      # minimal 3 km
