"""Kadar air bahan bakar mati.

Mode 'tetap' memakai nilai .fms apa adanya (seperti 'Use Fixed Fuel Moistures' di FlamMap).
Mode 'kondisi' menjalankan model timelag per jam dari cuaca (pengganti sederhana model Nelson
yang dipakai FARSITE): EMC Simard (1968) dari suhu & RH tingkat bahan bakar, respons eksponensial
1/10/100 jam, dan pembasahan oleh hujan. Suhu diturunkan dengan lapse rate dari elevasi stasiun,
dan pemanasan matahari menaikkan suhu bahan bakar terbuka di siang hari.
"""
from __future__ import annotations

import math

import numpy as np

LAPSE = 0.0065  # C/m


def emc_simard(t_c, rh):
    t = t_c * 9 / 5 + 32
    rh = np.clip(rh, 0, 100)
    return np.where(rh < 10, 0.03229 + 0.281073 * rh - 0.000578 * rh * t,
           np.where(rh < 50, 2.22749 + 0.160107 * rh - 0.01478 * t,
                    21.0606 + 0.005565 * rh ** 2 - 0.00035 * rh * t - 0.483199 * rh))


def _dewpoint(t, rh):
    a, b = 17.27, 237.7
    g = a * t / (b + t) + np.log(np.clip(rh, 1, 100) / 100.0)
    return b * g / (a - g)


def _rh_from_dew(t, td):
    a, b = 17.27, 237.7
    return 100 * np.exp(a * td / (b + td) - a * t / (b + t))


class FixedMoisture:
    def __init__(self, fms: dict):
        self.fms = fms

    def get(self, model, mask=None):
        v = self.fms.get(model, self.fms.get(0, (6, 7, 8, 60, 90)))
        return tuple(x / 100.0 for x in v)


class ConditionedMoisture:
    """Kadar air mati per sel yang diperbarui tiap jam; hidup tetap dari .fms."""

    def __init__(self, fms: dict, fuel, elev, cover, wx_elev):
        self.fms = fms
        self.fuel = fuel
        self.elev = elev.astype(np.float32)
        self.shade = np.clip(cover / 100.0, 0, 1).astype(np.float32)
        self.wx_elev = wx_elev
        d = fms.get(0, (6, 7, 8, 60, 90))
        init = np.zeros((3,) + fuel.shape, np.float32)
        for k in range(3):
            init[k] = d[k]
        for n, v in fms.items():
            m = fuel == n
            for k in range(3):
                init[k][m] = v[k]
        self.m = init  # persen
        self.last_rain = 0.0

    def step(self, rec, hours=1.0, hour_of_day=12):
        t = rec.temp - LAPSE * (self.elev - self.wx_elev)
        td = _dewpoint(np.float32(rec.temp), np.float32(rec.rh)) - LAPSE * 0.2 * (self.elev - self.wx_elev)
        rh = np.clip(_rh_from_dew(t, np.minimum(td, t)), 1, 100)
        # pemanasan matahari: maksimum siang hari cerah, berkurang oleh awan dan naungan kanopi
        sun = max(math.sin(math.pi * (hour_of_day - 6) / 12.0), 0.0) if 6 <= hour_of_day <= 18 else 0.0
        dT = 12.0 * sun * (1 - rec.cc / 100.0 * 0.75) * (1 - 0.8 * self.shade)
        tf = t + dT
        rhf = np.clip(_rh_from_dew(tf, np.minimum(td, t)), 1, 100)
        emc = emc_simard(tf, rhf).astype(np.float32)
        for k, tau in enumerate((1.0, 10.0, 100.0)):
            a = 1 - math.exp(-hours / tau)
            self.m[k] += (emc - self.m[k]) * a
        if rec.pcp > 0.1:
            add = (12.0, 4.0, 1.5)
            cap = (35.0, 35.0, 35.0)
            for k in range(3):
                self.m[k] = np.minimum(self.m[k] + add[k] * min(rec.pcp, 5.0), cap[k])
        np.clip(self.m, 1.0, 60.0, out=self.m)

    def get(self, model, mask):
        live = self.fms.get(model, self.fms.get(0, (6, 7, 8, 60, 90)))
        return (self.m[0][mask] / 100.0, self.m[1][mask] / 100.0, self.m[2][mask] / 100.0,
                live[3] / 100.0, live[4] / 100.0)
