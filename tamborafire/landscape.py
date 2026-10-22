"""Membangun landscape (.lcp) dari GeoTIFF dasar dan memperbaruinya tiap minggu dari Sentinel-2.

Aturan (keputusan proyek):
- Peta fuel dasar = K1-K4 (30-33) dari citra awal kemarau (Juni-Juli), hutan TL6/TU1, dst.
- Peta fuel jangan dibangun ulang dari citra sesudah kebakaran. Update mingguan karena itu bawaannya
  hanya menandai bekas terbakar baru (dNBR terhadap NBR awal kemarau) sebagai fuel 'terbakar'.
- Mode 'batas' / 'kuantil' tersedia untuk tahun berikutnya bila batas kelas sudah dikunci dari survei.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np

from .lcp import Landscape, write_lcp

LAYER_KEYS = ["elev", "slope", "aspect", "fuel", "cover", "height", "cbh", "cbd"]


def _read(path):
    import rasterio
    with rasterio.open(path) as d:
        return d.read(1), d.transform, d.crs


def load_base(folder, cfg):
    """Baca GeoTIFF dasar -> dict int16 + transform + crs wkt."""
    names = cfg["dasar"]
    out = {}
    tr = crs = None
    for k in LAYER_KEYS:
        a, tr, crs = _read(os.path.join(folder, names[k]))
        out[k] = a.astype(np.int16)
    return out, tr, crs


def base_nbr(folder, cfg):
    """NBR awal kemarau (DN mentah, sama dengan file latihan). Dibuat dari s2_median bila belum ada."""
    import rasterio
    p = os.path.join(folder, cfg["dasar"]["nbr"])
    if os.path.exists(p):
        a, _, _ = _read(p)
        return a.astype(np.float32) / 10000.0, p, False
    with rasterio.open(os.path.join(folder, cfg["dasar"]["s2_median"])) as d:
        desc = list(d.descriptions)
        b8 = d.read(desc.index("B08") + 1 if "B08" in desc else 4).astype(np.float32)
        b12 = d.read(desc.index("B12") + 1 if "B12" in desc else 6).astype(np.float32)
        prof = d.profile
    nbr = np.where((b8 + b12) > 0, (b8 - b12) / np.maximum(b8 + b12, 1), np.nan).astype(np.float32)
    prof.update(count=1, dtype="int16", nodata=-32768, compress="deflate")
    with rasterio.open(p, "w", **prof) as o:
        o.write(np.where(np.isfinite(nbr), np.round(nbr * 10000), -32768).astype(np.int16), 1)
    return nbr, p, True


def to_landscape(layers, transform, crs_wkt, cfg, description=""):
    lay = {k: layers[k].astype(np.int16) for k in LAYER_KEYS}
    return Landscape(transform.c, transform.f, transform.a, lay, int(cfg["aoi"].get("latitude", -8)),
                     (0, 0, 2, 0, 1, 3, 3, 3, 1, 0), description, crs_wkt)


def classify(ndvi, grass_mask, mode, cfg):
    k = cfg["mingguan"]
    if mode == "batas":
        b = k["batas_ndvi"]
    else:
        v = ndvi[grass_mask & np.isfinite(ndvi)]
        cum = np.cumsum(k["proporsi"])[:-1]
        b = list(np.quantile(v, cum)) if v.size else k["batas_ndvi"]
    cls = np.full(ndvi.shape, 30, np.int16)
    cls[ndvi >= b[0]] = 31
    cls[ndvi >= b[1]] = 32
    cls[ndvi >= b[2]] = 33
    return cls, [round(float(x), 4) for x in b]


def remove_small(mask, res, min_ha):
    if min_ha <= 0 or not mask.any():
        return mask
    from scipy import ndimage
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    if n == 0:
        return mask
    size = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1)) * res * res / 1e4
    keep = np.zeros(n + 1, bool)
    keep[1:] = size >= min_ha
    return keep[lab]


def update(base_layers, nbr0, composite, cfg, scar_prev=None, mode=None):
    """composite: dict b04,b08,b12 (float DN mentah, NaN = tidak ada pengamatan cerah).
    Mengembalikan (fuel baru, bekas_terbakar_musim, ringkasan)."""
    k = cfg["mingguan"]
    mode = mode or k["mode_kelas"]
    fuel0 = base_layers["fuel"].astype(np.int16)
    grass = (fuel0 >= 30) & (fuel0 <= 33)
    b4, b8, b12 = composite["b04"], composite["b08"], composite["b12"]
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = (b8 - b4) / (b8 + b4)
        nbr = (b8 - b12) / (b8 + b12)
    seen = np.isfinite(ndvi) & np.isfinite(nbr)
    dnbr = nbr0 - nbr
    burnable = (fuel0 >= 30) & (fuel0 < 90) | ((fuel0 >= 100) & (fuel0 < 300))
    new_scar = seen & burnable & (dnbr > k["dnbr_ambang"])
    res = float(cfg["aoi"]["res"])
    new_scar = remove_small(new_scar, res, k.get("luas_min_ha", 1.0))
    scar = new_scar if scar_prev is None else (scar_prev.astype(bool) | new_scar)
    fuel = fuel0.copy()
    info = dict(mode=mode, piksel_cerah_persen=round(100 * float(seen[grass].mean()) if grass.any() else 0, 1))
    if mode in ("batas", "kuantil"):
        cls, b = classify(ndvi, grass & seen, mode, cfg)
        upd = grass & seen
        fuel[upd] = cls[upd]
        info["batas_ndvi"] = b
    fuel[scar] = int(k["kode_terbakar"])
    ha = res * res / 1e4
    info["bekas_terbakar_baru_ha"] = round(float(new_scar.sum() * ha), 1)
    info["bekas_terbakar_musim_ha"] = round(float(scar.sum() * ha), 1)
    info["luas_fuel_ha"] = {str(int(v)): round(float(c * ha), 1) for v, c in zip(*np.unique(fuel, return_counts=True))}
    return fuel, scar.astype(np.uint8), ndvi.astype(np.float32), dnbr.astype(np.float32), info


def preview_png(fuel, path, title=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, BoundaryNorm
    codes = [30, 31, 32, 33, 91, 93, 98, 99, 142, 161, 186]
    cols = ["#f7e08a", "#e9b949", "#d08a2c", "#9b5a1a", "#7a7a7a", "#c9a7e0", "#4a7fb5", "#cfcfcf",
            "#8fbf6f", "#4f9a4f", "#1f5f2f"]
    lut = np.full(1000, -1, np.int16)
    for i, c in enumerate(codes):
        lut[c] = i
    idx = lut[np.clip(fuel, 0, 999)].astype(float)
    idx[idx < 0] = np.nan
    step = max(1, fuel.shape[1] // 1600)
    fig, ax = plt.subplots(figsize=(10, 8), dpi=110)
    ax.imshow(idx[::step, ::step], cmap=ListedColormap(cols), norm=BoundaryNorm(np.arange(-0.5, len(codes)), len(codes)),
              interpolation="nearest")
    ax.set_title(title)
    ax.axis("off")
    names = ["K1", "K2", "K3", "K4", "permukiman", "pertanian", "air", "batu/terbakar", "SH2", "TU1", "TL6"]
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=c, label=f"{k} {n}") for k, n, c in zip(codes, names, cols)], loc="lower left",
              fontsize=7, ncol=2)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
