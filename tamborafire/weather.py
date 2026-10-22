"""Cuaca per jam: unduh dari Open-Meteo (ERA5 / ERA5-Land / ECMWF IFS / prakiraan), tulis/baca .wxs.

Port dari web/pembuat_wxs_tambora.html. Format .wxs (RAWS_UNITS: Metric):
Year Mth Day Time Temp(C) RH(%) HrlyPcp(mm) WindSpd(km/j) WindDir(deg asal) CloudCov(%)
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
VARS = ["temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m",
        "wind_direction_10m", "cloud_cover"]
TZ = {"WITA": "Asia/Makassar", "WIB": "Asia/Jakarta", "WIT": "Asia/Jayapura", "UTC": "GMT"}


@dataclass
class WxRecord:
    time: dt.datetime   # waktu lokal (zona sesuai file)
    temp: float
    rh: float
    pcp: float
    ws: float           # km/j
    wd: float           # derajat asal angin
    cc: float


@dataclass
class Weather:
    elevation: float
    records: list

    def at(self, t: dt.datetime) -> WxRecord:
        """Rekaman jam yang sedang berlaku pada waktu t (jam terakhir <= t)."""
        best = self.records[0]
        for r in self.records:
            if r.time <= t:
                best = r
            else:
                break
        return best


def build_url(lat, lon, start, end, tz="WITA", model="best_match", elevation=None, forecast=False):
    q = dict(latitude=lat, longitude=lon, hourly=",".join(VARS), timezone=TZ.get(tz, tz),
             wind_speed_unit="kmh", temperature_unit="celsius", precipitation_unit="mm")
    if forecast:
        q.update(start_date=str(start), end_date=str(end))
    else:
        q.update(start_date=str(start), end_date=str(end))
    if model and model != "best_match":
        q["models"] = model
    if elevation is not None:
        q["elevation"] = elevation
    return (FORECAST if forecast else ARCHIVE) + "?" + urllib.parse.urlencode(q)


def _pick(h, v):
    if v in h:
        return h[v]
    for k in h:
        if k.startswith(v + "_"):
            return h[k]
    return None


def from_openmeteo(data: dict, elevation=None) -> Weather:
    if data.get("error"):
        raise RuntimeError("Open-Meteo menolak permintaan: " + str(data.get("reason")))
    h = data["hourly"]
    cols = {v: _pick(h, v) for v in VARS}
    miss = [v for v, c in cols.items() if c is None]
    if miss:
        raise RuntimeError("Variabel tidak ada di respons: " + ", ".join(miss))
    recs = []
    for i, t in enumerate(h["time"]):
        vals = [cols[v][i] for v in VARS]
        if any(x is None for x in vals):
            continue
        recs.append(WxRecord(dt.datetime.fromisoformat(t), round(vals[0]), max(0, min(99, round(vals[1]))),
                             max(0.0, vals[2]), round(vals[3]), round(vals[4]) % 361,
                             max(0, min(100, round(vals[5])))))
    if not recs:
        raise RuntimeError("Semua nilai kosong; data model ini mungkin belum tersedia.")
    elev = elevation if elevation is not None else round(data.get("elevation", 0))
    return Weather(float(elev), recs)


def fetch(lat, lon, start, end, tz="WITA", model="best_match", elevation=None, conditioning_days=3,
          timeout=60) -> Weather:
    """Unduh cuaca. start/end = date. Otomatis memakai API prakiraan untuk tanggal yang belum ada di arsip."""
    start = start if isinstance(start, dt.date) else dt.date.fromisoformat(str(start))
    end = end if isinstance(end, dt.date) else dt.date.fromisoformat(str(end))
    s = start - dt.timedelta(days=conditioning_days)
    today = dt.date.today()
    forecast = end >= today - dt.timedelta(days=5)
    url = build_url(lat, lon, s, end, tz, "best_match" if forecast else model, elevation, forecast)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.load(r)
    return from_openmeteo(data, elevation)


def write_wxs(w: Weather, path):
    lines = [f"RAWS_ELEVATION: {round(w.elevation)}", "RAWS_UNITS: Metric", f"RAWS: {len(w.records)}",
             "Year  Mth  Day  Time  Temp   RH  HrlyPcp  WindSpd  WindDir  CloudCov"]
    for r in w.records:
        t = r.time
        lines.append(" ".join([f"{t.year:4d}", f"{t.month:4d}", f"{t.day:4d}", f"{t.hour * 100 + t.minute:04d}".rjust(5),
                               f"{round(r.temp):5d}", f"{round(r.rh):4d}", f"{r.pcp:8.2f}", f"{round(r.ws):8d}",
                               f"{round(r.wd):8d}", f"{round(r.cc):9d}"]))
    with open(path, "w", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


def read_wxs(path) -> Weather:
    elev, metric, recs = 0.0, True, []
    for raw in open(path, encoding="latin-1"):
        s = raw.strip()
        if not s:
            continue
        u = s.upper()
        if u.startswith("RAWS_ELEVATION"):
            elev = float(s.split(":")[1])
            continue
        if u.startswith("RAWS_UNITS"):
            metric = "METRIC" in u
            continue
        p = s.split()
        if len(p) < 9 or not p[0].isdigit():
            continue
        y, mo, d, hm = int(p[0]), int(p[1]), int(p[2]), int(p[3])
        temp, rh, pcp, ws, wd = float(p[4]), float(p[5]), float(p[6]), float(p[7]), float(p[8])
        cc = float(p[9]) if len(p) > 9 else 0.0
        if not metric:  # F, in/100?, mph
            temp, pcp, ws = (temp - 32) * 5 / 9, pcp * 0.254, ws * 1.609344
        t = dt.datetime(y, mo, d) + dt.timedelta(hours=hm // 100, minutes=hm % 100)
        recs.append(WxRecord(t, temp, rh, pcp, ws, wd, cc))
    if not metric:
        elev *= 0.3048
    recs.sort(key=lambda r: r.time)
    return Weather(elev, recs)


def override_wind(w: Weather, start, end, ws=None, wd=None):
    """Koreksi angin pada rentang jam (seperti Edit Weather Stream di FlamMap)."""
    for r in w.records:
        if start <= r.time <= end:
            if ws is not None:
                r.ws = ws
            if wd is not None:
                r.wd = wd
    return w
