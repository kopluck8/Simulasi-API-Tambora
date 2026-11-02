"""Tes pemodelan terbalik (tamborafire.asal): baca titik panas, pengelompokan, simulasi mundur-maju, kalibrasi."""
import datetime as dt
import math
import os

import numpy as np
import pytest

from tamborafire import asal as A
from tamborafire import weather as WX
from tamborafire.lcp import Landscape, read_lcp

ROOT = os.path.dirname(os.path.dirname(__file__))
FMD20 = os.path.join(ROOT, "kalibrasi", "tambora_savana_cepat20.fmd")
FMS = os.path.join(ROOT, "kalibrasi", "tambora_latihan.fms")
LCP = os.path.join(ROOT, "contoh", "piong_9sep", "landscape_prakebakaran_9sep_30m.lcp")

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark id="placemark0"><name>Hotspot</name>
<description>Tanggal : 14 Oct 2026 13:16 WIB&lt;br&gt;Sumber  : NASA-NOAA20&lt;br&gt;Latitude  : -8.25850&lt;br&gt;Longitude  : 118.15060&lt;br&gt;Kepercayaan  : High&lt;br&gt;Desa  : Piong&lt;br&gt;Kecamatan  : Sanggar&lt;br&gt;Kota/Kabupaten  : Bima&lt;br&gt;Provinsi  : Nusa Tenggara Barat</description>
<Point><coordinates>118.15060,-8.25850</coordinates></Point></Placemark>
<Placemark id="placemark1"><name>Hotspot</name>
<description>Tanggal : 9 Sep 2026 01:52 WIB&lt;br&gt;Sumber  : LPN-MODIS&lt;br&gt;Kepercayaan  : Low&lt;br&gt;Desa  : Oi Bura</description>
<Point><coordinates>118.0, -8.3</coordinates></Point></Placemark>
</Document></kml>"""


def test_baca_kml_wib_ke_wita(tmp_path):
    p = tmp_path / "hotspot.kml"
    p.write_text(KML, encoding="utf-8")
    pts = A.baca_kml(str(p))
    assert len(pts) == 2
    a, b = pts
    assert a["t"] == dt.datetime(2026, 10, 14, 14, 16)          # 13:16 WIB = 14:16 WITA
    assert (a["sensor"], a["conf"], a["desa"], a["kab"]) == ("NOAA20", "H", "Piong", "Bima")
    assert a["lat"] == pytest.approx(-8.2585) and a["lon"] == pytest.approx(118.1506)
    assert b["t"] == dt.datetime(2026, 9, 9, 2, 52) and b["sensor"] == "MODIS" and b["conf"] == "L"


@pytest.mark.parametrize("teks,hasil", [
    ("14 Oct 2026 13:16 WIB", dt.datetime(2026, 10, 14, 14, 16)),
    ("1 Agu 2026 01:05 WITA", dt.datetime(2026, 8, 1, 1, 5)),
    ("2026-09-09 13:12", dt.datetime(2026, 9, 9, 13, 12)),
])
def test_waktu_teks(teks, hasil):
    assert A.waktu_teks(teks) == hasil


def test_simpan_dan_gabung_titik_panas(tmp_path):
    t = dt.datetime(2026, 9, 9, 13, 12)
    p1 = [dict(t=t, lat=-8.2585, lon=118.1506, sensor="SNPP", conf="H", desa="Piong", kec="", kab="Bima", src="SiPongi")]
    p2 = [dict(p1[0], t=t + dt.timedelta(minutes=3), src="FIRMS"),                       # duplikat (sumber lain)
          dict(p1[0], t=t + dt.timedelta(hours=12), sensor="NOAA20")]                     # titik baru
    gab = A.gabung(p1, p2)
    assert len(gab) == 2
    f = tmp_path / "titik.geojson"
    A.tulis_hotspot(gab, str(f), A.lintasan(gab), {"impor": []})
    pts, lin, meta = A.baca_hotspot(str(f))
    assert len(pts) == 2 and pts[0]["t"] == t and len(lin) == 2


def test_kelompokkan_jarak_dan_waktu():
    xy = np.array([[0, 0], [1000, 0], [5000, 0], [0, 200]], float)
    jam = np.array([0.0, 12.0, 12.0, 60.0])
    lab = A.kelompokkan(xy, jam)
    assert lab[0] == lab[1]           # 1 km, 12 jam -> satu kejadian
    assert lab[2] != lab[0]           # 5 km tanpa areal terbakar -> terpisah
    assert lab[3] != lab[0]           # 60 jam kemudian -> kejadian baru


class _Grid:
    """Grid kecil 30 m untuk tes (asal di pojok kiri atas = (0, 0), y ke atas negatif)."""
    res = 30.0
    ny, nx = 100, 300
    west, north = 0.0, 3000.0

    def rc(self, x, y):
        return ((self.north - np.asarray(y)) // self.res).astype(int), ((np.asarray(x) - self.west) // self.res).astype(int)


def test_kelompokkan_tersambung_areal_terbakar():
    """Api cepat: dua deteksi 5 km dan 6 jam terpisah tersambung bila garis di antaranya terbakar."""
    g = _Grid()
    xy = np.array([[500, 1500], [5500, 1500]], float)
    jam = np.array([0.0, 6.0])
    burn = np.zeros((g.ny, g.nx), bool)
    assert A.kelompokkan(xy, jam, burn, g).tolist() == [0, 1]
    burn[45:55, 10:190] = True
    assert A.kelompokkan(xy, jam, burn, g).tolist() == [0, 0]


def test_mutu_galat_nol_tetap_baik():
    h = dict(n_lintasan=3, sorensen=0.7, galat_waktu_jam=0.0)
    assert A._mutu(h) == "baik"
    assert A._mutu(dict(h, n_lintasan=1)) == "sedang"
    assert A._mutu(dict(h, galat_waktu_jam=None)) == "rendah"


def test_kalibrasi_memulihkan_faktor_per_kelas():
    benar = {"30": 0.6, "31": 0.8, "32": 1.25, "33": 1.6, "lain": 1.0}
    rng = np.random.default_rng(4)
    hasil = []
    for i in range(24):
        w = rng.dirichlet(np.ones(5) * 0.6)
        kom = dict(zip(["30", "31", "32", "33", "lain"], w.round(4)))
        k = math.exp(sum(math.log(benar[c]) * kom[c] for c in kom))
        hasil.append(dict(id=f"K{i}", tahap="bebas", sorensen=0.6, galat_waktu_jam=0.0, komposisi=kom,
                          k_p50=k, k_p05=k / 1.15, k_p95=k * 1.15))
    kal = A.kalibrasi(hasil)
    assert kal["cukup"] and kal["n"] == 24
    for c in ("31", "32", "33"):
        assert kal["kelas"][c]["faktor"] == pytest.approx(benar[c], rel=0.15)
        assert kal["kelas"][c]["p05"] <= kal["kelas"][c]["faktor"] <= kal["kelas"][c]["p95"]
    kc, ks = A.k_prior(kal, {"31": 1.0})
    assert kc == pytest.approx(kal["kelas"]["31"]["faktor"], rel=1e-6) and ks >= 0.3
    assert not A.kalibrasi(hasil[:2])["cukup"]


def _savana_datar(n=81, fuel=31):
    """Landscape datar seragam (savana K2) dengan atribut sama seperti contoh Piong 30 m."""
    ls0 = read_lcp(LCP)
    lay = {k: np.zeros((n, n), np.int16) for k in ls0.layers}
    lay["elev"][:] = 600
    lay["fuel"][:] = fuel
    return Landscape(ls0.west, ls0.north, 30.0, lay, crs_wkt=ls0.crs_wkt)


def _cuaca(ws=15, wd=90, mulai=dt.datetime(2026, 9, 1), jam=240):
    rec = [WX.WxRecord(mulai + dt.timedelta(hours=h), 30, 35, 0.0, ws, wd, 10) for h in range(jam)]
    return WX.Weather(600.0, rec)


def test_mundur_konsisten_dengan_maju():
    """B_i(x) dari simulasi mundur = jam deteksi - waktu tempuh maju dari x ke titik panas."""
    from tamborafire.simulate import Simulator
    ls = _savana_datar()
    sim = Simulator(ls, FMD20, FMS, crown=False)
    t0 = dt.datetime(2026, 9, 5, 0)
    md = A.Medan(sim, _cuaca(), t0, t0 + dt.timedelta(hours=12), angin="seragam")
    asal = np.zeros(ls.shape, bool)
    asal[40, 20] = True
    maju = md.maju(asal, 2.0, md.H, k=1.0)
    tujuan = np.zeros(ls.shape, bool)
    tujuan[40, 60] = True                      # 1,2 km ke barat (searah angin dari timur? angin DARI 90 = ke barat)
    t_tiba = float(maju[40, 60])
    assert np.isfinite(t_tiba) and t_tiba > 2.0
    B = md.mundur(tujuan, t_tiba, k=1.0)
    assert B[40, 20] == pytest.approx(2.0, abs=0.35)
    # faktor k: api 2x lebih cepat -> waktu tempuh setengahnya
    maju2 = md.maju(asal, 2.0, md.H, k=2.0)
    assert (maju2[40, 60] - 2.0) == pytest.approx((t_tiba - 2.0) / 2.0, rel=0.1)


def test_pita_piksel_dan_skor():
    """Titik panas cocok bila api menyala di pikselnya pada jam lintasan, dan galat bila api belum datang."""
    tiba = np.full((40, 40), np.inf)
    for c in range(40):
        tiba[:, c] = 1.0 + 0.1 * c             # muka api bergerak ke timur, 0,1 jam per sel
    rr, cc = np.mgrid[18:23, 18:23]                                # piksel 5 x 5 sel
    lo, hi = A.pita_piksel(tiba, [(rr.ravel(), cc.ravel())])
    assert lo[0] == pytest.approx(2.8) and hi[0] == pytest.approx(3.2)
    rsd = np.array([1.0])
    assert A._galat_pita(lo, hi, np.array([3.5]), rsd)[0] == 0.0               # masih menyala saat lintasan
    assert A._galat_pita(lo, hi, np.array([1.0]), rsd)[0] == pytest.approx(-1.55)  # simulasi terlambat
    assert A._galat_pita(lo, hi, np.array([6.0]), rsd)[0] == pytest.approx(1.8)    # simulasi terlalu awal


def test_rinci_lain_menamai_kode_fuel():
    """Kelas 'lain' di kalibrasi dirinci per kode fuel (hanya kejadian kalibrasi, tanpa K1-K4)."""
    class _Ctx:
        ls10_fuel30 = np.array([[31, 161, 161, 186], [32, 161, 93, 30], [161, 161, 186, 99]], np.int16)
    m1 = np.zeros((3, 4), bool)
    m1[:2, :] = True                      # 31,161,161,186,32,161,93,30 -> lain: 161 x3, 186, 93
    m2 = np.zeros((3, 4), bool)
    m2[2, :] = True                       # tidak dipakai kalibrasi
    evs = [dict(id="A", mask=m1), dict(id="B", mask=m2)]
    r = A.rinci_lain(_Ctx(), evs, {"A"})
    assert [x["kode"] for x in r] == [161, 93, 186]          # seri diurutkan menurut kode
    assert r[0]["nama"] == "TU1" and r[0]["persen"] == pytest.approx(60.0)
    assert r[1]["nama"] == "pertanian NB3" and r[2]["nama"] == "TL6"
    assert A.rinci_lain(_Ctx(), evs, set()) == []
