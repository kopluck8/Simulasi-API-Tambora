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


METODE = "dnbr-median-bergulir-v4"


def _groups(fuel0):
    """Kelompok untuk koreksi perubahan musiman: rumput K1-K4, lainnya (hutan/semak)."""
    grass = (fuel0 >= 30) & (fuel0 <= 33)
    other = ((fuel0 >= 100) & (fuel0 < 300)) | ((fuel0 >= 1) & (fuel0 <= 13))
    return [("rumput", grass), ("hutan/semak", other)]


def update(base_layers, nbr_ref, composite, cfg, scar_prev=None, mode=None):
    """Satu langkah update.

    nbr_ref: NBR acuan per piksel = pengamatan cerah terakhir SEBELUM jendela ini (bergulir), bukan
    median Juni-Juli. Dengan begitu yang terdeteksi hanya perubahan dalam ~3 minggu terakhir.
    Sisa pengeringan musiman dikoreksi dengan mengurangkan median dNBR tiap kelompok fuel
    (dNBR offset, Parks dkk. 2014), lalu ambang dNBR_terkoreksi > dnbr_ambang.
    Piksel yang lolos menjadi KANDIDAT (2) dulu; baru menjadi bekas terbakar (1, fuel 99) bila jendela
    berikutnya masih gelap dibanding NBR pra-kebakarannya. Ini membuang derau satu-jendela.

    composite: dict ndvi, nbr (median jendela, DN mentah), n (jumlah pengamatan cerah),
    atau dict b04,b08,b12 (satu pengamatan; dipakai di tes).
    Mengembalikan (fuel, status 0/1/2, ndvi, dnbr_terkoreksi, nbr_ref_baru, ringkasan)."""
    k = cfg["mingguan"]
    mode = mode or k["mode_kelas"]
    fuel0 = base_layers["fuel"].astype(np.int16)
    grass = (fuel0 >= 30) & (fuel0 <= 33)
    if "nbr" in composite:
        ndvi, nbr = composite["ndvi"], composite["nbr"].copy()
        nmin = int(k.get("pengamatan_min", 2))
        if "n" in composite:
            nbr[composite["n"] < nmin] = np.nan
    else:
        b4, b8, b12 = composite["b04"], composite["b08"], composite["b12"]
        with np.errstate(invalid="ignore", divide="ignore"):
            ndvi = (b8 - b4) / (b8 + b4)
            nbr = (b8 - b12) / (b8 + b12)
    seen = np.isfinite(ndvi) & np.isfinite(nbr) & np.isfinite(nbr_ref)
    dnbr = (nbr_ref - nbr).astype(np.float32)
    adj = np.full(dnbr.shape, np.nan, np.float32)
    offsets = {}
    offmap = np.zeros(dnbr.shape, np.float32)
    for name, g in _groups(fuel0):
        m = g & seen
        if m.sum() < 1000:
            continue
        off = float(np.median(dnbr[m]))
        offsets[name] = round(off, 4)
        offmap[g] = off
        adj[m] = dnbr[m] - off
    burnable = np.isfinite(adj)
    amb = float(k["dnbr_ambang"])
    res = float(cfg["aoi"]["res"])
    # status per piksel: 0 belum, 1 bekas terbakar terkonfirmasi, 2 kandidat (menunggu jendela berikutnya)
    prev = np.zeros(fuel0.shape, np.uint8) if scar_prev is None else scar_prev.astype(np.uint8)
    conf_prev, cand_prev = prev == 1, prev == 2
    # kandidat lama: NBR acuannya ditahan pada nilai pra-kebakaran, jadi dNBR sekarang = perubahan sejak sebelum api.
    # Terkonfirmasi bila masih gelap (bekas arang bertahan selama kemarau), gugur bila sudah pulih.
    keep = float(k.get("konfirmasi_faktor", 0.7)) * amb
    confirm = cand_prev & burnable & (adj > keep) & (dnbr > keep)
    confirm = remove_small(confirm, res, k.get("luas_min_ha", 1.0))
    drop = cand_prev & burnable & ~confirm
    still = cand_prev & ~burnable
    # kandidat baru: penurunan NBR terkoreksi DAN mentah melewati ambang
    new_c = burnable & (adj > amb) & (dnbr > amb) & ~conf_prev & ~cand_prev
    new_c = remove_small(new_c, res, k.get("luas_min_ha", 1.0))
    if not k.get("perlu_konfirmasi", True):
        confirm, new_c = confirm | new_c, np.zeros_like(new_c)
    status = np.zeros(fuel0.shape, np.uint8)
    status[conf_prev | confirm] = 1
    status[(still | new_c) & (status == 0)] = 2
    scar = status == 1
    # NBR acuan bergulir; untuk kandidat tahan nilai pra-kebakaran, digeser dengan perubahan musiman
    # kelompoknya, supaya jendela berikutnya membandingkan dengan "seandainya tidak terbakar"
    ref_new = np.where(status == 2, nbr_ref - offmap, np.where(np.isfinite(nbr), nbr, nbr_ref)).astype(np.float32)
    fuel = fuel0.copy()
    ha = res * res / 1e4
    info = dict(mode=mode, metode=METODE,
                piksel_cerah_persen=round(100 * float(seen[grass].mean()) if grass.any() else 0, 1),
                koreksi_musiman_dnbr=offsets,
                kandidat_baru_ha=round(float(new_c.sum() * ha), 1),
                kandidat_gugur_ha=round(float(drop.sum() * ha), 1),
                kandidat_menunggu_ha=round(float((status == 2).sum() * ha), 1))
    if mode in ("batas", "kuantil"):
        cls, b = classify(ndvi, grass & np.isfinite(ndvi), mode, cfg)
        upd = grass & np.isfinite(ndvi)
        fuel[upd] = cls[upd]
        info["batas_ndvi"] = b
    fuel[scar] = int(k["kode_terbakar"])
    newly = scar & ~conf_prev
    info["bekas_terbakar_baru_ha"] = round(float(newly.sum() * ha), 1)
    info["bekas_terbakar_musim_ha"] = round(float(scar.sum() * ha), 1)
    info["baru_per_kelompok_ha"] = {n: round(float((newly & g).sum() * ha), 1) for n, g in _groups(fuel0)}
    elev = base_layers.get("elev")
    if elev is not None and newly.any():
        e = elev[newly]
        info["baru_elevasi_persentil_m"] = [int(v) for v in np.percentile(e, [10, 50, 90])]
    info["luas_fuel_ha"] = {str(int(v)): round(float(c * ha), 1) for v, c in zip(*np.unique(fuel, return_counts=True))}
    return fuel, status, ndvi.astype(np.float32), adj, ref_new, info


def windows(start: dt.date, end: dt.date, days: int):
    """Potong [start, end] menjadi jendela berurutan sepanjang `days` hari (yang terakhir bisa lebih pendek)."""
    out = []
    s = start
    while s < end:
        e = min(s + dt.timedelta(days=days), end)
        out.append((s, e))
        s = e
    return out


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
