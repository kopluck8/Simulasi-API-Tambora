import math
import os

import numpy as np
import pytest

from tamborafire.fuels import fuel_table, read_fmd
from tamborafire.lcp import read_lcp, write_lcp
from tamborafire.rothermel import RothermelFuel
from tamborafire.spread import min_travel_time
from tamborafire import weather as WX

ROOT = os.path.dirname(os.path.dirname(__file__))
FMD20 = os.path.join(ROOT, "kalibrasi", "tambora_savana_cepat20.fmd")
FMS = os.path.join(ROOT, "kalibrasi", "tambora_latihan.fms")
LCP = os.path.join(ROOT, "contoh", "piong_9sep", "landscape_prakebakaran_9sep_30m.lcp")
CONTOH = os.path.join(ROOT, "contoh", "piong_9sep")


def test_fmd20_dibaca_english():
    m = read_fmd(FMD20)
    assert set(m) == {30, 31, 32, 33}
    assert m[31].sav1 == 2500 and m[31].depth == pytest.approx(1.27) and m[31].mx == 30


def test_rothermel_sama_dengan_flammap():
    """Nilai acuan dari flammap.dll 6.2 (lapisan Rate of Spread), sel datar, angin 20-ft 20 km/j,
    1-h 5% (tambora_latihan.fms), fmd20. Midflame = 20 x WAF."""
    t = fuel_table(FMD20)
    acuan = {30: (6.674, 12.173), 31: (7.608, 30.434), 32: (8.674, 61.365), 33: (9.477, 101.168)}
    for n, (mid_kmh, ros_fm) in acuan.items():
        r = RothermelFuel(t[n], .05, .07, .09, .40 if n < 32 else .45, .80)
        ros = r.R0 * (1 + r.phi_w(mid_kmh / 1.609344 * 88)) * 0.3048
        assert ros == pytest.approx(ros_fm, rel=0.01)


def test_elips_homogen():
    n = 201
    ros = np.full((n, n), 10.0, np.float32)
    head = np.full((n, n), 90.0, np.float32)
    e = math.sqrt(1 - 1 / 9)
    ecc = np.full((n, n), e, np.float32)
    ig = np.zeros((n, n), bool)
    ig[100, 50] = True
    a = min_travel_time(ros, head, ecc, ig, 10.0)
    assert a[100, 110] == pytest.approx(60.0, rel=0.02)          # kepala: 10 m/menit ke timur
    assert np.isnan(a).sum() == 0


def test_lcp_bolak_balik(tmp_path):
    ls = read_lcp(LCP)
    p = tmp_path / "x.lcp"
    write_lcp(ls, p)
    ls2 = read_lcp(p)
    for k in ls.layers:
        assert (ls.layers[k] == ls2.layers[k]).all()
    assert os.path.getsize(p) == 7316 + ls.shape[0] * ls.shape[1] * 8 * 2


def test_validasi_9sep():
    """Reproduksi kalibrasi arah: angin dari 70° 20 km/j -> Sørensen ~0,7 dan arah ~249°."""
    import shapefile
    from tamborafire.geo import read_ignition, read_polygons
    from tamborafire.simulate import Simulator
    from tamborafire.validate import evaluate
    ls = read_lcp(LCP)
    ig = read_ignition(os.path.join(CONTOH, "september_poligon60m.shp"), ls)
    scar = read_polygons(os.path.join(CONTOH, "bekas_terbakar_9sep2026_dNBR.shp"))
    x, y = shapefile.Reader(os.path.join(CONTOH, "september_titik.shp")).shapes()[0].points[0]
    sim = Simulator(ls, FMD20, FMS)
    h = sim.mtt(ig, 20, 70, 240, wind="seragam")
    r = evaluate(h.arrival, scar, ls, x, y)
    assert r["sorensen"] > 0.68
    assert abs(r["arah_model"] - r["arah_obs"]) < 8
    h2 = sim.mtt(ig, 9, 138, 240, wind="seragam")
    assert evaluate(h2.arrival, scar, ls, x, y)["sorensen"] < 0.5


def test_wxs_bolak_balik(tmp_path):
    src = os.path.join(ROOT, "contoh", "cuaca", "doro_tabe_nae_20260906_20260909_perbaikan.wxs")
    w = WX.read_wxs(src)
    assert len(w.records) == 96 and w.elevation == 326
    p = tmp_path / "a.wxs"
    WX.write_wxs(w, p)
    w2 = WX.read_wxs(p)
    assert [(r.time, r.ws, r.wd) for r in w2.records] == [(r.time, r.ws, r.wd) for r in w.records]
    assert open(p, "rb").read().count(b"\r\n") == 100


def test_openmeteo_ke_wxs():
    data = {"elevation": 270, "hourly": {"time": ["2026-09-09T13:00", "2026-09-09T14:00"],
            "temperature_2m": [30.4, 29.6], "relative_humidity_2m": [45, 50], "precipitation": [0, 0.2],
            "wind_speed_10m": [19.6, 22.1], "wind_direction_10m": [71, 75], "cloud_cover": [10, 20]}}
    w = WX.from_openmeteo(data)
    assert w.records[0].ws == 20 and w.records[1].wd == 75 and w.elevation == 270


def test_update_mingguan_tandai_bekas_terbakar():
    from tamborafire import landscape as L
    import tomllib
    cfg = tomllib.load(open(os.path.join(ROOT, "config", "tambora.toml"), "rb"))
    cfg["aoi"]["res"] = 30
    n = 50
    fuel = np.full((n, n), 31, np.int16)
    fuel[:, :5] = 186
    lay = {"fuel": fuel}
    nbr0 = np.full((n, n), 0.30, np.float32)
    b8 = np.full((n, n), 3000.0, np.float32)
    b4 = np.full((n, n), 1500.0, np.float32)
    nbr_now = np.full((n, n), 0.18, np.float32)        # pengeringan musiman: dNBR 0,12 di mana-mana
    nbr_now[20:30, 20:30] = -0.05                      # kebakaran: dNBR 0,35 (9 ha)
    nbr_now[35:45, 35:45] = 0.0                        # derau satu jendela (mis. bayangan awan)
    b12 = b8 * (1 - nbr_now) / (1 + nbr_now)
    f1, st1, ndvi, dnbr, ref, info = L.update(lay, nbr0, dict(b04=b4, b08=b8, b12=b12), cfg)
    assert (st1[20:30, 20:30] == 2).all() and (f1[20:30, 20:30] == 31).all()   # baru kandidat
    assert info["kandidat_baru_ha"] == pytest.approx(18.0)
    assert ref[25, 25] == pytest.approx(0.18, abs=1e-4)   # acuan pra-kebakaran ditahan, dikoreksi musiman
    nbr2 = np.full((n, n), 0.17, np.float32)
    nbr2[20:30, 20:30] = -0.02                         # bekas arang bertahan
    b12 = b8 * (1 - nbr2) / (1 + nbr2)
    f2, scar, ndvi, dnbr, ref2, info = L.update(lay, ref, dict(b04=b4, b08=b8, b12=b12), cfg, st1)
    assert (f2[20:30, 20:30] == 99).all() and (f2[35:45, 35:45] == 31).all() and f2[0, 10] == 31
    assert info["bekas_terbakar_baru_ha"] == pytest.approx(9.0)
    assert info["kandidat_gugur_ha"] == pytest.approx(9.0)
    assert ref[0, 10] == pytest.approx(0.18, abs=1e-4)   # acuan bergulir diperbarui


def test_update_mingguan_dengan_hotspot():
    from tamborafire import landscape as L
    import tomllib
    cfg = tomllib.load(open(os.path.join(ROOT, "config", "tambora.toml"), "rb"))
    cfg["aoi"]["res"] = 30
    n = 50
    lay = {"fuel": np.full((n, n), 31, np.int16)}
    nbr0 = np.full((n, n), 0.30, np.float32)
    b8 = np.full((n, n), 3000.0, np.float32)
    b4 = np.full((n, n), 1500.0, np.float32)
    nbr_now = np.full((n, n), 0.25, np.float32)
    nbr_now[5:15, 5:15] = 0.0                          # kebakaran dengan hotspot
    nbr_now[30:40, 30:40] = 0.0                        # gelap tanpa hotspot -> diabaikan
    b12 = b8 * (1 - nbr_now) / (1 + nbr_now)
    hot = np.zeros((n, n), bool)
    hot[9, 9] = True
    f, st, ndvi, dnbr, ref, info = L.update(lay, nbr0, dict(b04=b4, b08=b8, b12=b12), cfg, hotspot=hot)
    assert (f[5:15, 5:15] == 99).all() and (st[30:40, 30:40] == 0).all()
    assert info["bekas_terbakar_baru_ha"] == pytest.approx(9.0)
    assert info["tanpa_hotspot_diabaikan_ha"] == pytest.approx(9.0)


def test_firms_mask():
    from tamborafire import firms
    from rasterio.transform import from_origin
    tr = from_origin(600000, 9090000, 10, 10)
    from pyproj import Transformer
    lon, lat = Transformer.from_crs("EPSG:32750", "EPSG:4326", always_xy=True).transform(600505, 9089495)
    m = firms.mask([dict(lat=lat, lon=lon)], (100, 100), tr, 32750, 100)
    assert m[50, 50] and m[50, 59] and not m[50, 62] and m.sum() > 250
