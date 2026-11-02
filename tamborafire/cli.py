"""Perintah baris: python -m tamborafire <perintah> ...  (atau `tambora <perintah>` setelah pip install)."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tomllib
import zipfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cfg(path=None):
    path = path or os.path.join(ROOT, "config", "tambora.toml")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    if os.environ.get("GDRIVE_FOLDER_ID"):
        cfg["drive"]["folder_id"] = os.environ["GDRIVE_FOLDER_ID"]
    return cfg


def _rel(p):
    return p if os.path.isabs(p) or os.path.exists(p) else os.path.join(ROOT, p)


def _latlon_to_xy(args, cfg):
    if getattr(args, "latlon", None):
        from pyproj import Transformer
        lat, lon = map(float, args.latlon.split(","))
        x, y = Transformer.from_crs(4326, cfg["aoi"]["epsg"], always_xy=True).transform(lon, lat)
        args.xy = f"{x},{y}"


def _center(args):
    if args.xy:
        return tuple(map(float, args.xy.split(",")))
    import shapefile
    if str(args.api).lower().endswith((".geojson", ".json")):
        from shapely.geometry import shape
        d = json.load(open(args.api))
        b = shape((d["features"][0] if "features" in d else d)["geometry"]).bounds
    else:
        b = shapefile.Reader(str(args.api)).bbox
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _ls(args, cfg):
    from .lcp import read_lcp
    _latlon_to_xy(args, cfg)
    ls = read_lcp(args.lcp)
    if args.radius_km and not args.bbox:
        x, y = _center(args)
        r = args.radius_km * 1000
        args.bbox = f"{x - r},{y - r},{x + r},{y + r}"
    if args.bbox:
        w, s, e, n = map(float, args.bbox.split(","))
        ls = ls.crop(w, s, e, n)
    if args.res and args.res > ls.res:
        ls = ls.coarsen(int(round(args.res / ls.res)))
    return ls


def _ignition(args, ls):
    from .geo import read_ignition, ignition_from_xy
    if getattr(args, "api", None):
        m = read_ignition(args.api, ls, args.buffer)
    else:
        x, y = map(float, args.xy.split(","))
        m = ignition_from_xy(x, y, ls, args.buffer)
    if not m.any():
        sys.exit("Titik api di luar landscape / Analysis Area.")
    return m


def _ign_xy(args, ls, mask):
    if getattr(args, "xy", None):
        return tuple(map(float, args.xy.split(",")))
    r, c = np.nonzero(mask)
    return ls.west + (c.mean() + 0.5) * ls.res, ls.north - (r.mean() + 0.5) * ls.res


def _write_outputs(h, ls, out, args, x0, y0, step_min=60):
    from .geo import write_tif, perimeters, write_perimeters_shp, read_polygons
    os.makedirs(out, exist_ok=True)
    arr = np.where(np.isfinite(h.arrival), h.arrival, -1).astype(np.float32)
    write_tif(os.path.join(out, "waktu_tiba_menit.tif"), arr, ls, nodata=-1)
    write_tif(os.path.join(out, "ros_m_menit.tif"), h.ros.astype(np.float32), ls)
    write_tif(os.path.join(out, "panjang_nyala_m.tif"), h.flame.astype(np.float32), ls)
    write_tif(os.path.join(out, "intensitas_kw_m.tif"), h.intensity.astype(np.float32), ls)
    write_tif(os.path.join(out, "tipe_api.tif"), h.crown.astype(np.uint8), ls)
    tmax = float(np.nanmax(h.arrival)) if np.isfinite(h.arrival).any() else 0
    times = list(np.arange(step_min, tmax + step_min, step_min))
    per = perimeters(h.arrival, ls, times)
    if per:
        write_perimeters_shp(os.path.join(out, "perimeter.shp"), per, ls.crs_wkt, h.start)
    ha = float(np.isfinite(h.arrival).sum() * ls.res ** 2 / 1e4)
    ct = h.crown[np.isfinite(h.arrival)]
    tipe = {n: round(float((ct == v).sum() * ls.res ** 2 / 1e4), 1)
            for v, n in ((1, "permukaan"), (2, "tajuk_pasif"), (3, "tajuk_aktif"))}
    summ = dict(h.info, luas_terbakar_ha=round(ha, 1), sel_m=ls.res, metode_tajuk=getattr(args, "tajuk", "finney"),
                luas_per_tipe_api_ha=tipe, titik_api=[round(x0, 1), round(y0, 1)])
    if getattr(args, "validasi", None):
        from .validate import evaluate
        summ["validasi"] = evaluate(h.arrival, read_polygons(args.validasi), ls, x0, y0)
    json.dump(summ, open(os.path.join(out, "ringkasan.json"), "w"), indent=1, default=str)
    _png(h, ls, os.path.join(out, "peta_waktu_tiba.png"), x0, y0, getattr(args, "validasi", None))
    if getattr(args, "web", False):
        _web_outputs(h, ls, out, summ)
        try:
            from .tiga_d import dari_hasil
            dari_hasil(h, ls, summ, out)
        except Exception as e:  # data 3D opsional; simulasi tetap dianggap berhasil
            print("peringatan: data 3D tidak dibuat:", e)
    print(json.dumps(summ, indent=1, default=str))
    return summ


def _web_outputs(h, ls, out, summ):
    """Untuk halaman peta: overlay PNG transparan (waktu tiba), batas lintang/bujur, dan hasil.zip."""
    import matplotlib
    from PIL import Image
    from pyproj import Transformer
    a = np.where(np.isfinite(h.arrival), h.arrival / 60.0, np.nan)
    vmax = float(np.nanmax(a)) if np.isfinite(a).any() else 1.0
    rgba = (matplotlib.colormaps["inferno_r"](np.nan_to_num(a / max(vmax, 1e-6))) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(np.isfinite(a), 200, 0)
    Image.fromarray(rgba).save(os.path.join(out, "overlay.png"))
    tf = Transformer.from_crs(ls.crs_wkt or "EPSG:32750", 4326, always_xy=True)
    xs, ys = tf.transform([ls.west, ls.east, ls.west, ls.east], [ls.south, ls.south, ls.north, ls.north])
    lon0, lat0 = tf.transform(*summ["titik_api"])
    web = dict(summ, batas=[[min(ys), min(xs)], [max(ys), max(xs)]], jam_maks=round(vmax, 2),
               titik_latlon=[round(lat0, 5), round(lon0, 5)])
    json.dump(web, open(os.path.join(out, "web.json"), "w"), default=str)
    with zipfile.ZipFile(os.path.join(out, "hasil.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(out)):
            if f not in ("hasil.zip", "overlay.png", "web.json"):
                z.write(os.path.join(out, f), f)


def _png(h, ls, path, x0, y0, scar=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ext = [ls.west, ls.east, ls.south, ls.north]
    fig, ax = plt.subplots(figsize=(9, 8), dpi=110)
    elev = ls.physical()["elev"]
    ax.imshow(elev, cmap="Greys", extent=ext, alpha=0.6)
    a = np.where(np.isfinite(h.arrival), h.arrival / 60.0, np.nan)
    im = ax.imshow(a, cmap="inferno_r", extent=ext, alpha=0.85)
    fig.colorbar(im, ax=ax, label="jam sejak api mulai", shrink=0.8)
    if scar:
        from .geo import read_polygons
        g = read_polygons(scar)
        for p in getattr(g, "geoms", [g]):
            ax.plot(*p.exterior.xy, color="cyan", lw=1)
    ax.plot([x0], [y0], "c*", ms=12)
    ax.set_title("Waktu tiba api")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------- perintah
def cmd_mtt(args, cfg):
    from .simulate import Simulator
    ls = _ls(args, cfg)
    ig = _ignition(args, ls)
    sim = Simulator(ls, _rel(args.fmd or cfg["kalibrasi"]["fmd"]), _rel(args.fms or cfg["kalibrasi"]["fms"]),
                    ros_adjust={int(k): float(v) for k, v in cfg["kalibrasi"].get("ros_adjust", {}).items()},
                    crown=args.tajuk != "tanpa", crown_method=args.tajuk)
    h = sim.mtt(ig, args.kecepatan, args.arah, args.menit, wind=args.angin, mesh_res=args.mesh)
    x0, y0 = _ign_xy(args, ls, ig)
    return _write_outputs(h, ls, args.out, args, x0, y0, step_min=args.interval)


def cmd_farsite(args, cfg):
    from .simulate import Simulator
    from . import weather as WX
    from pyproj import Transformer
    ls = _ls(args, cfg)
    ig = _ignition(args, ls)
    x0, y0 = _ign_xy(args, ls, ig)
    start = dt.datetime.fromisoformat(args.mulai)
    end = start + dt.timedelta(hours=args.jam)
    if args.wxs:
        wx = WX.read_wxs(args.wxs)
        os.makedirs(args.out, exist_ok=True)
        import shutil
        shutil.copy(args.wxs, os.path.join(args.out, "cuaca_" + os.path.basename(args.wxs)))
    else:
        lon, lat = Transformer.from_crs(cfg["aoi"]["epsg"], 4326, always_xy=True).transform(x0, y0)
        wx = WX.fetch(round(lat, 4), round(lon, 4), start.date(), end.date(), cfg["simulasi"]["zona"],
                      cfg["simulasi"]["model_cuaca"], conditioning_days=3)
        os.makedirs(args.out, exist_ok=True)
        WX.write_wxs(wx, os.path.join(args.out, f"cuaca_{lat:.4f}_{lon:.4f}.wxs"))
    sim = Simulator(ls, _rel(args.fmd or cfg["kalibrasi"]["fmd"]), _rel(args.fms or cfg["kalibrasi"]["fms"]),
                    ros_adjust={int(k): float(v) for k, v in cfg["kalibrasi"].get("ros_adjust", {}).items()},
                    crown=args.tajuk != "tanpa", crown_method=args.tajuk)
    ov = {}
    if args.kecepatan is not None:
        ov["ws"] = args.kecepatan
    if args.arah is not None:
        ov["wd"] = args.arah
    cond = dt.datetime.fromisoformat(args.kondisi_mulai) if args.kondisi_mulai else None
    h = sim.farsite(ig, wx, start, end, wind=args.angin, conditioning_start=cond,
                    burn_period=tuple(args.burn), fixed_moisture=args.kadar_air_tetap, mesh_res=args.mesh,
                    wind_override=ov or None)
    return _write_outputs(h, ls, args.out, args, x0, y0, step_min=args.interval)


def cmd_wxs(args, cfg):
    from . import weather as WX
    wx = WX.fetch(args.lat, args.lon, dt.date.fromisoformat(args.mulai), dt.date.fromisoformat(args.sampai or args.mulai),
                  args.zona, args.model, args.elevasi, conditioning_days=0 if args.tanpa_kondisi else 3)
    WX.write_wxs(wx, args.out)
    print(f"{len(wx.records)} jam ditulis ke {args.out} (elevasi {wx.elevation:.0f} m)")


def cmd_landscape_dasar(args, cfg):
    from .landscape import load_base, to_landscape
    from .lcp import write_lcp
    lay, tr, crs = load_base(args.dasar, cfg)
    ls = to_landscape(lay, tr, crs.to_wkt(), cfg, "Tambora dasar")
    write_lcp(ls, args.out)
    print("ditulis", args.out, ls.shape)


def _waktu_citra(c):
    """Waktu akuisisi dari id Sentinel-2 L2A (S2C_MSIL2A_20261020T020711_...) atau ISO; UTC naif."""
    import re
    m = re.search(r"_(\d{8}T\d{6})_", c)
    if m:
        return dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    return dt.datetime.fromisoformat(c.replace("Z", "+00:00")).replace(tzinfo=None)


def cmd_update(args, cfg):
    """Update landscape: unduh dasar dari Drive, Sentinel-2 jendela tetap 21 hari sejak awal musim,
    tandai bekas terbakar, unggah. Dijalankan tiap ada citra baru; jendela yang belum lengkap dihitung ulang."""
    import rasterio
    from . import landscape as L
    from . import sentinel2 as S2
    from .lcp import write_lcp
    work = args.kerja
    os.makedirs(work, exist_ok=True)
    dasar = args.lokal_dasar or os.path.join(work, "dasar")
    drive = None
    p_state = os.path.join(work, "status_jendela_lama.tif")
    p_ref = os.path.join(work, "nbr_acuan_lama.tif")
    for p in (p_state, p_ref):
        if os.path.exists(p):
            os.remove(p)
    if not args.tanpa_drive:
        from .drive import Drive
        drive = Drive()
        root = cfg["drive"]["folder_id"].strip()
        if not root:
            sys.exit("Isi drive.folder_id di config/tambora.toml atau secret GDRIVE_FOLDER_ID.")
        try:
            inf = drive.info(root)
        except RuntimeError as e:
            sys.exit(f"Folder induk Drive tidak bisa dibuka (cek GDRIVE_FOLDER_ID): {e}")
        print(f"Folder Drive: {inf['name']}  (boleh menulis: {inf.get('capabilities', {}).get('canAddChildren')})")
        isi = [f["name"] for f in drive.ls(root)]
        print("Isi folder induk:", ", ".join(isi) or "(kosong)")
        if cfg["dasar"]["elev"] in isi:
            fid_dasar = root  # GeoTIFF dasar langsung di folder induk
        else:
            fid_dasar = drive.path(cfg["drive"]["dasar"], root)
        if not args.lokal_dasar:
            need = [cfg["dasar"][k] for k in L.LAYER_KEYS] + [cfg["dasar"]["nbr"]]
            drive.sync_down(fid_dasar, dasar, need)
            if not os.path.exists(os.path.join(dasar, cfg["dasar"]["nbr"])):
                drive.sync_down(fid_dasar, dasar, [cfg["dasar"]["s2_median"]])
        fid_ls = drive.path(cfg["drive"]["landscape"], root)
        fid_now = drive.path("terbaru", fid_ls)
        if not args.mulai_ulang:
            for name, p in (("status_jendela.tif", p_state), ("nbr_acuan.tif", p_ref)):
                f = drive.find(name, fid_now)
                if f:
                    drive.download(f["id"], p)
    elif not args.mulai_ulang:  # tanpa Drive: lanjutkan dari hasil run sebelumnya di folder kerja
        import shutil
        for name, p in (("status_jendela.tif", p_state), ("nbr_acuan.tif", p_ref)):
            if os.path.exists(os.path.join(work, "hasil", name)):
                shutil.copy(os.path.join(work, "hasil", name), p)
    lay, tr, crs = L.load_base(dasar, cfg)
    nbr0, nbr_path, made = L.base_nbr(dasar, cfg)
    if made and drive:
        drive.upload(nbr_path, fid_dasar)
    tgl = dt.date.fromisoformat(args.tanggal) if args.tanggal else dt.date.today()
    k = cfg["mingguan"]
    hari = int(k["hari"])
    mm, dd = map(int, k["awal_musim"].split("-"))
    season = dt.date(tgl.year if (tgl.month, tgl.day) >= (mm, dd) else tgl.year - 1, mm, dd)
    lama = {}
    if args.status and os.path.exists(args.status):
        try:
            lama = json.load(open(args.status))
        except ValueError:
            lama = {}
    if lama.get("awal_musim") != season.isoformat():
        lama = {}
    # status di batas jendela lengkap terakhir (bekas terbakar + NBR acuan bergulir)
    scar_prev, nbr_ref, last = None, nbr0.copy(), None
    if os.path.exists(p_state) and os.path.exists(p_ref):
        with rasterio.open(p_state) as d:
            tags = d.tags()
            t_state = dt.date.fromisoformat(tags["tanggal"]) if tags.get("tanggal") else None
            if (tags.get("awal_musim") == season.isoformat() and tags.get("metode") == L.METODE
                    and tags.get("skema") == L.SKEMA and t_state and t_state <= tgl and L.selaras(season, t_state, hari)):
                scar_prev = d.read(1)
                last = t_state
                with rasterio.open(p_ref) as r:
                    a = r.read(1).astype(np.float32)
                    nbr_ref = np.where(a == -32768, np.nan, a / 10000.0).astype(np.float32)
            else:
                print("Status lama diabaikan (metode/musim/skema berbeda); hitung ulang sejak", season)
    wins = L.jendela_musim(season, tgl, hari, last)
    if last is None:
        print(f"Hitung ulang sejak awal musim {season}: {len(wins)} jendela")
    else:
        print(f"Lanjut dari status {last}: {len(wins)} jendela ({sum(w[2] for w in wins)} lengkap)")
    ref_mask = None
    p_val = _rel(k.get("validasi_bekas", ""))
    if k.get("validasi_bekas") and os.path.exists(p_val):
        from .geo import read_polygons
        from rasterio.features import rasterize as _rz
        ref_mask = _rz([(read_polygons(p_val), 1)], out_shape=lay["fuel"].shape, transform=tr).astype(bool)
    from . import firms
    hot_pts = None
    if firms.key() and wins:
        try:
            hot_pts = firms.fetch(cfg, wins[0][0] - dt.timedelta(days=hari), wins[-1][1])
            print(f"Hotspot FIRMS: {len(hot_pts)} titik di sekitar AOI")
            if args.simpan_komposit:
                os.makedirs(args.simpan_komposit, exist_ok=True)
                json.dump(hot_pts, open(os.path.join(args.simpan_komposit, "hotspot_firms.json"), "w"))
        except Exception as e:  # tanpa hotspot tetap jalan dengan konfirmasi jendela berikutnya
            print("Hotspot FIRMS gagal diambil, pakai konfirmasi jendela berikutnya:", e)
    else:
        print("FIRMS_MAP_KEY kosong: bekas terbakar baru dihitung setelah jendela berikutnya masih gelap")
    st = dict(scar=scar_prev, fuel=L.terapkan_bekas(lay["fuel"], scar_prev, cfg), ref=nbr_ref, info={})
    used_all, per, terakhir = [], [], []

    def proses(w0, w1):
        items = S2.search(cfg, w1, 0, k["stac"], k["awan_maks"], start=w0)
        print(f"== {w0} s.d. {w1}: {len(items)} citra")
        terakhir.extend(f["properties"]["datetime"] for f in items)
        if not items:
            return
        try:
            comp, used = S2.median_composite(cfg, items, k["stac"])
        except RuntimeError as e:
            print(" ", e)
            return
        if args.simpan_komposit:
            os.makedirs(args.simpan_komposit, exist_ok=True)
            q = lambda a: np.where(np.isfinite(a[::3, ::3]), np.round(a[::3, ::3] * 10000), -32768).astype(np.int16)
            np.savez_compressed(os.path.join(args.simpan_komposit, f"komposit_{w0}_{w1}.npz"), nbr=q(comp["nbr"]),
                                ndvi=q(comp["ndvi"]), n=comp["n"][::3, ::3].astype(np.uint8))
        hot = None
        if hot_pts is not None:
            a0 = (w0 - dt.timedelta(days=hari)).isoformat()
            pts = [p for p in hot_pts if a0 <= p["tanggal"] <= w1.isoformat()]
            hot = firms.mask(pts, lay["fuel"].shape, tr, cfg["aoi"]["epsg"], float(k.get("hotspot_buffer_m", 750)))
            print(f"   hotspot {a0} s.d. {w1}: {len(pts)} titik")
        fuel, scar, ndvi, dnbr, ref, info = L.update(lay, st["ref"], comp, cfg, st["scar"], hotspot=hot)
        st.update(fuel=fuel, scar=scar, ref=ref, info=info)
        used_all.extend(used)
        per.append(dict(jendela=f"{w0}/{w1}", citra=len(used), baru_ha=info["bekas_terbakar_baru_ha"],
                        koreksi=info["koreksi_musiman_dnbr"]))
        msg = f"   bekas terbakar baru {info['bekas_terbakar_baru_ha']} ha, koreksi musiman {info['koreksi_musiman_dnbr']}"
        msg += (f"; kandidat baru {info['kandidat_baru_ha']} ha, gugur {info['kandidat_gugur_ha']} ha, "
                f"menunggu {info['kandidat_menunggu_ha']} ha; per kelompok {info['baru_per_kelompok_ha']}; "
                f"elevasi p10/50/90 {info.get('baru_elevasi_persentil_m')}")
        per[-1].update(kandidat_baru_ha=info["kandidat_baru_ha"], kandidat_gugur_ha=info["kandidat_gugur_ha"])
        if "tanpa_hotspot_diabaikan_ha" in info:
            msg += f"; petak gelap tanpa hotspot diabaikan {info['tanpa_hotspot_diabaikan_ha']} ha"
            per[-1]["tanpa_hotspot_diabaikan_ha"] = info["tanpa_hotspot_diabaikan_ha"]
        if ref_mask is not None and ref_mask.any():
            hit = (scar == 1) & ref_mask
            cand = (scar == 2) & ref_mask
            v = dnbr[ref_mask & np.isfinite(dnbr)]
            q = [round(float(x), 3) for x in np.percentile(v, [10, 50, 90])] if v.size else None
            o = dnbr[~ref_mask & np.isfinite(dnbr)]
            qo = [round(float(x), 3) for x in np.percentile(o, [50, 99, 99.9])] if o.size else None
            msg += (f"; bekas 9 Sep: terkonfirmasi {100 * hit.sum() / ref_mask.sum():.0f}%, kandidat "
                    f"{100 * cand.sum() / ref_mask.sum():.0f}%, dNBR terkoreksi di dalam p10/50/90 {q}, "
                    f"di luar p50/99/99.9 {qo}")
            per[-1]["validasi_9sep_persen"] = round(100 * float(hit.sum() / ref_mask.sum()), 1)
        print(msg)

    simpan = (scar_prev, nbr_ref.copy(), last or season)  # status untuk update berikutnya
    for w0, w1, lengkap in wins:
        proses(w0, w1)
        if lengkap:
            simpan = (None if st["scar"] is None else st["scar"].copy(), st["ref"].copy(), w1)
    scar, fuel = st["scar"], st["fuel"]
    if scar is None and last is None:
        sys.exit("Tidak ada citra cerah sama sekali; landscape tidak diperbarui.")
    if scar is None:
        scar = np.zeros(lay["fuel"].shape, np.uint8)
    fuel0 = lay["fuel"].astype(np.int16)
    lay["fuel"] = fuel
    out = os.path.join(work, "hasil")
    os.makedirs(out, exist_ok=True)
    stamp = tgl.isoformat()
    ls = L.to_landscape(lay, tr, crs.to_wkt(), cfg, f"Tambora {stamp}")
    name = f"tambora_{stamp.replace('-', '')}_10m"
    write_lcp(ls, os.path.join(out, name + ".lcp"))
    prof = dict(driver="GTiff", width=fuel.shape[1], height=fuel.shape[0], count=1, crs=crs, transform=tr,
                compress="deflate")
    with rasterio.open(os.path.join(out, "fuel_model.tif"), "w", dtype="int16", **prof) as d:
        d.write(fuel, 1)
    with rasterio.open(os.path.join(out, "bekas_terbakar_musim.tif"), "w", dtype="uint8", **prof) as d:
        d.write(scar, 1)
        d.update_tags(awal_musim=season.isoformat(), tanggal=stamp, metode=L.METODE)
    s_scar, s_ref, s_tgl = simpan
    with rasterio.open(os.path.join(out, "status_jendela.tif"), "w", dtype="uint8", **prof) as d:
        d.write(np.zeros(fuel.shape, np.uint8) if s_scar is None else s_scar.astype(np.uint8), 1)
        d.update_tags(awal_musim=season.isoformat(), tanggal=s_tgl.isoformat(), metode=L.METODE, skema=L.SKEMA)
    with rasterio.open(os.path.join(out, "nbr_acuan.tif"), "w", dtype="int16", nodata=-32768, **prof) as d:
        d.write(np.where(np.isfinite(s_ref), np.round(s_ref * 10000), -32768).astype(np.int16), 1)
    ha = float(cfg["aoi"]["res"]) ** 2 / 1e4
    info = dict(st["info"])
    info.setdefault("mode", k["mode_kelas"])
    info.setdefault("metode", L.METODE)
    for kunci in ("hotspot", "piksel_cerah_persen", "koreksi_musiman_dnbr"):
        if kunci not in info and kunci in lama:
            info[kunci] = lama[kunci]
    musim = round(float((scar == 1).sum() * ha), 1)
    info["bekas_terbakar_musim_ha"] = musim
    if "bekas_terbakar_musim_ha" in lama:
        info["bekas_terbakar_baru_ha"] = round(max(0.0, musim - float(lama["bekas_terbakar_musim_ha"])), 1)
        info["baru_sejak"] = lama.get("tanggal")
    info["luas_fuel_ha"] = {str(int(v)): round(float(c * ha), 1) for v, c in zip(*np.unique(fuel, return_counts=True))}
    # riwayat jendela: gabung dengan status lama, jendela yang sama (tanggal awal sama) diganti
    jend = {j["jendela"].split("/")[0]: j for j in lama.get("jendela", [])}
    jend.update({j["jendela"].split("/")[0]: j for j in per})
    t_akhir = [_waktu_citra(t) for t in terakhir]
    if lama.get("citra_terakhir"):
        t_akhir.append(_waktu_citra(lama["citra_terakhir"]))
    info.update(tanggal=stamp, citra=used_all, awal_musim=season.isoformat(), lcp=name + ".lcp",
                jendela=[jend[s] for s in sorted(jend)], skema=L.SKEMA, status_jendela=s_tgl.isoformat(),
                jadwal="tiap ada citra Sentinel-2 baru (dicek 2x sehari)")
    if t_akhir:
        info["citra_terakhir"] = max(t_akhir).isoformat() + "Z"
    p_res = _rel(cfg.get("kawasan", {}).get("resort", ""))
    if cfg.get("kawasan", {}).get("resort") and os.path.exists(p_res):
        info["kawasan"] = L.statistik_kawasan(p_res, fuel0, fuel, scar, tr, cfg["aoi"]["epsg"], float(cfg["aoi"]["res"]))
        print("Sisa K1-K4 di kawasan (ha):", info["kawasan"]["total"]["sisa"])
    json.dump(info, open(os.path.join(out, "ringkasan.json"), "w"), indent=1)
    L.preview_png(fuel, os.path.join(out, "pratinjau_fuel.png"), f"Fuel model Tambora {stamp}")
    zp = os.path.join(out, name + ".zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in (name + ".lcp", name + ".prj"):
            z.write(os.path.join(out, f), f)
        z.write(_rel(cfg["kalibrasi"]["fmd"]), os.path.basename(cfg["kalibrasi"]["fmd"]))
        z.write(_rel(cfg["kalibrasi"]["fms"]), os.path.basename(cfg["kalibrasi"]["fms"]))
    print(json.dumps({k2: v for k2, v in info.items() if k2 not in ("citra", "jendela", "kawasan")}, indent=1))
    if drive and not args.tanpa_unggah:
        arsip = drive.path(f"arsip/{stamp}", fid_ls)
        for f in ("fuel_model.tif", "bekas_terbakar_musim.tif", "ringkasan.json", "pratinjau_fuel.png"):
            drive.upload(os.path.join(out, f), arsip)
        for f in ("fuel_model.tif", "bekas_terbakar_musim.tif", "status_jendela.tif", "nbr_acuan.tif",
                  "ringkasan.json", "pratinjau_fuel.png"):
            drive.upload(os.path.join(out, f), fid_now)
        drive.upload(zp, fid_now, "landscape_terbaru_10m.zip")
        drive.upload(os.path.join(out, name + ".lcp"), fid_now, "landscape_terbaru_10m.lcp")
        drive.upload(os.path.join(out, name + ".prj"), fid_now, "landscape_terbaru_10m.prj")
        print("diunggah ke Google Drive:", cfg["drive"]["landscape"] + "/terbaru")
    if args.status and not args.tanpa_unggah:
        os.makedirs(os.path.dirname(args.status), exist_ok=True)
        json.dump(info, open(args.status, "w"), indent=1)


def cmd_cek_citra(args, cfg):
    """Ada citra Sentinel-2 baru (awan < awan_maks) sejak citra terakhir di status? Tulis baru=true/false
    ke $GITHUB_OUTPUT supaya workflow hanya memperbarui landscape bila ada citra baru."""
    from . import sentinel2 as S2
    k = cfg["mingguan"]
    st = json.load(open(args.status)) if args.status and os.path.exists(args.status) else {}
    if st.get("citra_terakhir"):
        last = _waktu_citra(st["citra_terakhir"])
    elif st.get("citra"):
        last = max(_waktu_citra(c) for c in st["citra"])
    else:
        last = None
    baru, ket = True, "belum ada status landscape"
    if last is not None:
        try:
            items = S2.search(cfg, dt.date.today(), 0, k["stac"], k["awan_maks"], start=last.date())
            new = [f for f in items if _waktu_citra(f["properties"]["datetime"]) > last + dt.timedelta(minutes=30)]
            baru = bool(new)
        except Exception as e:  # coba lagi pada jadwal cek berikutnya
            print("Katalog Sentinel-2 tidak bisa dicek:", e)
            new, baru = [], False
        tgl = sorted({f["properties"]["datetime"][:10] for f in new})
        ket = f"citra terakhir {last:%Y-%m-%d %H:%M} UTC; baru: {', '.join(tgl) or 'tidak ada'}"
    print(("ADA citra baru: " if baru else "Tidak ada citra baru: ") + ket)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"baru={'true' if baru else 'false'}\n")


def cmd_ambil_landscape(args, cfg):
    """Unduh landscape terbaru dari Drive (untuk simulasi di Actions atau di PC)."""
    from .drive import Drive
    d = Drive()
    root = cfg["drive"]["folder_id"]
    fid = d.path(cfg["drive"]["landscape"] + "/terbaru", root)
    os.makedirs(args.out, exist_ok=True)
    d.sync_down(fid, args.out, ["landscape_terbaru_10m.lcp", "landscape_terbaru_10m.prj", "ringkasan.json"])
    print(os.path.join(args.out, "landscape_terbaru_10m.lcp"))


def cmd_ambil_dasar(args, cfg):
    """Unduh GeoTIFF dasar (landscape awal musim) dan, bila diminta, bekas terbakar Sentinel-2 terbaru dari Drive."""
    from . import landscape as L
    from .drive import Drive
    d = Drive()
    root = cfg["drive"]["folder_id"].strip()
    if not root:
        sys.exit("Isi drive.folder_id di config/tambora.toml atau secret GDRIVE_FOLDER_ID.")
    isi = [f["name"] for f in d.ls(root)]
    fid = root if cfg["dasar"]["elev"] in isi else d.path(cfg["drive"]["dasar"], root)
    need = [cfg["dasar"][k] for k in L.LAYER_KEYS]
    d.sync_down(fid, args.out, need)
    kurang = [n for n in need if not os.path.exists(os.path.join(args.out, n))]
    if kurang:
        sys.exit("GeoTIFF dasar tidak lengkap di Drive: " + ", ".join(kurang))
    if args.bekas:
        f = d.find("bekas_terbakar_musim.tif", d.path(cfg["drive"]["landscape"] + "/terbaru", root))
        if f:
            os.makedirs(os.path.dirname(args.bekas) or ".", exist_ok=True)
            d.download(f["id"], args.bekas)
            print("bekas terbakar Sentinel-2:", args.bekas)
        else:
            print("bekas_terbakar_musim.tif belum ada di Drive landscape/terbaru")
    print("dasar:", args.out)


def cmd_unggah(args, cfg):
    from .drive import Drive
    d = Drive()
    fid = d.path(args.ke, cfg["drive"]["folder_id"])
    for f in args.file:
        if os.path.isdir(f):
            for g in sorted(os.listdir(f)):
                p = os.path.join(f, g)
                if os.path.isfile(p):
                    print("unggah", g); d.upload(p, fid)
        else:
            print("unggah", f); d.upload(f, fid)


def cmd_areal_terbakar(args, cfg):
    """Delineasi manual areal terbakar di Drive -> docs/status/areal_terbakar.json + docs/data/areal_terbakar.geojson."""
    from . import delineasi as D
    k = cfg.get("delineasi", {})
    lama = {}
    if os.path.exists(args.status):
        try:
            lama = json.load(open(args.status, encoding="utf-8"))
        except ValueError:
            lama = {}
    kerja = os.path.join(args.kerja, "delineasi")
    mmdd = k.get("awal_musim", cfg["mingguan"]["awal_musim"])
    mode = k.get("mode", "gabung")
    luas_min = float(k.get("luas_min_ha", 0.01))
    if args.lokal:
        sets, sidik = D.kumpulan_lokal(args.lokal), None
    else:
        from .drive import Drive
        drive = Drive()
        fid = drive.path(cfg["drive"].get("delineasi", "delineasi"), cfg["drive"]["folder_id"])
        files = D.daftar_drive(drive, fid)
        sidik = D.sidik(files, f"{mmdd}|{mode}|{luas_min}")
        print(f"folder delineasi: {len(files)} file, sidik {sidik}")
        if sidik == lama.get("sidik") and not args.paksa:
            print("Tidak ada perubahan di folder delineasi.")
            return
        import shutil
        shutil.rmtree(kerja, ignore_errors=True)
        sets = D.unduh_drive(drive, files, kerja)
    hasil, gj = D.olah(sets, _rel(cfg["kawasan"].get("tn", "docs/data/batas_tn.geojson")), _rel(cfg["kawasan"]["resort"]),
                       mmdd, mode, luas_min)
    hasil.update(sidik=sidik, diperbarui=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat())
    D.tulis(hasil, gj, args.status, args.geojson)
    for p in hasil["peringatan"]:
        print("peringatan:", p)
    print(f"{hasil['jumlah_delineasi']} tanggal delineasi, terbakar di kawasan {hasil['terbakar_ha']} ha "
          f"(terakhir {hasil['tanggal']}), {len(gj['features'])} poligon -> {args.status}, {args.geojson}")


def _token_repo():
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY") or "kopluck8/Simulasi-API-Tambora"
    if not tok:
        sys.exit("perlu GH_TOKEN/GITHUB_TOKEN")
    return tok, repo


def cmd_terbitkan_3d(args, cfg):
    from .tiga_d import terbitkan
    tok, repo = _token_repo()
    if not os.path.exists(args.file):
        print("tidak ada", args.file, "- dilewati")
        return
    terbitkan(args.file, args.id, repo, tok)


def cmd_isi_3d(args, cfg):
    """Buat data 3D untuk simulasi lama di GitHub Release (sim-*) yang belum punya."""
    import re
    import requests
    from .tiga_d import dari_folder, terbitkan, ada
    tok, repo = _token_repo()
    hdr = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    rel = requests.get(f"https://api.github.com/repos/{repo}/releases?per_page=100", headers=hdr, timeout=60).json()
    rel = [r for r in rel if r.get("tag_name", "").startswith("sim-")]
    if args.id:
        rel = [r for r in rel if r["tag_name"] == "sim-" + args.id]
    os.makedirs(args.kerja, exist_ok=True)
    gagal = 0
    for r in rel:
        sid = r["tag_name"][4:]
        if not args.ulang and ada(repo, sid, tok):
            print(sid, "sudah ada")
            continue
        m = re.search(r"```json\s*(.*?)```", r.get("body") or "", re.S)
        web = json.loads(m.group(1)) if m else {}
        mas = web.get("masukan") or {}
        lcp = _rel(cfg["simulasi"].get("lcp_contoh", "contoh/piong_9sep/landscape_prakebakaran_9sep_30m.lcp")) \
            if mas.get("ls") == "contoh" else args.lcp
        aset = {a["name"]: a["browser_download_url"] for a in r.get("assets", [])}
        if "hasil.zip" not in aset or not os.path.exists(lcp):
            print(sid, "dilewati: hasil.zip atau landscape tidak ada")
            continue
        zp = os.path.join(args.kerja, f"{sid}.zip")
        with requests.get(aset["hasil.zip"], timeout=300, stream=True) as q:
            q.raise_for_status()
            with open(zp, "wb") as f:
                for b in q.iter_content(1 << 20):
                    f.write(b)
        cat = None if mas.get("ls") == "contoh" else "elevasi & bahan bakar dari landscape terbaru saat data 3D dibuat"
        try:
            data = dari_folder(zp, lcp, web, mas, cat)
        except Exception as e:
            print(sid, "gagal:", e)
            gagal += 1
            continue
        out = os.path.join(args.kerja, f"{sid}.bin.gz")
        open(out, "wb").write(data)
        print(sid, f"{len(data) / 1e6:.2f} MB")
        terbitkan(out, sid, repo, tok)
    if gagal:
        sys.exit(f"{gagal} simulasi gagal dibuat data 3D-nya")


def _filter_aoi(points, cfg, pad_m=3000.0):
    from pyproj import Transformer
    if not points:
        return []
    a = cfg["aoi"]
    tf = Transformer.from_crs(4326, a["epsg"], always_xy=True)
    x, y = tf.transform([p["lon"] for p in points], [p["lat"] for p in points])
    return [p for p, xi, yi in zip(points, x, y)
            if a["west"] - pad_m <= xi <= a["east"] + pad_m and a["south"] - pad_m <= yi <= a["north"] + pad_m]


def cmd_titik_panas(args, cfg):
    """Impor titik panas (KMZ/KML SiPongi, CSV FIRMS) ke data/titik_panas/titik_panas.geojson."""
    from . import asal as A
    pts, lin, meta = A.baca_hotspot(args.simpan) if os.path.exists(args.simpan) else ([], [], {})
    for f in args.file:
        baru = A.baca_kml(f) if f.lower().endswith((".kmz", ".kml")) else A.baca_csv(f)
        aoi = _filter_aoi(baru, cfg)
        n0 = len(pts)
        pts = A.gabung(pts, aoi)
        lin = A.gabung_lintasan(lin, A.lintasan(baru))
        rentang = [min(p["t"] for p in baru).strftime("%Y-%m-%d %H:%M"), max(p["t"] for p in baru).strftime("%Y-%m-%d %H:%M")] if baru else None
        meta.setdefault("impor", []).append(dict(file=os.path.basename(f), diimpor=dt.date.today().isoformat(), titik=len(baru),
                                                 di_aoi=len(aoi), baru=len(pts) - n0, rentang_wita=rentang))
        print(f"{f}: {len(baru)} titik, {len(aoi)} di sekitar AOI, {len(pts) - n0} baru; lintasan satelit {len(lin)}")
    A.tulis_hotspot(pts, args.simpan, lin, meta)
    print("ditulis", args.simpan, len(pts), "titik")


def cmd_asal_api(args, cfg):
    """Pemodelan terbalik: titik dan jam api mulai tiap kejadian dari titik panas + areal terbakar."""
    from . import asal as A
    from . import firms
    from .lcp import read_lcp
    pts, lin, meta = A.baca_hotspot(args.titik)
    if not pts:
        sys.exit("Belum ada titik panas di " + args.titik)
    if not args.tanpa_firms and firms.key():
        sejak = dt.date.fromisoformat(args.firms_sejak) if args.firms_sejak else max(p["t"] for p in pts).date() - dt.timedelta(days=3)
        try:
            baru = A.ambil_firms("115.8,-9.2,119.4,-8.0", sejak, dt.date.today())
            n0 = len(pts)
            pts = A.gabung(pts, _filter_aoi(baru, cfg))
            lin = A.gabung_lintasan(lin, A.lintasan(baru))
            meta["firms"] = dict(diperbarui=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                                 sejak=sejak.isoformat(), titik_baru=len(pts) - n0)
            print(f"FIRMS {sejak} s.d. hari ini: {len(baru)} titik NTB, {len(pts) - n0} baru di sekitar AOI")
            A.tulis_hotspot(pts, args.titik, lin, meta)
        except Exception as e:  # tanpa FIRMS tetap jalan dengan data yang ada
            print("FIRMS gagal, pakai titik panas yang ada:", e)
    if args.lcp:
        ls10 = read_lcp(args.lcp)
    else:
        from . import landscape as L
        lay, tr, crs = L.load_base(args.dasar, cfg)
        ls10 = L.to_landscape(lay, tr, crs.to_wkt(), cfg, "Tambora dasar")
    grid = A.Grid(cfg, 3 * ls10.res)
    tn = A.raster_poligon(_rel(cfg["kawasan"].get("tn", "docs/data/batas_tn.geojson")), grid)
    burn, binfo = A.areal_terbakar(grid, _rel(args.delineasi), tn, args.bekas_s2)
    t_min = min(p["t"] for p in pts).date() - dt.timedelta(days=6)
    t_max = min(max(p["t"] for p in pts).date() + dt.timedelta(days=2), dt.date.today())
    cuaca = A.CuacaUji() if args.cuaca_uji else A.Cuaca(os.path.join(args.kerja, "cuaca"), t_min, t_max,
                                                          cfg["simulasi"]["zona"], cfg["simulasi"]["model_cuaca"])
    fmd, fms = _rel(cfg["kalibrasi"]["fmd"]), _rel(cfg["kalibrasi"]["fms"])
    ra = {int(k): float(v) for k, v in cfg["kalibrasi"].get("ros_adjust", {}).items()}
    ctx = A.Konteks(cfg, ls10, fmd, fms, ra, pts, lin, burn, tn, cuaca, root=ROOT)
    evs, info = A.siapkan(ctx, min_ha=args.min_ha)
    lama = {} if args.ulang else A.hasil_lama(args.status, args.keluaran)
    hanya = set(args.hanya.split(",")) if args.hanya else None
    hasil, gagal = A.jalankan(ctx, evs, lama, proses=args.proses, hanya=hanya)
    src = [dict(f) for f in meta.get("impor", [])]
    m = dict(titik_panas=dict(impor=src, firms=meta.get("firms"),
                              rentang_wita=[min(p["t"] for p in pts).strftime("%Y-%m-%d %H:%M"),
                                            max(p["t"] for p in pts).strftime("%Y-%m-%d %H:%M")]),
             areal_terbakar=dict(binfo, file=os.path.relpath(_rel(args.delineasi), ROOT),
                                 s2=os.path.basename(args.bekas_s2) if args.bekas_s2 else None),
             cuaca="uji (buatan)" if args.cuaca_uji else "Open-Meteo ERA5 / ERA5-Land (arsip), prakiraan untuk 6 hari terakhir",
             landscape=os.path.basename(args.lcp) if args.lcp else "GeoTIFF dasar awal kemarau (Drive/dasar)",
             fmd=cfg["kalibrasi"]["fmd"], fms=cfg["kalibrasi"]["fms"], ros_adjust=cfg["kalibrasi"].get("ros_adjust", {}))
    A.tulis_hasil(ctx, evs, info, hasil, gagal, args.status, args.keluaran, meta=m)


def cmd_drive_auth(args, cfg):
    from .drive import authorize_interactive
    tok = authorize_interactive(args.client_id, args.client_secret)
    print("\nRefresh token (simpan sebagai secret GDRIVE_REFRESH_TOKEN di GitHub):\n" + tok)


def cmd_validasi(args, cfg):
    import rasterio
    from .lcp import Landscape
    from .geo import read_polygons
    from .validate import evaluate
    with rasterio.open(args.waktu_tiba) as d:
        a = d.read(1).astype(np.float32)
        a[a < 0] = np.nan
        ls = Landscape(d.transform.c, d.transform.f, d.transform.a, {"elev": a})
    x, y = map(float, args.xy.split(","))
    print(json.dumps(evaluate(a, read_polygons(args.bekas), ls, x, y), indent=1))


def main(argv=None):
    p = argparse.ArgumentParser(prog="tambora", description="Simulasi rambatan api savana TN Tambora")
    p.add_argument("--config", help="file konfigurasi (bawaan config/tambora.toml)")
    sp = p.add_subparsers(dest="cmd", required=True)

    def common(q):
        q.add_argument("--lcp", required=True, help="landscape .lcp")
        g = q.add_mutually_exclusive_group(required=True)
        g.add_argument("--api", help="titik/poligon api (.shp atau .geojson, UTM 50S)")
        g.add_argument("--xy", help="titik api 'x,y' UTM 50S")
        g.add_argument("--latlon", help="titik api 'lintang,bujur' desimal, mis. -8.2585,118.1506")
        q.add_argument("--radius-km", type=float, help="potong Analysis Area persegi sekitar titik api")
        q.add_argument("--buffer", type=float, default=0, help="buffer titik api (m)")
        q.add_argument("--bbox", help="Analysis Area 'barat,selatan,timur,utara' (UTM)")
        q.add_argument("--res", type=float, help="resolusi hitung (m), mis. 30 untuk AOI penuh")
        q.add_argument("--fmd"), q.add_argument("--fms")
        q.add_argument("--angin", default="otomatis", choices=["otomatis", "medan", "windninja", "seragam"])
        q.add_argument("--mesh", type=float, default=100, help="resolusi mesh WindNinja (m)")
        q.add_argument("--interval", type=float, default=60, help="interval perimeter (menit)")
        q.add_argument("--validasi", help="poligon bekas terbakar untuk dibandingkan (.shp)")
        q.add_argument("--tajuk", default="finney", choices=["finney", "scott", "tanpa"],
                       help="metode api tajuk FlamMap: Finney (1998), Scott/Reinhardt (2001), atau tanpa api tajuk")
        q.add_argument("--web", action="store_true", help="tulis juga overlay PNG + web.json + hasil.zip untuk halaman peta")
        q.add_argument("--out", required=True)

    q = sp.add_parser("mtt", help="seperti MTT FlamMap: angin & kadar air tetap")
    common(q)
    q.add_argument("--kecepatan", type=float, required=True, help="angin 20-ft km/j")
    q.add_argument("--arah", type=float, required=True, help="arah ASAL angin (derajat)")
    q.add_argument("--menit", type=float, default=480)
    q.set_defaults(f=cmd_mtt)

    q = sp.add_parser("farsite", help="seperti FARSITE: cuaca & angin per jam dari .wxs / ERA5")
    common(q)
    q.add_argument("--wxs", help=".wxs (kosong = unduh otomatis dari Open-Meteo di titik api)")
    q.add_argument("--mulai", required=True, help="'2026-09-09 13:00' waktu lokal (sesuai .wxs)")
    q.add_argument("--jam", type=float, default=24)
    q.add_argument("--kondisi-mulai", help="awal conditioning kadar air (bawaan = awal .wxs)")
    q.add_argument("--burn", type=float, nargs=2, default=[0, 24], help="burn period jam_mulai jam_akhir")
    q.add_argument("--kadar-air-tetap", action="store_true", help="pakai .fms apa adanya")
    q.add_argument("--kecepatan", type=float, help="paksa kecepatan angin (km/j) untuk semua jam")
    q.add_argument("--arah", type=float, help="paksa arah asal angin untuk semua jam")
    q.set_defaults(f=cmd_farsite)

    q = sp.add_parser("wxs", help="buat .wxs dari ERA5/ECMWF (Open-Meteo)")
    q.add_argument("--lat", type=float, default=-8.2585), q.add_argument("--lon", type=float, default=118.1506)
    q.add_argument("--mulai", required=True), q.add_argument("--sampai")
    q.add_argument("--zona", default="WITA"), q.add_argument("--model", default="best_match")
    q.add_argument("--elevasi", type=float), q.add_argument("--tanpa-kondisi", action="store_true")
    q.add_argument("--out", required=True)
    q.set_defaults(f=cmd_wxs)

    q = sp.add_parser("landscape-dasar", help="buat .lcp dari GeoTIFF dasar")
    q.add_argument("--dasar", required=True), q.add_argument("--out", required=True)
    q.set_defaults(f=cmd_landscape_dasar)

    q = sp.add_parser("update-mingguan", aliases=["update-landscape"],
                     help="perbarui landscape dari Sentinel-2 (tiap ada citra baru) dan simpan ke Drive")
    q.add_argument("--tanggal"), q.add_argument("--kerja", default="kerja")
    q.add_argument("--lokal-dasar", help="folder GeoTIFF dasar lokal (lewati unduh dari Drive)")
    q.add_argument("--tanpa-drive", action="store_true"), q.add_argument("--status")
    q.add_argument("--tanpa-unggah", action="store_true", help="uji: jangan unggah hasil ke Drive")
    q.add_argument("--simpan-komposit", metavar="DIR", help="diagnostik: simpan median NBR/NDVI tiap jendela (30 m) ke DIR")
    q.add_argument("--mulai-ulang", action="store_true", help="abaikan status lama, hitung ulang sejak awal musim")
    q.set_defaults(f=cmd_update)

    q = sp.add_parser("cek-citra", help="ada citra Sentinel-2 baru sejak update landscape terakhir?")
    q.add_argument("--status", default="docs/status/landscape_terbaru.json")
    q.set_defaults(f=cmd_cek_citra)

    q = sp.add_parser("ambil-landscape", help="unduh landscape terbaru dari Drive")
    q.add_argument("--out", default="kerja/terbaru")
    q.set_defaults(f=cmd_ambil_landscape)

    q = sp.add_parser("ambil-dasar", help="unduh GeoTIFF dasar (dan bekas terbakar Sentinel-2) dari Drive")
    q.add_argument("--out", default="kerja/dasar")
    q.add_argument("--bekas", help="simpan juga bekas_terbakar_musim.tif ke path ini")
    q.set_defaults(f=cmd_ambil_dasar)

    q = sp.add_parser("unggah", help="unggah file/folder ke Drive")
    q.add_argument("file", nargs="+"), q.add_argument("--ke", required=True, help="folder tujuan di Drive, mis. dasar")
    q.set_defaults(f=cmd_unggah)

    q = sp.add_parser("areal-terbakar", help="rekap + GeoJSON areal terbakar dari SHP delineasi manual di Drive")
    q.add_argument("--status", default="docs/status/areal_terbakar.json")
    q.add_argument("--geojson", default="docs/data/areal_terbakar.geojson")
    q.add_argument("--kerja", default="kerja")
    q.add_argument("--lokal", help="uji: baca folder lokal, bukan Drive")
    q.add_argument("--paksa", action="store_true", help="olah ulang walau isi folder Drive tidak berubah")
    q.set_defaults(f=cmd_areal_terbakar)

    q = sp.add_parser("terbitkan-3d", help="simpan data3d.bin.gz ke cabang hasil-3d (tab 3D halaman simulasi)")
    q.add_argument("file"), q.add_argument("--id", required=True)
    q.set_defaults(f=cmd_terbitkan_3d)

    q = sp.add_parser("isi-3d", help="buat data 3D untuk simulasi lama di GitHub Release")
    q.add_argument("--lcp", default="kerja/terbaru/landscape_terbaru_10m.lcp", help="landscape untuk elevasi & fuel")
    q.add_argument("--id", help="satu simulasi saja (tanpa awalan sim-)")
    q.add_argument("--ulang", action="store_true", help="buat ulang walau sudah ada")
    q.add_argument("--kerja", default="kerja/isi3d")
    q.set_defaults(f=cmd_isi_3d)

    q = sp.add_parser("titik-panas", help="impor titik panas KMZ/KML SiPongi atau CSV FIRMS")
    q.add_argument("file", nargs="+")
    q.add_argument("--simpan", default="data/titik_panas/titik_panas.geojson")
    q.set_defaults(f=cmd_titik_panas)

    q = sp.add_parser("asal-api", help="pemodelan terbalik: titik dan jam api mulai tiap kejadian")
    q.add_argument("--titik", default="data/titik_panas/titik_panas.geojson", help="penyimpanan titik panas")
    q.add_argument("--dasar", default="kerja/dasar", help="folder GeoTIFF dasar (landscape awal musim)")
    q.add_argument("--lcp", help="atau landscape .lcp 10 m awal musim (tanpa bekas terbakar)")
    q.add_argument("--delineasi", default="docs/data/areal_terbakar.geojson")
    q.add_argument("--bekas-s2", help="bekas_terbakar_musim.tif (Sentinel-2) untuk areal di luar kawasan TN")
    q.add_argument("--status", default="docs/status/asal_api.json")
    q.add_argument("--keluaran", default="docs/data/asal_api")
    q.add_argument("--kerja", default="kerja/asal")
    q.add_argument("--proses", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    q.add_argument("--min-ha", type=float, default=1.0, help="luas areal terbakar minimal per kejadian")
    q.add_argument("--hanya", help="id kejadian dipisah koma (lainnya diambil dari hasil lama)")
    q.add_argument("--ulang", action="store_true", help="hitung ulang semua kejadian (abaikan cache)")
    q.add_argument("--tanpa-firms", action="store_true", help="jangan tambah titik panas dari FIRMS")
    q.add_argument("--firms-sejak", help="tanggal awal pengambilan FIRMS (bawaan: 3 hari sebelum titik terakhir)")
    q.add_argument("--cuaca-uji", action="store_true", help="uji tanpa jaringan: cuaca buatan")
    q.set_defaults(f=cmd_asal_api)

    q = sp.add_parser("drive-auth", help="sekali saja: dapatkan refresh token Google Drive")
    q.add_argument("--client-id", required=True), q.add_argument("--client-secret", required=True)
    q.set_defaults(f=cmd_drive_auth)

    q = sp.add_parser("validasi", help="Sørensen & arah terhadap bekas terbakar")
    q.add_argument("--waktu-tiba", required=True), q.add_argument("--bekas", required=True)
    q.add_argument("--xy", required=True)
    q.set_defaults(f=cmd_validasi)

    args = p.parse_args(argv)
    cfg = load_cfg(args.config)
    return args.f(args, cfg)


if __name__ == "__main__":
    main()
