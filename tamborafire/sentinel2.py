"""Ambil citra Sentinel-2 L2A terbaru untuk AOI dan buat komposit 'pengamatan cerah terakhir'.

Sumber STAC tanpa akun: Microsoft Planetary Computer (bawaan, URL ditandatangani otomatis)
atau Element84 Earth Search (AWS). Nilai DN mentah dipakai apa adanya (termasuk offset +1000
baseline 04.00), sama dengan NDVI/NBR di file latihan dan batas kelas K1-K4.
"""
from __future__ import annotations

import datetime as dt
import time

import numpy as np
import requests

SRC = {
    "planetary": dict(url="https://planetarycomputer.microsoft.com/api/stac/v1/search", coll="sentinel-2-l2a",
                      bands=dict(b04="B04", b08="B08", b12="B12", scl="SCL"), cloud="eo:cloud_cover"),
    "earthsearch": dict(url="https://earth-search.aws.element84.com/v1/search", coll="sentinel-2-l2a",
                        bands=dict(b04="red", b08="nir", b12="swir22", scl="scl"), cloud="eo:cloud_cover"),
}
CLEAR_SCL = {4, 5, 6, 7, 11}   # vegetasi, tanah, air, unclassified, salju; tolak awan/bayangan/cirrus


def _bbox_ll(cfg):
    from pyproj import Transformer
    a = cfg["aoi"]
    t = Transformer.from_crs(a["epsg"], 4326, always_xy=True)
    xs, ys = t.transform([a["west"], a["east"], a["west"], a["east"]], [a["south"], a["south"], a["north"], a["north"]])
    return [min(xs), min(ys), max(xs), max(ys)]


def search(cfg, end: dt.date, days: int, source="planetary", max_cloud=70, start: dt.date | None = None):
    s = SRC[source]
    start = start or end - dt.timedelta(days=days)
    body = {"collections": [s["coll"]], "bbox": _bbox_ll(cfg), "limit": 200,
            "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
            "query": {s["cloud"]: {"lt": max_cloud}}}
    r = requests.post(s["url"], json=body, timeout=120)
    r.raise_for_status()
    feats = r.json()["features"]
    feats.sort(key=lambda f: f["properties"]["datetime"])
    return feats


_tokens = {}


def _sign(href, source):
    if source != "planetary":
        return href
    acct = href.split("//")[1].split(".")[0]
    cont = href.split(".net/")[1].split("/")[0]
    key = acct + "/" + cont
    tok = _tokens.get(key)
    if not tok or tok[1] < time.time() + 300:
        r = requests.get(f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{acct}/{cont}", timeout=60)
        r.raise_for_status()
        d = r.json()
        _tokens[key] = tok = (d["token"], time.time() + 3000)
    return href + "?" + tok[0]


def _read_to_grid(href, cfg, resampling):
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_origin
    a = cfg["aoi"]
    res = float(a["res"])
    nx, ny = int((a["east"] - a["west"]) / res), int((a["north"] - a["south"]) / res)
    tr = from_origin(a["west"], a["north"], res, res)
    out = np.zeros((ny, nx), np.float32)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="2"):
        with rasterio.open(href) as src:
            reproject(rasterio.band(src, 1), out, dst_transform=tr, dst_crs=f"EPSG:{a['epsg']}",
                      resampling=getattr(Resampling, resampling), src_nodata=0, dst_nodata=0)
    return out


def latest_clear_composite(cfg, items, source="planetary", log=print):
    """Untuk tiap piksel: nilai dari pengamatan cerah paling baru. Mengembalikan dict b04,b08,b12 (NaN
    bila tidak pernah cerah), tanggal pengamatan (hari sejak 1970) dan daftar citra yang dipakai."""
    s = SRC[source]
    comp = None
    used = []
    for f in items:  # urut lama -> baru, yang baru menimpa
        assets = f["assets"]
        try:
            scl = _read_to_grid(_sign(assets[s["bands"]["scl"]]["href"], source), cfg, "nearest")
            clear = np.isin(scl.astype(np.int16), list(CLEAR_SCL))
            if clear.sum() == 0:
                continue
            bands = {k: _read_to_grid(_sign(assets[v]["href"], source), cfg,
                                      "nearest" if k == "b12" else "bilinear")
                     for k, v in s["bands"].items() if k != "scl"}
        except Exception as e:  # satu citra gagal tidak menghentikan update
            log(f"lewati {f['id']}: {e}")
            continue
        if comp is None:
            comp = {k: np.full(scl.shape, np.nan, np.float32) for k in bands}
            comp["hari"] = np.full(scl.shape, -1, np.int32)
        ok = clear & (bands["b08"] > 0)
        d = dt.date.fromisoformat(f["properties"]["datetime"][:10])
        for k in bands:
            comp[k][ok] = bands[k][ok]
        comp["hari"][ok] = (d - dt.date(1970, 1, 1)).days
        used.append(f["id"])
        log(f"pakai {f['id']} ({ok.mean() * 100:.0f}% AOI cerah)")
    if comp is None:
        raise RuntimeError("Tidak ada citra cerah di jendela waktu ini.")
    return comp, used
