"""Pemodelan terbalik asal api (inverse modelling): dari mana dan pukul berapa api mulai.

Masukan: titik panas VIIRS/MODIS (SiPongi KMZ dan/atau NASA FIRMS) dengan jam deteksinya, areal terbakar
hasil delineasi (ditambah bekas terbakar Sentinel-2 di luar kawasan bila ada), landscape awal musim,
fmd/fms kalibrasi, dan cuaca per jam ERA5/ERA5-Land dari Open-Meteo (sumber yang sama dengan generator .wxs).

Langkah:
1. Titik panas dibersihkan (WIB -> WITA; kepercayaan rendah hanya dipakai bila dekat areal terbakar) dan
   dikelompokkan per kejadian: dua titik terhubung bila berjarak <= 1,5 km dan selisih waktunya <= 36 jam.
2. Areal terbakar dibagi ke kejadian menurut titik panas terdekat di poligon yang sama.
3. Per kejadian: grid laju api (ROS), arah kepala, dan eksentrisitas per jam dihitung sekali dengan Rothermel,
   kadar air terkondisi cuaca per jam, dan angin medan, persis seperti mode FARSITE.
4. Simulasi mundur dari tiap titik panas terpilih (waktu dibalik, arah kepala api diputar 180°) memberi
   B_i(x): waktu paling lambat api berada di sel x agar sampai di titik panas i pada jam deteksinya.
   Bila api mulai di x pada t0, r_i = B_i(x) - t0 adalah jeda deteksi yang harus >= 0 dan kecil.
   Skor waktu dihitung untuk semua sel sekaligus, untuk beberapa faktor laju api k.
5. Simulasi maju dari kandidat terbaik (titik, t0, k) dinilai dengan jam deteksi dan bentuk areal terbakar
   (Sørensen luas sama). Hasilnya peluang titik asal (zona 50% dan 90%), jam mulai dengan selang 90%,
   arah datang api, dan faktor k.
6. Faktor k dari kejadian yang terlihat pada >= 2 lintasan satelit digabung per kelas bahan bakar
   (regresi log k terhadap komposisi K1-K4 areal terbakarnya) menjadi saran ros_adjust.

Semua waktu di modul ini memakai WITA (UTC+8) tanpa zona (naive datetime).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import zipfile

import numpy as np
from numba import njit

VERSI = 1
ZONA_JAM = {"WIB": 7, "WITA": 8, "WIT": 9, "UTC": 0, "GMT": 0, "Z": 0}
_BULAN = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_BULAN.update({"mei": 5, "agu": 8, "agt": 8, "okt": 10, "des": 12, "nop": 11})
PIKSEL_M = {"MODIS": 1000.0}      # lainnya VIIRS 375 m
RADIUS_M = {"MODIS": 500.0}       # jari-jari sel awal simulasi mundur; VIIRS 190 m
NONBAKAR = set(range(91, 100))


# =============================================================== titik panas
def _sensor(s):
    u = str(s).upper().replace("-", "_")
    for k in ("NOAA21", "NOAA20", "SNPP", "MODIS"):
        if k in u.replace("_", ""):
            return k
    if "NPP" in u:
        return "SNPP"
    if "TERRA" in u or "AQUA" in u:
        return "MODIS"
    return u.strip() or "?"


def _conf(s):
    u = str(s).strip().lower()
    if not u:
        return "M"
    try:
        v = float(u)
        return "L" if v < 30 else ("M" if v < 80 else "H")
    except ValueError:
        pass
    if u[0] in "lr":      # low, rendah
        return "L"
    if u[0] in "ht":      # high, tinggi
        return "H"
    return "M"            # medium, nominal, sedang


def waktu_teks(s):
    """'14 Oct 2026 13:16 WIB' -> datetime WITA."""
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\s+(\d{4})\s+(\d{1,2})[:.](\d{2})(?::\d{2})?\s*([A-Za-z]*)", s)
    if not m:
        m2 = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", s)
        if not m2:
            raise ValueError("format waktu tidak dikenal: " + s)
        t = dt.datetime(*map(int, m2.groups()))
        return t
    d, mo, y, hh, mi, z = m.groups()
    t = dt.datetime(int(y), _BULAN[mo.lower()[:3]], int(d), int(hh), int(mi))
    return t + dt.timedelta(hours=8 - ZONA_JAM.get(z.upper() or "WIB", 7))


def baca_kml(path):
    """KMZ/KML titik panas SiPongi. Mengembalikan list dict (t WITA, lat, lon, sensor, conf, desa, kec, kab)."""
    if str(path).lower().endswith(".kmz"):
        with zipfile.ZipFile(path) as z:
            nama = next(n for n in z.namelist() if n.lower().endswith(".kml"))
            teks = z.read(nama).decode("utf-8", "replace")
    else:
        teks = open(path, encoding="utf-8", errors="replace").read()
    out = []
    for pm in re.finditer(r"<Placemark\b.*?</Placemark>", teks, re.S):
        blk = pm.group(0)
        d = re.search(r"<description>(.*?)</description>", blk, re.S)
        c = re.search(r"<coordinates>\s*([-\d.]+)\s*,\s*([-\d.]+)", blk)
        if not d or not c:
            continue
        desc = re.sub(r"<!\[CDATA\[|\]\]>", "", html.unescape(d.group(1)))
        f = {}
        for part in re.split(r"<br\s*/?>|\n", desc, flags=re.I):
            part = re.sub(r"<[^>]+>", "", part)
            if ":" in part:
                k, v = part.split(":", 1)
                f[k.strip().lower()] = v.strip()
        if "tanggal" not in f:
            continue
        try:
            t = waktu_teks(f["tanggal"])
        except (ValueError, KeyError):
            continue
        out.append(dict(t=t, lat=float(c.group(2)), lon=float(c.group(1)), sensor=_sensor(f.get("sumber", "")),
                        conf=_conf(f.get("kepercayaan", "")), desa=f.get("desa", ""), kec=f.get("kecamatan", ""),
                        kab=f.get("kota/kabupaten", ""), src="SiPongi"))
    return out


def baca_csv(path):
    """CSV FIRMS (latitude, longitude, acq_date, acq_time UTC, satellite/instrument, confidence)."""
    import csv
    out = []
    for row in csv.DictReader(open(path, encoding="utf-8-sig")):
        p = _dari_firms_row(row, row.get("instrument", "") + row.get("satellite", ""))
        if p:
            out.append(p)
    return out


def _dari_firms_row(row, sumber):
    try:
        lat, lon = float(row["latitude"]), float(row["longitude"])
        d = dt.date.fromisoformat(row["acq_date"])
        hm = str(row.get("acq_time", "0")).strip().zfill(4)
    except (KeyError, ValueError):
        return None
    t = dt.datetime(d.year, d.month, d.day, int(hm[:2]), int(hm[2:])) + dt.timedelta(hours=8)
    s = _sensor(sumber)
    if s in ("?", "") or s == "VIIRS":
        s = _sensor(row.get("satellite", "")) if row.get("satellite") else s
        s = {"N": "SNPP", "N20": "NOAA20", "N21": "NOAA21", "1": "NOAA20", "T": "MODIS", "A": "MODIS"}.get(
            str(row.get("satellite", "")).strip(), s)
    return dict(t=t, lat=lat, lon=lon, sensor=s, conf=_conf(row.get("confidence", "")), desa="", kec="", kab="",
                src="FIRMS")


def ambil_firms(bbox, mulai: dt.date, sampai: dt.date, sumber=None, log=print):
    """Titik panas dengan jam deteksi dari NASA FIRMS (perlu FIRMS_MAP_KEY). bbox = 'barat,selatan,timur,utara'."""
    import csv
    import io
    import requests
    from . import firms
    k = firms.key()
    if not k:
        raise RuntimeError("FIRMS_MAP_KEY kosong")
    if not sumber:
        sumber = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT"]
        if (dt.date.today() - mulai).days > 60:      # arsip NRT hanya ~2 bulan; data lama dari arsip standar (SP)
            sumber += ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "MODIS_SP"]
    out, gagal = [], {}
    for src in sumber:
        d = mulai
        while d <= sampai:
            n = min(5, (sampai - d).days + 1)
            try:
                r = requests.get(firms.API.format(key=k, src=src, bbox=bbox, days=n, date=d.isoformat()), timeout=90)
                txt = r.text.strip()
                if r.status_code != 200 or txt.lower().startswith(("invalid", "error")):
                    raise RuntimeError(f"{r.status_code} {txt[:120]}")
                for row in csv.DictReader(io.StringIO(txt)):
                    p = _dari_firms_row(row, src)
                    if p:
                        out.append(p)
            except Exception as e:  # satu sumber/periode gagal tidak menghentikan semua
                gagal.setdefault(src, str(e))
            d += dt.timedelta(days=n)
    for s, e in gagal.items():
        log(f"   FIRMS {s}: sebagian gagal ({e})")
    return out


def _xy_kasar(lat, lon):
    lat = np.asarray(lat, float)
    return np.column_stack([np.asarray(lon, float) * 111320.0 * math.cos(math.radians(-8.25)), lat * 110540.0])


def gabung(lama, baru, dt_menit=6, jarak_m=60):
    """Tambahkan titik baru yang belum ada. Duplikat = deteksi yang sama dari sumber lain (SiPongi vs FIRMS):
    sensor sama, beda waktu <= 6 menit dan jarak <= 60 m. Piksel VIIRS bertetangga (>= ~200 m) tetap dua titik."""
    from scipy.spatial import cKDTree
    if not lama:
        return sorted(baru, key=lambda p: p["t"])
    if not baru:
        return sorted(lama, key=lambda p: p["t"])
    xy = _xy_kasar([p["lat"] for p in lama], [p["lon"] for p in lama])
    tree = cKDTree(xy)
    out = list(lama)
    xb = _xy_kasar([p["lat"] for p in baru], [p["lon"] for p in baru])
    for p, q in zip(baru, xb):
        dup = False
        for j in tree.query_ball_point(q, jarak_m):
            o = lama[j]
            if o["sensor"] == p["sensor"] and abs((o["t"] - p["t"]).total_seconds()) <= dt_menit * 60:
                dup = True
                break
        if not dup:
            out.append(p)
    return sorted(out, key=lambda p: p["t"])


def lintasan(points, jeda_menit=20):
    """Waktu lintasan satelit (per sensor) dari waktu deteksi di seluruh wilayah data."""
    per = {}
    for p in points:
        per.setdefault(p["sensor"], []).append(p["t"])
    out = []
    for s, ts in per.items():
        ts.sort()
        grup = [[ts[0]]]
        for t in ts[1:]:
            if (t - grup[-1][-1]).total_seconds() <= jeda_menit * 60:
                grup[-1].append(t)
            else:
                grup.append([t])
        out.extend((s, g[len(g) // 2]) for g in grup)
    out.sort(key=lambda x: x[1])
    return out


def tulis_hotspot(points, path, lintas=None, meta=None):
    feats = []
    for p in points:
        pr = dict(t=p["t"].strftime("%Y-%m-%dT%H:%M"), s=p["sensor"], c=p["conf"], src=p.get("src", ""))
        for k in ("desa", "kec", "kab"):
            if p.get(k):
                pr[k] = p[k]
        for k in ("ev", "tn"):
            if p.get(k) is not None:
                pr[k] = p[k]
        feats.append(dict(type="Feature", geometry=dict(type="Point", coordinates=[round(p["lon"], 5), round(p["lat"], 5)]),
                          properties=pr))
    d = dict(type="FeatureCollection", zona="WITA", features=feats)
    if meta:
        d["meta"] = meta
    if lintas is not None:
        d["lintasan"] = {}
        for s, t in lintas:
            d["lintasan"].setdefault(s, []).append(t.strftime("%Y-%m-%dT%H:%M"))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))


def baca_hotspot(path):
    d = json.load(open(path, encoding="utf-8"))
    pts = []
    for f in d.get("features", []):
        pr = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        pts.append(dict(t=dt.datetime.fromisoformat(pr["t"]), lat=lat, lon=lon, sensor=pr.get("s", "?"),
                        conf=pr.get("c", "M"), desa=pr.get("desa", ""), kec=pr.get("kec", ""), kab=pr.get("kab", ""),
                        src=pr.get("src", "")))
    lin = [(s, dt.datetime.fromisoformat(t)) for s, ts in (d.get("lintasan") or {}).items() for t in ts]
    lin.sort(key=lambda x: x[1])
    return pts, lin, d.get("meta", {})


def gabung_lintasan(a, b):
    """Gabung daftar lintasan; lintasan sensor sama yang berselisih <= 20 menit dianggap sama."""
    out = sorted(a + b, key=lambda x: x[1])
    res = []
    for s, t in out:
        if any(s == s2 and abs((t - t2).total_seconds()) <= 1200 for s2, t2 in res[-8:]):
            continue
        res.append((s, t))
    return res


# =============================================================== grid AOI & areal terbakar
class Grid:
    """Grid AOI (UTM) untuk pembagian areal terbakar ke kejadian."""

    def __init__(self, cfg, res=30.0):
        a = cfg["aoi"]
        self.epsg = int(a["epsg"])
        self.west, self.north, self.res = float(a["west"]), float(a["north"]), float(res)
        self.nx = int((float(a["east"]) - self.west) // res)
        self.ny = int((self.north - float(a["south"])) // res)
        from pyproj import Transformer
        self.ke_utm = Transformer.from_crs(4326, self.epsg, always_xy=True)
        self.ke_ll = Transformer.from_crs(self.epsg, 4326, always_xy=True)

    @property
    def shape(self):
        return self.ny, self.nx

    @property
    def transform(self):
        from rasterio.transform import from_origin
        return from_origin(self.west, self.north, self.res, self.res)

    def rc(self, x, y):
        return (np.floor((self.north - np.asarray(y)) / self.res).astype(int),
                np.floor((np.asarray(x) - self.west) / self.res).astype(int))

    def xy(self, r, c):
        return self.west + (np.asarray(c) + 0.5) * self.res, self.north - (np.asarray(r) + 0.5) * self.res


def _geojson_utm(path, tf):
    from shapely.geometry import shape
    import shapely
    d = json.load(open(path, encoding="utf-8"))
    out = []
    for f in d.get("features", []):
        g = shape(f["geometry"])
        out.append((shapely.transform(g, lambda a: np.column_stack(tf.transform(a[:, 0], a[:, 1]))), f.get("properties", {})))
    return out


def raster_poligon(path, grid):
    from rasterio.features import rasterize
    geoms = [g for g, _ in _geojson_utm(path, grid.ke_utm)]
    if not geoms:
        return np.zeros(grid.shape, bool)
    return rasterize([(g, 1) for g in geoms], out_shape=grid.shape, transform=grid.transform).astype(bool)


def areal_terbakar(grid, delineasi, tn_mask=None, s2_tif=None, log=print):
    """Maska terbakar: delineasi manual, ditambah bekas terbakar Sentinel-2 (nilai 1) di luar kawasan TN."""
    m = raster_poligon(delineasi, grid) if delineasi and os.path.exists(delineasi) else np.zeros(grid.shape, bool)
    info = dict(delineasi_ha=round(float(m.sum() * grid.res ** 2 / 1e4), 1))
    if s2_tif and os.path.exists(s2_tif):
        import rasterio
        from rasterio.warp import reproject, Resampling
        dst = np.zeros(grid.shape, np.uint8)
        with rasterio.open(s2_tif) as d:
            src = (d.read(1) == 1).astype(np.uint8)
            reproject(src, dst, src_transform=d.transform, src_crs=d.crs, dst_transform=grid.transform,
                      dst_crs=f"EPSG:{grid.epsg}", resampling=Resampling.max)
        s2 = dst.astype(bool)
        if tn_mask is not None:
            s2 &= ~tn_mask
        info["s2_luar_tn_ha"] = round(float((s2 & ~m).sum() * grid.res ** 2 / 1e4), 1)
        m |= s2
    log(f"areal terbakar: delineasi {info['delineasi_ha']} ha" +
        (f", + Sentinel-2 di luar TN {info['s2_luar_tn_ha']} ha" if "s2_luar_tn_ha" in info else ""))
    return m, info


# =============================================================== kejadian
def kelompokkan(xy, jam, burn=None, grid=None, jarak_m=1500.0, jeda_jam=36.0, v_kmj=0.6, maks_m=12000.0, porsi=0.8):
    """Komponen terhubung titik panas. Dua titik terhubung bila berjarak <= jarak_m dan selisih waktunya
    <= jeda_jam, atau (api yang merambat jauh di antara dua lintasan satelit) bila berjarak <= jarak_m + v_kmj x
    selisih jam (maks. maks_m) dan garis lurus di antaranya >= porsi berada di areal terbakar."""
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    n = len(jam)
    if n == 0:
        return np.zeros(0, int)
    pr = cKDTree(xy).query_pairs(jarak_m, output_type="ndarray")
    if len(pr):
        pr = pr[np.abs(jam[pr[:, 0]] - jam[pr[:, 1]]) <= jeda_jam]
    pr = [pr] if len(pr) else []
    if burn is not None and grid is not None:
        o = np.argsort(jam, kind="stable")
        js = jam[o]
        for a in range(n):
            b1 = np.searchsorted(js, js[a] + jeda_jam, side="right")
            if b1 <= a + 1:
                continue
            bb = np.arange(a + 1, b1)
            i, j = o[a], o[bb]
            d = np.hypot(xy[j, 0] - xy[i, 0], xy[j, 1] - xy[i, 1])
            dtj = js[bb] - js[a]
            ok = (d > jarak_m) & (d <= np.minimum(jarak_m + v_kmj * 1000.0 * dtj, maks_m))
            for jj, dd in zip(j[ok], d[ok]):
                m = max(int(dd / grid.res), 2)
                t = np.linspace(0, 1, m)
                r, c = grid.rc(xy[i, 0] + t * (xy[jj, 0] - xy[i, 0]), xy[i, 1] + t * (xy[jj, 1] - xy[i, 1]))
                v = (r >= 0) & (r < grid.ny) & (c >= 0) & (c < grid.nx)
                if v.all() and burn[r, c].mean() >= porsi:
                    pr.append(np.array([[i, jj]]))
    pr = np.vstack(pr) if pr else np.zeros((0, 2), int)
    A = coo_matrix((np.ones(len(pr)), (pr[:, 0], pr[:, 1])), shape=(n, n))
    _, lab = connected_components(A, directed=False)
    first = {}
    for i in np.argsort(jam, kind="stable"):
        first.setdefault(lab[i], len(first))
    return np.array([first[l] for l in lab])


def bagi_areal(grid, burn, hs_xy, hs_ev, radius_m=400.0, maks_m=2500.0):
    """Bagi sel terbakar ke kejadian. Satu poligon (komponen terhubung) hanya dibagi ke kejadian yang titik
    panasnya <= radius_m dari poligon itu; sel ke titik panas terdekat (<= maks_m bila poligon dipakai lebih dari
    satu kejadian). Mengembalikan raster kejadian (-1 = tanpa titik panas), label komponen, dan info per titik."""
    from scipy import ndimage
    from scipy.spatial import cKDTree
    lab, nlab = ndimage.label(burn, structure=np.ones((3, 3), bool))
    ev = np.full(grid.shape, -1, np.int32)
    if nlab == 0 or len(hs_ev) == 0:
        return ev, lab, nlab
    rad = int(math.ceil(radius_m / grid.res))
    r, c = grid.rc(hs_xy[:, 0], hs_xy[:, 1])
    kand = {}   # komponen -> daftar indeks titik panas
    yy, xx = np.mgrid[-rad:rad + 1, -rad:rad + 1]
    disk = (yy * yy + xx * xx) <= rad * rad
    for i in range(len(hs_ev)):
        r0, r1, c0, c1 = r[i] - rad, r[i] + rad + 1, c[i] - rad, c[i] + rad + 1
        if r1 <= 0 or c1 <= 0 or r0 >= grid.ny or c0 >= grid.nx:
            continue
        sub = lab[max(r0, 0):min(r1, grid.ny), max(c0, 0):min(c1, grid.nx)]
        dk = disk[max(0, -r0):disk.shape[0] - max(0, r1 - grid.ny), max(0, -c0):disk.shape[1] - max(0, c1 - grid.nx)]
        for l in np.unique(sub[dk & (sub > 0)]):
            kand.setdefault(int(l), []).append(i)
    obj = ndimage.find_objects(lab)
    for l, idx in kand.items():
        sl = obj[l - 1]
        m = lab[sl] == l
        rr, cc = np.nonzero(m)
        x, y = grid.xy(rr + sl[0].start, cc + sl[1].start)
        evs = np.unique(hs_ev[idx])
        if len(evs) == 1:
            sub = ev[sl]
            sub[m] = evs[0]
            continue
        tree = cKDTree(hs_xy[idx])
        d, j = tree.query(np.column_stack([x, y]))
        e = np.where(d <= maks_m, hs_ev[np.array(idx)[j]], -1)
        sub = ev[sl]
        sub[m] = e
    return ev, lab, nlab


# =============================================================== mesin per kejadian
class Medan:
    """Grid ROS, arah kepala, dan eksentrisitas per jam untuk satu kejadian (FARSITE: kadar air terkondisi cuaca
    per jam, angin medan). Dipakai bersama oleh simulasi maju dan mundur. Waktu = jam sejak self.t_awal."""

    def __init__(self, sim, weather, t_awal: dt.datetime, t_akhir: dt.datetime, angin="medan", kondisi_jam=72,
                 log=None):
        from . import moisture as M
        from . import wind as W
        p = sim.p
        self.res = float(sim.ls.res)
        self.shape = p["fuel"].shape
        self.t_awal = t_awal.replace(minute=0, second=0, microsecond=0)
        self.H = max(1, int(math.ceil((t_akhir - self.t_awal).total_seconds() / 3600)))
        wm = sim.wind_model(angin)
        moist = M.ConditionedMoisture(sim.fms, p["fuel"], p["elev"], p["cover"], weather.elevation)
        cs = self.t_awal - dt.timedelta(hours=kondisi_jam)
        for rec in weather.records:
            if cs <= rec.time < self.t_awal:
                moist.step(rec, 1.0, rec.time.hour)
        ny, nx = self.shape
        self.ros = np.zeros((self.H, ny, nx), np.float32)
        self.head = np.zeros((self.H, ny, nx), np.float16)
        self.ecc = np.zeros((self.H, ny, nx), np.float16)
        self.cuaca = []
        rumput = (p["fuel"] >= 30) & (p["fuel"] <= 33)
        for h in range(self.H):
            t = self.t_awal + dt.timedelta(hours=h)
            rec = weather.at(t)
            if h > 0:
                moist.step(rec, 1.0, t.hour)
            if wm is None:
                spd, dirn = W.uniform(self.shape, rec.ws, rec.wd)
            else:
                spd, dirn = wm.field(rec.ws, rec.wd)
            ros, head, ecc, fli, flame, ct = sim.grids(spd, dirn, moist)
            self.ros[h], self.head[h], self.ecc[h] = ros, head, ecc
            m1 = float(np.median(moist.m[0][rumput])) if rumput.any() else None
            self.cuaca.append(dict(t=t.strftime("%Y-%m-%dT%H:%M"), ws=round(float(rec.ws), 1), wd=round(float(rec.wd) % 360),
                                   suhu=round(float(rec.temp), 1), rh=round(float(rec.rh)), hujan=round(float(rec.pcp), 2),
                                   m1=None if m1 is None else round(m1, 1),
                                   ros_rumput=round(float(np.median(ros[rumput])), 2) if rumput.any() else None))
        self.bakar = self.ros.max(axis=0) > 0
        if log:
            log(f"   grid per jam: {self.H} jam x {ny}x{nx} sel ({self.res:.0f} m)")

    def kasar(self, f):
        """Tampilan grid f kali lebih kasar (sel tengah blok, seperti Landscape.coarsen) untuk penyaringan cepat."""
        if f <= 1:
            return self
        o = object.__new__(Medan)
        a = f // 2
        o.res, o.t_awal, o.H, o.cuaca = self.res * f, self.t_awal, self.H, self.cuaca
        o.ros, o.head, o.ecc = self.ros[:, a::f, a::f], self.head[:, a::f, a::f], self.ecc[:, a::f, a::f]
        o.bakar = self.bakar[a::f, a::f]
        o.shape = o.bakar.shape
        return o

    def jam(self, t: dt.datetime) -> float:
        return (t - self.t_awal).total_seconds() / 3600.0

    def waktu(self, j: float) -> dt.datetime:
        return self.t_awal + dt.timedelta(hours=float(j))

    def _g(self, h, k, balik):
        h = min(max(int(h), 0), self.H - 1)
        hd = self.head[h].astype(np.float32)
        if balik:
            hd = (hd + 180.0) % 360.0
        return self.ros[h] * np.float32(k), hd, self.ecc[h].astype(np.float32)

    def mundur(self, mask, t_i, k=1.0, t_min=0.0):
        """Simulasi waktu-terbalik dari maska (titik panas) pada jam t_i ke belakang sampai t_min.
        Mengembalikan B (jam): waktu paling lambat api di tiap sel agar tiba di maska pada t_i (NaN = tidak bisa)."""
        from .spread import SpreadState
        st = SpreadState(self.shape, mask, self.res)
        s, t = 0.0, float(t_i)
        while t > t_min + 1e-9:
            h = int(math.ceil(t - 1e-9)) - 1
            ta = max(float(h), t_min)
            dur = (t - ta) * 60.0
            g = self._g(h, k, True)
            lanjut = ta > t_min + 1e-9 and h - 1 >= 0
            nx_ = self._g(h - 1, k, True) if lanjut else None
            st.advance(g, s + dur, nx_, s + dur + 60.0 if lanjut else None)
            s += dur
            t = ta
        a = st.arrival()
        return (float(t_i) - a / 60.0).astype(np.float32)

    def maju(self, mask, t0, t_akhir, k=1.0):
        """Simulasi FARSITE dari maska pada jam t0 sampai t_akhir. Mengembalikan jam tiba (NaN = tidak terbakar)."""
        from .spread import SpreadState
        st = SpreadState(self.shape, mask, self.res)
        clock, t = 0.0, float(t0)
        t_akhir = min(float(t_akhir), float(self.H))
        while t < t_akhir - 1e-9:
            h = int(math.floor(t + 1e-9))
            tb = min(h + 1.0, t_akhir)
            dur = (tb - t) * 60.0
            g = self._g(h, k, False)
            lanjut = tb < t_akhir - 1e-9 and h + 1 < self.H
            nx_ = self._g(h + 1, k, False) if lanjut else None
            st.advance(g, clock + dur, nx_, clock + dur + 60.0 if lanjut else None)
            clock += dur
            t = tb
        return (float(t0) + st.arrival() / 60.0).astype(np.float32)


# =============================================================== skor
# Parameter skor (jam): galat waktu dasar, galat relatif waktu tempuh, batas atas suku per titik panas (robust,
# ~3 sigma), penalti per jam api sudah menyala sebelum lintasan satelit yang tidak mendeteksinya, tenggang
# (api < 1 jam boleh tidak terdeteksi), batas penalti per lintasan.
PAR = dict(sigma_a=0.5, sigma_rel=0.25, batas=9.0, nd_per_jam=1.0, nd_tenggang=1.0, nd_maks=3.0)
BETA = 20.0   # bobot bentuk areal: beda Sørensen 0,1 = 2 satuan skor
ANGIN = "medan"   # model angin inversi (cepat; WindNinja per jam terlalu lambat untuk ratusan simulasi)
RESIDENSI_JAM = 1.0   # piksel masih menyala sesudah muka api lewat (rumput; hutan/semak 6 jam)


def _par():
    return np.array([PAR[k] for k in ("sigma_a", "sigma_rel", "batas", "nd_per_jam", "nd_tenggang", "nd_maks")],
                    np.float64)


@njit(cache=True)
def _rho(e, tau, da, db, par):
    """Suku skor satu titik panas. e = jam deteksi - jam tiba model (= B_i(x) - t0). Nol bila -da <= e <= db:
    muka api boleh baru mencapai pusat piksel sampai da jam sesudah lintasan, atau sudah lewat sampai db jam
    sebelumnya (piksel masih menyala). Di luar itu kuadrat terhadap sigma = sqrt(sa^2 + (sr x waktu tempuh)^2)."""
    s2 = par[0] * par[0] + (par[1] * tau) ** 2
    if e < -da:
        v = (e + da) * (e + da) / s2
    elif e > db:
        v = 0.5 * (e - db) * (e - db) / s2
    else:
        v = 0.0
    return v if v < par[2] else par[2]


@njit(cache=True)
def _nd(t0, nd, ndw, par):
    L = 0.0
    for p in range(nd.size):
        g = nd[p] - par[4] - t0
        if g > 0.0:
            v = par[3] * g
            L += ndw[p] * (v if v < par[5] else par[5])
    return L


@njit(cache=True)
def _skor_sel(B, j, t0, ti, da, db, w, nd, ndw, par):
    L = _nd(t0, nd, ndw, par)
    for i in range(ti.size):
        b = B[i, j]
        if b != b:
            L += w[i] * par[2]
            continue
        tau = ti[i] - b
        L += w[i] * _rho(b - t0, tau if tau > 0.0 else 0.0, da[i], db[i], par)
    return L


@njit(cache=True)
def _profil(B, ti, da, db, w, idx, t0s, nd, ndw, par):
    """Untuk tiap sel kandidat: skor waktu terbaik atas t0, t0 terbaiknya, dan log Σ_t0 exp(-skor/2)."""
    M = idx.size
    K = t0s.size
    best = np.full(M, 1e30)
    tb = np.zeros(M)
    lse = np.zeros(M)
    tmp = np.empty(K)
    for m in range(M):
        j = idx[m]
        for q in range(K):
            L = _skor_sel(B, j, t0s[q], ti, da, db, w, nd, ndw, par)
            tmp[q] = L
            if L < best[m]:
                best[m] = L
                tb[m] = t0s[q]
        mx = -0.5 * best[m]
        s = 0.0
        for q in range(K):
            s += math.exp(-0.5 * tmp[q] - mx)
        lse[m] = mx + math.log(s)
    return best, tb, lse


def pita_piksel(arr, piksel):
    """(T_min, T_max): jam api pertama masuk dan terakhir menyala di dalam piksel tiap titik panas (NaN = tidak)."""
    lo = np.full(len(piksel), np.nan)
    hi = np.full(len(piksel), np.nan)
    for i, (r, c) in enumerate(piksel):
        v = arr[r, c]
        v = v[np.isfinite(v)]
        if v.size:
            lo[i], hi[i] = float(v.min()), float(v.max())
    return lo, hi


def skor_pita(lo, hi, ti, rsd, w, nd, ndw, t0, geser, par, tol=0.25):
    """Skor waktu simulasi maju untuk tiap pergeseran jam mulai: titik panas cocok bila jam deteksinya berada di
    antara api pertama masuk piksel (dikurangi toleransi) dan api terakhir di piksel ditambah lama menyala."""
    out = np.empty(len(geser))
    for q, g in enumerate(geser):
        L = float(_nd(t0 + g, nd, ndw, par))
        for i in range(len(ti)):
            if not np.isfinite(lo[i]):
                L += w[i] * par[2]
                continue
            tau = max(lo[i] - t0, 0.0)
            s2 = par[0] ** 2 + (par[1] * tau) ** 2
            a, b = lo[i] + g - tol, hi[i] + g + rsd[i]
            v = (ti[i] - a) ** 2 / s2 if ti[i] < a else (0.5 * (ti[i] - b) ** 2 / s2 if ti[i] > b else 0.0)
            L += w[i] * min(v, par[2])
        out[q] = L
    return out


def _galat_pita(lo, hi, ti, rsd, tol=0.25):
    """Jam di luar rentang menyala piksel (negatif = model terlambat, positif = model terlalu awal)."""
    e = np.full(len(ti), np.nan)
    ok = np.isfinite(lo)
    a, b = lo - tol, hi + rsd
    e[ok] = np.where(ti[ok] < a[ok], ti[ok] - a[ok], np.where(ti[ok] > b[ok], ti[ok] - b[ok], 0.0))
    return e


def sorensen_sama_luas(arr, obs):
    from .validate import sorensen_equal_area
    s, _, mod = sorensen_equal_area(arr, obs)
    return float(s), mod


# =============================================================== utilitas geometri & waktu
def azimut(x0, y0, x1, y1):
    return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360.0


MATA = ["utara", "timur laut", "timur", "tenggara", "selatan", "barat daya", "barat", "barat laut"]


def mata_angin(deg):
    return MATA[int(round((deg % 360) / 45.0)) % 8]


def _jam_wita(t: dt.datetime):
    return t.strftime("%Y-%m-%dT%H:%M")


def sidik(*bagian):
    h = hashlib.sha1()
    for b in bagian:
        h.update(json.dumps(b, sort_keys=True, default=str).encode())
    return h.hexdigest()[:12]


# =============================================================== cuaca
class Cuaca:
    """Cuaca per jam Open-Meteo (ERA5 / ERA5-Land arsip, prakiraan untuk beberapa hari terakhir), sama dengan
    generator .wxs di halaman simulasi. Satu unduhan per sel 0,05° untuk seluruh rentang, disimpan di folder."""

    def __init__(self, folder, mulai: dt.date, sampai: dt.date, zona="WITA", model="best_match", log=print):
        self.folder, self.mulai, self.sampai, self.zona, self.model, self.log = folder, mulai, sampai, zona, model, log
        self.mem = {}
        os.makedirs(folder, exist_ok=True)

    def _unduh(self, lat, lon):
        import urllib.request
        from . import weather as WX
        hari_ini = dt.date.today()
        batas = hari_ini - dt.timedelta(days=6)
        recs, elev = [], None
        a1 = min(self.sampai, batas)
        if self.mulai <= a1:
            url = WX.build_url(lat, lon, self.mulai, a1, self.zona, self.model)
            with urllib.request.urlopen(url, timeout=120) as r:
                w = WX.from_openmeteo(json.load(r))
            recs, elev = w.records, w.elevation
        if self.sampai > batas:
            p0 = max(self.mulai, hari_ini - dt.timedelta(days=12))
            url = WX.build_url(lat, lon, p0, self.sampai, self.zona, "best_match", forecast=True)
            with urllib.request.urlopen(url, timeout=120) as r:
                w = WX.from_openmeteo(json.load(r))
            akhir = recs[-1].time if recs else dt.datetime.min
            recs = recs + [x for x in w.records if x.time > akhir]
            elev = elev if elev is not None else w.elevation
        return WX.Weather(float(elev or 0.0), recs)

    def ambil(self, lat, lon):
        from . import weather as WX
        la, lo = round(lat * 20) / 20, round(lon * 20) / 20
        key = f"{la:.2f}_{lo:.2f}"
        if key in self.mem:
            return self.mem[key]
        p = os.path.join(self.folder, f"om_{key}_{self.mulai}_{self.sampai}.json")
        if os.path.exists(p):
            d = json.load(open(p))
            w = WX.Weather(d["elev"], [WX.WxRecord(dt.datetime.fromisoformat(r[0]), *r[1:]) for r in d["rec"]])
        else:
            for coba in range(4):
                try:
                    w = self._unduh(la, lo)
                    break
                except Exception as e:  # jaringan / batas laju Open-Meteo
                    if coba == 3:
                        raise
                    self.log(f"   cuaca {key} gagal ({e}), coba lagi")
                    import time
                    time.sleep(10 * (coba + 1))
            json.dump(dict(lat=la, lon=lo, elev=w.elevation,
                           rec=[[r.time.isoformat(), r.temp, r.rh, r.pcp, r.ws, r.wd, r.cc] for r in w.records]), open(p, "w"))
        self.mem[key] = w
        return w


def potong_cuaca(w, t0, t1):
    from . import weather as WX
    return WX.Weather(w.elevation, [r for r in w.records if t0 <= r.time <= t1])


# =============================================================== konteks bersama
class Konteks:
    """Data bersama untuk semua kejadian (landscape dasar 10 m, grid 30 m, areal terbakar, titik panas, lapisan)."""

    def __init__(self, cfg, ls10, fmd, fms, ros_adjust, hs, lintas, burn, tn_mask, cuaca, root=".", log=print):
        self.cfg, self.ls10, self.fmd, self.fms, self.ros_adjust = cfg, ls10, fmd, fms, ros_adjust or {}
        self.g = Grid(cfg, 3 * ls10.res)
        self.hs, self.lintas, self.burn, self.tn, self.cuaca, self.log = hs, lintas, burn, tn_mask, cuaca, log
        x, y = self.g.ke_utm.transform([p["lon"] for p in hs], [p["lat"] for p in hs])
        self.xy = np.column_stack([x, y]) if hs else np.zeros((0, 2))
        self.jam = np.array([(p["t"] - dt.datetime(2000, 1, 1)).total_seconds() / 3600 for p in hs])
        self.lapis = {}
        for nama in ("batas_tn", "batas_resort", "jalur_patroli"):
            pth = os.path.join(root, "docs", "data", nama + ".geojson")
            if os.path.exists(pth):
                self.lapis[nama] = _geojson_utm(pth, self.g.ke_utm)
        # lintasan satelit (jam sejak 2000), untuk prior "tidak terdeteksi"
        self.lt_jam = np.array([(t - dt.datetime(2000, 1, 1)).total_seconds() / 3600 for _, t in lintas])
        self.lt_s = [s for s, _ in lintas]
        g = self.g
        self.ls10_fuel30 = ls10.layers["fuel"][1::3, 1::3][:g.ny, :g.nx]
        self.resort = np.zeros(g.shape, np.int16)
        self.nama_resort = {}
        if self.lapis.get("batas_resort"):
            from rasterio.features import rasterize
            rs = [(geom, n + 1) for n, (geom, _) in enumerate(self.lapis["batas_resort"])]
            self.nama_resort = {n + 1: pr.get("nama") for n, (_, pr) in enumerate(self.lapis["batas_resort"])}
            self.resort = rasterize(rs, out_shape=g.shape, transform=g.transform).astype(np.int16)
        h = hashlib.sha1()
        for f in (fmd, fms):
            if f and os.path.exists(f):
                h.update(open(f, "rb").read())
        self.fmd_sig = h.hexdigest()[:10]
        self.kal = None

    def jam_ke_waktu(self, j):
        return dt.datetime(2000, 1, 1) + dt.timedelta(hours=float(j))

    def ll(self, x, y):
        lon, lat = self.g.ke_ll.transform(x, y)
        return round(float(lat), 5), round(float(lon), 5)


def _resort(ctx, x, y):
    from shapely.geometry import Point
    pt = Point(x, y)
    for g, pr in ctx.lapis.get("batas_resort", []):
        if g.contains(pt):
            return pr.get("nama")
    return None


def _di_tn(ctx, x, y):
    from shapely.geometry import Point
    pt = Point(x, y)
    return any(g.contains(pt) for g, _ in ctx.lapis.get("batas_tn", []))


def _jarak_jalur(ctx, x, y):
    from shapely.geometry import Point
    pt = Point(x, y)
    best = {}
    for g, pr in ctx.lapis.get("jalur_patroli", []):
        tp = pr.get("tipe") or "Jalur"
        d = g.distance(pt)
        if tp not in best or d < best[tp][0]:
            best[tp] = (d, pr.get("nama") or tp)
    return {k: dict(jarak_m=round(v[0]), nama=v[1]) for k, v in sorted(best.items(), key=lambda kv: kv[1][0])}


# =============================================================== analisis per kejadian
def _cakram(x, y, ls, rad):
    """(baris, kolom) sel dalam radius rad m dari (x, y) di grid ls (minimal satu sel)."""
    ny, nx = ls.shape
    c0, r0 = (x - ls.west) / ls.res - 0.5, (ls.north - y) / ls.res - 0.5
    k = int(math.ceil(rad / ls.res)) + 1
    rr, cc = np.mgrid[int(r0) - k:int(r0) + k + 2, int(c0) - k:int(c0) + k + 2]
    d = np.hypot((rr - r0) * ls.res, (cc - c0) * ls.res)
    m = (d <= rad) & (rr >= 0) & (rr < ny) & (cc >= 0) & (cc < nx)
    if not m.any():
        rc, cc0 = int(round(r0)), int(round(c0))
        if 0 <= rc < ny and 0 <= cc0 < nx:
            return np.array([rc]), np.array([cc0])
        return np.array([], int), np.array([], int)
    return rr[m], cc[m]


def _kelompok_lintasan(idx, ctx, jeda=0.5):
    """Bagi titik panas (indeks urut waktu) ke lintasan (sensor sama, beda <= 30 menit)."""
    grup = []
    for i in idx:
        s = ctx.hs[i]["sensor"]
        for g in grup[::-1][:4]:
            if g["s"] == s and abs(ctx.jam[i] - g["t"]) <= jeda:
                g["i"].append(i)
                break
        else:
            grup.append(dict(s=s, t=ctx.jam[i], i=[i]))
    grup.sort(key=lambda g: g["t"])
    return grup


def _pilih_titik(grup, ctx, maks=16, per=4):
    """Titik panas untuk skor waktu: maksimal `per` per lintasan (farthest-point), total `maks`,
    dua lintasan pertama diutamakan."""
    pil = []
    for g in grup:
        ii = list(g["i"])
        xy = ctx.xy[ii]
        ch = [0]
        while len(ch) < min(per, len(ii)):
            d = np.min(np.hypot(xy[:, None, 0] - xy[None, ch, 0], xy[:, None, 1] - xy[None, ch, 1]), axis=1)
            j = int(np.argmax(d))
            if d[j] < 150:
                break
            ch.append(j)
        pil.append([ii[j] for j in ch])
    if sum(len(p) for p in pil) <= maks:
        return [i for p in pil for i in p]
    out = [i for p in pil[:2] for i in p][:maks]
    sisa = [i for p in pil[2:] for i in p]
    n = maks - len(out)
    if n > 0 and sisa:
        out += [sisa[int(round(j))] for j in np.linspace(0, len(sisa) - 1, n)]
    return sorted(set(out), key=lambda i: ctx.jam[i])


def _domain(ctx, ev_mask, hs_idx, margin_m=2500.0, maks_sel=700_000):
    """Analysis Area kejadian: kotak areal terbakar + titik panas + margin, sejajar grid 30 m AOI."""
    g = ctx.g
    rr, cc = np.nonzero(ev_mask)
    r_h, c_h = g.rc(ctx.xy[hs_idx, 0], ctx.xy[hs_idx, 1])
    r = np.concatenate([rr, r_h])
    c = np.concatenate([cc, c_h])
    lebar = max(r.max() - r.min(), c.max() - c.min()) * g.res
    m = int(math.ceil(max(margin_m, 0.25 * lebar) / g.res))
    r0, r1 = max(int(r.min()) - m, 0), min(int(r.max()) + m + 1, g.ny)
    c0, c1 = max(int(c.min()) - m, 0), min(int(c.max()) + m + 1, g.nx)
    f = 1
    while (r1 - r0) * (c1 - c0) / (f * f) > maks_sel:
        f *= 2
    r1 -= (r1 - r0) % f
    c1 -= (c1 - c0) % f
    return r0, r1, c0, c1, f


def _blok(a, f, cara="any"):
    if f == 1:
        return a
    ny, nx = a.shape[0] // f, a.shape[1] // f
    b = a[:ny * f, :nx * f].reshape(ny, f, nx, f)
    if cara == "mayor":
        return b.mean(axis=(1, 3)) >= 0.5
    return b.any(axis=(1, 3)) if cara == "any" else b.max(axis=(1, 3))


def _poligon_utm(mask, ls):
    """Maska -> geometri shapely (UTM), gabungan semua sel bernilai True."""
    from rasterio.features import shapes
    from shapely.geometry import shape
    from shapely.ops import unary_union
    return unary_union([shape(s) for s, v in shapes(mask.astype(np.uint8), mask=mask, transform=ls.transform) if v == 1])


def _poligon_ll(ctx, mask, ls, toleransi=None):
    """Maska -> geometri GeoJSON (lintang/bujur) yang disederhanakan."""
    import shapely
    from shapely.geometry import mapping
    if not mask.any():
        return None
    g = _poligon_utm(mask, ls).simplify(toleransi or ls.res * 0.75)
    g = shapely.transform(g, lambda a: np.column_stack(ctx.g.ke_ll.transform(a[:, 0], a[:, 1])))
    g = shapely.set_precision(g, 1e-5)
    return mapping(g)


def _hanya_garis(g):
    """Ambil bagian garis saja dari hasil irisan (buang titik)."""
    import shapely
    from shapely.geometry import MultiLineString
    parts = [p for p in shapely.get_parts(g) if p.geom_type in ("LineString", "LinearRing") and p.length > 0]
    for p in shapely.get_parts(g):
        if p.geom_type == "MultiLineString":
            parts.extend(q for q in p.geoms if q.length > 0)
    return MultiLineString([list(p.coords) for p in parts]) if parts else None


def _garis_ll(ctx, geom):
    import shapely
    from shapely.geometry import mapping
    g = shapely.transform(geom, lambda a: np.column_stack(ctx.g.ke_ll.transform(a[:, 0], a[:, 1])))
    return mapping(shapely.set_precision(g, 1e-5))


def _wquant(x, w, q):
    o = np.argsort(x)
    x, w = np.asarray(x)[o], np.asarray(w)[o]
    c = np.cumsum(w)
    c = c / c[-1]
    return [float(np.interp(qq, c, x)) for qq in q]


KELAS = ("30", "31", "32", "33")


def _komposisi(f):
    """Porsi sel areal terbakar per kelas: K1-K4 (30-33) dan 'lain' (hutan, semak, dll.)."""
    n = max(int(f.size), 1)
    out = {k: round(float((f == int(k)).sum()) / n, 4) for k in KELAS}
    out["lain"] = round(max(0.0, 1.0 - sum(out.values())), 4)
    return out


class _G:
    """Grid sederhana (barat, utara, sel, ukuran) untuk _cakram dan poligonisasi."""

    def __init__(self, west, north, res, shape):
        self.west, self.north, self.res, self.shape = float(west), float(north), float(res), tuple(shape)

    @property
    def transform(self):
        from rasterio.transform import from_origin
        return from_origin(self.west, self.north, self.res, self.res)


def _sel_bakar(x, y, grd, bakar, rad0, rad1=150.0):
    """Sel pusat titik panas yang bisa terbakar (dicari sampai rad1 m bila pusatnya batu/air)."""
    rr, cc = _cakram(x, y, grd, rad0)
    ok = bakar[rr, cc] if rr.size else np.zeros(0, bool)
    if ok.any():
        return rr[ok], cc[ok]
    rr, cc = _cakram(x, y, grd, rad1)
    ok = bakar[rr, cc] if rr.size else np.zeros(0, bool)
    if not ok.any():
        return None
    rr, cc = rr[ok], cc[ok]
    d = np.hypot(grd.west + (cc + 0.5) * grd.res - x, grd.north - (rr + 0.5) * grd.res - y)
    j = int(np.argmin(d))
    return rr[j:j + 1], cc[j:j + 1]


def analisis(ctx, ev, k_grid, logp_k, stage="bebas", maks_titik=16, log=print, f_layar=2):
    """Inversi satu kejadian. ev: dict(id, idx (titik panas), mask (areal kejadian di grid 30 m AOI),
    blok (areal kejadian sebelumnya)). Penyaringan (simulasi mundur untuk semua sel) di grid f_layar x lebih kasar,
    simulasi maju di grid analisis. Mengembalikan dict hasil (serializable) + data peta (_peta, _wxs)."""
    import time as _time
    from scipy import ndimage
    from .simulate import Simulator
    from .geo import perimeters
    _dbg = os.environ.get("ASAL_DEBUG")
    _t0 = _time.time()
    g = ctx.g
    idx = sorted(ev["idx"], key=lambda i: ctx.jam[i])
    j_first, j_last = ctx.jam[idx[0]], ctx.jam[idx[-1]]
    j_lo = math.floor(j_first - 30.0)
    j_win = min(j_last, j_first + 72.0)
    j_hi = math.ceil(j_win + 2.0)
    t_lo, t_hi = ctx.jam_ke_waktu(j_lo), ctx.jam_ke_waktu(j_hi)
    # ---------------- domain & landscape
    r0, r1, c0, c1, f = _domain(ctx, ev["mask"], idx)
    ls = ctx.ls10.crop(g.west + c0 * g.res, g.north - r1 * g.res, g.west + c1 * g.res, g.north - r0 * g.res)
    ls = ls.coarsen(int(round(3 * f)))
    ny, nx = ls.shape
    obs = _blok(ev["mask"][r0:r1, c0:c1], f)[:ny, :nx]
    blok = _blok(ev["blok"][r0:r1, c0:c1], f)[:ny, :nx] if ev.get("blok") is not None else np.zeros_like(obs)
    fuel = ls.layers["fuel"]
    asli = fuel.copy()
    if blok.any():
        dekat = np.zeros(ls.shape, bool)
        for i in idx:
            rr, cc = _cakram(ctx.xy[i, 0], ctx.xy[i, 1], ls, 300.0)
            dekat[rr, cc] = True
        fuel[blok & ~obs & ~dekat] = 99
    sim = Simulator(ls, ctx.fmd, ctx.fms, crown=True, ros_adjust=ctx.ros_adjust, crown_method="finney")
    # ---------------- titik panas, cuaca, grid per jam
    grup = _kelompok_lintasan([i for i in idx if ctx.jam[i] <= j_win + 1e-6], ctx)
    pil = _pilih_titik(grup, ctx, maks_titik)
    lat_c = float(np.mean([ctx.hs[i]["lat"] for i in grup[0]["i"]]))
    lon_c = float(np.mean([ctx.hs[i]["lon"] for i in grup[0]["i"]]))
    wx = potong_cuaca(ctx.cuaca.ambil(lat_c, lon_c), t_lo - dt.timedelta(days=4), t_hi + dt.timedelta(hours=2))
    if not wx.records or wx.records[0].time > t_lo - dt.timedelta(hours=24) or wx.records[-1].time < t_hi - dt.timedelta(hours=1):
        raise RuntimeError("data cuaca tidak mencakup waktu kejadian")
    md = Medan(sim, wx, t_lo, t_hi, angin=ANGIN)
    mk = md.kasar(f_layar)
    a = f_layar // 2
    off = (a + 0.5 - f_layar / 2.0) * ls.res
    gk = _G(ls.west + off, ls.north - off, ls.res * f_layar, mk.shape)
    if _dbg:
        print(f"   [waktu] grid per jam {_time.time() - _t0:.1f} dtk, domain {ls.shape} ({ls.res:.0f} m), H {md.H}")
    halus, kasar_, pik_f, pik_k, ti, da, db, w, rsd, info_t = [], [], [], [], [], [], [], [], [], []
    for i in pil:
        p = ctx.hs[i]
        x, y = ctx.xy[i]
        sf = _sel_bakar(x, y, ls, md.bakar, 0.75 * ls.res)
        sk = _sel_bakar(x, y, gk, mk.bakar, 0.75 * gk.res)
        if sf is None or sk is None:
            continue
        halus.append(sf)
        kasar_.append(sk)
        rp = PIKSEL_M.get(p["sensor"], 375.0) * 0.56     # jari-jari lingkaran seluas piksel
        pik_f.append(_cakram(x, y, ls, rp))
        pik_k.append(_cakram(x, y, gk, rp))
        ti.append(ctx.jam[i] - j_lo)
        modis = p["sensor"] == "MODIS"
        hutan = np.median(asli[sf]) >= 100
        # waktu muka api melintasi setengah piksel dengan laju sisi (flank) R(1-e) pada jam deteksi:
        # selama itu pusat piksel boleh belum/sudah dilewati api walau piksel terdeteksi menyala
        hh = min(max(int(ti[-1]), 0), md.H - 1)
        rs = md.ros[hh][sf].astype(float)
        es = md.ecc[hh][sf].astype(float)
        sisi = float(np.median(rs * (1 - es))) if rs.size else 0.0
        setengah = PIKSEL_M.get(p["sensor"], 375.0) / 2.0
        lintas = min(setengah / max(sisi, 0.05) / 60.0, 12.0)
        da.append(min(0.5 * lintas + 0.5, 6.0))
        db.append(min(lintas + (6.0 if hutan else RESIDENSI_JAM), 12.0))
        w.append(0.5 if modis else 1.0)
        rsd.append(6.0 if hutan else RESIDENSI_JAM)
        info_t.append(i)
    if not ti:
        raise RuntimeError("titik panas tidak berada di bahan bakar yang bisa terbakar")
    ti, da, db, w, rsd = map(np.array, (ti, da, db, w, rsd))
    t_first = float(ti.min())
    # lintasan satelit tanpa deteksi sebelum deteksi pertama
    sel = (ctx.lt_jam >= j_lo) & (ctx.lt_jam <= j_first - 0.5)
    nd_t, nd_w = [], []
    for j, sname in zip(ctx.lt_jam[sel], np.array(ctx.lt_s, dtype=object)[sel]):
        v = 0.5 if sname == "MODIS" else 1.0
        if nd_t and j - j_lo - nd_t[-1] < 1.5:
            nd_w[-1] = max(nd_w[-1], v)
            continue
        nd_t.append(j - j_lo)
        nd_w.append(v)
    nd_t, nd_w = np.array(nd_t, float), np.array(nd_w, float)
    kosong = [[_jam_wita(ctx.jam_ke_waktu(float(j))), str(sname)]
              for j, sname in zip(ctx.lt_jam[sel], np.array(ctx.lt_s, dtype=object)[sel])]
    par = _par()
    # ---------------- prior lokasi (grid kasar)
    obs_k = _blok(obs, f_layar, "mayor")[:gk.shape[0], :gk.shape[1]] if f_layar > 1 else obs
    pri = np.full(gk.shape, math.log(0.05), np.float64)
    dalam = ndimage.binary_dilation(_blok(obs, f_layar)[:gk.shape[0], :gk.shape[1]] if f_layar > 1 else obs, iterations=1)
    for (rr, cc), i in zip(kasar_, info_t):
        if ctx.jam[i] - j_first <= 2.0:
            dalam[rr, cc] = True
    pri[dalam] = 0.0
    pri[~mk.bakar] = -np.inf
    # ---------------- penyaringan: simulasi mundur dari tiap titik panas, untuk tiap k
    t0s = np.arange(0.0, t_first + 1e-6, 0.25)
    nsel = int(np.prod(gk.shape))
    flat_ok = np.isfinite(pri.ravel())
    lp_k, tb_k = {}, {}
    i_first = int(np.argmin(ti))
    for k in k_grid:
        B = np.empty((len(ti), nsel), np.float32)
        for n, ((rr, cc), t_i) in enumerate(zip(kasar_, ti)):
            m = np.zeros(gk.shape, bool)
            m[rr, cc] = True
            B[n] = mk.mundur(m, t_i, k, 0.0).ravel()
        cand = np.flatnonzero(flat_ok & np.isfinite(B[i_first]))
        lp = np.full(nsel, -np.inf)
        tbf = np.full(nsel, np.nan)
        if cand.size:
            bst, tb, lse = _profil(B, ti, da, db, w, cand, t0s, nd_t, nd_w, par)
            lp[cand] = pri.ravel()[cand] + lse + logp_k.get(k, 0.0)
            tbf[cand] = tb
        lp_k[k], tb_k[k] = lp, tbf
    if _dbg:
        print(f"   [waktu] mundur+profil {_time.time() - _t0:.1f} dtk ({len(ti)} titik x {len(k_grid)} k, {gk.shape})")
    ks = list(k_grid)
    lp_all = np.logaddexp.reduce(np.stack([lp_k[k] for k in ks]), axis=0)
    fin = np.flatnonzero(np.isfinite(lp_all))
    if not fin.size:
        raise RuntimeError("tidak ada sel yang bisa mencapai titik panas pertama dalam 30 jam")
    mkk = np.array([np.logaddexp.reduce(lp_k[k][np.isfinite(lp_k[k])]) if np.isfinite(lp_k[k]).any() else -np.inf
                    for k in ks])
    pk_layar = np.exp(mkk - mkk.max())
    pk_layar /= pk_layar.sum()
    # ---------------- kandidat (non-maximum suppression) -> sel halus
    o = fin[np.argsort(-lp_all[fin])]
    nms = max(300.0, 3 * gk.res)
    kand = []
    for j in o[:50000]:
        R, C = divmod(int(j), gk.shape[1])
        if all(math.hypot((R - A_) * gk.res, (C - B_) * gk.res) >= nms for A_, B_, _ in kand):
            kand.append((R, C, int(j)))
            if len(kand) >= 10:
                break
    # ---------------- simulasi maju dari kandidat (grid kasar, semua k)
    geser = np.arange(-2.0, 2.0001, 0.25)
    hasil = []
    nobs_k = int(obs_k.sum())
    nobs = int(obs.sum())
    for R, C, j in kand[:8]:
        for k in ks:
            t0 = float(tb_k[k][j])
            if not np.isfinite(t0):
                continue
            m = np.zeros(gk.shape, bool)
            m[R, C] = True
            arr = mk.maju(m, t0, mk.H, k)
            lo, hi = pita_piksel(arr, pik_k)
            Lg = skor_pita(lo, hi, ti, rsd, w, nd_t, nd_w, t0, geser, par)
            qg = int(np.argmin(Lg))
            sc = sorensen_sama_luas(arr, obs_k)[0] if nobs_k else 0.0
            lse_g = float(np.logaddexp.reduce(-0.5 * Lg))
            S = -2 * lse_g + BETA * (1 - sc) - 2 * pri[R, C] - 2 * logp_k.get(k, 0.0)
            hasil.append(dict(R=R, C=C, k=k, t0=t0, g=float(geser[qg]), L=float(Lg[qg]), Lg=Lg, sc=sc, S=S))
            if _dbg:
                print(f"      kand R{R} C{C} k{k} t0 {t0:.2f} g {geser[qg]:+.2f} Lmin {Lg[qg]:.2f} lse {lse_g:.2f} "
                      f"sc {sc:.3f} pri {pri[R, C]:.2f} S {S:.2f}  e {np.round(_galat_pita(lo + geser[qg], hi + geser[qg], ti, rsd), 1)}")
    if not hasil:
        raise RuntimeError("simulasi maju dari kandidat gagal")
    if _dbg:
        print(f"   [waktu] maju {_time.time() - _t0:.1f} dtk ({len(hasil)} simulasi)")
    Ss = np.array([h["S"] for h in hasil])
    wt = np.exp(-0.5 * (Ss - Ss.min()))
    wt /= wt.sum()
    hb = hasil[int(np.argmin(Ss))]
    # ---------------- simulasi akhir dari titik asal terbaik (grid analisis)
    xk, yk = gk.west + (hb["C"] + 0.5) * gk.res, gk.north - (hb["R"] + 0.5) * gk.res
    sf = _sel_bakar(xk, yk, ls, md.bakar, 0.75 * gk.res, 2 * gk.res)
    hb["r"], hb["c"] = int(sf[0][0]), int(sf[1][0])
    t0b = hb["t0"] + hb["g"]
    m = np.zeros(ls.shape, bool)
    m[hb["r"], hb["c"]] = True
    arr = md.maju(m, t0b, md.H, hb["k"])
    lo, hi = pita_piksel(arr, pik_f)
    Lf = skor_pita(lo, hi, ti, rsd, w, nd_t, nd_w, t0b, geser, par)
    gf = float(geser[int(np.argmin(Lf))])
    if abs(gf) > 1e-6 and Lf.min() < Lf[len(geser) // 2] - 0.5:
        t0b += gf
        arr = md.maju(m, t0b, md.H, hb["k"])
        lo, hi = pita_piksel(arr, pik_f)
    else:
        gf = 0.0
    sc = sorensen_sama_luas(arr, obs)[0] if nobs else 0.0
    if os.environ.get("ASAL_BENAR"):   # uji kembar: skor di titik/jam/k sebenarnya
        xb, yb, tb_, kb_ = os.environ["ASAL_BENAR"].split(",")
        rb, cb = _sel_bakar(float(xb), float(yb), ls, md.bakar, 20)
        mb = np.zeros(ls.shape, bool)
        mb[rb[0], cb[0]] = True
        t0t = md.jam(dt.datetime.fromisoformat(tb_))
        ab = md.maju(mb, t0t, md.H, float(kb_))
        lb, hb_ = pita_piksel(ab, pik_f)
        Lb = skor_pita(lb, hb_, ti, rsd, w, nd_t, nd_w, t0t, geser, par)
        print(f"   BENAR (halus): t0 {t0t:.2f} k {kb_} L(0) {Lb[len(geser) // 2]:.2f} min {Lb.min():.2f} sc {sorensen_sama_luas(ab, obs)[0]:.3f}"
              f"  e {np.round(_galat_pita(lb, hb_, ti, rsd), 1)}")
        print(f"   TERBAIK (halus): t0 {t0b:.2f} k {hb['k']} L {Lf.min():.2f} sc {sc:.3f}  e {np.round(_galat_pita(lo, hi, ti, rsd), 1)}")
    # ---------------- peta peluang titik asal: skor waktu (semua sel) x faktor bentuk (dari kandidat)
    RC = np.array([[h["R"], h["C"]] for h in hasil], float)
    SC = np.array([h["sc"] for h in hasil])
    yy, xx = np.divmod(fin, gk.shape[1])
    if fin.size * len(RC) <= 6e7:
        ww = 1.0 / ((yy[:, None] - RC[None, :, 0]) ** 2 + (xx[:, None] - RC[None, :, 1]) ** 2 + 4.0)
        sci = (ww * SC[None, :]).sum(1) / ww.sum(1)
    else:
        sci = np.full(fin.size, float(SC.max()))
    post = lp_all[fin] - 0.5 * BETA * (1 - sci)
    pp = np.exp(post - post.max())
    pp /= pp.sum()
    oo = np.argsort(-pp)
    cs = np.cumsum(pp[oo])
    z50 = np.zeros(nsel, bool)
    z90 = np.zeros(nsel, bool)
    z50[fin[oo[:int(np.searchsorted(cs, 0.5)) + 1]]] = True
    z90[fin[oo[:int(np.searchsorted(cs, 0.9)) + 1]]] = True
    z50, z90 = z50.reshape(gk.shape), z90.reshape(gk.shape)
    # ---------------- sebaran jam mulai: campuran kandidat simulasi maju
    sb = np.arange(-30.0, 3.0001, 0.25)
    dens = np.zeros(sb.size)
    for h, wv in zip(hasil, wt):
        if wv < 1e-4:
            continue
        pr = np.exp(-0.5 * (h["Lg"] - h["Lg"].min()))
        pr /= pr.sum()
        dens += wv * np.interp(sb, h["t0"] + geser + gf - t_first, pr, left=0, right=0)
    if dens.sum() <= 0:
        dens[int(np.argmin(np.abs(sb - (t0b - t_first))))] = 1
    dens /= dens.sum()
    q05, q50, q95 = _wquant(sb, dens + 1e-12, [0.05, 0.5, 0.95])
    jam_hari = np.zeros(24)
    for v, pv in zip(sb, dens):
        jam_hari[ctx.jam_ke_waktu(j_lo + t_first + v).hour] += pv
    # ---------------- faktor k
    kk = np.array([h["k"] for h in hasil])
    lk = _wquant(np.log(kk), wt, [0.05, 0.5, 0.95])
    # ---------------- ringkasan geometri & arah
    x0, y0 = ls.west + (hb["c"] + 0.5) * ls.res, ls.north - (hb["r"] + 0.5) * ls.res
    lat0, lon0 = ctx.ll(x0, y0)
    rr, cc = np.nonzero(obs)
    xc, yc = ((ls.west + (cc.mean() + 0.5) * ls.res, ls.north - (rr.mean() + 0.5) * ls.res) if rr.size else (x0, y0))
    arah = azimut(x0, y0, xc, yc) if math.hypot(xc - x0, yc - y0) > ls.res else None
    t_mulai = ctx.jam_ke_waktu(j_lo + t0b)
    pusat = ctx.ll(xc, yc)
    res = dict(
        t_mulai=_jam_wita(t_mulai),
        t_mulai_p05=_jam_wita(ctx.jam_ke_waktu(j_lo + t_first + q05)),
        t_mulai_p50=_jam_wita(ctx.jam_ke_waktu(j_lo + t_first + q50)),
        t_mulai_p95=_jam_wita(ctx.jam_ke_waktu(j_lo + t_first + q95)),
        jam_sebelum_deteksi=round(float(t_first - t0b), 2),
        jam_hari=[round(float(v), 4) for v in jam_hari],
        asal=dict(lat=lat0, lon=lon0, x=round(x0, 1), y=round(y0, 1), di_tn=_di_tn(ctx, x0, y0),
                  resort=_resort(ctx, x0, y0), jalur=_jarak_jalur(ctx, x0, y0),
                  fuel=int(asli[hb["r"], hb["c"]]), elev=int(ls.layers["elev"][hb["r"], hb["c"]])),
        jarak_ke_titik_pertama_m=round(float(np.min(np.hypot(ctx.xy[grup[0]["i"], 0] - x0, ctx.xy[grup[0]["i"], 1] - y0)))),
        arah_rambat=None if arah is None else round(arah), arah_rambat_teks=None if arah is None else mata_angin(arah),
        api_datang_dari=None if arah is None else mata_angin(arah + 180),
        pusat_areal=list(pusat), jarak_ke_pusat_m=round(math.hypot(xc - x0, yc - y0)),
        k=round(float(hb["k"]), 3), k_p05=round(math.exp(lk[0]), 3), k_p50=round(math.exp(lk[1]), 3),
        k_p95=round(math.exp(lk[2]), 3), k_layar={str(k): round(float(v), 4) for k, v in zip(ks, pk_layar)},
        sorensen=round(float(sc), 3), cuaca_mulai=md.cuaca[min(max(int(t0b), 0), md.H - 1)], sel_m=ls.res, tahap=stage,
        komposisi=_komposisi(asli[obs]) if obs.any() else {}, n_lintasan=len(grup), n_titik_skor=len(ti),
    )
    e = _galat_pita(lo, hi, ti, rsd)
    res["residu"] = [dict(t=_jam_wita(ctx.hs[i]["t"]), s=ctx.hs[i]["sensor"], lat=round(ctx.hs[i]["lat"], 5),
                          lon=round(ctx.hs[i]["lon"], 5),
                          masuk=None if not np.isfinite(a_) else _jam_wita(ctx.jam_ke_waktu(j_lo + a_)),
                          padam=None if not np.isfinite(b_) else _jam_wita(ctx.jam_ke_waktu(j_lo + b_)),
                          galat_jam=None if not np.isfinite(ev_) else round(float(ev_), 2))
                     for i, a_, b_, ev_ in zip(info_t, lo, hi, e)]
    okf = np.isfinite(e)
    res["galat_waktu_jam"] = round(float(np.sqrt(np.mean(e[okf] ** 2))), 2) if okf.any() else None
    res["titik_cocok_persen"] = round(100.0 * float(np.mean(np.abs(e[okf]) < 1e-6)), 1) if okf.any() else 0.0
    res["titik_tak_tercapai"] = int((~okf).sum())
    # ---------------- masuk kawasan TN
    tn_d = _blok(ctx.tn[r0:r1, c0:c1], f)[:ny, :nx]
    if not res["asal"]["di_tn"] and (obs & tn_d).any():
        aa = np.where(tn_d & np.isfinite(arr), arr, np.inf)
        if np.isfinite(aa).any():
            jj = int(np.argmin(aa))
            r_, c_ = divmod(jj, nx)
            xe, ye = ls.west + (c_ + 0.5) * ls.res, ls.north - (r_ + 0.5) * ls.res
            la, lo = ctx.ll(xe, ye)
            res["masuk_tn"] = dict(lat=la, lon=lo, t=_jam_wita(ctx.jam_ke_waktu(j_lo + float(aa.flat[jj]))),
                                   resort=_resort(ctx, xe, ye), arah=mata_angin(azimut(x0, y0, xe, ye)))
    # ---------------- data peta
    peta = dict(z50=_poligon_ll(ctx, z50, gk), z90=_poligon_ll(ctx, z90, gk), areal=_poligon_ll(ctx, obs, ls, ls.res))
    # garis waktu (isokron) hanya di dalam areal kejadian: muka api simulasi terbaik tiap 1-6 jam
    obs_d2 = ndimage.binary_dilation(obs, iterations=2)
    am = np.where(np.isfinite(arr) & obs_d2, (arr - t0b) * 60.0, np.nan)
    a_obs = am[obs & np.isfinite(am)]
    lama = float(np.percentile(a_obs, 98)) / 60.0 if a_obs.size else 0.0
    step = 1 if lama <= 12 else (2 if lama <= 30 else (3 if lama <= 48 else 6))
    mulai_jam = math.ceil(t0b) - t0b
    times = [60.0 * (mulai_jam + hh) for hh in range(0, int(lama) + step + 1, step) if mulai_jam + hh > 0]
    iso = []
    if a_obs.size:
        dalam_fp = _poligon_utm(ndimage.binary_erosion(obs_d2, iterations=1), ls)
        for tm, geom in perimeters(am, ls, times[:40]):
            garis = _hanya_garis(geom.simplify(ls.res * 0.5).boundary.intersection(dalam_fp))
            if garis is None:
                continue
            iso.append(dict(t=_jam_wita(ctx.jam_ke_waktu(j_lo + t0b + tm / 60.0)),
                            g=_garis_ll(ctx, garis.simplify(ls.res * 1.2))))
    peta["isokron"] = iso
    peta["cuaca"] = md.cuaca
    peta["sebaran_mulai"] = [[_jam_wita(ctx.jam_ke_waktu(j_lo + t_first + float(v))), round(float(pv), 4)]
                             for v, pv in zip(sb, dens) if pv >= 2e-4]
    peta["lintasan_kosong"] = kosong
    res["_peta"] = peta
    res["_wxs"] = wx
    if _dbg:
        print(f"   [waktu] total {_time.time() - _t0:.1f} dtk")
    return res


# =============================================================== kalibrasi
def kalibrasi(hasil, min_sorensen=0.3, maks_galat=3.0, n_boot=400, seed=1):
    """Gabung faktor k kejadian (tahap bebas) per kelas bahan bakar: log k_e = Σ_c f_ec b_c + galat,
    dengan b_c = b0 + d_c, d_c ~ N(0, 0,5²) dan b0 ~ N(0, 1²) (ridge). Selang dari bootstrap kejadian."""
    def _galat(h):
        v = h.get("galat_waktu_jam")
        return 99.0 if v is None else float(v)

    rows = [h for h in hasil if h.get("tahap") == "bebas" and (h.get("sorensen") or 0.0) >= min_sorensen
            and _galat(h) <= maks_galat and h.get("komposisi")]
    kol = list(KELAS) + ["lain"]
    if len(rows) < 3:
        return dict(n=len(rows), cukup=False, catatan="kejadian dengan >= 2 lintasan satelit yang cocok belum cukup (< 3)")
    F = np.array([[h["komposisi"].get(k, 0.0) for k in kol] for h in rows])
    y = np.array([math.log(h["k_p50"]) for h in rows])
    sd = np.array([max((math.log(h["k_p95"]) - math.log(h["k_p05"])) / 3.29, 0.15) for h in rows])
    w = 1.0 / (sd ** 2 + 0.15 ** 2)

    def fit(F, y, w):
        C = F.shape[1]
        # parameter: b0, d_1..d_C
        X = np.hstack([np.ones((len(y), 1)), F])     # Σ f = 1 -> b_c = b0 + d_c
        P = np.diag([1.0 / 1.0 ** 2] + [1.0 / 0.5 ** 2] * C)
        A = X.T @ (w[:, None] * X) + P
        th = np.linalg.solve(A, X.T @ (w * y))
        return th[0] + th[1:], th[0]

    b, b0 = fit(F, y, w)
    rng = np.random.default_rng(seed)
    bs, b0s = [], []
    for _ in range(n_boot):
        ii = rng.integers(0, len(y), len(y))
        bb, bb0 = fit(F[ii], y[ii], w[ii])
        bs.append(bb)
        b0s.append(bb0)
    bs = np.array(bs)
    neff = F.sum(axis=0)
    per = {}
    for j, kname in enumerate(kol):
        per[kname] = dict(faktor=round(math.exp(b[j]), 3), p05=round(math.exp(np.percentile(bs[:, j], 5)), 3),
                          p95=round(math.exp(np.percentile(bs[:, j], 95)), 3), n_efektif=round(float(neff[j]), 2))
    gm = float(np.exp(np.sum(w * y) / np.sum(w)))
    saran = {k: per[k]["faktor"] for k in KELAS if per[k]["n_efektif"] >= 1.0}
    return dict(n=len(rows), cukup=True, global_faktor=round(gm, 3),
                global_p05=round(math.exp(np.percentile(b0s, 5)), 3), global_p95=round(math.exp(np.percentile(b0s, 95)), 3),
                kelas=per, saran_ros_adjust=saran,
                kejadian=[dict(id=h["id"], k=h["k_p50"], k_p05=h["k_p05"], k_p95=h["k_p95"], komposisi=h["komposisi"],
                               sorensen=h["sorensen"]) for h in rows])


def k_prior(kal, komposisi):
    """Pusat dan sebaran log k untuk kejadian 1 lintasan (tahap prior)."""
    if not kal or not kal.get("cukup"):
        return 1.0, 0.5
    lk = sum(math.log(kal["kelas"][k]["faktor"]) * komposisi.get(k, 0.0) for k in list(KELAS) + ["lain"])
    sp = [math.log(kal["kelas"][k]["p95"] / kal["kelas"][k]["p05"]) / 3.29 for k in list(KELAS) + ["lain"]]
    s = max(0.3, sum(v * komposisi.get(k, 0.0) for v, k in zip(sp, list(KELAS) + ["lain"])))
    return math.exp(lk), s


K_BEBAS = (0.35, 0.5, 0.71, 1.0, 1.41, 2.0, 2.83)


def _mutu(h):
    galat = h.get("galat_waktu_jam")
    galat = 99.0 if galat is None else float(galat)
    sc = h.get("sorensen") or 0.0
    if h.get("n_lintasan", 0) >= 2 and sc >= 0.5 and galat <= 2.0:
        return "baik"
    if sc >= 0.35 and galat <= 4.0:
        return "sedang"
    return "rendah"


# =============================================================== alur lengkap
def siapkan(ctx, min_ha=1.0, log=print):
    """Bersihkan titik panas, kelompokkan per kejadian, bagi areal terbakar. Mengembalikan daftar kejadian."""
    from scipy import ndimage
    g = ctx.g
    r, c = g.rc(ctx.xy[:, 0], ctx.xy[:, 1])
    di_aoi = (r >= 0) & (r < g.ny) & (c >= 0) & (c < g.nx)
    jarak = ndimage.distance_transform_edt(~ctx.burn) * g.res if ctx.burn.any() else np.full(g.shape, 1e9)
    d = np.full(len(ctx.hs), 1e9)
    d[di_aoi] = jarak[r[di_aoi], c[di_aoi]]
    tn = np.zeros(len(ctx.hs), bool)
    tn[di_aoi] = ctx.tn[r[di_aoi], c[di_aoi]]
    pakai = di_aoi & np.array([(p["conf"] != "L") or dd <= 400 for p, dd in zip(ctx.hs, d)])
    ip = np.flatnonzero(pakai)
    lab = kelompokkan(ctx.xy[ip], ctx.jam[ip], ctx.burn, g)
    ev_hs = np.full(len(ctx.hs), -1)
    ev_hs[ip] = lab
    ras, comp, ncomp = bagi_areal(g, ctx.burn, ctx.xy[ip], lab)
    ha = g.res ** 2 / 1e4
    luas = np.bincount(ras[ras >= 0].ravel(), minlength=lab.max() + 1 if len(lab) else 0) * ha
    evs = []
    for e in range(int(lab.max()) + 1 if len(lab) else 0):
        idx = list(ip[lab == e])
        idx.sort(key=lambda i: ctx.jam[i])
        t1 = ctx.hs[idx[0]]["t"]
        grup = _kelompok_lintasan(idx, ctx)
        evs.append(dict(e=e, idx=idx, t_awal=t1, t_akhir=ctx.hs[idx[-1]]["t"], j_awal=ctx.jam[idx[0]],
                        j_akhir=ctx.jam[idx[-1]], n=len(idx), n_lintasan=len(grup), luas_ha=round(float(luas[e]), 1),
                        tn=round(float(np.mean(tn[idx])), 2)))
    # id stabil: waktu deteksi pertama
    pakai_id = {}
    for ev in evs:
        base = "K" + ev["t_awal"].strftime("%Y%m%d-%H%M")
        n = pakai_id.get(base, 0)
        pakai_id[base] = n + 1
        ev["id"] = base + ("" if n == 0 else "-" + "abcdefghij"[min(n, 9)])
    for i in range(len(ctx.hs)):
        ctx.hs[i]["ev"] = evs[ev_hs[i]]["id"] if ev_hs[i] >= 0 else None
        ctx.hs[i]["tn"] = bool(tn[i])
    tanpa = []
    for l in range(1, ncomp + 1):
        m = (comp == l) & (ras < 0)
        a = float(m.sum() * ha)
        if a >= 5 and not ((comp == l) & (ras >= 0)).any():
            rr, cc = np.nonzero(m)
            x, y = g.xy(rr.mean(), cc.mean())
            la, lo = ctx.ll(x, y)
            tanpa.append(dict(luas_ha=round(a, 1), lat=la, lon=lo, resort=_resort(ctx, x, y)))
    info = dict(titik_aoi=int(di_aoi.sum()), titik_dipakai=int(pakai.sum()), titik_rendah_dibuang=int((di_aoi & ~pakai).sum()),
                kejadian=len(evs), areal_tanpa_titik_panas_ha=round(float(((ras < 0) & ctx.burn).sum() * ha), 1),
                poligon_tanpa_titik_panas=sorted(tanpa, key=lambda t: -t["luas_ha"])[:30])
    log(f"titik panas: {info['titik_aoi']} di AOI, {info['titik_dipakai']} dipakai; {len(evs)} kejadian; "
        f"areal tanpa titik panas {info['areal_tanpa_titik_panas_ha']} ha")
    for ev in evs:
        ev["layak"] = ev["luas_ha"] >= min_ha
        ev["ras_id"] = ev["e"]
    ctx.ras = ras
    return evs, info


def _tahap(ev, ctx):
    """'bebas' bila kejadian terlihat pada >= 2 lintasan (>= 3 jam) dengan pergeseran posisi >= 300 m."""
    if ev["n_lintasan"] < 2:
        return "prior"
    grup = _kelompok_lintasan([i for i in ev["idx"] if ctx.jam[i] <= ev["j_awal"] + 72], ctx)
    if len(grup) < 2 or grup[-1]["t"] - grup[0]["t"] < 3.0:
        return "prior"
    a = ctx.xy[grup[0]["i"]]
    for gg in grup[1:]:
        b = ctx.xy[gg["i"]]
        if np.min(np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1]), axis=0).max() >= 300:
            return "bebas"
    return "prior"


_CTX = None


def _kerja(arg):
    ev, k_grid, logp_k, stage = arg
    import time
    t = time.time()
    try:
        h = analisis(_CTX, ev, k_grid, logp_k, stage, log=None)
        h["detik"] = round(time.time() - t, 1)
        return ev["id"], h, None
    except Exception as e:  # satu kejadian gagal tidak menghentikan semua
        import traceback
        return ev["id"], None, f"{e.__class__.__name__}: {e} | {traceback.format_exc(limit=3)[-400:]}"


def _pemanasan():
    """Kompilasi numba sekali di proses induk sebelum fork."""
    from .spread import SpreadState
    m = np.zeros((8, 8), bool)
    m[4, 4] = True
    st = SpreadState((8, 8), m, 30.0)
    o = np.ones((8, 8), np.float32)
    st.advance((o, o * 0, o * 0.5), 10.0, (o, o * 0, o * 0.5), 70.0)
    B = np.zeros((1, 4), np.float32)
    o1 = np.ones(1)
    _profil(B, o1, o1, o1, o1, np.arange(4), np.arange(3.0), np.zeros(0), np.zeros(0), _par())


def jalankan(ctx, evs, lama=None, proses=4, hanya=None, log=print):
    """Tahap bebas (k dicari) -> kalibrasi -> tahap prior (k dari kalibrasi). lama = {id: hasil lama} untuk cache."""
    global _CTX
    _CTX = ctx
    _pemanasan()
    lama = lama or {}
    for ev in evs:
        if not ev["layak"]:
            continue
        ev["mask"] = ctx.ras == ev["ras_id"]
        ev["tahap"] = _tahap(ev, ctx)
    # areal kejadian sebelumnya (selesai >= 6 jam sebelum kejadian ini mulai) = sudah terbakar
    selesai = sorted([(ev["j_akhir"], ev["ras_id"]) for ev in evs if ev["layak"]])
    for ev in evs:
        if ev["layak"]:
            dulu = [rid for ja, rid in selesai if ja < ev["j_awal"] - 6.0]
            ev["blok"] = np.isin(ctx.ras, dulu) if dulu else None
    hasil, gagal = {}, {}

    def sig(ev, extra):
        hs = [(ctx.hs[i]["t"].isoformat(), round(ctx.hs[i]["lat"], 5), round(ctx.hs[i]["lon"], 5), ctx.hs[i]["sensor"]) for i in ev["idx"]]
        mk = hashlib.sha1(np.packbits(ev["mask"]).tobytes()).hexdigest()[:10]
        bk = hashlib.sha1(np.packbits(ev["blok"]).tobytes()).hexdigest()[:10] if ev["blok"] is not None else "-"
        return sidik(VERSI, PAR, BETA, hs, mk, bk, extra, ctx.fmd_sig, ctx.ros_adjust)

    def putaran(daftar, stage):
        tugas = []
        for ev in daftar:
            if hanya and ev["id"] not in hanya:
                if ev["id"] in lama:
                    hasil[ev["id"]] = lama[ev["id"]]
                continue
            if stage == "bebas":
                kg = K_BEBAS
                lpk = {k: -0.5 * math.log(k) ** 2 for k in kg}
                extra = "bebas"
            else:
                kc, ks = k_prior(ctx.kal, _komposisi(ctx.ls10_fuel30[ev["mask"]]))
                kc = round(kc, 2)
                kg = tuple(round(kc * f, 3) for f in (0.71, 1.0, 1.41))
                lpk = {k: -0.5 * (math.log(k / kc) / ks) ** 2 for k in kg}
                extra = ("prior", kc, round(ks, 2))
            ev["sidik"] = sig(ev, extra)
            h0 = lama.get(ev["id"])
            if h0 and h0.get("sidik") == ev["sidik"]:
                hasil[ev["id"]] = h0
                continue
            tugas.append((ev, kg, lpk, stage))
        if not tugas:
            return
        tugas.sort(key=lambda t: -float(t[0]["mask"].sum()) * len(t[1]))
        log(f"tahap {stage}: {len(tugas)} kejadian dihitung" + (f" dengan {proses} proses" if proses > 1 else ""))
        if proses > 1:
            import multiprocessing as mp
            with mp.get_context("fork").Pool(proses, maxtasksperchild=4) as pool:
                it = pool.imap_unordered(_kerja, tugas)
                for eid, h, err in it:
                    _simpan(eid, h, err)
        else:
            for t in tugas:
                _simpan(*_kerja(t))

    def _simpan(eid, h, err):
        ev = next(e for e in evs if e["id"] == eid)
        if err:
            gagal[eid] = err
            log(f"   {eid}: gagal ({err[:160]})")
            return
        h["sidik"] = ev["sidik"]
        hasil[eid] = h
        log(f"   {eid}: mulai {h['t_mulai']} (90%: {h['t_mulai_p05'][11:]}–{h['t_mulai_p95'][11:]}), "
            f"k {h['k']}, Sørensen {h['sorensen']}, galat {h['galat_waktu_jam']} j, {h.get('detik')} dtk")

    layak = [ev for ev in evs if ev["layak"]]
    putaran([ev for ev in layak if ev["tahap"] == "bebas"], "bebas")
    for eid, h in hasil.items():
        h["id"] = eid
    ctx.kal = kalibrasi([h for h in hasil.values() if h.get("tahap") == "bebas"])
    log("kalibrasi: " + json.dumps({k: v for k, v in ctx.kal.items() if k != "kejadian"}, default=str)[:600])
    putaran([ev for ev in layak if ev["tahap"] == "prior"], "prior")
    for eid, h in hasil.items():
        h["id"] = eid
        h["mutu"] = _mutu(h)
    return hasil, gagal


# =============================================================== keluaran
def _ringkas_kejadian(ctx, ev, h):
    """Satu baris tabel kejadian untuk docs/status/asal_api.json."""
    g = ctx.g
    m = ev["mask"]
    rs = ctx.resort[m]
    rs = rs[rs > 0]
    resort = ctx.nama_resort.get(int(np.bincount(rs).argmax())) if rs.size else None
    from collections import Counter
    sensor = Counter(ctx.hs[i]["sensor"] for i in ev["idx"])
    desa = Counter(ctx.hs[i]["desa"] for i in ev["idx"] if ctx.hs[i].get("desa"))
    out = dict(id=ev["id"], deteksi_awal=_jam_wita(ev["t_awal"]), deteksi_akhir=_jam_wita(ev["t_akhir"]), n_titik=ev["n"],
               n_lintasan=ev["n_lintasan"], sensor=dict(sensor), luas_ha=ev["luas_ha"],
               tn_persen=round(100 * float(ctx.tn[m].mean()), 1) if m.any() else 0.0, resort=resort,
               desa=desa.most_common(1)[0][0] if desa else None, tahap=ev.get("tahap"))
    if h:
        for k, v in h.items():
            if not k.startswith("_") and k not in ("residu",):
                out[k] = v
    return out


def tulis_hasil(ctx, evs, info, hasil, gagal, status_path, folder, meta=None, log=print):
    """docs/status/asal_api.json (ringkasan), docs/data/asal_api/peta.geojson (asal, zona, arah),
    docs/data/asal_api/k/<id>.json (detail), docs/data/asal_api/wxs/<id>.wxs, titik_panas.geojson."""
    from . import weather as WX
    os.makedirs(os.path.join(folder, "k"), exist_ok=True)
    os.makedirs(os.path.join(folder, "wxs"), exist_ok=True)
    baris, fitur = [], []
    ada = set()
    for ev in evs:
        if not ev.get("layak"):
            continue
        h = hasil.get(ev["id"])
        r = _ringkas_kejadian(ctx, ev, h)
        if not h:
            r["mutu"] = "gagal"
            r["catatan"] = (gagal.get(ev["id"]) or "tidak dihitung").split(" | ")[0][:200]
            baris.append(r)
            continue
        ada.add(ev["id"])
        pk = os.path.join(folder, "k", ev["id"] + ".json")
        if "_peta" in h:
            det = dict(id=ev["id"], **h["_peta"], residu=h.get("residu", []))
            json.dump(det, open(pk, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
            WX.write_wxs(h["_wxs"], os.path.join(folder, "wxs", ev["id"] + ".wxs"))
        baris.append(r)
        try:
            det = json.load(open(pk, encoding="utf-8"))
        except (OSError, ValueError):
            det = {}
        a = h["asal"]
        sifat = dict(id=ev["id"], t_mulai=h["t_mulai"], mutu=r["mutu"], luas_ha=ev["luas_ha"], resort=r["resort"])
        fitur.append(dict(type="Feature", geometry=dict(type="Point", coordinates=[a["lon"], a["lat"]]),
                          properties=dict(sifat, jenis="asal")))
        for z in ("z90", "z50"):
            if det.get(z):
                fitur.append(dict(type="Feature", geometry=det[z], properties=dict(id=ev["id"], jenis=z, mutu=r["mutu"])))
        if h.get("pusat_areal") and h.get("jarak_ke_pusat_m", 0) > 60:
            fitur.append(dict(type="Feature", properties=dict(id=ev["id"], jenis="arah", mutu=r["mutu"]),
                              geometry=dict(type="LineString", coordinates=[[a["lon"], a["lat"]],
                                                                           [h["pusat_areal"][1], h["pusat_areal"][0]]])))
    # hapus detail kejadian yang tidak ada lagi
    for sub, ext in (("k", ".json"), ("wxs", ".wxs")):
        for f in os.listdir(os.path.join(folder, sub)):
            if f.endswith(ext) and f[:-len(ext)] not in ada:
                os.remove(os.path.join(folder, sub, f))
    json.dump(dict(type="FeatureCollection", features=fitur), open(os.path.join(folder, "peta.geojson"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    # titik panas untuk peta
    aoi = [p for p in ctx.hs if p.get("tn") is not None and _dalam_grid(ctx, p)]
    tulis_hotspot(aoi, os.path.join(folder, "titik_panas.geojson"))
    # jam rawan
    jr_semua, jr_bs, det_awal = np.zeros(24), np.zeros(24), np.zeros(24)
    n_bs = 0
    for r in baris:
        if r.get("jam_hari"):
            v = np.array(r["jam_hari"])
            jr_semua += v
            if r.get("mutu") in ("baik", "sedang"):
                jr_bs += v
                n_bs += 1
            det_awal[int(r["deteksi_awal"][11:13])] += 1
    det_semua = np.zeros(24)
    for p in aoi:
        det_semua[p["t"].hour] += 1
    jam_rawan = dict(semua=[round(float(v), 3) for v in jr_semua], baik_sedang=[round(float(v), 3) for v in jr_bs],
                     n_semua=int(sum(1 for r in baris if r.get("jam_hari"))), n_baik_sedang=n_bs,
                     deteksi_pertama=[int(v) for v in det_awal], deteksi_semua=[int(v) for v in det_semua])
    ok = [r for r in baris if r.get("t_mulai")]
    from collections import Counter
    st = dict(
        versi=VERSI, dibuat=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), zona="WITA",
        metode=dict(nama="inversi FARSITE mundur-maju (backward/forward travel-time inversion)",
                    parameter=dict(PAR, beta_bentuk=BETA, k_bebas=list(K_BEBAS), jarak_kelompok_m=1500, jeda_kelompok_jam=36,
                                   jendela_sebelum_deteksi_jam=30, jendela_skor_jam=72, sel_m=ctx.g.res, angin="medan",
                                   tajuk="finney")),
        data=dict(meta or {}, **info),
        ringkasan=dict(dianalisis=len(baris), berhasil=len(ok), gagal=len(baris) - len(ok),
                       mutu=dict(Counter(r.get("mutu") for r in baris)),
                       asal_di_luar_tn=sum(1 for r in ok if not r["asal"]["di_tn"]),
                       luas_dianalisis_ha=round(sum(r["luas_ha"] for r in baris), 1),
                       luas_berhasil_ha=round(sum(r["luas_ha"] for r in ok), 1)),
        jam_rawan=jam_rawan, kalibrasi=ctx.kal or {}, kejadian=sorted(baris, key=lambda r: r["deteksi_awal"]))
    os.makedirs(os.path.dirname(status_path) or ".", exist_ok=True)
    json.dump(st, open(status_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    log(f"ditulis {status_path}: {len(baris)} kejadian, {len(ok)} berhasil")
    return st


def _dalam_grid(ctx, p):
    x, y = ctx.g.ke_utm.transform(p["lon"], p["lat"])
    r, c = ctx.g.rc(x, y)
    return 0 <= int(r) < ctx.g.ny and 0 <= int(c) < ctx.g.nx


def hasil_lama(status_path, folder):
    """Hasil run sebelumnya (untuk cache per kejadian)."""
    try:
        st = json.load(open(status_path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for r in st.get("kejadian", []):
        if r.get("t_mulai") and r.get("sidik") and os.path.exists(os.path.join(folder, "k", r["id"] + ".json")):
            out[r["id"]] = r
    return out


# =============================================================== cuaca uji (tanpa jaringan)
class CuacaUji:
    """Cuaca buatan untuk uji di luar GitHub Actions (siklus harian kemarau, angin timur-tenggara)."""

    def __init__(self, ws=20.0, wd=110.0):
        self.ws, self.wd = ws, wd

    def ambil(self, lat, lon):
        from . import weather as WX
        recs = []
        t = dt.datetime(2026, 1, 1)
        while t < dt.datetime(2026, 12, 31):
            h = t.hour
            s = math.sin(math.pi * (h - 8) / 12.0) if 8 <= h <= 20 else -0.3
            temp = 26 + 6 * s
            rh = 70 - 35 * max(s, -0.4)
            ws = self.ws * (0.6 + 0.5 * max(s, 0))
            recs.append(WX.WxRecord(t, round(temp), round(min(rh, 95)), 0.0, round(ws), self.wd, 10))
            t += dt.timedelta(hours=1)
        return WX.Weather(300.0, recs)
