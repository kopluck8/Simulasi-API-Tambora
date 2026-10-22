"""Titik panas (hotspot) VIIRS/MODIS dari NASA FIRMS, untuk memastikan bekas terbakar memang kebakaran.

Perlu MAP_KEY gratis dari https://firms.modaps.eosdis.nasa.gov/api/map_key/ (env FIRMS_MAP_KEY).
API area: maksimal 5 hari per permintaan, jadi rentang panjang dipecah.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os

import numpy as np

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{bbox}/{days}/{date}"
SUMBER = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT",
          "VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "MODIS_SP"]


def key():
    return os.environ.get("FIRMS_MAP_KEY", "").strip()


def bbox_lonlat(cfg, pad_m=2000):
    from rasterio.warp import transform_bounds
    a = cfg["aoi"]
    w, s, e, n = transform_bounds(f"EPSG:{a['epsg']}", "EPSG:4326", a["west"] - pad_m, a["south"] - pad_m,
                                  a["east"] + pad_m, a["north"] + pad_m)
    return f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}"


def fetch(cfg, start: dt.date, end: dt.date, sumber=None):
    """Hotspot [start, end] (tanggal UTC). Mengembalikan list dict lat, lon, tanggal, sumber, conf."""
    import requests
    k = key()
    if not k:
        raise RuntimeError("FIRMS_MAP_KEY belum diisi")
    bbox = bbox_lonlat(cfg)
    out, seen, per, gagal = [], set(), {}, {}
    for src in sumber or SUMBER:
        d = start
        while d <= end:
            days = min(5, (end - d).days + 1)
            try:
                r = requests.get(API.format(key=k, src=src, bbox=bbox, days=days, date=d.isoformat()), timeout=60)
                txt = r.text.strip()
                if r.status_code != 200 or txt.lower().startswith(("invalid", "error")):
                    raise RuntimeError(f"{r.status_code} {txt[:150]}")
            except Exception as e:  # satu sumber/tanggal gagal (mis. arsip SP belum tersedia) tidak menghentikan semua
                gagal.setdefault(src, str(e))
                d += dt.timedelta(days=days)
                continue
            for row in csv.DictReader(io.StringIO(txt)):
                try:
                    lat, lon = float(row["latitude"]), float(row["longitude"])
                except (KeyError, ValueError):
                    continue
                t = row.get("acq_date", "")
                sig = (round(lat, 3), round(lon, 3), t)
                if sig in seen:
                    continue
                seen.add(sig)
                per[src] = per.get(src, 0) + 1
                out.append(dict(lat=lat, lon=lon, tanggal=t, sumber=src, conf=row.get("confidence", "")))
            d += dt.timedelta(days=days)
    print("   titik panas per sumber:", per or "(tidak ada)")
    for src, e in gagal.items():
        print(f"   {src}: sebagian gagal ({e})")
    if gagal and len(gagal) == len(sumber or SUMBER):
        raise RuntimeError("semua sumber FIRMS gagal: " + next(iter(gagal.values())))
    return out


def mask(points, shape, transform, epsg, buffer_m):
    """Raster bool: piksel dalam radius buffer_m dari hotspot mana pun."""
    from pyproj import Transformer
    from scipy import ndimage
    m = np.zeros(shape, bool)
    if not points:
        return m
    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    x, y = tf.transform([p["lon"] for p in points], [p["lat"] for p in points])
    inv = ~transform
    for xi, yi in zip(x, y):
        c, r = inv * (xi, yi)
        r, c = int(r), int(c)
        if 0 <= r < shape[0] and 0 <= c < shape[1]:
            m[r, c] = True
    res = abs(transform.a)
    rad = int(round(buffer_m / res))
    yy, xx = np.ogrid[-rad:rad + 1, -rad:rad + 1]
    return ndimage.binary_dilation(m, structure=(xx * xx + yy * yy) <= rad * rad)
