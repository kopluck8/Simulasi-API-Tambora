"""Angin di atas medan.

- 'seragam': satu kecepatan/arah untuk semua sel.
- 'medan': model empiris MicroMet (Liston & Elder 2006): angin dipercepat di punggungan/lereng
  hadap angin, diperlambat di lembah/lereng balik, dan dibelokkan mengikuti lereng.
  Cepat, cocok untuk simulasi per jam.
- 'windninja': memanggil WindNinja_cli (domain average) bila terpasang, hasil di-cache per
  kombinasi (kecepatan, arah). Resolusi mesh bawaan 100 m seperti di FlamMap.
"""
from __future__ import annotations

import glob
import math
import os
import shutil
import subprocess
import tempfile

import numpy as np


def uniform(shape, ws, wd):
    return np.full(shape, ws, np.float32), np.full(shape, wd % 360, np.float32)


class TerrainWind:
    def __init__(self, elev, slope_deg, aspect_deg, res, eta=500.0, gs=0.5, gc=0.5):
        from scipy.ndimage import shift  # noqa: F401  (pastikan scipy ada)
        self.beta = np.radians(slope_deg).astype(np.float32)
        self.xi = np.radians(aspect_deg).astype(np.float32)
        self.gs, self.gc = gs, gc
        k = max(int(round(eta / res)), 1)
        z = elev.astype(np.float32)
        zp = np.pad(z, k, mode="edge")
        ny, nx = z.shape

        def s(dr, dc):
            return zp[k + dr:k + dr + ny, k + dc:k + dc + nx]

        e2 = 2 * eta
        d2 = 2 * math.sqrt(2) * eta
        oc = 0.25 * ((z - 0.5 * (s(0, -k) + s(0, k))) / e2 + (z - 0.5 * (s(-k, 0) + s(k, 0))) / e2 +
                     (z - 0.5 * (s(k, -k) + s(-k, k))) / d2 + (z - 0.5 * (s(-k, -k) + s(k, k))) / d2)
        m = np.nanmax(np.abs(oc)) or 1.0
        self.oc = (oc / (2 * m)).astype(np.float32)

    def field(self, ws, wd):
        th = math.radians(wd % 360)
        os_ = self.beta * np.cos(th - self.xi)
        m = np.nanmax(np.abs(os_)) or 1.0
        os_ = os_ / (2 * m)
        ww = 1 + self.gs * os_ + self.gc * self.oc
        spd = (ws * ww).astype(np.float32)
        thd = -0.5 * os_ * np.sin(2 * (self.xi - th))
        d = (np.degrees(th + thd) % 360).astype(np.float32)
        return spd, d


def find_windninja():
    for name in ("WindNinja_cli", "WindNinja_cli.exe"):
        p = shutil.which(name)
        if p:
            return p
    for pat in (r"C:\WindNinja\*\bin\WindNinja_cli.exe", r"C:\Program Files\WindNinja\*\bin\WindNinja_cli.exe"):
        g = sorted(glob.glob(pat))
        if g:
            return g[-1]
    env = os.environ.get("WINDNINJA_CLI")
    return env if env and os.path.exists(env) else None


class WindNinja:
    def __init__(self, elev, transform, crs_wkt, mesh_res=100.0, vegetation="grass", exe=None, threads=None,
                 workdir=None):
        import rasterio
        self.exe = exe or find_windninja()
        if not self.exe:
            raise FileNotFoundError("WindNinja_cli tidak ditemukan. Pasang WindNinja atau isi env WINDNINJA_CLI.")
        self.shape = elev.shape
        self.transform = transform
        self.mesh_res = mesh_res
        self.veg = vegetation
        self.threads = threads or os.cpu_count() or 1
        self.dir = workdir or tempfile.mkdtemp(prefix="windninja_")
        self.dem = os.path.join(self.dir, "dem.tif")
        with rasterio.open(self.dem, "w", driver="GTiff", width=self.shape[1], height=self.shape[0], count=1,
                           dtype="float32", crs=crs_wkt, transform=transform) as d:
            d.write(elev.astype(np.float32), 1)
        self.cache = {}

    def field(self, ws, wd):
        key = (int(round(ws)), int(round(wd)) % 360)
        if key in self.cache:
            return self.cache[key]
        import rasterio
        from rasterio.warp import reproject, Resampling
        out = os.path.join(self.dir, f"o_{key[0]}_{key[1]}")
        os.makedirs(out, exist_ok=True)
        cfg = os.path.join(out, "wn.cfg")
        with open(cfg, "w") as f:
            f.write(f"""num_threads = {self.threads}
elevation_file = {self.dem}
initialization_method = domainAverageInitialization
time_zone = Asia/Makassar
input_speed = {max(key[0], 0.1)}
input_speed_units = kph
input_direction = {key[1]}
input_wind_height = 20.0
units_input_wind_height = ft
output_wind_height = 20.0
units_output_wind_height = ft
vegetation = {self.veg}
mesh_resolution = {self.mesh_res}
units_mesh_resolution = m
output_speed_units = kph
write_ascii_output = true
ascii_out_resolution = -1
units_ascii_out_resolution = m
output_path = {out}
""")
        subprocess.run([self.exe, cfg], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        vel = glob.glob(os.path.join(out, "*_vel.asc"))
        ang = glob.glob(os.path.join(out, "*_ang.asc"))
        if not vel or not ang:
            raise RuntimeError("WindNinja tidak menghasilkan *_vel.asc / *_ang.asc di " + out)
        res = []
        for fn, rs in ((vel[0], Resampling.bilinear), (ang[0], Resampling.nearest)):
            with rasterio.open(fn) as s:
                dst = np.zeros(self.shape, np.float32)
                reproject(rasterio.band(s, 1), dst, dst_transform=self.transform, dst_crs=s.crs or None,
                          resampling=rs)
                res.append(dst)
        self.cache[key] = (res[0], res[1] % 360)
        return self.cache[key]


def make_wind_model(kind, elev, slope, aspect, res, transform=None, crs_wkt=None, mesh_res=100.0):
    if kind == "seragam":
        return None
    if kind == "medan":
        return TerrainWind(elev, slope, aspect, res)
    if kind == "windninja":
        return WindNinja(elev, transform, crs_wkt, mesh_res)
    if kind == "otomatis":
        try:
            return WindNinja(elev, transform, crs_wkt, mesh_res)
        except FileNotFoundError:
            return TerrainWind(elev, slope, aspect, res)
    raise ValueError("model angin tidak dikenal: " + kind)
