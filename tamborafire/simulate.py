"""Menjalankan simulasi: mode MTT (kondisi tetap) dan FARSITE (cuaca per jam)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from . import moisture as M
from . import rothermel as R
from . import wind as W
from .fuels import fuel_table, read_fms
from .spread import SpreadState


@dataclass
class Hasil:
    arrival: np.ndarray            # menit sejak mulai (NaN = tidak terbakar)
    ros: np.ndarray                # m/menit pada saat sel terbakar (MTT: kondisi tetap)
    flame: np.ndarray              # m
    intensity: np.ndarray          # kW/m
    crown: np.ndarray              # 0-3
    start: dt.datetime | None = None
    info: dict = field(default_factory=dict)


def _depth_lookup(models):
    lut = np.zeros(1000, np.float32)
    for n, m in models.items():
        if 0 <= n < 1000:
            lut[n] = m.depth
    return lut


def _apply_ros_adjust(ros, fuel, adjust):
    if adjust:
        for n, f in adjust.items():
            ros[fuel == n] *= f
    return ros


class Simulator:
    def __init__(self, ls, fmd=None, fms=None, crown=True, foliar=100.0, ros_adjust=None, crown_method="finney"):
        self.ls = ls
        self.p = ls.physical()
        self.models = fuel_table(fmd)
        self.fms = read_fms(fms) if fms else {0: (6, 7, 8, 60, 90)}
        self.crown = crown
        self.foliar = foliar
        self.crown_method = crown_method   # "finney" (1998) atau "scott" (Scott & Reinhardt 2001)
        self.ros_adjust = ros_adjust or {}
        self.depth = _depth_lookup(self.models)
        p = self.p
        dep = self.depth[np.clip(p["fuel"], 0, 999)]
        self.waf = R.waf_grid(dep, p["cover"], p["height"], p["cbh"])

    def grids(self, ws, wd, moist):
        p = self.p
        ws_a = np.broadcast_to(np.asarray(ws, np.float32), p["fuel"].shape)
        wd_a = np.broadcast_to(np.asarray(wd, np.float32), p["fuel"].shape)
        ros, head, ecc, fli, flame, ct = R.surface_grids(
            p["fuel"], p["slope"], p["aspect"], ws_a, wd_a, self.depth, self.models, moist.get,
            p["cover"], p["height"], p["cbh"], p["cbd"], self.crown, self.foliar, self.waf,
            crown_method=self.crown_method)
        _apply_ros_adjust(ros, p["fuel"], self.ros_adjust)
        return ros, head, ecc, fli, flame, ct

    def wind_model(self, kind, mesh_res=100.0):
        p = self.p
        return W.make_wind_model(kind, p["elev"], p["slope"], p["aspect"], self.ls.res, self.ls.transform,
                                 self.ls.crs_wkt, mesh_res)

    # ------------------------------------------------------------------ MTT
    def mtt(self, ignition, ws, wd, minutes, wind="seragam", mesh_res=100.0, moist=None, order=3):
        wm = self.wind_model(wind, mesh_res)
        if wm is None:
            spd, dirn = W.uniform(self.p["fuel"].shape, ws, wd)
        else:
            spd, dirn = wm.field(ws, wd)
        moist = moist or M.FixedMoisture(self.fms)
        ros, head, ecc, fli, flame, ct = self.grids(spd, dirn, moist)
        st = SpreadState(ros.shape, ignition, self.ls.res, order)
        st.advance((ros, head, ecc), 1e29, t_max=minutes)
        arr = st.arrival()
        return Hasil(arr, ros, flame, fli, ct, info=dict(mode="mtt", ws=ws, wd=wd, minutes=minutes, wind=wind))

    # -------------------------------------------------------------- FARSITE
    def farsite(self, ignition, weather, start: dt.datetime, end: dt.datetime, wind="medan",
                conditioning_start: dt.datetime | None = None, burn_period=(0, 24), fixed_moisture=False,
                mesh_res=100.0, order=3, wind_override=None, log=print):
        """Simulasi waktu-berubah. weather: Weather (jam lokal). burn_period = (jam_mulai, jam_akhir)."""
        p = self.p
        wm = self.wind_model(wind, mesh_res)
        if fixed_moisture:
            moist = M.FixedMoisture(self.fms)
        else:
            moist = M.ConditionedMoisture(self.fms, p["fuel"], p["elev"], p["cover"], weather.elevation)
            t = conditioning_start or weather.records[0].time
            for rec in weather.records:
                if t <= rec.time < start:
                    moist.step(rec, 1.0, rec.time.hour)

        def _wx(t):
            rec = weather.at(t)
            ws, wd = rec.ws, rec.wd
            if wind_override:
                ws = wind_override.get("ws", ws)
                wd = wind_override.get("wd", wd)
            return rec, ws, wd

        def _grids(t):
            rec, ws, wd = _wx(t)
            if wm is None:
                spd, dirn = W.uniform(p["fuel"].shape, ws, wd)
            else:
                spd, dirn = wm.field(ws, wd)
            return self.grids(spd, dirn, moist), rec

        # periode = jam-jam aktif (burn period). "Jam bakar" = menit aktif kumulatif.
        hours = []
        t = start
        while t < end:
            t1 = min((t.replace(minute=0, second=0) + dt.timedelta(hours=1)), end)
            h = t.hour + t.minute / 60
            b0, b1 = burn_period
            active = (b0 <= h < b1) if b0 <= b1 else (h >= b0 or h < b1)
            hours.append((t, t1, active))
            t = t1
        st = SpreadState(p["fuel"].shape, ignition, self.ls.res, order)
        burn_clock = 0.0
        wall_of = []  # (burn_clock_awal, wall_awal) untuk konversi
        ros_out = np.zeros(p["fuel"].shape, np.float32)
        fl_out = np.zeros_like(ros_out)
        fi_out = np.zeros_like(ros_out)
        ct_out = np.zeros(p["fuel"].shape, np.int8)
        cur = None
        act = [h for h in hours if h[2]]
        for k, (t0, t1, active) in enumerate(hours):
            if not fixed_moisture and k > 0:
                moist.step(weather.at(t0), (t0 - hours[k - 1][0]).total_seconds() / 3600, t0.hour)
            if not active:
                continue
            g, rec = _grids(t0)
            _, ws_u, wd_u = _wx(t0)
            dur = (t1 - t0).total_seconds() / 60
            nxt = None
            # laju periode berikutnya (perkiraan dari kadar air sekarang)
            ia = act.index((t0, t1, active))
            if ia + 1 < len(act):
                nxt, _ = _grids(act[ia + 1][0])
            before = np.isfinite(st.arrival())
            wall_of.append((burn_clock, t0, dur))
            st.advance(g[:3], burn_clock + dur, nxt[:3] if nxt else None,
                       burn_clock + dur + 60 if nxt else None)
            burn_clock += dur
            new = np.isfinite(st.arrival()) & ~before
            ros_out[new], fl_out[new], fi_out[new], ct_out[new] = g[0][new], g[4][new], g[3][new], g[5][new]
            if log:
                ha = np.isfinite(st.arrival()).sum() * self.ls.res ** 2 / 1e4
                m1 = ""
                if not fixed_moisture:
                    grass = (p["fuel"] >= 30) & (p["fuel"] <= 33)
                    if grass.any():
                        m1 = f"  1-h rumput {np.median(moist.m[0][grass]):.1f}%"
                log(f"{t0:%Y-%m-%d %H:%M}  angin {ws_u:.0f} km/j dari {wd_u:.0f}°  "
                    f"T {rec.temp:.0f}°C RH {rec.rh:.0f}%{m1}  luas {ha:,.1f} ha")
        # jam bakar -> menit dinding sejak start
        bc = st.arrival()
        wall = np.full_like(bc, np.nan)
        for b0, t0, dur in wall_of:
            m = (bc >= b0) & (bc <= b0 + dur + 1e-6)
            wall[m] = (t0 - start).total_seconds() / 60 + (bc[m] - b0)
        late = np.isfinite(bc) & np.isnan(wall)
        wall[late] = np.nan
        return Hasil(wall, ros_out, fl_out, fi_out, ct_out, start,
                     info=dict(mode="farsite", start=str(start), end=str(end), wind=wind,
                               burn_period=burn_period, fixed_moisture=fixed_moisture))
