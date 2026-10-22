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
                  foliar_moisture=100.0, waf=None, wind_limit=False, crown_method="finney"):
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
    flame = np.where(fli > 0, 0.0775 * np.power(fli, 0.46), 0.0).astype(np.float32)  # Byram, m
    if crown and cbd is not None:
        _crown(ros, head, ecc, fli, flame, ctype, fuel, slope_deg, upslope, cbh, cbd, cover, height, ws20_kmh, wto,
               models, moist_fn, foliar_moisture, crown_method)
    return ros, head, ecc, fli, flame, ctype


HC_CROWN = 18000.0  # kJ/kg, panas tajuk seperti flammap.dll


def _r10(fuel, sel, slope_deg, upslope, ws20_kmh, wto, models, moist_fn):
    """ROS model 10 (ft/menit) per sel, dengan kadar air model fuel sel itu sendiri, angin 0,4 x angin 20-ft
    dan lereng (dijumlah sebagai vektor), seperti flammap.dll. Juga arah kepala dan angin untuk LB tajuk (mph)."""
    out = np.zeros(int(sel.sum()), np.float64)
    fs = fuel[sel]
    tan2 = np.tan(np.radians(slope_deg[sel])) ** 2
    up = np.radians(upslope[sel])
    aw = np.radians(wto[sel])
    u20 = ws20_kmh[sel] * MPH_PER_KMH * 88.0
    hd = np.zeros(out.size)
    lbw = np.zeros(out.size)
    for n in np.unique(fs):
        m = fs == n
        mo = moist_fn(int(n), sel & (fuel == n))
        mo = tuple(float(np.mean(x)) for x in mo)
        rf = RothermelFuel(models[10], *mo)
        ps = rf.slope_k * tan2[m]
        pw = rf.phi_w(0.4 * u20[m])
        x = pw * np.sin(aw[m]) + ps * np.sin(up[m])
        y = pw * np.cos(aw[m]) + ps * np.cos(up[m])
        out[m] = rf.R0 * (1 + np.hypot(x, y))
        hd[m] = np.degrees(np.arctan2(x, y)) % 360.0
        # LB tajuk: Anderson dengan 0,5 x angin 20-ft + angin-setara-lereng model 10 (seperti flammap.dll)
        us = rf.eff_wind(ps)
        ux = 0.5 * u20[m] * np.sin(aw[m]) + us * np.sin(up[m])
        uy = 0.5 * u20[m] * np.cos(aw[m]) + us * np.cos(up[m])
        lbw[m] = np.hypot(ux, uy) / 88.0
    return out, hd, lbw


def _crown(ros, head, ecc, fli, flame, ctype, fuel, slope_deg, upslope, cbh, cbd, cover, height, ws20_kmh, wto,
           models, moist_fn, fmc, method="finney"):
    """Api tajuk seperti flammap.dll (dicek dengan Wine):
    - inisiasi Van Wagner (1977): FLI permukaan >= I0 = (0,010 CBH (460 + 25,9 FMC))^1,5
    - ROS tajuk aktif Rothermel (1991) = 3,34 x ROS model 10 (kadar air sel, angin 0,4 x U20, lereng)
    - Finney (1998): R = Rs + CFB (Rtajuk - Rs); aktif bila R >= 3/CBD, pasif tetap ROS permukaan
      Scott & Reinhardt (2001): aktif bila Rtajuk >= 3/CBD; pasif maupun aktif menyebar dengan Rtajuk
    - FLI = R (HA + CFB x CBD (tinggi - CBH) x 18000) / 60 dengan CFB Van Wagner (1993) untuk Finney;
      Scott & Reinhardt: CFB = 1 dan tidak kurang dari FLI permukaan. Panjang nyala tajuk Thomas (1963)."""
    can = (ctype > 0) & (cbh > 0) & (cbd > 0) & (cover > 0)
    if not np.any(can):
        return
    i0 = np.power(0.010 * cbh * (460.0 + 25.9 * fmc), 1.5)  # kW/m
    init = can & (fli >= i0)
    if not np.any(init):
        return
    rs = ros[init].astype(np.float64)
    ha = fli[init] * 60.0 / np.maximum(rs, 1e-9)            # kJ/m2
    r10, hd10, lbw = _r10(fuel, init, slope_deg, upslope, ws20_kmh, wto, models, moist_fn)
    rac = 3.34 * r10 * 0.3048                                 # m/menit
    c = cbd[init].astype(np.float64)
    rcrit = 3.0 / c
    r_init = i0[init] * 60.0 / np.maximum(ha, 1e-9)
    with np.errstate(divide="ignore", invalid="ignore"):
        ac = -np.log(0.1) / (0.9 * (rcrit - r_init))
        cfb = np.clip(1.0 - np.exp(-ac * (rs - r_init)), 0.0, 1.0)
    cfb = np.where(np.isfinite(cfb), cfb, 1.0)
    if method in ("scott", "scott-reinhardt", "sr"):
        active = rac >= rcrit
        r = rac
    else:
        rf = rs + cfb * (rac - rs)
        active = rf >= rcrit
        r = np.where(active, rf, rs)
    w = c * np.maximum(height[init] - cbh[init], 0.0)         # kg/m2
    if method in ("scott", "scott-reinhardt", "sr"):
        ic = np.maximum(fli[init], r * (ha + w * HC_CROWN) / 60.0)  # flammap.dll: CFB = 1, minimal FLI permukaan
    else:
        ic = r * (ha + cfb * w * HC_CROWN) / 60.0             # kW/m
    ros[init] = r
    fli[init] = ic
    flame[init] = 0.3048 * 0.2 * np.power(ic / 3.4613, 2.0 / 3.0)  # Thomas (1963), m
    ctype[init] = np.where(active, 3, 2).astype(np.int8)
    lb = length_to_breadth(lbw)
    use = np.ones_like(active) if method in ("scott", "scott-reinhardt", "sr") else active
    ecc[init] = np.where(use, np.sqrt(lb * lb - 1) / lb, ecc[init])
    head[init] = np.where(active, hd10, head[init])
