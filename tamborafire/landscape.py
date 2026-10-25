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


METODE = "dnbr-median-bergulir-v5"


def _groups(fuel0):
    """Kelompok untuk koreksi perubahan musiman: rumput K1-K4, lainnya (hutan/semak)."""
    grass = (fuel0 >= 30) & (fuel0 <= 33)
    other = ((fuel0 >= 100) & (fuel0 < 300)) | ((fuel0 >= 1) & (fuel0 <= 13))
    return [("rumput", grass), ("hutan/semak", other)]


def local_offset(dnbr, group, seen, blk):
    """Koreksi musiman LOKAL: median dNBR kelompok per blok blk x blk piksel, dihaluskan 3x3 blok.
    Dataran rendah mengering lebih dulu daripada lereng atas, jadi satu median se-AOI tidak cukup."""
    from scipy import ndimage
    m = group & seen
    gm = float(np.median(dnbr[m])) if m.any() else 0.0
    H, W = dnbr.shape
    nb = (-(-H // blk), -(-W // blk))
    grid = np.full(nb, gm, np.float32)
    for i in range(nb[0]):
        for j in range(nb[1]):
            sl = (slice(i * blk, (i + 1) * blk), slice(j * blk, (j + 1) * blk))
            mm = m[sl]
            if mm.sum() > 0.01 * blk * blk:
                grid[i, j] = np.median(dnbr[sl][mm])
    grid = ndimage.median_filter(grid, 3, mode="nearest")
    return np.kron(grid, np.ones((blk, blk), np.float32))[:H, :W], gm


def update(base_layers, nbr_ref, composite, cfg, scar_prev=None, mode=None, hotspot=None):
    """Satu langkah update (satu jendela citra).

    nbr_ref: NBR acuan per piksel = pengamatan cerah terakhir SEBELUM jendela ini (bergulir).
    dNBR = nbr_ref - NBR median jendela. Pengeringan musiman dikoreksi dengan mengurangkan median dNBR
    LOKAL (blok ~1,5 km) per kelompok fuel. Bekas terbakar dicari seperti produk burned-area global:
    piksel benih rumput yang sangat gelap (dnbr_benih) lalu tumbuh ke tetangga yang cukup gelap (dnbr_ambang).

    hotspot: raster bool area sekitar titik panas FIRMS pada periode jendela. Bila ada, hanya petak yang
    menyentuh hotspot yang dihitung bekas terbakar (langsung, 1). Bila tidak ada (tanpa FIRMS_MAP_KEY),
    petak baru menjadi KANDIDAT (2) dan baru dihitung bila jendela berikutnya masih gelap.

    composite: dict ndvi, nbr (median jendela), n (jumlah pengamatan cerah), atau dict b04,b08,b12.
    Mengembalikan (fuel, status 0/1/2, ndvi, dnbr_terkoreksi, nbr_ref_baru, ringkasan)."""
    from scipy import ndimage
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
    res = float(cfg["aoi"]["res"])
    ha = res * res / 1e4
    seen = np.isfinite(ndvi) & np.isfinite(nbr) & np.isfinite(nbr_ref)
    dnbr = (nbr_ref - nbr).astype(np.float32)
    adj = np.full(dnbr.shape, np.nan, np.float32)
    offmap = np.zeros(dnbr.shape, np.float32)
    blk = max(1, int(round(float(k.get("koreksi_blok_km", 1.5)) * 1000 / res)))
    offsets = {}
    for name, g in _groups(fuel0):
        m = g & seen
        if m.sum() < 1000:
            continue
        off, gm = local_offset(dnbr, g, seen, blk)
        offsets[name] = round(gm, 4)
        offmap[g] = off[g]
        adj[m] = dnbr[m] - off[m]
    burnable = np.isfinite(adj)
    amb = float(k["dnbr_ambang"])
    benih = float(k.get("dnbr_benih", 0.15))
    minha = k.get("luas_min_ha", 1.0)
    prev = np.zeros(fuel0.shape, np.uint8) if scar_prev is None else scar_prev.astype(np.uint8)
    conf_prev, cand_prev = prev == 1, prev == 2
    # kandidat lama (hanya tanpa hotspot): acuan ditahan pada nilai pra-kebakaran
    keep = float(k.get("konfirmasi_faktor", 0.7)) * amb
    confirm = remove_small(cand_prev & burnable & (adj > keep) & (dnbr > keep), res, minha)
    drop = cand_prev & burnable & ~confirm
    still = cand_prev & ~burnable
    # petak baru: benih rumput sangat gelap, tumbuh ke tetangga yang cukup gelap
    seed = burnable & grass & (adj > benih) & (dnbr > benih)
    grow = burnable & (adj > amb) & (dnbr > amb)
    lab, _ = ndimage.label(grow | seed, structure=np.ones((3, 3)))
    hit = np.unique(lab[seed])
    patch = np.isin(lab, hit[hit > 0]) & ~conf_prev & ~cand_prev
    patch = remove_small(patch, res, minha)
    info_hot = {}
    if hotspot is not None:
        lab2, _ = ndimage.label(patch, structure=np.ones((3, 3)))
        h = np.unique(lab2[patch & hotspot])
        with_hot = np.isin(lab2, h[h > 0])
        info_hot = dict(tanpa_hotspot_diabaikan_ha=round(float((patch & ~with_hot).sum() * ha), 1))
        confirm = confirm | with_hot
        new_c = np.zeros_like(patch)
    elif k.get("perlu_konfirmasi", True):
        new_c = patch
    else:
        confirm, new_c = confirm | patch, np.zeros_like(patch)
    status = np.zeros(fuel0.shape, np.uint8)
    status[conf_prev | confirm] = 1
    status[(still | new_c) & (status == 0)] = 2
    scar = status == 1
    # NBR acuan bergulir; untuk kandidat tahan nilai pra-kebakaran, digeser dengan perubahan musiman lokal
    ref_new = np.where(status == 2, nbr_ref - offmap, np.where(np.isfinite(nbr), nbr, nbr_ref)).astype(np.float32)
    fuel = fuel0.copy()
    info = dict(mode=mode, metode=METODE, hotspot=hotspot is not None,
                piksel_cerah_persen=round(100 * float(seen[grass].mean()) if grass.any() else 0, 1),
                koreksi_musiman_dnbr=offsets,
                kandidat_baru_ha=round(float(new_c.sum() * ha), 1),
                kandidat_gugur_ha=round(float(drop.sum() * ha), 1),
                kandidat_menunggu_ha=round(float((status == 2).sum() * ha), 1), **info_hot)
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
        info["baru_elevasi_persentil_m"] = [int(v) for v in np.percentile(elev[newly], [10, 50, 90])]
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


# Jendela citra berjangkar tetap pada awal musim (1 Jun, 22 Jun, 13 Jul, ...). Status (bekas terbakar + NBR
# acuan) hanya disimpan di batas jendela yang sudah lengkap; jendela yang sedang berjalan dihitung ulang
# setiap ada citra baru, jadi update tiap ~5 hari tetap membandingkan median 21 hari dengan acuan sebelumnya.
SKEMA = "jendela-tetap"


def selaras(awal: dt.date, tanggal: dt.date, hari: int) -> bool:
    """Apakah `tanggal` jatuh tepat di batas jendela tetap yang berjangkar pada `awal`."""
    return tanggal >= awal and (tanggal - awal).days % hari == 0


def jendela_musim(awal: dt.date, sampai: dt.date, hari: int, mulai: dt.date | None = None):
    """Jendela tetap dari `mulai` (batas jendela; None = awal musim) sampai `sampai`.
    Mengembalikan [(w0, w1, lengkap)]; jendela terakhir bisa belum lengkap."""
    return [(w0, w1, (w1 - w0).days == hari) for w0, w1 in windows(mulai or awal, sampai, hari)]


def terapkan_bekas(fuel0, scar, cfg):
    """Fuel dasar dengan bekas terbakar (status 1) diganti kode_terbakar."""
    fuel = np.asarray(fuel0).astype(np.int16).copy()
    if scar is not None:
        fuel[scar == 1] = int(cfg["mingguan"]["kode_terbakar"])
    return fuel


def statistik_kawasan(path, fuel0, fuel, scar, transform, epsg, res):
    """Luas K1-K4 (30-33) awal musim dan sisa, serta bekas terbakar musim ini, per resort.
    path: GeoJSON poligon WGS84 dengan properti nama (dan kode)."""
    import shapely
    from pyproj import Transformer
    from rasterio.features import rasterize
    from shapely.geometry import shape
    to = Transformer.from_crs(4326, epsg, always_xy=True).transform
    tf = lambda fn, g: shapely.transform(g, lambda c: np.column_stack(fn(c[:, 0], c[:, 1])))
    ha = res * res / 1e4
    kelas = (30, 31, 32, 33)
    resort, tot = [], None
    for f in json.load(open(path))["features"]:
        m = rasterize([(tf(to, shape(f["geometry"])), 1)], out_shape=fuel.shape, transform=transform,
                      fill=0, dtype="uint8").astype(bool)
        r = dict(nama=f["properties"].get("nama"), kode=f["properties"].get("kode"),
                 luas_ha=round(float(m.sum() * ha), 1),
                 awal={str(c): round(float(((fuel0 == c) & m).sum() * ha), 1) for c in kelas},
                 sisa={str(c): round(float(((fuel == c) & m).sum() * ha), 1) for c in kelas},
                 terbakar_musim_ha=round(float(((scar == 1) & m).sum() * ha), 1) if scar is not None else 0.0)
        resort.append(r)
        if tot is None:
            tot = dict(luas_ha=0.0, awal={str(c): 0.0 for c in kelas}, sisa={str(c): 0.0 for c in kelas},
                       terbakar_musim_ha=0.0)
        tot["luas_ha"] += r["luas_ha"]
        tot["terbakar_musim_ha"] += r["terbakar_musim_ha"]
        for c in kelas:
            tot["awal"][str(c)] += r["awal"][str(c)]
            tot["sisa"][str(c)] += r["sisa"][str(c)]
    if tot:
        tot = json.loads(json.dumps(tot), parse_float=lambda s: round(float(s), 1))
    return dict(resort=resort, total=tot)


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
