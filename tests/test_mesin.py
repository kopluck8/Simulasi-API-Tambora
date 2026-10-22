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
    nbr_now = np.full((n, n), 0.30, np.float32)
    nbr_now[20:30, 20:30] = 0.05                       # dNBR 0,25: terbakar (9 ha)
    b12 = b8 * (1 - nbr_now) / (1 + nbr_now)
    f2, scar, ndvi, dnbr, info = L.update(lay, nbr0, dict(b04=b4, b08=b8, b12=b12), cfg)
    assert (f2[20:30, 20:30] == 99).all() and f2[0, 10] == 31
    assert info["bekas_terbakar_baru_ha"] == pytest.approx(9.0)
