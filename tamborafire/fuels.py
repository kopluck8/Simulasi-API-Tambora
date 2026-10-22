"""Fuel model (.fmd) dan kadar air (.fms).

Satuan internal = ENGLISH Rothermel: beban lb/ft2, SAV 1/ft, kedalaman ft, panas BTU/lb.
"""
from __future__ import annotations

from dataclasses import dataclass

TAC_TO_LBFT2 = 2000.0 / 43560.0          # ton/acre -> lb/ft2
THA_TO_TAC = 1.0 / 2.2417                # ton/ha -> ton/acre
NONBURNABLE = {91, 92, 93, 98, 99, -9999, 0}


@dataclass
class FuelModel:
    number: int
    code: str
    h1: float      # beban t/ac
    h10: float
    h100: float
    herb: float
    woody: float
    dynamic: bool
    sav1: float    # 1/ft
    savherb: float
    savwoody: float
    depth: float   # ft
    mx: float      # %
    heat_dead: float = 8000.0
    heat_live: float = 8000.0
    name: str = ""


# Scott & Burgan (2005) 40 model + Anderson (1982) 13 model.
# kolom: 1h 10h 100h herb woody dinamis sav1 savherb savwoody depth mx
_STD = """
1 FM1 0.74 0 0 0 0 0 3500 1500 1500 1.0 12
2 FM2 2.00 1.00 0.50 0.50 0 0 3000 1500 1500 1.0 15
3 FM3 3.01 0 0 0 0 0 1500 1500 1500 2.5 25
4 FM4 5.01 4.01 2.00 0 5.01 0 2000 1500 1500 6.0 20
5 FM5 1.00 0.50 0 0 2.00 0 2000 1500 1500 2.0 20
6 FM6 1.50 2.50 2.00 0 0 0 1750 1500 1500 2.5 25
7 FM7 1.13 1.87 1.50 0 0.37 0 1750 1500 1550 2.5 40
8 FM8 1.50 1.00 2.50 0 0 0 2000 1500 1500 0.2 30
9 FM9 2.92 0.41 0.15 0 0 0 2500 1500 1500 0.2 25
10 FM10 3.01 2.00 5.01 0 2.00 0 2000 1500 1500 1.0 25
11 FM11 1.50 4.51 5.51 0 0 0 1500 1500 1500 1.0 15
12 FM12 4.01 14.03 16.53 0 0 0 1500 1500 1500 2.3 20
13 FM13 7.01 23.04 28.05 0 0 0 1500 1500 1500 3.0 25
101 GR1 0.10 0 0 0.30 0 1 2200 2000 9999 0.4 15
102 GR2 0.10 0 0 1.00 0 1 2000 1800 9999 1.0 15
103 GR3 0.10 0.40 0 1.50 0 1 1500 1300 9999 2.0 30
104 GR4 0.25 0 0 1.90 0 1 2000 1800 9999 2.0 15
105 GR5 0.40 0 0 2.50 0 1 1800 1600 9999 1.5 40
106 GR6 0.10 0 0 3.40 0 1 2200 2000 9999 1.5 40
107 GR7 1.00 0 0 5.40 0 1 2000 1800 9999 3.0 15
108 GR8 0.50 1.00 0 7.30 0 1 1500 1300 9999 4.0 30
109 GR9 1.00 1.00 0 9.00 0 1 1800 1600 9999 5.0 40
121 GS1 0.20 0 0 0.50 0.65 1 2000 1800 1800 0.9 15
122 GS2 0.50 0.50 0 0.60 1.00 1 2000 1800 1800 1.5 15
123 GS3 0.30 0.25 0 1.45 1.25 1 1800 1600 1600 1.8 40
124 GS4 1.90 0.30 0.10 3.40 7.10 1 1800 1600 1600 2.1 40
141 SH1 0.25 0.25 0 0.15 1.30 1 2000 1800 1600 1.0 15
142 SH2 1.35 2.40 0.75 0 3.85 0 2000 9999 1600 1.0 15
143 SH3 0.45 3.00 0 0 6.20 0 1600 9999 1400 2.4 40
144 SH4 0.85 1.15 0.20 0 2.55 0 2000 1800 1600 3.0 30
145 SH5 3.60 2.10 0 0 2.90 0 750 9999 1600 6.0 15
146 SH6 2.90 1.45 0 0 1.40 0 750 9999 1600 2.0 30
147 SH7 3.50 5.30 2.20 0 3.40 0 750 9999 1600 6.0 15
148 SH8 2.05 3.40 0.85 0 4.35 0 750 9999 1600 3.0 40
149 SH9 4.50 2.45 0 1.55 7.00 1 750 1800 1500 4.4 40
161 TU1 0.20 0.90 1.50 0.20 0.90 1 2000 1800 1600 0.6 20
162 TU2 0.95 1.80 1.25 0 0.20 0 2000 9999 1600 1.0 30
163 TU3 1.10 0.15 0.25 0.65 1.10 1 1800 1600 1400 1.3 30
164 TU4 4.50 0 0 0 2.00 0 2300 9999 2000 0.5 12
165 TU5 4.00 4.00 3.00 0 3.00 0 1500 9999 750 1.0 25
181 TL1 1.00 2.20 3.60 0 0 0 2000 9999 9999 0.2 30
182 TL2 1.40 2.30 2.20 0 0 0 2000 9999 9999 0.2 25
183 TL3 0.50 2.20 2.80 0 0 0 2000 9999 9999 0.3 20
184 TL4 0.50 1.50 4.20 0 0 0 2000 9999 9999 0.4 25
185 TL5 1.15 2.50 4.40 0 0 0 2000 9999 1600 0.6 25
186 TL6 2.40 1.20 1.20 0 0 0 2000 9999 9999 0.3 25
187 TL7 0.30 1.40 8.10 0 0 0 2000 9999 9999 0.4 25
188 TL8 5.80 1.40 1.10 0 0 0 1800 9999 9999 0.3 35
189 TL9 6.65 3.30 4.15 0 0 0 1800 9999 1600 0.6 35
201 SB1 1.50 3.00 11.00 0 0 0 2000 9999 9999 1.0 25
202 SB2 4.50 4.25 4.00 0 0 0 2000 9999 9999 1.0 25
203 SB3 5.50 2.75 3.00 0 0 0 2000 9999 9999 1.2 25
204 SB4 5.25 3.50 5.25 0 0 0 2000 9999 9999 2.7 25
"""


def standard_models() -> dict[int, FuelModel]:
    out = {}
    for line in _STD.strip().splitlines():
        p = line.split()
        out[int(p[0])] = FuelModel(int(p[0]), p[1], *map(float, p[2:7]), p[7] == "1",
                                   *map(float, p[8:13]), name=p[1])
    return out


def read_fmd(path) -> dict[int, FuelModel]:
    """Baca .fmd FlamMap (ENGLISH atau METRIC). Baris pertama boleh 'ENGLISH'/'METRIC'."""
    metric = False
    out = {}
    for raw in open(path, encoding="latin-1"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        u = line.upper()
        if u in ("ENGLISH", "METRIC"):
            metric = u == "METRIC"
            continue
        p = line.split()
        if len(p) < 15:
            continue
        n = int(p[0])
        loads = [float(x) for x in p[2:7]]
        dyn = p[7].lower().startswith("d")
        sav = [float(x) for x in p[8:11]]
        depth, mx, hd, hl = float(p[11]), float(p[12]), float(p[13]), float(p[14])
        if metric:  # t/ha, 1/cm, cm, kJ/kg
            loads = [x * THA_TO_TAC for x in loads]
            sav = [x * 30.48 for x in sav]
            depth = depth / 30.48
            hd, hl = hd / 2.326, hl / 2.326
        out[n] = FuelModel(n, p[1], *loads, dyn, *sav, depth, mx, hd, hl, " ".join(p[15:]) or p[1])
    return out


def read_fms(path) -> dict[int, tuple]:
    """.fms: model 1h 10h 100h herb woody [1000h]. Model 0 = default semua model."""
    out = {}
    for raw in open(path, encoding="latin-1"):
        p = raw.split()
        if len(p) < 6:
            continue
        try:
            out[int(p[0])] = tuple(float(x) for x in p[1:6])
        except ValueError:
            continue
    return out


def fuel_table(fmd_path=None) -> dict[int, FuelModel]:
    t = standard_models()
    if fmd_path:
        t.update(read_fmd(fmd_path))
    return t
