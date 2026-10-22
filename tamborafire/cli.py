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
    summ = dict(h.info, luas_terbakar_ha=round(ha, 1), sel_m=ls.res,
                titik_api=[round(x0, 1), round(y0, 1)])
    if getattr(args, "validasi", None):
        from .validate import evaluate
        summ["validasi"] = evaluate(h.arrival, read_polygons(args.validasi), ls, x0, y0)
    json.dump(summ, open(os.path.join(out, "ringkasan.json"), "w"), indent=1, default=str)
    _png(h, ls, os.path.join(out, "peta_waktu_tiba.png"), x0, y0, getattr(args, "validasi", None))
    print(json.dumps(summ, indent=1, default=str))
    return summ


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
                    ros_adjust={int(k): float(v) for k, v in cfg["kalibrasi"].get("ros_adjust", {}).items()})
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
    else:
        lon, lat = Transformer.from_crs(cfg["aoi"]["epsg"], 4326, always_xy=True).transform(x0, y0)
        wx = WX.fetch(round(lat, 4), round(lon, 4), start.date(), end.date(), cfg["simulasi"]["zona"],
                      cfg["simulasi"]["model_cuaca"], conditioning_days=3)
        os.makedirs(args.out, exist_ok=True)
        WX.write_wxs(wx, os.path.join(args.out, f"cuaca_{lat:.4f}_{lon:.4f}.wxs"))
    sim = Simulator(ls, _rel(args.fmd or cfg["kalibrasi"]["fmd"]), _rel(args.fms or cfg["kalibrasi"]["fms"]),
                    ros_adjust={int(k): float(v) for k, v in cfg["kalibrasi"].get("ros_adjust", {}).items()})
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


def cmd_update(args, cfg):
    """Update mingguan: unduh dasar dari Drive, Sentinel-2 terbaru, tandai bekas terbakar, unggah hasil."""
    import rasterio
    from . import landscape as L
    from . import sentinel2 as S2
    from .lcp import write_lcp
    work = args.kerja
    os.makedirs(work, exist_ok=True)
    dasar = args.lokal_dasar or os.path.join(work, "dasar")
    drive = None
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
        prev = drive.find("bekas_terbakar_musim.tif", fid_now)
        if prev:
            drive.download(prev["id"], os.path.join(work, "bekas_terbakar_musim_lama.tif"))
    lay, tr, crs = L.load_base(dasar, cfg)
    nbr0, nbr_path, made = L.base_nbr(dasar, cfg)
    if made and drive:
        drive.upload(nbr_path, fid_dasar)
    tgl = dt.date.fromisoformat(args.tanggal) if args.tanggal else dt.date.today()
    k = cfg["mingguan"]
    items = S2.search(cfg, tgl, int(k["hari"]), k["stac"], k["awan_maks"])
    print(f"{len(items)} citra Sentinel-2 {tgl - dt.timedelta(days=int(k['hari']))} s.d. {tgl}")
    comp, used = S2.latest_clear_composite(cfg, items, k["stac"])
    # bekas terbakar musim ini (reset tiap awal musim)
    scar_prev = None
    p_prev = os.path.join(work, "bekas_terbakar_musim_lama.tif")
    mm, dd = map(int, k["awal_musim"].split("-"))
    season = dt.date(tgl.year if (tgl.month, tgl.day) >= (mm, dd) else tgl.year - 1, mm, dd)
    if os.path.exists(p_prev):
        with rasterio.open(p_prev) as d:
            if d.tags().get("awal_musim") == season.isoformat():
                scar_prev = d.read(1)
    fuel, scar, ndvi, dnbr, info = L.update(lay, nbr0, comp, cfg, scar_prev)
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
        d.update_tags(awal_musim=season.isoformat(), tanggal=stamp)
    with rasterio.open(os.path.join(out, "dnbr.tif"), "w", dtype="int16", nodata=-32768, **prof) as d:
        d.write(np.where(np.isfinite(dnbr), np.round(dnbr * 1000), -32768).astype(np.int16), 1)
    info.update(tanggal=stamp, citra=used, awal_musim=season.isoformat(), lcp=name + ".lcp")
    json.dump(info, open(os.path.join(out, "ringkasan.json"), "w"), indent=1)
    L.preview_png(fuel, os.path.join(out, "pratinjau_fuel.png"), f"Fuel model Tambora {stamp}")
    zp = os.path.join(out, name + ".zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in (name + ".lcp", name + ".prj"):
            z.write(os.path.join(out, f), f)
        z.write(_rel(cfg["kalibrasi"]["fmd"]), os.path.basename(cfg["kalibrasi"]["fmd"]))
        z.write(_rel(cfg["kalibrasi"]["fms"]), os.path.basename(cfg["kalibrasi"]["fms"]))
    print(json.dumps(info, indent=1))
    if drive:
        arsip = drive.path(f"arsip/{stamp}", fid_ls)
        for f in ("fuel_model.tif", "bekas_terbakar_musim.tif", "dnbr.tif", "ringkasan.json", "pratinjau_fuel.png"):
            drive.upload(os.path.join(out, f), arsip)
            drive.upload(os.path.join(out, f), fid_now)
        drive.upload(zp, fid_now, "landscape_terbaru_10m.zip")
        drive.upload(os.path.join(out, name + ".lcp"), fid_now, "landscape_terbaru_10m.lcp")
        drive.upload(os.path.join(out, name + ".prj"), fid_now, "landscape_terbaru_10m.prj")
        print("diunggah ke Google Drive:", cfg["drive"]["landscape"] + "/terbaru")
    if args.status:
        os.makedirs(os.path.dirname(args.status), exist_ok=True)
        json.dump(info, open(args.status, "w"), indent=1)


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

    q = sp.add_parser("update-mingguan", help="perbarui landscape dari Sentinel-2 dan simpan ke Drive")
    q.add_argument("--tanggal"), q.add_argument("--kerja", default="kerja")
    q.add_argument("--lokal-dasar", help="folder GeoTIFF dasar lokal (lewati unduh dari Drive)")
    q.add_argument("--tanpa-drive", action="store_true"), q.add_argument("--status")
    q.set_defaults(f=cmd_update)

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
