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

    q = sp.add_parser("unggah", help="unggah file/folder ke Drive")
    q.add_argument("file", nargs="+"), q.add_argument("--ke", required=True, help="folder tujuan di Drive, mis. dasar")
    q.set_defaults(f=cmd_unggah)

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
