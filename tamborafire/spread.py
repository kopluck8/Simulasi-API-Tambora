"""Perambatan api di grid: waktu tempuh minimum (seperti MTT FlamMap) dengan ROS elips
per sel, dan versi waktu-berubah (seperti FARSITE) dengan cuaca/angin per jam.

Waktu tiba dihitung dengan Dijkstra pada stensil 32 arah. Dari sel u ke sel v pada azimut phi,
ROS = Rmax (1-e) / (1 - e cos(phi - arah_kepala)) (elips dengan titik api di fokus), dan waktu
tempuh = jarak x rata-rata (1/ROS) di titik-titik sepanjang garis u->v.
"""
from __future__ import annotations

import math

import numpy as np
from numba import njit

INF = np.float64(1e30)


def stencil(order=3):
    """Offset (dr, dc) dengan |dr|,|dc| <= order dan gcd = 1 (16 arah untuk 2, 32 untuk 3)."""
    out = []
    for dr in range(-order, order + 1):
        for dc in range(-order, order + 1):
            if (dr, dc) != (0, 0) and math.gcd(abs(dr), abs(dc)) == 1:
                out.append((dr, dc))
    return np.array(out, np.int64)


@njit(cache=True)
def _heap_push(hk, hv, n, key, val):
    i = n
    hk[i] = key
    hv[i] = val
    while i > 0:
        p = (i - 1) >> 1
        if hk[p] <= hk[i]:
            break
        hk[p], hk[i] = hk[i], hk[p]
        hv[p], hv[i] = hv[i], hv[p]
        i = p
    return n + 1


@njit(cache=True)
def _heap_pop(hk, hv, n):
    k, v = hk[0], hv[0]
    n -= 1
    hk[0] = hk[n]
    hv[0] = hv[n]
    i = 0
    while True:
        l = 2 * i + 1
        if l >= n:
            break
        r = l + 1
        c = l
        if r < n and hk[r] < hk[l]:
            c = r
        if hk[i] <= hk[c]:
            break
        hk[c], hk[i] = hk[i], hk[c]
        hv[c], hv[i] = hv[i], hv[c]
        i = c
    return k, v, n


@njit(cache=True)
def _slowness(ros, head, ecc, r, c, phi):
    R = ros[r, c]
    if R <= 0.0:
        return INF
    e = ecc[r, c]
    cosd = math.cos(phi - head[r, c])
    v = R * (1.0 - e) / (1.0 - e * cosd)
    if v <= 1e-9:
        return INF
    return 1.0 / v


@njit(cache=True)
def _edge_time(ros, head, ecc, r, c, dr, dc, res):
    """Menit untuk merambat dari pusat (r,c) ke (r+dr, c+dc)."""
    phi = math.atan2(dc, -dr)  # azimut: utara = baris berkurang
    n = max(abs(dr), abs(dc))
    s = 0.0
    for k in range(n + 1):
        t = k / n
        rr = int(round(r + dr * t))
        cc = int(round(c + dc * t))
        w = 0.5 if (k == 0 or k == n) else 1.0
        sl = _slowness(ros, head, ecc, rr, cc, phi)
        if sl >= INF:
            return INF
        s += w * sl
    dist = res * math.sqrt(dr * dr + dc * dc)
    return dist * s / n


@njit(cache=True)
def _run(arr, hk, hv, nheap, settled, ros, head, ecc, ros2, head2, ecc2,
         t_end, t_end2, offs, res, t_max):
    """Proses Dijkstra sampai kunci teratas > t_end. Tepi yang melewati t_end memakai
    laju periode berikutnya (ros2...) untuk sisa jaraknya. Mengembalikan ukuran heap."""
    ny, nx = arr.shape
    while nheap > 0:
        if hk[0] > t_end or hk[0] > t_max:
            break
        t, v, nheap = _heap_pop(hk, hv, nheap)
        r = v // nx
        c = v % nx
        if settled[r, c] or t > arr[r, c]:
            continue
        settled[r, c] = True
        for i in range(offs.shape[0]):
            dr = offs[i, 0]
            dc = offs[i, 1]
            r2 = r + dr
            c2 = c + dc
            if r2 < 0 or r2 >= ny or c2 < 0 or c2 >= nx or settled[r2, c2]:
                continue
            dt = _edge_time(ros, head, ecc, r, c, dr, dc, res)
            if t + dt > t_end:
                # sebagian jarak dengan laju sekarang, sisanya dengan laju periode berikutnya
                frac = 0.0 if dt >= INF else (t_end - t) / dt
                dt2 = _edge_time(ros2, head2, ecc2, r, c, dr, dc, res)
                if dt2 >= INF:
                    continue
                t2 = t_end + (1.0 - frac) * dt2
            else:
                t2 = t + dt
            if t2 < arr[r2, c2] and t2 <= t_max:
                arr[r2, c2] = t2
                if nheap >= hk.shape[0]:
                    continue
                nheap = _heap_push(hk, hv, nheap, t2, r2 * nx + c2)
    return nheap


class SpreadState:
    """Status Dijkstra yang bisa dilanjutkan per periode cuaca."""

    def __init__(self, shape, ignition_mask, res, order=3, t0=0.0, heap_factor=8):
        self.arr = np.full(shape, INF, np.float64)
        self.settled = np.zeros(shape, np.bool_)
        n = int(shape[0] * shape[1] * heap_factor) + 16
        n = min(n, 400_000_000)
        self.hk = np.empty(n, np.float64)
        self.hv = np.empty(n, np.int64)
        self.nheap = 0
        self.res = float(res)
        self.offs = stencil(order)
        idx = np.flatnonzero(ignition_mask)
        if idx.size == 0:
            raise ValueError("Titik/poligon api tidak berada di dalam landscape.")
        nx = shape[1]
        for v in idx:
            self.arr.flat[v] = t0
            self.nheap = _heap_push(self.hk, self.hv, self.nheap, t0, int(v))

    def advance(self, grids, t_end, grids_next=None, t_end_next=None, t_max=1e29):
        ros, head, ecc = grids
        if grids_next is None:
            grids_next, t_end_next = grids, 1e29
        h = np.radians(head).astype(np.float64)
        h2 = np.radians(grids_next[1]).astype(np.float64)
        self.nheap = _run(self.arr, self.hk, self.hv, self.nheap, self.settled,
                          ros.astype(np.float64), h, ecc.astype(np.float64),
                          grids_next[0].astype(np.float64), h2, grids_next[2].astype(np.float64),
                          float(t_end), float(t_end_next), self.offs, self.res, float(t_max))

    def arrival(self):
        a = self.arr.astype(np.float32)
        a[(self.arr >= INF * 0.5) | ~self.settled] = np.nan
        return a


def min_travel_time(ros, head, ecc, ignition_mask, res, t_max=1e29, order=3):
    """MTT: kondisi tetap. Mengembalikan waktu tiba (menit, NaN = tidak terbakar)."""
    st = SpreadState(ros.shape, ignition_mask, res, order)
    st.advance((ros, head, ecc), 1e29, t_max=t_max)
    return st.arrival()
