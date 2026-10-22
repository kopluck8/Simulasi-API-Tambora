"""Uji hasil simulasi terhadap bekas terbakar (mis. dNBR Sentinel-2)."""
from __future__ import annotations

import math

import numpy as np

from .geo import rasterize


def sorensen_equal_area(arrival, observed_mask):
    """Ambil sel yang paling awal terbakar dengan jumlah = luas bekas terbakar, lalu hitung
    Sørensen 2|A∩B|/(|A|+|B|). Mengembalikan (skor, menit saat luas sama, maska model)."""
    n_obs = int(observed_mask.sum())
    a = np.where(np.isfinite(arrival), arrival, np.inf).ravel()
    n_burn = int(np.isfinite(a).sum())
    k = min(n_obs, n_burn)
    if k == 0:
        return 0.0, float("nan"), np.zeros_like(observed_mask)
    idx = np.argpartition(a, k - 1)[:k]
    mod = np.zeros(a.size, bool)
    mod[idx] = True
    mod = mod.reshape(observed_mask.shape)
    inter = (mod & observed_mask).sum()
    return 2 * inter / (mod.sum() + n_obs), float(a[idx].max()), mod


def centroid_direction(mask, ls, x0, y0):
    """Azimut (derajat) dan jarak (m) dari titik api ke pusat area."""
    r, c = np.nonzero(mask)
    if r.size == 0:
        return float("nan"), float("nan")
    x = ls.west + (c.mean() + 0.5) * ls.res
    y = ls.north - (r.mean() + 0.5) * ls.res
    return math.degrees(math.atan2(x - x0, y - y0)) % 360, math.hypot(x - x0, y - y0)


def evaluate(arrival, scar_geom, ls, x0, y0):
    obs = rasterize([scar_geom], ls)
    s, tmin, mod = sorensen_equal_area(arrival, obs)
    d_obs, _ = centroid_direction(obs, ls, x0, y0)
    d_mod, dist = centroid_direction(mod, ls, x0, y0)
    return dict(sorensen=round(float(s), 3), menit_luas_sama=round(tmin, 1),
                luas_obs_ha=round(obs.sum() * ls.res ** 2 / 1e4, 1),
                arah_obs=round(d_obs, 1), arah_model=round(d_mod, 1))
