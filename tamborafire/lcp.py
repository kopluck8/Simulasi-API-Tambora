"""Baca/tulis landscape FARSITE/FlamMap (.lcp).

Header 7316 byte, data int16 BIP, baris dari utara ke selatan.
Lapisan: elevasi, lereng, aspek, fuel, tutupan kanopi, [tinggi, CBH, CBD], [duff, woody].
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

HEADER_SIZE = 7316
LAYERS = ["elev", "slope", "aspect", "fuel", "cover", "height", "cbh", "cbd", "duff", "woody"]


@dataclass
class Landscape:
    west: float
    north: float
    res: float
    layers: dict = field(default_factory=dict)  # nama -> int16 array (ny, nx)
    latitude: int = -8
    units: tuple = (0, 0, 2, 0, 1, 3, 3, 3, 1, 0)  # E S A F C H B P D W
    description: str = ""
    crs_wkt: str | None = None

    @property
    def shape(self):
        return self.layers["elev"].shape

    @property
    def east(self):
        return self.west + self.shape[1] * self.res

    @property
    def south(self):
        return self.north - self.shape[0] * self.res

    @property
    def transform(self):
        from rasterio.transform import from_origin
        return from_origin(self.west, self.north, self.res, self.res)

    # --- satuan fisik -------------------------------------------------
    def physical(self):
        """Kembalikan lapisan dalam satuan SI: m, derajat, %, m, m, kg/m3."""
        L = self.layers
        eu, su, au, _, cu, hu, bu, pu = self.units[:8]
        elev = L["elev"].astype(np.float32) * (0.3048 if eu == 1 else 1.0)
        slope = L["slope"].astype(np.float32)
        if su == 1:  # persen
            slope = np.degrees(np.arctan(slope / 100.0)).astype(np.float32)
        aspect = L["aspect"].astype(np.float32)
        if au == 0:  # kelas grass (1-25) tidak didukung, anggap derajat
            pass
        cover = L["cover"].astype(np.float32)
        if cu == 0:  # kelas 0-4
            cover = np.choose(np.clip(L["cover"], 0, 4), [0, 10, 35, 65, 90]).astype(np.float32)
        out = dict(elev=elev, slope=slope, aspect=aspect, fuel=L["fuel"].astype(np.int32), cover=cover)

        def _h(a, u):
            a = a.astype(np.float32)
            return {1: a, 2: a * 0.3048, 3: a / 10.0, 4: a * 0.3048 / 10.0}.get(u, a)

        if "height" in L:
            out["height"] = _h(L["height"], hu)
            out["cbh"] = _h(L["cbh"], bu)
            d = L["cbd"].astype(np.float32)
            out["cbd"] = {1: d, 2: d * 16.0185, 3: d / 100.0, 4: d * 16.0185 / 1000.0}.get(pu, d / 100.0)
        else:
            z = np.zeros_like(elev)
            out.update(height=z, cbh=z, cbd=z)
        return out

    def crop(self, west, south, east, north):
        c0 = max(int(np.floor((west - self.west) / self.res)), 0)
        c1 = min(int(np.ceil((east - self.west) / self.res)), self.shape[1])
        r0 = max(int(np.floor((self.north - north) / self.res)), 0)
        r1 = min(int(np.ceil((self.north - south) / self.res)), self.shape[0])
        lay = {k: v[r0:r1, c0:c1].copy() for k, v in self.layers.items()}
        return Landscape(self.west + c0 * self.res, self.north - r0 * self.res, self.res, lay,
                         self.latitude, self.units, self.description, self.crs_wkt)

    def coarsen(self, factor: int):
        """Resample ke sel lebih besar (seperti 'resolution of calculations' MTT).
        Fuel diambil dari sel tengah blok, lapisan lain dirata-rata (aspek lewat vektor)."""
        if factor <= 1:
            return self
        ny, nx = self.shape
        ny2, nx2 = ny // factor, nx // factor
        lay = {}
        for k, v in self.layers.items():
            b = v[:ny2 * factor, :nx2 * factor].reshape(ny2, factor, nx2, factor)
            if k == "fuel":
                lay[k] = b[:, factor // 2, :, factor // 2].copy()
            elif k == "aspect":
                a = np.radians(b.astype(np.float64))
                s, c = np.sin(a).mean(axis=(1, 3)), np.cos(a).mean(axis=(1, 3))
                lay[k] = np.round(np.degrees(np.arctan2(s, c)) % 360).astype(np.int16)
            else:
                lay[k] = np.round(b.astype(np.float64).mean(axis=(1, 3))).astype(np.int16)
        return Landscape(self.west, self.north, self.res * factor, lay, self.latitude,
                         self.units, self.description, self.crs_wkt)


def _theme_stats(a):
    vals = np.unique(a)
    lo, hi = int(vals.min()), int(vals.max())
    if len(vals) <= 100:
        lst = [int(v) for v in vals] + [0] * (100 - len(vals))
        n = len(vals)
    else:
        lst, n = [0] * 100, -1
    return lo, hi, n, lst


def read_lcp(path) -> Landscape:
    with open(path, "rb") as f:
        h = f.read(HEADER_SIZE)
        crown, ground, lat = struct.unpack("<3i", h[:12])
        o = 44 + 10 * 412
        nx, ny = struct.unpack("<2i", h[o:o + 8]); o += 8
        east, west, north, south = struct.unpack("<4d", h[o:o + 32]); o += 32
        o += 4
        xres, yres = struct.unpack("<2d", h[o:o + 16]); o += 16
        units = struct.unpack("<10h", h[o:o + 20]); o += 20
        desc = h[o + 2560:o + 3072].split(b"\0")[0].decode("latin-1")
        nb = 5 + (3 if crown == 21 else 0) + (2 if ground == 21 else 0)
        data = np.fromfile(f, dtype="<i2", count=nx * ny * nb).reshape(ny, nx, nb)
    names = LAYERS[:5] + (LAYERS[5:8] if crown == 21 else []) + (LAYERS[8:] if ground == 21 else [])
    layers = {n: np.ascontiguousarray(data[:, :, i]) for i, n in enumerate(names)}
    wkt = None
    prj = str(path)[:-4] + ".prj"
    try:
        wkt = open(prj).read()
    except OSError:
        pass
    return Landscape(west, north, xres, layers, lat, units, desc, wkt)


def write_lcp(ls: Landscape, path):
    crown = all(k in ls.layers for k in ("height", "cbh", "cbd"))
    ground = all(k in ls.layers for k in ("duff", "woody"))
    names = LAYERS[:5] + (LAYERS[5:8] if crown else []) + (LAYERS[8:] if ground else [])
    ny, nx = ls.shape
    h = bytearray()
    h += struct.pack("<3i", 21 if crown else 20, 21 if ground else 20, int(ls.latitude))
    h += struct.pack("<4d", ls.west, ls.east, ls.south, ls.north)
    for n in LAYERS:
        if n in names:
            lo, hi, num, lst = _theme_stats(ls.layers[n])
        else:
            lo, hi, num, lst = 0, 0, 0, [0] * 100
        h += struct.pack("<3i", lo, hi, num) + struct.pack("<100i", *lst)
    h += struct.pack("<2i", nx, ny)
    h += struct.pack("<4d", ls.east, ls.west, ls.north, ls.south)
    h += struct.pack("<i", 0)
    h += struct.pack("<2d", ls.res, ls.res)
    h += struct.pack("<10h", *ls.units)
    for n in LAYERS:
        h += (n + ".asc").encode()[:255].ljust(256, b"\0") if n in names else b"\0" * 256
    h += ls.description.encode("latin-1")[:511].ljust(512, b"\0")
    assert len(h) == HEADER_SIZE
    data = np.stack([ls.layers[n].astype("<i2") for n in names], axis=-1)
    with open(path, "wb") as f:
        f.write(bytes(h))
        f.write(data.tobytes())
    if ls.crs_wkt:
        with open(str(path)[:-4] + ".prj", "w") as f:
            f.write(ls.crs_wkt)
