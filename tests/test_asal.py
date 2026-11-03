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


def test_zona_luar_tn_1500_m():
    """Kejadian di luar TN hanya dihitung bila titik panas atau areal terbakarnya <= 1,5 km dari batas TN."""
    cfg = {"aoi": {"epsg": 32750, "west": 0, "north": 3000, "east": 9000, "south": 0}}
    g = A.Grid(cfg, 30.0)                                   # 300 x 100 sel, TN = x < 3 km
    tn = np.zeros(g.shape, bool)
    tn[:, :100] = True
    burn = np.zeros(g.shape, bool)
    burn[45:55, 25:40] = True                               # A: di dalam TN
    burn[10:20, 135:145] = True                             # B: 1,05-1,35 km di luar TN
    burn[45:55, 140:240] = True                             # C: titik panas 4 km di luar, areal sampai 1,2 km
    burn[45:55, 262:272] = True                             # D: 4,9 km di luar TN
    t0 = dt.datetime(2026, 9, 1, 13)
    xy = [(1000, 1500), (4200, 2535), (6500, 1500), (8000, 1500)]

    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.g, ctx.tn, ctx.burn = g, tn, burn
    ctx.xy = np.array(xy, float)
    ctx.jam = np.array([0.0, 100.0, 200.0, 300.0])
    ctx.hs = [dict(t=t0 + dt.timedelta(hours=float(j)), conf="H", sensor="NOAA20", lat=-8.2, lon=118.0) for j in ctx.jam]
    ctx.ll = lambda x, y: (-8.2, 118.0)
    evs, info = A.siapkan(ctx, min_ha=1.0, zona_m=1500, log=lambda *a: None)
    assert [ev["zona"] for ev in evs] == [True, True, True, False]
    assert [ev["layak"] for ev in evs] == [True, True, True, False]
    assert info["kejadian_zona"] == 3 and info["kejadian_luar_zona"] == 1
    assert [p["zona"] for p in ctx.hs] == [True, True, False, False]
    assert evs[2]["luas_ha"] > 8                            # areal kejadian C tetap utuh (bagian > 1,5 km ikut)
    evs2, info2 = A.siapkan(ctx, min_ha=1.0, zona_m=None, log=lambda *a: None)
    assert all(ev["layak"] for ev in evs2) and "kejadian_zona" not in info2


def test_laju_api_per_jam():
    """Muka api ke timur 0,1 jam per sel 30 m = 30 m / 6 menit = 5 m/menit, dirangkum per jam WITA."""
    tiba = np.full((20, 40), np.nan)
    for c in range(40):
        tiba[:, c] = 1.0 + 0.1 * c                     # jam tiba 1,0 - 4,9 (relatif j0)
    areal = np.ones(tiba.shape, bool)
    j0 = 24 * 100 + 10                                 # jam 10:00 WITA
    r = A.laju_api(tiba, areal, 30.0, j0)
    assert r["rata"] == pytest.approx(5.0, rel=1e-6) and r["kepala"] == pytest.approx(5.0, rel=1e-6)
    assert [h[0] for h in r["jam"]] == [11, 12, 13, 14]
    assert all(h[1] == pytest.approx(5.0) for h in r["jam"])
    diag = np.add.outer(np.arange(20), np.arange(40)) * 0.1
    r2 = A.laju_api(diag, areal, 30.0, 0)
    assert r2["rata"] == pytest.approx(30.0 / (6.0 * math.sqrt(2)), abs=0.01)
    datar = np.ones((10, 10))                          # tanpa gradien (sel menyala bersamaan) -> diabaikan
    assert A.laju_api(datar, np.ones((10, 10), bool), 30.0, 0) is None


def _kotak_ll(g, x0, y0, x1, y1):
    xs, ys = [x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0]
    lon, lat = g.ke_ll.transform(xs, ys)
    return [[round(a, 8), round(b, 8)] for a, b in zip(lon, lat)]


def test_bidang_bersebelahan_beda_bulan(tmp_path):
    """Bidang delineasi tidak digabung: bidang A (Agustus) dan B (Oktober) bersinggungan, titik panas Oktober yang
    'tumpah' ke tepi A tidak mengambil A. Waktu terbakar bidang dari titik panas di dalamnya."""
    import json
    cfg = {"aoi": {"epsg": 32750, "west": 600000, "north": 9090000, "east": 609000, "south": 9087000}}
    g = A.Grid(cfg, 30.0)
    kotak = [(600600, 9088200, 601800, 9089400),     # B001 = A, 144 ha
             (601800, 9088200, 603000, 9089400),     # B002 = B, bersinggungan dengan A
             (604000, 9088500, 604600, 9089100),     # B003 = C, titik panas hanya 200 m di luar
             (607000, 9088500, 607600, 9089100),     # B004 = D, tanpa titik panas
             (605510, 9088010, 605530, 9088030),     # B005 = E, 0,04 ha (lebih kecil dari sel 30 m)
             (603200, 9087100, 605000, 9088100),     # B006 = F, 180 ha, dua api berbeda bulan di dalamnya
             (605800, 9087100, 606800, 9088100)]     # B007 = G, api Juni + api Agustus di tepi timur (tanpa bidang lain)
    fc = dict(type="FeatureCollection", features=[
        dict(type="Feature", properties=dict(id=f"B{i:03d}", luas_ha=round((k[2] - k[0]) * (k[3] - k[1]) / 1e4, 2)),
             geometry=dict(type="Polygon", coordinates=[_kotak_ll(g, *k)])) for i, k in enumerate(kotak, 1)])
    pb = tmp_path / "bidang.geojson"
    pb.write_text(json.dumps(fc))
    tn = np.ones(g.shape, bool)
    burn, info_b, satuan = A.areal_terbakar(g, None, tn, None, bidang=str(pb), log=lambda *a: None)
    assert info_b["bidang"] == 7 and satuan["n_bidang"] == 7
    assert (satuan["lab"] == 1).sum() == 1600 and (satuan["lab"] == 2).sum() == 1600 and not (satuan["lab"] == 5).any()
    t = lambda s: dt.datetime.fromisoformat(s)
    titik = [((601200, 9088800), "2026-08-10T13:00"), ((601000, 9088600), "2026-08-10T13:00"),
             ((601300, 9089000), "2026-08-11T01:30"),                                    # A dalam (Agustus)
             ((602400, 9088800), "2026-10-05T13:10"), ((602500, 9088700), "2026-10-05T13:10"),   # B dalam (Oktober)
             ((601780, 9088800), "2026-10-05T13:10"), ((601790, 9088900), "2026-10-05T13:10"),
             ((601760, 9088700), "2026-10-06T01:40"), ((601700, 9088800), "2026-10-06T01:40"),
             ((601650, 9088600), "2026-10-06T01:40"), ((601690, 9089000), "2026-10-06T01:40"),   # Oktober tumpah ke tepi A
             ((603600, 9087600), "2026-07-01T13:00"), ((603700, 9087500), "2026-07-01T13:00"),   # F barat (Juli)
             ((604600, 9087600), "2026-09-20T13:30"), ((604500, 9087700), "2026-09-20T13:30"),   # F timur (September)
             ((606300, 9087600), "2026-06-10T13:00"), ((606350, 9087550), "2026-06-10T13:00"),   # G tengah (Juni)
             ((606750, 9087400), "2026-08-25T01:30"), ((606740, 9087600), "2026-08-25T01:30"),
             ((606720, 9087800), "2026-08-25T01:30"), ((606700, 9087500), "2026-08-25T01:30"),   # G tepi timur (Agustus)
             ((604800, 9088800), "2026-08-20T13:05"),                                    # dekat C
             ((605520, 9088020), "2026-09-15T13:20")]                                    # di bidang kecil E

    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.g, ctx.tn, ctx.burn, ctx.satuan, ctx.lapis = g, tn, burn, satuan, {}
    ctx.xy = np.array([p for p, _ in titik], float)
    ctx.hs = [dict(t=t(s), conf="H", sensor="NOAA20", lat=-8.2, lon=118.0) for _, s in titik]
    ctx.jam = np.array([(p["t"] - dt.datetime(2000, 1, 1)).total_seconds() / 3600 for p in ctx.hs])
    ctx.ll = lambda x, y: (-8.2, 118.0)
    evs, info = A.siapkan(ctx, min_ha=1.0, log=lambda *a: None)
    per = {ev["id"]: ev for ev in evs}
    assert sorted(per) == ["K20260610-1300", "K20260701-1300", "K20260810-1300", "K20260820-1305", "K20260825-0130",
                           "K20260915-1320", "K20260920-1330", "K20261005-1310"]
    assert per["K20260810-1300"]["bidang"] == ["B001"] and per["K20261005-1310"]["bidang"] == ["B002"]
    assert per["K20260820-1305"]["bidang"] == ["B003"] and per["K20260915-1320"]["bidang"] == ["B005"]
    assert per["K20260810-1300"]["luas_ha"] == pytest.approx(144.0) and per["K20261005-1310"]["luas_ha"] == pytest.approx(144.0)
    assert per["K20260701-1300"]["bidang"] == ["B006"] and per["K20260920-1330"]["bidang"] == ["B006"]
    luas_f = (satuan["lab"] == 6).sum() * 0.09                        # luas F di grid 30 m
    assert per["K20260701-1300"]["luas_ha"] + per["K20260920-1330"]["luas_ha"] == pytest.approx(luas_f)
    assert 60 < per["K20260701-1300"]["luas_ha"] < 120                 # F dibagi: dua kejadian kuat
    assert per["K20260610-1300"]["bidang"] == ["B007"] and per["K20260825-0130"]["bidang"] == ["B007"]
    assert 0 < per["K20260825-0130"]["luas_ha"] < per["K20260610-1300"]["luas_ha"]   # api Agustus: bagian timur G
    assert per["K20261005-1310"]["bidang"] == ["B002"]                 # limpahan Oktober di tepi A tidak mengambil A
    assert info["bidang"] == 7 and info["bidang_titik_di_dalam"] == 5 and info["bidang_titik_dekat"] == 1
    assert info["bidang_tanpa_titik"] == 1
    wb = A.waktu_bidang(ctx, evs, {}, [])
    a, b, c, d, e, f, g7 = (wb[f"B00{i}"] for i in range(1, 8))
    assert a["kejadian"] == "K20260810-1300" and a["sumber"] == "titik panas di bidang" and a["n_titik"] == 3
    assert (a["waktu_awal"], a["waktu_akhir"]) == ("2026-08-10T13:00", "2026-08-11T01:30")
    assert a["titik_kejadian_lain"] == [dict(kejadian="K20261005-1310", n=6, awal="2026-10-05T13:10")]
    assert "bagian" not in a and a["luas_ha"] == 144.0
    assert b["kejadian"] == "K20261005-1310" and (b["waktu_awal"], b["n_titik"]) == ("2026-10-05T13:10", 2)
    assert c["sumber"] == "titik panas dekat (≤ 400 m)" and c["waktu_awal"] == "2026-08-20T13:05" and c["n_titik"] == 0
    assert d["sumber"] == "tanpa titik panas" and d["kejadian"] is None and "waktu_awal" not in d
    assert e["kejadian"] == "K20260915-1320" and e["sumber"] == "titik panas di bidang"
    assert f["sumber"] == "titik panas di bidang (2 kejadian)" and f["luas_ha"] == 180.0
    assert [x["kejadian"] for x in f["bagian"]] == ["K20260701-1300", "K20260920-1330"]
    assert (f["waktu_awal"], f["waktu_akhir"]) == ("2026-07-01T13:00", "2026-09-20T13:30")
    assert sum(x["luas_bagian_ha"] for x in f["bagian"]) == pytest.approx(luas_f)
    assert [x["kejadian"] for x in g7["bagian"]] == ["K20260610-1300", "K20260825-0130"] and g7["kejadian"] == "K20260610-1300"
    # tanpa file bidang: komponen terhubung (A+B jadi satu) dibagi ke titik panas terdekat seperti sebelumnya
    ctx.satuan = None
    evs2, info2 = A.siapkan(ctx, min_ha=1.0, log=lambda *a: None)
    assert all(ev["bidang"] == [] for ev in evs2) and "bidang" not in info2


def test_tiba_bidang_dari_simulasi():
    """Jam tiba api simulasi per bidang (p10/p50/p90 WITA) dan persen sel bidang yang tercapai."""
    lab = np.zeros((10, 40), np.int32)
    lab[:, :20], lab[:, 20:] = 1, 2

    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.satuan = dict(lab=lab, n_bidang=2, props=[dict(id="B001"), dict(id="B002")])
    ctx.jam_ke_waktu = lambda j: dt.datetime(2000, 1, 1) + dt.timedelta(hours=float(j))
    tiba = np.tile(np.arange(40) * 0.1, (10, 1))
    tiba[:, 30:] = np.nan                               # separuh B002 tidak tercapai simulasi
    j0 = (dt.datetime(2026, 8, 10, 10) - dt.datetime(2000, 1, 1)).total_seconds() / 3600
    bt = A.tiba_bidang(ctx, tiba, lab > 0, (0, 10, 0, 40, 1), j0)
    assert bt["B001"]["tercapai"] == 100.0 and bt["B002"]["tercapai"] == 50.0
    assert bt["B001"]["awal"] < bt["B001"]["tengah"] < bt["B001"]["akhir"] <= bt["B002"]["awal"]
    assert bt["B001"]["tengah"] == "2026-08-10T10:57"
    ctx.satuan = None
    assert A.tiba_bidang(ctx, tiba, lab > 0, (0, 10, 0, 40, 1), j0) is None


@pytest.mark.parametrize("asli,bulan,tanggal", [
    ({"Bulan": "Agustus", "Tahun": "2026"}, "2026-08", None),
    ({"BULAN": "8"}, "2026-08", None),
    ({"bulan": "Sept."}, "2026-09", None),
    ({"Tanggal": "27", "Bulan": "Juni", "Tahun": 2026}, "2026-06", "2026-06-27"),
    ({"tgl": "2026-07-05"}, "2026-07", "2026-07-05"),
    ({"Tanggal": "05/09/2026"}, "2026-09", "2026-09-05"),
    ({"Bulan": "", "Tahun": "2026"}, None, None),
    ({"X": 0.0}, None, None),
])
def test_waktu_shp(asli, bulan, tanggal):
    w = A.waktu_shp(asli, 2026)
    assert (w["bulan"] if w else None) == bulan and (w["tanggal"] if w else None) == tanggal
    if w and tanggal:
        assert (w["akhir"] - w["awal"]).days == 11             # +-5 hari dari tanggal tercatat


def test_bidang_mengikuti_bulan_di_shp(tmp_path):
    """Bila SHP mencatat bulan terbakar, kejadian pada bulan itu yang memiliki bidang walaupun kejadian lain lebih
    banyak titiknya di dalam bidang; bila tidak ada kejadian pada bulan itu, aturan titik panas biasa dan ditandai beda."""
    import json
    cfg = {"aoi": {"epsg": 32750, "west": 600000, "north": 9090000, "east": 609000, "south": 9087000}}
    g = A.Grid(cfg, 30.0)
    kotak = [((600600, 9088200, 601800, 9089400), {"Bulan": "Juni", "Tahun": "2026"}),
             ((601800, 9088200, 603000, 9089400), {"Bulan": "Agustus"})]
    fc = dict(type="FeatureCollection", features=[
        dict(type="Feature", properties=dict(id=f"B{i:03d}", luas_ha=144.0, tanggal="2026-10-30", asli=a),
             geometry=dict(type="Polygon", coordinates=[_kotak_ll(g, *k)])) for i, (k, a) in enumerate(kotak, 1)])
    pb = tmp_path / "bidang.geojson"
    pb.write_text(json.dumps(fc))
    tn = np.ones(g.shape, bool)
    burn, info_b, satuan = A.areal_terbakar(g, None, tn, None, bidang=str(pb), log=lambda *a: None)
    assert info_b["bidang_bulan_shp"] == 2 and satuan["shp"][1]["bulan"] == "2026-08"
    titik = [((601200, 9088800), "2026-06-15T13:00"),                                          # Juni, 1 titik
             ((601000, 9088600), "2026-07-20T13:00"), ((601300, 9089000), "2026-07-20T13:00"),
             ((601400, 9088700), "2026-07-20T13:00"),                                          # Juli, 3 titik
             ((602400, 9088800), "2026-10-05T13:10")]                                          # B002: hanya Oktober

    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.g, ctx.tn, ctx.burn, ctx.satuan, ctx.lapis = g, tn, burn, satuan, {}
    ctx.xy = np.array([p for p, _ in titik], float)
    ctx.hs = [dict(t=dt.datetime.fromisoformat(s), conf="H", sensor="NOAA20", lat=-8.2, lon=118.0) for _, s in titik]
    ctx.jam = np.array([A.jam_sejak_2000(p["t"]) for p in ctx.hs])
    ctx.ll = lambda x, y: (-8.2, 118.0)
    evs, info = A.siapkan(ctx, min_ha=1.0, log=lambda *a: None)
    per = {ev["id"]: ev for ev in evs}
    assert per["K20260615-1300"]["bidang"] == ["B001"] and per["K20260615-1300"]["luas_ha"] == pytest.approx(144.0)
    assert per["K20260720-1300"]["bidang"] == [] and per["K20260720-1300"]["luas_ha"] == 0
    assert per["K20261005-1310"]["bidang"] == ["B002"]
    assert info["bidang_cocok_shp"] == 1 and info["bidang_beda_shp"] == 1
    wb = A.waktu_bidang(ctx, evs, {}, [])
    a, b = wb["B001"], wb["B002"]
    assert a["kejadian"] == "K20260615-1300" and a["cocok_shp"] is True and a["bulan_shp"] == "2026-06"
    assert a["waktu_awal"] == "2026-06-15T13:00" and a["titik_kejadian_lain"][0]["kejadian"] == "K20260720-1300"
    assert b["kejadian"] == "K20261005-1310" and b["cocok_shp"] is False and b["bulan_shp"] == "2026-08"
