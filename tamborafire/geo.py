"""Masukan/keluaran spasial: titik api, GeoTIFF, perimeter."""
from __future__ import annotations

import json

import numpy as np


def read_ignition(path, ls, buffer_m=0.0):
    """Shapefile/GeoJSON titik, garis, atau poligon -> mask boolean di grid landscape."""
    from shapely.geometry import shape, Point
    geoms = []
    if str(path).lower().endswith((".geojson", ".json")):
        d = json.load(open(path))
        feats = d["features"] if "features" in d else [d]
        geoms = [shape(f["geometry"]) for f in feats]
    else:
        import shapefile
        r = shapefile.Reader(str(path))
        geoms = [shape(s.__geo_interface__) for s in r.shapes()]
    return rasterize(geoms, ls, buffer_m)


def ignition_from_xy(x, y, ls, buffer_m=0.0):
    from shapely.geometry import Point
    return rasterize([Point(x, y)], ls, buffer_m)


def rasterize(geoms, ls, buffer_m=0.0):
    from rasterio.features import rasterize as rz
    ny, nx = ls.shape
    out = np.zeros((ny, nx), bool)
    polys = []
    for g in geoms:
        if buffer_m > 0:
            g = g.buffer(buffer_m)
        if g.geom_type in ("Point", "MultiPoint"):
            pts = [g] if g.geom_type == "Point" else list(g.geoms)
            for p in pts:
                c = int((p.x - ls.west) / ls.res)
                r = int((ls.north - p.y) / ls.res)
                if 0 <= r < ny and 0 <= c < nx:
                    out[r, c] = True
        else:
            polys.append(g)
    if polys:
        m = rz([(g, 1) for g in polys], out_shape=(ny, nx), transform=ls.transform, all_touched=True)
        out |= m.astype(bool)
    return out


def write_tif(path, arr, ls, nodata=None, crs=None):
    import rasterio
    arr = np.asarray(arr)
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                       dtype=arr.dtype, crs=crs or ls.crs_wkt, transform=ls.transform, nodata=nodata,
                       compress="deflate") as d:
        d.write(arr, 1)


def read_polygons(path):
    import shapefile
    from shapely.geometry import shape
    from shapely.ops import unary_union
    r = shapefile.Reader(str(path))
    return unary_union([shape(s.__geo_interface__) for s in r.shapes()])


def perimeters(arrival, ls, times, start=None):
    """Poligon area terbakar untuk tiap waktu (menit). Kembalikan list (t, geometry)."""
    from rasterio.features import shapes
    from shapely.geometry import shape
    from shapely.ops import unary_union
    out = []
    for t in times:
        m = np.isfinite(arrival) & (arrival <= t)
        if not m.any():
            continue
        g = unary_union([shape(s) for s, v in shapes(m.astype(np.uint8), mask=m, transform=ls.transform) if v == 1])
        out.append((t, g))
    return out


def write_perimeters_shp(path, perims, crs_wkt=None, start=None):
    import datetime as dt
    import shapefile
    from shapely.geometry import mapping
    w = shapefile.Writer(str(path), shapeType=shapefile.POLYGON)
    w.field("menit", "N", 10, 1)
    w.field("waktu", "C", 20)
    w.field("luas_ha", "N", 12, 2)
    for t, g in perims:
        gi = mapping(g)
        polys = [gi["coordinates"]] if gi["type"] == "Polygon" else list(gi["coordinates"])
        parts = []
        for poly in polys:
            ext = list(poly[0])
            if _ccw(ext):
                ext = ext[::-1]
            parts.append(ext)
            for hole in poly[1:]:
                h = list(hole)
                if not _ccw(h):
                    h = h[::-1]
                parts.append(h)
        w.poly(parts)
        wkt = (start + dt.timedelta(minutes=float(t))).strftime("%Y-%m-%d %H:%M") if start else ""
        w.record(float(t), wkt, g.area / 1e4)
    w.close()
    if crs_wkt:
        open(str(path).rsplit(".", 1)[0] + ".prj", "w").write(crs_wkt)


def _ccw(ring):
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        s += (x2 - x1) * (y2 + y1)
    return s < 0
