"""Model Rothermel (1972) permukaan + Albini (1976), vektor angin-lereng seperti FARSITE/BehavePlus,
dan api tajuk Van Wagner (1977) / Rothermel (1991) versi sederhana (Scott & Reinhardt 2001).

Semua fungsi bekerja per kelas fuel model (skalar) lalu dipetakan ke grid oleh `surface_grids`.
"""
from __future__ import annotations

import math

import numpy as np

from .fuels import TAC_TO_LBFT2, NONBURNABLE, FuelModel

RHO_P = 32.0
ST = 0.0555
SE = 0.010
FT_PER_M = 3.28084
MPH_PER_KMH = 0.621371


def _size_class(sav):
    if sav >= 1200: return 0
    if sav >= 192: return 1
    if sav >= 96: return 2
    if sav >= 48: return 3
    if sav >= 16: return 4
    return 5


class RothermelFuel:
    """Prakomputasi bagian Rothermel yang tidak bergantung pada angin/lereng."""

    def __init__(self, fm: FuelModel, m1, m10, m100, mherb, mwoody):
        """Kadar air dalam fraksi (0.06 = 6%)."""
        self.fm = fm
        herb, herb_dead = fm.herb, 0.0
        if fm.dynamic and fm.herb > 0:
            cured = min(max((1.20 - mherb) / 0.90, 0.0), 1.0)
            herb_dead, herb = fm.herb * cured, fm.herb * (1 - cured)
        dead = [(fm.h1, fm.sav1, m1), (fm.h10, 109.0, m10), (fm.h100, 30.0, m100),
                (herb_dead, fm.savherb, m1)]
        live = [(herb, fm.savherb, mherb), (fm.woody, fm.savwoody, mwoody)]
        dead = [(w * TAC_TO_LBFT2, s, m) for w, s, m in dead if w > 0]
        live = [(w * TAC_TO_LBFT2, s, m) for w, s, m in live if w > 0]
        self.ok = bool(dead) and fm.depth > 0
        if not self.ok:
            return
        cats = [dead, live]
        A = [[w * s / RHO_P for w, s, m in c] for c in cats]
        Acat = [sum(a) for a in A]
        AT = sum(Acat)
        f = [[a / Acat[i] for a in A[i]] if Acat[i] > 0 else [] for i in range(2)]
        fcat = [a / AT for a in Acat]
        sigma = sum(fcat[i] * sum(f[i][j] * cats[i][j][1] for j in range(len(cats[i]))) for i in range(2))
        w0 = sum(w for c in cats for w, s, m in c)
        beta = w0 / (fm.depth * RHO_P)
        bop = 3.348 * sigma ** -0.8189
        rr = beta / bop
        gmax = sigma ** 1.5 / (495 + 0.0594 * sigma ** 1.5)
        Aexp = 133.0 * sigma ** -0.7913
        gamma = gmax * rr ** Aexp * math.exp(Aexp * (1 - rr))
        # Mx hidup
        mxd = fm.mx / 100.0
        mxl = mxd
        if live:
            wd = sum(w * math.exp(-138 / s) for w, s, m in dead)
            wl = sum(w * math.exp(-500 / s) for w, s, m in live)
            mfd = sum(w * math.exp(-138 / s) * m for w, s, m in dead) / wd
            mxl = max(2.9 * (wd / wl) * (1 - mfd / mxd) - 0.226, mxd)
        mx = [mxd, mxl]
        heat = [fm.heat_dead, fm.heat_live]
        IR = 0.0
        for i in range(2):
            c = cats[i]
            if not c:
                continue
            # bobot ukuran g (Albini 1976)
            gs = [0.0] * 6
            for j, (w, s, m) in enumerate(c):
                gs[_size_class(s)] += f[i][j]
            wn = sum(gs[_size_class(s)] * w * (1 - ST) for w, s, m in c)
            mf = sum(f[i][j] * c[j][2] for j in range(len(c)))
            r = min(mf / mx[i], 1.0)
            etam = max(1 - 2.59 * r + 5.11 * r * r - 3.52 * r ** 3, 0.0)
            etas = min(0.174 * SE ** -0.19, 1.0)
            IR += wn * heat[i] * etam * etas
        IR *= gamma
        xi = math.exp((0.792 + 0.681 * sigma ** 0.5) * (beta + 0.1)) / (192 + 0.2595 * sigma)
        rhob = w0 / fm.depth
        qsum = sum(fcat[i] * sum(f[i][j] * math.exp(-138 / cats[i][j][1]) * (250 + 1116 * cats[i][j][2])
                                 for j in range(len(cats[i]))) for i in range(2))
        self.IR = IR                      # BTU/ft2/min
        self.R0 = IR * xi / (rhob * qsum) if IR > 0 else 0.0  # ft/min
        self.C = 7.47 * math.exp(-0.133 * sigma ** 0.55)
        self.B = 0.02526 * sigma ** 0.54
        self.E = 0.715 * math.exp(-3.59e-4 * sigma)
        self.rr = rr
        self.beta = beta
        self.sigma = sigma
        self.HA = IR * 384.0 / sigma      # BTU/ft2
        self.slope_k = 5.275 * beta ** -0.3

    def phi_w(self, u_ftmin):
        return self.C * np.power(np.maximum(u_ftmin, 0.0), self.B) * self.rr ** -self.E

    def eff_wind(self, phi):
        """Kecepatan angin efektif (ft/min) dari phi gabungan."""
        return np.power(np.maximum(phi, 0.0) * self.rr ** self.E / self.C, 1.0 / self.B)


def waf_grid(depth_ft, cover, height_m, cbh_m):
    """Wind adjustment factor 20-ft -> midflame (Albini & Baughman 1979, seperti FlamMap)."""
    H = np.maximum(depth_ft, 0.01)
    unsh = 1.83 / np.log((20 + 0.36 * H) / (0.13 * H))
    hc = np.maximum(height_m * FT_PER_M, 1.0)
    fill = cover / 100.0 * math.pi / 12.0  # seperti flammap.dll (tanpa crown ratio)
    sh = 0.555 / (np.sqrt(np.maximum(fill * hc, 1e-6)) * np.log((20 + 0.36 * hc) / (0.13 * hc)))
    sheltered = (cover > 5) & (height_m > 0)
    return np.where(sheltered, np.minimum(sh, unsh), unsh).astype(np.float32)


def length_to_breadth(u_mph):
    """Rasio panjang/lebar elips, Anderson (1983) dengan angin efektif midflame dalam m/s,
    persis seperti flammap.dll 6.2 (dicek langsung: lapisan ELLIPSEDIM_A/B)."""
    u = u_mph * 0.44704
    lb = 0.936 * np.exp(0.2566 * u) + 0.461 * np.exp(-0.1548 * u) - 0.397
    return np.clip(lb, 1.0, 8.0)


def surface_grids(fuel, slope_deg, aspect_deg, ws20_kmh, wdir_from_deg, depth_lookup, models,
                  moist_fn, cover=None, height=None, cbh=None, cbd=None, crown=True,
                  foliar_moisture=100.0, waf=None, wind_limit=False):
    """Hitung ROS kepala (m/menit), arah kepala (azimut derajat, arah menuju), eksentrisitas,
    intensitas (kW/m), panjang nyala (m), tipe api (0 tidak, 1 permukaan, 2 pasif, 3 aktif).

    moist_fn(model_number, mask) -> (m1, m10, m100, mherb, mwoody) fraksi, skalar atau array[mask].
    """
    shp = fuel.shape
    ros = np.zeros(shp, np.float32)
    head = np.zeros(shp, np.float32)
    ecc = np.zeros(shp, np.float32)
    fli = np.zeros(shp, np.float32)
    ctype = np.zeros(shp, np.int8)
    tan2 = np.tan(np.radians(slope_deg)) ** 2
    upslope = (aspect_deg + 180.0) % 360.0
    wto = (wdir_from_deg + 180.0) % 360.0
    if waf is None:
        dep = depth_lookup[np.clip(fuel, 0, len(depth_lookup) - 1)]
        waf = waf_grid(dep, cover if cover is not None else np.zeros(shp), height if height is not None else np.zeros(shp),
                       cbh if cbh is not None else np.zeros(shp))
    umid = ws20_kmh * waf * MPH_PER_KMH * 88.0  # ft/min
    for n in np.unique(fuel):
        n = int(n)
        if n in NONBURNABLE or n not in models:
            continue
        mask = fuel == n
        mo = moist_fn(n, mask)
        if all(np.ndim(x) == 0 for x in mo):
            groups = [(mask, mo)]
        else:
            # kadar air bervariasi per sel: kuantisasi 1-h ke 0,5% agar tetap cepat
            arrs = [np.broadcast_to(np.asarray(x, np.float32), (int(mask.sum()),)) for x in mo]
            key = np.round(arrs[0] * 200).astype(np.int32) * 1000 + np.round(arrs[1] * 100).astype(np.int32)
            idx = np.flatnonzero(mask)
            groups = []
            for k in np.unique(key):
                sel = key == k
                sub = np.zeros(shp, bool)
                sub.flat[idx[sel]] = True
                groups.append((sub, tuple(float(a[sel].mean()) for a in arrs)))
        for gm, (m1, m10, m100, mh, mw) in groups:
            rf = RothermelFuel(models[n], m1, m10, m100, mh, mw)
            if not rf.ok or rf.R0 <= 0:
                continue
            pw = rf.phi_w(umid[gm])
            ps = rf.slope_k * tan2[gm]
            # vektor (arah menuju): angin dan lereng
            aw, asl = np.radians(wto[gm]), np.radians(upslope[gm])
            x = pw * np.sin(aw) + ps * np.sin(asl)
            y = pw * np.cos(aw) + ps * np.cos(asl)
            phi = np.hypot(x, y)
            r = rf.R0 * (1 + phi)  # ft/min
            ue = rf.eff_wind(phi)
            # batas angin (Rothermel 1972: U_eff <= 0.9 IR); FlamMap 6.2 tidak memakainya
            lim = 0.9 * rf.IR
            over = (ue > lim) if wind_limit else np.zeros(ue.shape, bool)
            if np.any(over):
                phimax = rf.phi_w(np.float64(lim))
                r = np.where(over, rf.R0 * (1 + phimax), r)
                ue = np.minimum(ue, lim)
            # LB seperti flammap.dll: jumlah vektor angin midflame + angin-setara-lereng
            us = rf.eff_wind(ps)
            ux = umid[gm] * np.sin(aw) + us * np.sin(asl)
            uy = umid[gm] * np.cos(aw) + us * np.cos(asl)
            lb = length_to_breadth(np.minimum(np.hypot(ux, uy), np.maximum(ue, lim) if np.any(over) else np.inf) / 88.0)
            e = np.sqrt(lb * lb - 1) / lb
            hd = np.degrees(np.arctan2(x, y)) % 360.0
            rm = r * 0.3048  # m/min
            ib = rf.HA * r / 60.0 * 3.4613  # BTU/ft/s -> kW/m
            ros[gm], head[gm], ecc[gm], fli[gm] = rm, hd, e, ib
            ctype[gm] = 1
    if crown and cbd is not None:
        _crown(ros, head, ecc, fli, ctype, fuel, cbh, cbd, cover, ws20_kmh, wto, models, moist_fn, foliar_moisture)
    flame = np.where(fli > 0, 0.0775 * np.power(fli, 0.46), 0.0).astype(np.float32)  # Byram, m
    return ros, head, ecc, fli, flame, ctype


def _crown(ros, head, ecc, fli, ctype, fuel, cbh, cbd, cover, ws20_kmh, wto, models, moist_fn, fmc):
    can = (ctype > 0) & (cbh > 0) & (cbd > 0) & (cover > 0)
    if not np.any(can):
        return
    i0 = np.power(0.010 * cbh * (460.0 + 25.9 * fmc), 1.5)  # kW/m (Van Wagner 1977)
    init = can & (fli >= i0)
    if not np.any(init):
        return
    # Rothermel 1991: 3.34 x ROS FM10 dengan angin midflame = 0.4 x angin 20-ft
    m = moist_fn(10, init)
    m = tuple(float(np.mean(x)) for x in m)
    rf = RothermelFuel(models[10], *m)
    u = ws20_kmh[init] * 0.4 * MPH_PER_KMH * 88.0
    r10 = rf.R0 * (1 + rf.phi_w(u)) * 0.3048
    ra = 3.34 * r10
    rcrit = 3.0 / cbd[init]
    active = ra >= rcrit
    sub_ros = ros[init]
    sub_ros = np.where(active, np.maximum(ra, sub_ros), sub_ros)
    ros[init] = sub_ros
    ct = ctype[init]
    ct = np.where(active, 3, 2).astype(np.int8)
    ctype[init] = ct
    # arah tajuk mengikuti angin
    head[init] = np.where(active, wto[init], head[init])
