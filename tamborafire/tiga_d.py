"""Data untuk tab 3D di halaman simulasi.

Satu file biner ber-gzip (data3d.bin.gz) berisi elevasi, bahan bakar, dan keluaran simulasi
(waktu tiba, laju api, panjang nyala, intensitas, tipe api), dipotong di sekitar area terbakar.

Format (little-endian):
    b"TF3D" | uint32 panjang_header | header JSON (UTF-8) | pad ke kelipatan 4 | larik ...
Tiap larik baris-per-baris (baris 0 = utara), tipe & lokasi byte ada di header["larik"].
Nilai asli = mentah * skala + offset; nilai `kosong` = tidak ada (mis. sel tidak terbakar).
"""
from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import os
import struct
import zipfile

import numpy as np

MAGIC = b"TF3D"
VERSI = 1
NAMA_FILE = "data3d.bin.gz"


# ------------------------------------------------------------------ potong & perkecil
def _jendela(arrival, ign_rc, res, margin_m=1200.0, min_m=3000.0):
    """Baris/kolom (r0, r1, c0, c1) kotak area terbakar + tepi, minimal min_m per sisi."""
    ny, nx = arrival.shape
    burned = np.isfinite(arrival)
    if burned.any():
        rr, cc = np.nonzero(burned)
        r0, r1, c0, c1 = rr.min(), rr.max() + 1, cc.min(), cc.max() + 1
    else:
        r, c = int(round(ign_rc[0])), int(round(ign_rc[1]))
        r0, r1, c0, c1 = r, r + 1, c, c + 1
    tepi = max(margin_m, 0.25 * max(r1 - r0, c1 - c0) * res) / res
    r0, r1, c0, c1 = r0 - tepi, r1 + tepi, c0 - tepi, c1 + tepi
    minc = min_m / res
    for a, b in (("r", None), ("c", None)):
        lo, hi = (r0, r1) if a == "r" else (c0, c1)
        if hi - lo < minc:
            mid = (lo + hi) / 2
            lo, hi = mid - minc / 2, mid + minc / 2
        if a == "r":
            r0, r1 = lo, hi
        else:
            c0, c1 = lo, hi
    r0, c0 = max(int(np.floor(r0)), 0), max(int(np.floor(c0)), 0)
    r1, c1 = min(int(np.ceil(r1)), ny), min(int(np.ceil(c1)), nx)
    return r0, r1, c0, c1


def _blok(a, f, cara):
    if f <= 1:
        return a
    ny, nx = a.shape[0] // f, a.shape[1] // f
    b = a[:ny * f, :nx * f].reshape(ny, f, nx, f)
    if cara == "min":       # waktu tiba: sel tercepat di blok (luas terbakar tidak hilang)
        x = np.where(np.isfinite(b), b, np.inf).min(axis=(1, 3))
        return np.where(np.isfinite(x), x, np.nan)
    if cara == "max":
        return b.max(axis=(1, 3))
    if cara == "mean":
        return b.astype(np.float64).mean(axis=(1, 3))
    return b[:, f // 2, :, f // 2]   # "tengah" (kode bahan bakar)


def _u16(a, presisi, kosong_mask=None):
    """Kuantisasi ke uint16; skala minimal = presisi, 65535 dipakai untuk 'kosong'."""
    a = np.asarray(a, np.float64)
    ok = np.isfinite(a) if kosong_mask is None else (~kosong_mask & np.isfinite(a))
    lo = float(np.floor(a[ok].min())) if ok.any() and a[ok].min() < 0 else 0.0
    hi = float(a[ok].max() - lo) if ok.any() else 0.0
    skala = max(presisi, hi / 65000.0)
    raw = np.full(a.shape, 65535, np.uint16)
    raw[ok] = np.clip(np.round((a[ok] - lo) / skala), 0, 65534).astype(np.uint16)
    return raw, skala, lo


# ------------------------------------------------------------------ georeferensi
def _affine(src, dst):
    """Kuadrat terkecil: dst ≈ [a b c; d e f] @ [x y 1]."""
    A = np.column_stack([src[:, 0], src[:, 1], np.ones(len(src))])
    cx, *_ = np.linalg.lstsq(A, dst[:, 0], rcond=None)
    cy, *_ = np.linalg.lstsq(A, dst[:, 1], rcond=None)
    return [float(v) for v in cx] + [float(v) for v in cy]


def _geo(west, north, res, ny, nx, crs):
    from pyproj import Transformer
    tf = Transformer.from_crs(crs or "EPSG:32750", 4326, always_xy=True)
    u, v = np.meshgrid(np.linspace(0, nx, 5), np.linspace(0, ny, 5))
    u, v = u.ravel(), v.ravel()
    lon, lat = tf.transform(west + u * res, north - v * res)
    lon, lat = np.asarray(lon), np.asarray(lat)
    uv = np.column_stack([u, v])
    ll = np.column_stack([lon, lat])
    sudut = {}
    for k, (cu, cv) in dict(bl=(0, 0), tl=(nx, 0), bd=(0, ny), tg=(nx, ny)).items():
        x, y = tf.transform(west + cu * res, north - cv * res)
        sudut[k] = [round(float(y), 6), round(float(x), 6)]
    return dict(piksel_ke_lonlat=_affine(uv, ll), lonlat_ke_piksel=_affine(ll, uv), sudut=sudut)


# ------------------------------------------------------------------ pembangun
def bangun(arrival, ros, flame, fli, crown, elev, fuel, west, north, res, crs, ign_xy, meta, maks=1024):
    """Kembalikan bytes gzip. Semua larik pada grid simulasi yang sama (baris 0 = utara)."""
    arrival = np.asarray(arrival, np.float64)
    ign_rc = ((north - ign_xy[1]) / res - 0.5, (ign_xy[0] - west) / res - 0.5)
    r0, r1, c0, c1 = _jendela(arrival, ign_rc, res)
    sl = (slice(r0, r1), slice(c0, c1))
    f = int(np.ceil(max(r1 - r0, c1 - c0) / maks))
    lar = dict(
        elev=_blok(np.asarray(elev, np.float64)[sl], f, "mean"),
        tiba=_blok(arrival[sl], f, "min"),
        ros=_blok(np.nan_to_num(np.asarray(ros, np.float64)[sl]), f, "max"),
        nyala=_blok(np.nan_to_num(np.asarray(flame, np.float64)[sl]), f, "max"),
        fli=_blok(np.nan_to_num(np.asarray(fli, np.float64)[sl]), f, "max"),
        tipe=_blok(np.asarray(crown)[sl].astype(np.int16), f, "max"),
        fuel=_blok(np.asarray(fuel)[sl].astype(np.int32), f, "tengah"),
    )
    ny, nx = lar["elev"].shape
    res2 = res * f
    w2, n2 = west + c0 * res, north - r0 * res
    burned = np.isfinite(lar["tiba"])
    bagian, larik, pos = [], [], 0

    def tambah(nama, raw, tipe, skala=1.0, offset=0.0, kosong=None):
        nonlocal pos
        b = np.ascontiguousarray(raw).astype("<" + raw.dtype.str[1:]).tobytes()
        pad = (-len(b)) % 4
        larik.append(dict(nama=nama, tipe=tipe, skala=skala, offset=offset, kosong=kosong, byte=pos, panjang=len(b)))
        bagian.append(b + b"\0" * pad)
        pos += len(b) + pad

    raw, s, o = _u16(lar["elev"], 0.1)
    elev_lo, elev_hi = float(np.nanmin(lar["elev"])), float(np.nanmax(lar["elev"]))
    tambah("elev", raw, "u16", s, o)
    raw, s, o = _u16(lar["tiba"], 0.1)
    tambah("tiba", raw, "u16", s, o, 65535)
    for nama, pres in (("ros", 0.01), ("nyala", 0.01), ("fli", 1.0)):
        raw, s, o = _u16(lar[nama], pres)
        tambah(nama, raw, "u16", s, o)
    tambah("tipe", np.clip(lar["tipe"], 0, 255).astype(np.uint8), "u8")
    fu = lar["fuel"]
    if fu.max() <= 255 and fu.min() >= 0:
        tambah("fuel", fu.astype(np.uint8), "u8")
    else:
        tambah("fuel", np.clip(fu, 0, 65535).astype(np.uint16), "u16")

    tmax = float(np.nanmax(lar["tiba"])) if burned.any() else 0.0
    head = dict(
        versi=VERSI, dibuat=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        kolom=nx, baris=ny, sel_m=res2, sel_asli_m=res, perkecil=f,
        utm=dict(barat=w2, utara=n2, crs=crs if (crs or "").startswith("EPSG") else None),
        elev_min=round(elev_lo, 1), elev_maks=round(elev_hi, 1),
        titik_api=[round((ign_xy[0] - w2) / res2, 2), round((n2 - ign_xy[1]) / res2, 2)],
        menit_maks=round(tmax, 1), luas_terbakar_ha=round(float(burned.sum() * res2 ** 2 / 1e4), 1),
        larik=larik, **_geo(w2, n2, res2, ny, nx, crs))
    head.update(meta or {})
    hj = json.dumps(head, default=str, separators=(",", ":")).encode()
    hj += b" " * ((-(len(hj) + 8)) % 4)
    raw_all = MAGIC + struct.pack("<I", len(hj)) + hj + b"".join(bagian)
    return gzip.compress(raw_all, 9, mtime=0)


def baca(data: bytes):
    """Untuk tes: bytes (gzip atau tidak) -> (header, {nama: larik float, NaN = kosong})."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    assert data[:4] == MAGIC
    n = struct.unpack("<I", data[4:8])[0]
    h = json.loads(data[8:8 + n])
    base = 8 + n
    out = {}
    for L in h["larik"]:
        dtp = {"u16": "<u2", "u8": "u1"}[L["tipe"]]
        a = np.frombuffer(data, dtp, L["panjang"] // np.dtype(dtp).itemsize, base + L["byte"]).reshape(h["baris"], h["kolom"])
        v = a.astype(np.float64) * L["skala"] + L["offset"]
        if L["kosong"] is not None:
            v[a == L["kosong"]] = np.nan
        out[L["nama"]] = v
    return h, out


# ------------------------------------------------------------------ cuaca
def _cuaca_dari_wxs(path, mulai, akhir, angin=None):
    from .weather import read_wxs
    w = read_wxs(path)
    ws_o = wd_o = None
    if angin:
        a = str(angin).split(",")
        if len(a) == 2 and a[0].strip() and a[1].strip():
            ws_o, wd_o = float(a[0]), float(a[1])
    out = []
    t = mulai.replace(minute=0, second=0, microsecond=0)
    while t < akhir:
        r = w.at(max(t, mulai))
        out.append(dict(t=t.strftime("%Y-%m-%dT%H:%M"), ws=round(ws_o if ws_o is not None else r.ws, 1),
                        wd=round((wd_o if wd_o is not None else r.wd) % 360, 0), suhu=round(r.temp, 1),
                        rh=round(r.rh, 0), hujan=round(r.pcp, 2)))
        t += dt.timedelta(hours=1)
    return out


def _meta(summ, jam=None, masukan=None, catatan=None):
    info = {k: summ.get(k) for k in ("mode", "start", "end", "wind", "ws", "wd", "minutes", "metode_tajuk",
                                     "luas_per_tipe_api_ha", "fixed_moisture") if k in summ}
    m = dict(simulasi=info)
    if summ.get("titik_latlon"):
        m["titik_latlon"] = summ["titik_latlon"]
    if jam:
        m["jam"] = jam
    if masukan:
        m["masukan"] = masukan
    if catatan:
        m["catatan"] = catatan
    return m


def dari_hasil(h, ls, summ, out, wxs=None):
    """Dipanggil setelah simulasi (--web): tulis out/data3d.bin.gz."""
    phys = ls.physical()
    jam = list(getattr(h, "jam", []) or [])
    if not jam and wxs and summ.get("mode") == "farsite" and summ.get("start"):
        s0 = dt.datetime.fromisoformat(str(summ["start"]))
        jam = _cuaca_dari_wxs(wxs, s0, dt.datetime.fromisoformat(str(summ["end"])))
    data = bangun(h.arrival, h.ros, h.flame, h.intensity, h.crown, phys["elev"], phys["fuel"],
                  ls.west, ls.north, ls.res, _crs_text(ls.crs_wkt), summ["titik_api"], _meta(summ, jam))
    p = os.path.join(out, NAMA_FILE)
    open(p, "wb").write(data)
    print(f"data 3D: {p} ({len(data) / 1e6:.2f} MB)")
    return p


def _crs_text(wkt):
    if not wkt:
        return "EPSG:32750"
    try:
        from pyproj import CRS
        e = CRS.from_user_input(wkt).to_epsg()
        return f"EPSG:{e}" if e else wkt
    except Exception:
        return wkt


def dari_folder(src, lcp_path, web=None, masukan=None, catatan=None):
    """Untuk simulasi lama: src = folder hasil atau hasil.zip; lcp_path = landscape asal (elevasi & fuel)."""
    import rasterio
    from .lcp import read_lcp
    tmp = None
    if str(src).endswith(".zip"):
        import tempfile
        tmp = tempfile.mkdtemp()
        zipfile.ZipFile(src).extractall(tmp)
        src = tmp
    def tif(n):
        with rasterio.open(os.path.join(src, n)) as r:
            return r.read(1).astype(np.float64), r.transform, r.crs
    arr, tr, crs = tif("waktu_tiba_menit.tif")
    arr[arr < 0] = np.nan
    ros, flame, fli, ct = (tif(n)[0] for n in ("ros_m_menit.tif", "panjang_nyala_m.tif", "intensitas_kw_m.tif", "tipe_api.tif"))
    west, north, res = tr.c, tr.f, tr.a
    ny, nx = arr.shape
    ls = read_lcp(lcp_path)
    sub = ls.crop(west, north - ny * res, west + nx * res, north)
    fct = int(round(res / ls.res))
    if fct > 1:
        sub = sub.coarsen(fct)
    if sub.shape == arr.shape and abs(sub.west - west) < 1e-3 and abs(sub.north - north) < 1e-3:
        ph = sub.physical()
        elev, fuel = ph["elev"], ph["fuel"]
    else:   # grid tidak persis sama: ambil sel terdekat
        ph = ls.physical()
        cc = np.clip(((west + (np.arange(nx) + 0.5) * res - ls.west) / ls.res).astype(int), 0, ls.shape[1] - 1)
        rr = np.clip(((ls.north - (north - (np.arange(ny) + 0.5) * res)) / ls.res).astype(int), 0, ls.shape[0] - 1)
        elev, fuel = ph["elev"][np.ix_(rr, cc)], ph["fuel"][np.ix_(rr, cc)]
    summ = dict(json.load(open(os.path.join(src, "ringkasan.json"))))
    if web:
        summ.update({k: v for k, v in web.items() if k not in summ})
    jam = []
    if summ.get("mode") == "farsite" and summ.get("start"):
        wx = [f for f in sorted(os.listdir(src)) if f.lower().endswith(".wxs")]
        if wx:
            jam = _cuaca_dari_wxs(os.path.join(src, wx[0]), dt.datetime.fromisoformat(str(summ["start"])),
                                  dt.datetime.fromisoformat(str(summ["end"])), (masukan or {}).get("angin"))
    crs_s = f"EPSG:{crs.to_epsg()}" if crs and crs.to_epsg() else (crs.to_wkt() if crs else "EPSG:32750")
    return bangun(arr, ros, flame, fli, ct, elev, fuel, west, north, res, crs_s, summ["titik_api"],
                  _meta(summ, jam, masukan, catatan))


# ------------------------------------------------------------------ terbit ke cabang hasil-3d
CABANG = "hasil-3d"


def _gh(method, url, token, **kw):
    import requests
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    return requests.request(method, url, headers=h, timeout=120, **kw)


def ada(repo, sim_id, token):
    r = _gh("GET", f"https://api.github.com/repos/{repo}/contents/sim/{sim_id}/{NAMA_FILE}?ref={CABANG}", token)
    return r.json().get("sha") if r.status_code == 200 else None


def terbitkan(path, sim_id, repo, token, coba=5):
    """Simpan file ke cabang hasil-3d (sim/<id>/data3d.bin.gz) lewat Contents API; halaman membacanya
    dari raw.githubusercontent.com (CORS terbuka, tanpa batas API)."""
    import base64
    import time
    isi = base64.b64encode(open(path, "rb").read()).decode()
    url = f"https://api.github.com/repos/{repo}/contents/sim/{sim_id}/{NAMA_FILE}"
    _buat_cabang(repo, token)
    for k in range(coba):
        body = dict(message=f"Data 3D simulasi {sim_id}", content=isi, branch=CABANG)
        sha = ada(repo, sim_id, token)
        if sha:
            body["sha"] = sha
        r = _gh("PUT", url, token, json=body)
        if r.status_code in (200, 201):
            print(f"data 3D terbit: {CABANG}/sim/{sim_id}/{NAMA_FILE}")
            return True
        print(f"gagal ({r.status_code}): {r.text[:300]}; coba lagi")
        time.sleep(3 * (k + 1))
    raise SystemExit("data 3D gagal diterbitkan")


def _buat_cabang(repo, token):
    """Cabang yatim berisi README saja, supaya tidak menggandakan isi main."""
    import base64
    api = f"https://api.github.com/repos/{repo}/git"
    if _gh("GET", f"{api}/ref/heads/{CABANG}", token).status_code == 200:
        return
    teks = ("# Data 3D simulasi\n\nCabang ini diisi otomatis oleh workflow *Simulasi api* dan *Data 3D*.\n"
            "Tiap simulasi: `sim/<id>/data3d.bin.gz`, dibaca tab 3D di halaman simulasi.\n")
    blob = _gh("POST", f"{api}/blobs", token, json=dict(content=base64.b64encode(teks.encode()).decode(),
                                                         encoding="base64")).json()["sha"]
    tree = _gh("POST", f"{api}/trees", token, json=dict(tree=[dict(path="README.md", mode="100644", type="blob",
                                                                  sha=blob)])).json()["sha"]
    com = _gh("POST", f"{api}/commits", token, json=dict(message="Cabang data 3D", tree=tree, parents=[])).json()["sha"]
    _gh("POST", f"{api}/refs", token, json=dict(ref=f"refs/heads/{CABANG}", sha=com))
