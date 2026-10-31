"""Delineasi manual areal terbakar: SHP di Google Drive -> rekap luas per resort + GeoJSON untuk peta.

Folder Drive: <folder induk>/delineasi (cfg drive.delineasi). Satu delineasi per unggahan, dalam salah satu bentuk:
- .zip berisi .shp, .shx, .dbf, .prj (paling aman),
- .shp, .shx, .dbf, .prj lepas dengan nama sama,
- .geojson (WGS84).
Tanggal delineasi dibaca dari nama file (2026-10-30, 20261030, 30-10-2026, 30okt2026), lalu nama subfolder,
lalu tanggal unggah ke Drive.

Mode "gabung" (bawaan): semua delineasi sejak awal musim digabung (union). Luas kumulatif pada suatu tanggal =
union semua delineasi sampai tanggal itu; luas baru = selisihnya dengan tanggal sebelumnya. File boleh berisi
seluruh areal terbakar sampai tanggal itu atau hanya areal baru; hasilnya sama dan tidak terhitung dua kali.
Mode "terbaru": file per tanggal dianggap seluruh areal terbakar musim ini (koreksi yang memperkecil ikut terpakai).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import zipfile

import requests

UTM = 32750  # UTM 50S, sama dengan [aoi].epsg
BULAN = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "mei": 5, "may": 5, "jun": 6, "jul": 7, "agu": 8, "agt": 8,
         "ags": 8, "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "des": 12, "dec": 12}
SHP_PART = (".shp", ".shx", ".dbf", ".prj", ".cpg")


# ------------------------------------------------------------------ tanggal
def tanggal_dari_teks(teks):
    """Tanggal pertama yang masuk akal di nama file/folder, atau None."""
    s = teks.lower()
    pola = [
        (r"(?<!\d)(20\d\d)[-_. ]?(\d\d)[-_. ]?(\d\d)(?!\d)", lambda m: (m[1], m[2], m[3])),
        (r"(?<!\d)(\d{1,2})[-_. ](\d{1,2})[-_. ](20\d\d)(?!\d)", lambda m: (m[3], m[2], m[1])),
        (r"(?<!\d)(\d{1,2})[-_. ]?(" + "|".join(BULAN) + r")[a-z]*[-_. ]?(20\d\d)(?!\d)",
         lambda m: (m[3], BULAN[m[2]], m[1])),
        (r"(?<!\d)(\d\d)(\d\d)(20\d\d)(?!\d)", lambda m: (m[3], m[2], m[1])),
    ]
    for p, ambil in pola:
        for m in re.finditer(p, s):
            try:
                y, mo, d = ambil(m)
                return dt.date(int(y), int(mo), int(d))
            except ValueError:
                continue
    return None


def awal_musim(tgl, mmdd):
    mm, dd = map(int, mmdd.split("-"))
    return dt.date(tgl.year if (tgl.month, tgl.day) >= (mm, dd) else tgl.year - 1, mm, dd)


# ------------------------------------------------------------------ geometri
def _ke_utm(crs_src):
    from pyproj import Transformer
    t = Transformer.from_crs(crs_src, UTM, always_xy=True)
    return lambda g: _transform(t, g)


def _transform(t, g):
    import numpy as np
    import shapely
    return shapely.transform(g, lambda c: np.column_stack(t.transform(c[:, 0], c[:, 1])))


def _poligon(g):
    """Hanya bagian poligon yang valid, 2D."""
    import shapely
    from shapely.geometry import MultiPolygon, Polygon
    g = shapely.force_2d(g)
    if not g.is_valid:
        g = shapely.make_valid(g)
    if isinstance(g, (Polygon, MultiPolygon)):
        return g
    parts = [p for p in getattr(g, "geoms", []) if isinstance(p, (Polygon, MultiPolygon))]
    return shapely.unary_union(parts) if parts else None


def _ubah(crs):
    """Fungsi geometri -> UTM 50S dari CRS apa pun (WKT .prj, 'EPSG:4326', urn GeoJSON, kode EPSG)."""
    from pyproj import CRS
    c = CRS.from_user_input(crs)
    return (lambda g: g) if c.to_epsg() == UTM else _ke_utm(c)


def baca_file(path):
    """Poligon (UTM 50S) dari .shp/.geojson/.zip. Kembalikan (list geometri, list peringatan)."""
    from shapely.geometry import shape
    pesan, geoms = [], []
    low = path.lower()
    if low.endswith(".zip"):
        dest = path[:-4] + "_isi"
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest)
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith("/") or "__MACOSX" in n or os.path.basename(n).startswith("."):
                    continue
                if os.path.splitext(n)[1].lower() in SHP_PART + (".geojson", ".json"):
                    with z.open(n) as src, open(os.path.join(dest, os.path.basename(n)), "wb") as out:
                        shutil.copyfileobj(src, out)
        isi = sorted(os.path.join(dest, f) for f in os.listdir(dest))
        utama = [f for f in isi if f.lower().endswith((".shp", ".geojson", ".json"))]
        if not utama:
            return [], [f"{os.path.basename(path)}: tidak berisi .shp atau .geojson"]
        for f in utama:
            g, p = baca_file(f)
            geoms += g
            pesan += [f"{os.path.basename(path)}/{x}" for x in p]
        return geoms, pesan
    if low.endswith((".geojson", ".json")):
        d = json.load(open(path, encoding="utf-8"))
        feats = d["features"] if "features" in d else [d]
        raw = [shape(f["geometry"]) for f in feats if f.get("geometry")]
        crs = (d.get("crs") or {}).get("properties", {}).get("name")
        if crs is None:
            crs = UTM if raw and max(abs(c) for g in raw for c in g.bounds) > 360 else 4326
        ubah = _ubah(crs)
    else:
        import shapefile
        r = shapefile.Reader(path)
        raw = [shape(s.__geo_interface__) for s in r.shapes() if s.shapeType != shapefile.NULL]
        if r.shapeType not in (shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM):
            return [], [f"{os.path.basename(path)}: bukan poligon ({r.shapeTypeName}), dilewati"]
        prj = os.path.splitext(path)[0] + ".prj"
        if os.path.exists(prj):
            crs = open(prj, encoding="utf-8", errors="ignore").read()
        else:
            derajat = bool(raw) and max(abs(c) for g in raw for c in g.bounds) <= 360
            crs = 4326 if derajat else UTM
            pesan.append(f"{os.path.basename(path)}: tanpa .prj, dianggap {'WGS84' if derajat else 'UTM 50S'}")
        ubah = _ubah(crs)
    for g in raw:
        g = _poligon(ubah(g))
        if g is not None and not g.is_empty:
            geoms.append(g)
    if raw and not geoms:
        pesan.append(f"{os.path.basename(path)}: tidak ada poligon yang terbaca")
    return geoms, pesan


def batas(path):
    """Fitur GeoJSON WGS84 -> list (properties, geometri UTM)."""
    from shapely.geometry import shape
    d = json.load(open(path, encoding="utf-8"))
    ubah = _ke_utm(4326)
    return [(f["properties"], _poligon(ubah(shape(f["geometry"])))) for f in d["features"]]


# ------------------------------------------------------------------ sumber file
def kumpulan_lokal(folder):
    """Folder lokal (uji tanpa Drive): sama aturannya dengan folder Drive."""
    item = []
    for akar, _, files in os.walk(folder):
        for f in sorted(files):
            p = os.path.join(akar, f)
            mtime = dt.datetime.fromtimestamp(os.path.getmtime(p), dt.timezone.utc)
            item.append(dict(nama=f, path=p, subfolder=os.path.relpath(akar, folder) if akar != folder else "",
                             waktu=mtime.isoformat()))
    return _kelompokkan(item)


def _kelompokkan(item):
    """File -> daftar set delineasi {nama, path utama, tanggal, asal_tanggal}. Komponen SHP digabung per nama."""
    sets, shp = [], {}
    for f in item:
        base, ext = os.path.splitext(f["nama"])
        ext = ext.lower()
        if ext in (".zip", ".geojson", ".json"):
            sets.append(dict(f, utama=f["path"]))
        elif ext in SHP_PART:
            k = (f["subfolder"], base)
            shp.setdefault(k, []).append(f)
    for (_, base), fs in shp.items():
        utama = [f for f in fs if f["nama"].lower().endswith(".shp")]
        if utama:
            sets.append(dict(utama[0], utama=utama[0]["path"], komponen=[f["path"] for f in fs]))
    for s in sets:
        t = tanggal_dari_teks(os.path.splitext(s["nama"])[0])
        asal = "nama file"
        if t is None and s.get("subfolder"):
            t, asal = tanggal_dari_teks(s["subfolder"]), "nama folder"
        if t is None:
            w = dt.datetime.fromisoformat(s["waktu"].replace("Z", "+00:00")).astimezone(dt.timezone(dt.timedelta(hours=8)))
            t, asal = w.date(), "tanggal unggah"
        s.update(tanggal=t, asal_tanggal=asal)
    return sorted(sets, key=lambda s: (s["tanggal"], s["nama"]))


FOLDER = "application/vnd.google-apps.folder"


def _ls(drive, parent):
    from .drive import API
    out, page = [], None
    while True:
        p = dict(q=f"'{parent}' in parents and trashed = false", pageSize=1000,
                 fields="nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime,createdTime)",
                 supportsAllDrives="true", includeItemsFromAllDrives="true")
        if page:
            p["pageToken"] = page
        r = drive._check(requests.get(API + "/files", headers=drive._h(), params=p, timeout=60))
        d = r.json()
        out += d["files"]
        page = d.get("nextPageToken")
        if not page:
            return out


def daftar_drive(drive, folder_id, kedalaman=3, sub=""):
    """Semua file di folder (dan subfolder) beserta sidik untuk mendeteksi perubahan."""
    out = []
    for f in _ls(drive, folder_id):
        if f["mimeType"] == FOLDER:
            if kedalaman > 0:
                out += daftar_drive(drive, f["id"], kedalaman - 1, (sub + "/" if sub else "") + f["name"])
        else:
            out.append(dict(f, subfolder=sub))
    return out


def sidik(files):
    s = sorted(f"{f.get('subfolder', '')}/{f['name']}|{f.get('md5Checksum', '')}|{f.get('modifiedTime', '')}"
               for f in files if os.path.splitext(f["name"])[1].lower() in SHP_PART + (".zip", ".geojson", ".json"))
    return hashlib.sha1("\n".join(s).encode()).hexdigest()[:16]


def unduh_drive(drive, files, kerja):
    os.makedirs(kerja, exist_ok=True)
    item = []
    for f in files:
        ext = os.path.splitext(f["name"])[1].lower()
        if ext not in SHP_PART + (".zip", ".geojson", ".json"):
            continue
        sub = re.sub(r"[^\w.-]+", "_", f["subfolder"]) if f["subfolder"] else ""
        d = os.path.join(kerja, sub) if sub else kerja
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f["name"])
        drive.download(f["id"], p)
        item.append(dict(nama=f["name"], path=p, subfolder=f["subfolder"], waktu=f.get("createdTime") or f["modifiedTime"]))
    return _kelompokkan(item)


# ------------------------------------------------------------------ olah
def _ha(g):
    return 0.0 if g is None or g.is_empty else g.area / 10000.0


def _geojson(g, ke_wgs):
    from shapely.geometry import mapping

    def bulat(c):
        return [round(c[0], 6), round(c[1], 6)] if isinstance(c[0], float) else [bulat(x) for x in c]
    m = mapping(ke_wgs(g.simplify(2.0, preserve_topology=True)))
    m["coordinates"] = bulat(m["coordinates"]) if m["type"] == "Polygon" else [bulat(p) for p in m["coordinates"]]
    return m


def olah(sets, tn_path, resort_path, mmdd="06-01", mode="gabung", luas_min_ha=0.01):
    """Kembalikan (ringkasan, geojson)."""
    import shapely
    from pyproj import Transformer
    ke_wgs = lambda g, t=Transformer.from_crs(UTM, 4326, always_xy=True): _transform(t, g)
    tn_props, tn = batas(tn_path)[0]
    resort = batas(resort_path)
    peringatan = []
    hasil = dict(sumber="delineasi manual", mode=mode, jumlah_delineasi=0, tanggal=None, awal_musim=None,
                 terbakar_ha=0.0, luar_kawasan_ha=0.0, luas_kawasan_ha=round(_ha(tn), 1),
                 resort=[dict(nama=p["nama"], kode=p.get("kode"), luas_ha=round(_ha(g), 1), terbakar_ha=0.0) for p, g in resort],
                 riwayat=[], file=[], peringatan=peringatan)
    fitur = []
    if not sets:
        peringatan.append("Belum ada file delineasi di folder Drive.")
        return hasil, dict(type="FeatureCollection", features=fitur)
    musim = awal_musim(max(s["tanggal"] for s in sets), mmdd)
    hasil["awal_musim"] = musim.isoformat()
    per_tgl = {}
    for s in sets:
        if s["tanggal"] < musim:
            peringatan.append(f"{s['nama']} ({s['tanggal']}) sebelum awal musim {musim}, diabaikan.")
            continue
        geoms, p = baca_file(s["utama"])
        peringatan += p
        hasil["file"].append(dict(nama=s["nama"], subfolder=s.get("subfolder", ""), tanggal=s["tanggal"].isoformat(),
                                  asal_tanggal=s["asal_tanggal"], poligon=len(geoms),
                                  luas_ha=round(_ha(shapely.unary_union(geoms)), 1) if geoms else 0.0))
        if geoms:
            per_tgl.setdefault(s["tanggal"], []).extend(geoms)
    sebelum = None
    for tgl in sorted(per_tgl):
        g = shapely.unary_union(per_tgl[tgl])
        g = g.buffer(0)
        kini = g if (mode == "terbaru" or sebelum is None) else shapely.unary_union([sebelum, g])
        baru = kini if sebelum is None else kini.difference(sebelum)
        baru = _poligon(baru) if baru is not None and not baru.is_empty else None
        hasil["riwayat"].append(dict(tanggal=tgl.isoformat(), baru_ha=round(_ha(baru and baru.intersection(tn)), 1),
                                     kumulatif_ha=round(_ha(kini.intersection(tn)), 1),
                                     file=[f["nama"] for f in hasil["file"] if f["tanggal"] == tgl.isoformat()]))
        if baru is not None and not baru.is_empty:
            for p, rg in resort:
                bag = _poligon(baru.intersection(rg))
                if bag is not None and _ha(bag) >= luas_min_ha:
                    fitur.append(dict(type="Feature", geometry=_geojson(bag, ke_wgs),
                                      properties=dict(tanggal=tgl.isoformat(), resort=p["nama"], luas_ha=round(_ha(bag), 2))))
            luar = _poligon(baru.difference(tn))
            if luar is not None and _ha(luar) >= luas_min_ha:
                fitur.append(dict(type="Feature", geometry=_geojson(luar, ke_wgs),
                                  properties=dict(tanggal=tgl.isoformat(), resort=None, luar_kawasan=True, luas_ha=round(_ha(luar), 2))))
        sebelum = kini
    if sebelum is not None:
        hasil.update(jumlah_delineasi=len(per_tgl), tanggal=max(per_tgl).isoformat(),
                     terbakar_ha=round(_ha(sebelum.intersection(tn)), 1),
                     luar_kawasan_ha=round(_ha(sebelum.difference(tn)), 1))
        for r, (p, rg) in zip(hasil["resort"], resort):
            r["terbakar_ha"] = round(_ha(sebelum.intersection(rg)), 1)
    hasil["persen_kawasan"] = round(hasil["terbakar_ha"] / hasil["luas_kawasan_ha"] * 100, 2) if hasil["luas_kawasan_ha"] else None
    return hasil, dict(type="FeatureCollection", features=fitur)


def tulis(hasil, gj, status, geojson):
    for p in (status, geojson):
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(geojson, "w", encoding="utf-8") as f:
        json.dump(gj, f, ensure_ascii=False, separators=(",", ":"))
    with open(status, "w", encoding="utf-8") as f:
        json.dump(hasil, f, ensure_ascii=False, indent=1)
