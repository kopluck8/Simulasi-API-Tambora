/* Tab 3D: rambatan api di atas medan 3D (three.js), dengan alat waktu dan alat 3D.
   Data per simulasi: cabang hasil-3d, sim/<id>/data3d.bin.gz (dibuat tamborafire/tiga_d.py). */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';

const T = window.TAMBORA;
const C = window.SIM_CONFIG || {};
const URL_DATA = C.data3d || ('https://raw.githubusercontent.com/' + T.repo + '/hasil-3d/sim/');
const $ = (id) => document.getElementById(id);
const angka = T.angka, esc = T.esc;
const LANGIT = 0x0d1210;
const HP = window.matchMedia('(max-width: 980px)').matches;

/* ---------- palet ---------- */
const RAMP = {
  tiba: ['#2d0f4f', '#6a1b7a', '#b7317a', '#e8624a', '#f9a03f', '#fce46a'],
  api: ['#fff3b0', '#fdd35c', '#f9a03f', '#ee6a2d', '#d7301f', '#9e0a1c']
};
const WARNA_TIPE = { 1: '#f6c343', 2: '#ec6a2c', 3: '#b5121b' };
const NAMA_TIPE = { 0: 'tidak terbakar', 1: 'api permukaan', 2: 'api tajuk pasif', 3: 'api tajuk aktif' };
const HIPSO = [[0, '#29443a'], [0.22, '#3f5f43'], [0.42, '#6f7a4c'], [0.6, '#9a8a62'], [0.78, '#ad9f8c'], [0.92, '#cfc8bd'], [1, '#efebe4']];
const LAPIS = {
  tiba: { judul: 'Waktu tiba api', satuan: 'pukul', ramp: 'tiba' },
  ros: { judul: 'Laju api (ROS)', satuan: 'm/menit', ramp: 'api' },
  nyala: { judul: 'Panjang nyala', satuan: 'm', ramp: 'api' },
  fli: { judul: 'Intensitas garis api', satuan: 'kW/m', ramp: 'api', log: true },
  tipe: { judul: 'Tipe api', kategori: true }
};
const MATA = ['U', 'TL', 'T', 'TG', 'S', 'BD', 'B', 'BL'];
const mata = (d) => MATA[Math.round((((d % 360) + 360) % 360) / 45) % 8];
const HARI = ['Min', 'Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab'];
const BULAN = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
const p2 = (n) => String(n).padStart(2, '0');

function hexRgb(h) { const v = parseInt(h.slice(1), 16); return [(v >> 16) & 255, (v >> 8) & 255, v & 255]; }
function rampRgba(stops, n) {
  const out = new Uint8Array(n * 4), c = stops.map(hexRgb);
  for (let i = 0; i < n; i++) {
    const x = i / (n - 1) * (c.length - 1), k = Math.min(c.length - 2, Math.floor(x)), f = x - k;
    for (let j = 0; j < 3; j++) out[i * 4 + j] = Math.round(c[k][j] + (c[k + 1][j] - c[k][j]) * f);
    out[i * 4 + 3] = 255;
  }
  return out;
}
function teksturRamp(data, w, nearest) {
  const t = new THREE.DataTexture(data, w, 1, THREE.RGBAFormat);
  t.colorSpace = THREE.SRGBColorSpace;
  t.minFilter = t.magFilter = nearest ? THREE.NearestFilter : THREE.LinearFilter;
  t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
  t.needsUpdate = true;
  return t;
}
const TEX_RAMP = {};
function ramp(nama) {
  if (TEX_RAMP[nama]) return TEX_RAMP[nama];
  if (nama === 'tipe') {
    const d = new Uint8Array(16);
    [1, 2, 3].forEach((k) => { const c = hexRgb(WARNA_TIPE[k]); d.set([c[0], c[1], c[2], 255], k * 4); });
    return (TEX_RAMP[nama] = teksturRamp(d, 4, true));
  }
  return (TEX_RAMP[nama] = teksturRamp(rampRgba(RAMP[nama], 256), 256, false));
}
function gradienCss(stops) { return 'linear-gradient(90deg,' + stops.join(',') + ')'; }

/* ---------- shader medan ---------- */
const U = {
  uArr: { value: null }, uVal: { value: null }, uRamp: { value: null }, uBase: { value: null }, uSat: { value: null },
  uSatM: { value: new THREE.Matrix3() }, uDasar: { value: 0 }, uGrid: { value: new THREE.Vector2(1, 1) },
  uT: { value: 0 }, uVmin: { value: 0 }, uVmax: { value: 1 }, uVlog: { value: 0 }, uVcat: { value: 0 }, uVtiba: { value: 1 },
  uFront: { value: 45 }, uGlow: { value: 1 }, uIso: { value: 1 }, uIsoOff: { value: 0 }, uKontur: { value: 25 },
  uGridOn: { value: 0 }, uUtm: { value: new THREE.Vector2() }, uSel: { value: 10 }, uWaktu: { value: 0 },
  uOpas: { value: 0.85 }, uBayang: { value: 0 }
};
const FRAG_KEPALA = `
uniform sampler2D uArr, uVal, uRamp, uBase, uSat;
uniform mat3 uSatM;
uniform vec2 uGrid, uUtm;
uniform float uDasar, uT, uVmin, uVmax, uVlog, uVcat, uVtiba, uFront, uGlow, uIso, uIsoOff, uKontur, uGridOn, uSel, uWaktu, uOpas, uBayang;
varying vec2 vG;
varying float vEl;
vec3 gApi = vec3(0.0);
float tibaDi(ivec2 p) { p = clamp(p, ivec2(0), ivec2(uGrid) - 1); return texelFetch(uArr, p, 0).r; }
float garis(float x, float w) { float f = max(fwidth(x), 1e-5); return 1.0 - smoothstep(0.0, f * w, abs(fract(x - 0.5) - 0.5)); }
float acak(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
vec3 warnaMedan() {
  vec2 g = vG * uGrid;
  vec3 c = uDasar > 0.5 ? texture2D(uSat, (uSatM * vec3(vG, 1.0)).xy).rgb : texture2D(uBase, vG).rgb;
  if (uKontur > 0.0) {
    float l = garis(vEl / uKontur, 1.0), m = garis(vEl / (uKontur * 5.0), 1.7);
    c = mix(c, c * 0.42, max(l * 0.5, m * 0.85));
  }
  if (uGridOn > 0.5) {
    vec2 u = vec2(uUtm.x + g.x * uSel, uUtm.y - g.y * uSel) / 1000.0;
    c = mix(c, vec3(0.72, 0.93, 1.0), max(garis(u.x, 1.2), garis(u.y, 1.2)) * 0.5);
  }
  vec2 q = g - 0.5;
  ivec2 p0 = ivec2(floor(q));
  vec2 f = fract(q);
  float a00 = tibaDi(p0), a10 = tibaDi(p0 + ivec2(1, 0)), a01 = tibaDi(p0 + ivec2(0, 1)), a11 = tibaDi(p0 + ivec2(1, 1));
  float w00 = (1.0 - f.x) * (1.0 - f.y), w10 = f.x * (1.0 - f.y), w01 = (1.0 - f.x) * f.y, w11 = f.x * f.y;
  float b00 = step(a00, uT), b10 = step(a10, uT), b01 = step(a01, uT), b11 = step(a11, uT);
  float bf = w00 * b00 + w10 * b10 + w01 * b01 + w11 * b11;
  float aa = max(fwidth(bf), 0.02);
  float terbakar = smoothstep(0.5 - aa, 0.5 + aa, bf);
  if (uBayang > 0.5) {
    float e = w00 * step(a00, 1e6) + w10 * step(a10, 1e6) + w01 * step(a01, 1e6) + w11 * step(a11, 1e6);
    c = mix(c, vec3(1.0, 0.55, 0.22), smoothstep(0.4, 0.6, e) * (1.0 - terbakar) * 0.22);
  }
  if (terbakar > 0.0) {
    // sel terbakar dengan bobot terbesar memberi nilai lapisan
    ivec2 pp = p0; float bw = w00 * b00;
    if (w10 * b10 > bw) { bw = w10 * b10; pp = p0 + ivec2(1, 0); }
    if (w01 * b01 > bw) { bw = w01 * b01; pp = p0 + ivec2(0, 1); }
    if (w11 * b11 > bw) { bw = w11 * b11; pp = p0 + ivec2(1, 1); }
    pp = clamp(pp, ivec2(0), ivec2(uGrid) - 1);
    float ap = texelFetch(uArr, pp, 0).r;
    bool semua = b00 * b10 * b01 * b11 > 0.5;
    float ai = semua ? (w00 * a00 + w10 * a10 + w01 * a01 + w11 * a11) : ap;
    float v = uVtiba > 0.5 ? ai : texelFetch(uVal, pp, 0).r;
    float x = uVcat > 0.5 ? (v + 0.5) / 4.0
      : (uVlog > 0.5 ? log(1.0 + max(v - uVmin, 0.0)) / log(1.0 + uVmax - uVmin) : (v - uVmin) / max(uVmax - uVmin, 1e-6));
    vec4 r = texture2D(uRamp, vec2(clamp(x, 0.002, 0.998), 0.5));
    c = mix(c, r.rgb, terbakar * uOpas * r.a);
    if (uIso > 0.0 && semua) {
      float l = garis((ai + uIsoOff) / (60.0 * uIso), 1.3);
      c = mix(c, vec3(0.97), l * terbakar * 0.9);
    }
    float umur = uT - ap;
    if (uGlow > 0.0 && umur < uFront) {
      float k = 1.0 - smoothstep(0.0, uFront, umur);
      float kedip = 0.72 + 0.28 * sin(uWaktu * 7.0 + acak(floor(g * 0.5)) * 6.283);
      vec3 panas = mix(vec3(1.0, 0.2, 0.02), vec3(1.0, 0.78, 0.32), k);
      gApi = panas * k * kedip * uGlow * 2.6 * terbakar;
      c = mix(c, panas, 0.55 * k * terbakar);
    }
  }
  return c;
}
`;
function bahanMedan() {
  const m = new THREE.MeshStandardMaterial({ roughness: 0.93, metalness: 0 });
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, U);
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nattribute float aElev;\nvarying vec2 vG;\nvarying float vEl;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvG = uv;\nvEl = aElev;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\n' + FRAG_KEPALA)
      .replace('vec4 diffuseColor = vec4( diffuse, opacity );', 'vec4 diffuseColor = vec4( warnaMedan(), opacity );')
      .replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\ntotalEmissiveRadiance += gApi;');
  };
  return m;
}

/* ---------- keadaan ---------- */
let siap = false, gagalGL = false;
let renderer, scene, camera, controls, composer, bloomPass, labelR, matahari;
let grup = null, medan = null, grupVektor = null, grupLabel = null, garisKepala = null, labelKepala = [];
let penandaInfo = null, garisUkur = null, labelUkur = [];
const bahanGaris = [];
let D = null;
const S = { t: 0, main: false, ulang: true, hudTerakhir: 0, idAktif: null, mintaId: null, ukur: false, titikUkur: [], muatNo: 0, waktuAkhir: 0 };

/* ---------- membaca data ---------- */
async function unduh(id) {
  const r = await fetch(URL_DATA + encodeURIComponent(id) + '/data3d.bin.gz', { cache: 'no-cache' });
  if (r.status === 404) throw Object.assign(new Error('belum ada'), { kode: 404 });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const total = +r.headers.get('content-length') || 0;
  let buf;
  if (r.body && total) {
    const rd = r.body.getReader(), bag = []; let n = 0;
    for (;;) {
      const { done, value } = await rd.read();
      if (done) break;
      bag.push(value); n += value.length;
      pesan('Memuat data 3D… ' + Math.round(100 * n / total) + '% (' + angka(total / 1e6, 1) + ' MB)');
    }
    buf = new Uint8Array(n); let o = 0;
    bag.forEach((b) => { buf.set(b, o); o += b.length; });
  } else buf = new Uint8Array(await r.arrayBuffer());
  if (buf[0] === 0x1f && buf[1] === 0x8b) {
    if (!('DecompressionStream' in window)) throw new Error('Browser ini belum mendukung DecompressionStream. Perbarui browser.');
    buf = new Uint8Array(await new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer());
  }
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}
function urai(buf) {
  const sig = String.fromCharCode.apply(null, new Uint8Array(buf, 0, 4));
  if (sig !== 'TF3D') throw new Error('Format data 3D tidak dikenal.');
  const n = new DataView(buf).getUint32(4, true);
  const h = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, n)));
  const base = 8 + n, N = h.kolom * h.baris, L = {};
  h.larik.forEach((l) => { L[l.nama] = { l, raw: l.tipe === 'u16' ? new Uint16Array(buf, base + l.byte, N) : new Uint8Array(buf, base + l.byte, N) }; });
  return { h, L };
}
function nilai(x, kosong) {
  const { l, raw } = x, out = new Float32Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = (l.kosong !== null && raw[i] === l.kosong) ? kosong : raw[i] * l.skala + l.offset;
  return out;
}
function persentil(arr, idx, p) {
  if (!idx.length) return 0;
  const step = Math.max(1, Math.floor(idx.length / 20000)), s = [];
  for (let j = 0; j < idx.length; j += step) s.push(arr[idx[j]]);
  s.sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(p * (s.length - 1)))];
}
function waktuMs(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(s || ''));
  return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) : null;
}
function olah(h, L, rel) {
  const nx = h.kolom, ny = h.baris, N = nx * ny, sel = h.sel_m;
  const elev = nilai(L.elev, 0), tiba = nilai(L.tiba, 1e7), ros = nilai(L.ros, 0), nyala = nilai(L.nyala, 0), fli = nilai(L.fli, 0);
  const tipe = L.tipe.raw, fuel = L.fuel.raw;
  let nb = 0;
  for (let i = 0; i < N; i++) if (tiba[i] < 1e6) nb++;
  const K = 2097152, kunci = new Float64Array(nb);
  for (let i = 0, k = 0; i < N; i++) if (tiba[i] < 1e6) kunci[k++] = Math.round(tiba[i] * 10) * K + i;
  kunci.sort();
  const urut = new Uint32Array(nb), tUrut = new Float32Array(nb), dMaks = new Float32Array(nb), dArg = new Uint32Array(nb);
  const kum = [new Uint32Array(nb + 1), new Uint32Array(nb + 1), new Uint32Array(nb + 1), new Uint32Array(nb + 1)];
  const ic = h.titik_api[0], ir = h.titik_api[1];
  let best = -1, arg = 0;
  for (let j = 0; j < nb; j++) {
    const key = kunci[j], i = key % K;
    urut[j] = i; tUrut[j] = Math.floor(key / K) / 10;
    const c = i % nx, r = (i / nx) | 0, d = Math.hypot(c + 0.5 - ic, r + 0.5 - ir) * sel;
    if (d > best) { best = d; arg = i; }
    dMaks[j] = best; dArg[j] = arg;
    for (let q = 0; q < 4; q++) kum[q][j + 1] = kum[q][j];
    kum[Math.min(3, tipe[i])][j + 1]++;
  }
  let emin = Infinity, emax = -Infinity, imax = 0;
  for (let i = 0; i < N; i++) { if (elev[i] < emin) emin = elev[i]; if (elev[i] > emax) { emax = elev[i]; imax = i; } }
  const d = T.dataDari(rel) || {}, mas = d.masukan || h.masukan || {}, sim = h.simulasi || {};
  const mode = String(mas.mode || sim.mode || d.mode || '').toLowerCase();
  let lama = parseFloat(mas.lama);
  const t0s = mode === 'farsite' ? (sim.start || mas.mulai) : mas.mulai;
  if (!(lama > 0) && sim.start && sim.end) lama = (waktuMs(sim.end) - waktuMs(sim.start)) / 3600e3;
  if (!(lama > 0) && sim.minutes) lama = sim.minutes / 60;
  const dur = Math.max(60, Math.ceil(Math.max((lama || 0) * 60, h.menit_maks || 0)));
  const t0 = waktuMs(t0s);
  const jam = (h.jam || []).map((j) => Object.assign({ ms: waktuMs(j.t) }, j)).filter((j) => j.ms !== null);
  return {
    h, rel, d, mas, mode, nx, ny, N, sel, elev, tiba, ros, nyala, fli, tipe, fuel, nb, urut, tUrut, dMaks, dArg, kum,
    elevMin: emin, elevMaks: emax, puncak: imax, dur, t0, jamAsumsi: mode !== 'farsite', jam,
    batas: { ros: Math.max(1, persentil(ros, urut, 0.98)), nyala: Math.max(0.5, persentil(nyala, urut, 0.98)), fli: Math.max(50, persentil(fli, urut, 0.98)) },
    W: nx * sel, H: ny * sel, offJam: t0 !== null ? new Date(t0).getUTCMinutes() : 0,
    // luas per sel (ha), dikoreksi ke luas asli bila grid 3D diperkecil
    cel: nb && h.luas_terbakar_ha ? h.luas_terbakar_ha / nb : sel * sel / 1e4
  };
}
function ub(t) { // banyak sel dengan waktu tiba <= t
  const a = D.tUrut; let lo = 0, hi = a.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= t) lo = m + 1; else hi = m; }
  return lo;
}

/* ---------- waktu & format ---------- */
function jamPada(m) { if (D.t0 === null) return null; const x = new Date(D.t0 + m * 60000); return p2(x.getUTCHours()) + ':' + p2(x.getUTCMinutes()); }
function tglPada(m) { if (D.t0 === null) return ''; const x = new Date(D.t0 + m * 60000); return HARI[x.getUTCDay()] + ', ' + x.getUTCDate() + ' ' + BULAN[x.getUTCMonth()] + ' ' + x.getUTCFullYear(); }
function lamaTeks(m) { m = Math.max(0, Math.round(m)); const j = Math.floor(m / 60), n = m % 60; return (j ? j + ' j ' : '') + p2(n) + ' mnt'; }
function waktuTeks(m, panjang) {
  const j = jamPada(m);
  if (!j) return 'T+' + lamaTeks(m);
  return j + ' WITA' + (panjang ? ', ' + tglPada(m) : '');
}
function cuacaPada(m) {
  if (D.jam.length && D.t0 !== null) {
    const ms = D.t0 + m * 60000; let r = D.jam[0];
    for (const j of D.jam) { if (j.ms <= ms) r = j; else break; }
    return r;
  }
  const s = D.h.simulasi || {};
  if (s.ws !== undefined) return { ws: s.ws, wd: s.wd };
  const a = String(D.mas.angin || '').split(',');
  return a.length === 2 && a[0] !== '' ? { ws: +a[0], wd: +a[1] } : null;
}
function lonlat(gx, gy) { const a = D.h.piksel_ke_lonlat; return [a[0] * gx + a[1] * gy + a[2], a[3] * gx + a[4] * gy + a[5]]; }
function kePiksel(lon, lat) { const a = D.h.lonlat_ke_piksel; return [a[0] * lon + a[1] * lat + a[2], a[3] * lon + a[4] * lat + a[5]]; }
function elevDi(gx, gy) { // gx, gy: koordinat pusat sel (0 = pusat sel pertama)
  const nx = D.nx, ny = D.ny, x = Math.max(0, Math.min(nx - 1, gx)), y = Math.max(0, Math.min(ny - 1, gy));
  const x0 = Math.floor(x), y0 = Math.floor(y), x1 = Math.min(nx - 1, x0 + 1), y1 = Math.min(ny - 1, y0 + 1), fx = x - x0, fy = y - y0, e = D.elev;
  return (e[y0 * nx + x0] * (1 - fx) + e[y0 * nx + x1] * fx) * (1 - fy) + (e[y1 * nx + x0] * (1 - fx) + e[y1 * nx + x1] * fx) * fy;
}
function keDunia(gx, gy, angkat) { // gx, gy: koordinat tepi (0..nx)
  return new THREE.Vector3((gx / D.nx - 0.5) * D.W, elevDi(gx - 0.5, gy - 0.5) - D.elevMin + (angkat || 0), (gy / D.ny - 0.5) * D.H);
}

/* ---------- three.js ---------- */
function pesan(t, kelas) {
  const e = $('t3Pesan');
  e.hidden = !t; e.className = 't3-pesan' + (kelas ? ' ' + kelas : '');
  e.innerHTML = t ? '<div>' + t + '</div>' : '';
}
function siapkan() {
  if (siap || gagalGL) return siap;
  if (HP) { $('t3Detail').value = '256'; }
  const wadah = $('t3Kanvas');
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  } catch (e) {
    gagalGL = true;
    pesan('Browser atau perangkat ini tidak mendukung WebGL 2, jadi tampilan 3D tidak bisa dibuka. Coba Chrome, Edge, Firefox, atau Safari terbaru.', 'err');
    return false;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(LANGIT, 1);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  wadah.appendChild(renderer.domElement);
  scene = new THREE.Scene();
  scene.fog = new THREE.Fog(LANGIT, 1e5, 2e5);
  camera = new THREE.PerspectiveCamera(42, 1, 10, 2e5);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.maxPolarAngle = 1.42; controls.screenSpacePanning = false; controls.autoRotateSpeed = 0.6;
  scene.add(new THREE.HemisphereLight(0xdfe9ff, 0x3a3328, 0.9));
  matahari = new THREE.DirectionalLight(0xfff1dc, 2.6);
  matahari.castShadow = true;
  matahari.shadow.mapSize.set(HP ? 1024 : 2048, HP ? 1024 : 2048);
  matahari.shadow.bias = -0.0004;
  scene.add(matahari, matahari.target);
  composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  bloomPass = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.75, 0.5, 0.92);
  composer.addPass(bloomPass);
  composer.addPass(new OutputPass());
  labelR = new CSS2DRenderer();
  labelR.domElement.className = 't3-label-lapis';
  wadah.appendChild(labelR.domElement);
  new ResizeObserver(ukurUlang).observe(wadah);
  ukurUlang();
  pasangKendali();
  siap = true;
  requestAnimationFrame(putaran);
  return true;
}
function ukurUlang() {
  const w = $('t3Kanvas').clientWidth || 600, h = $('t3Kanvas').clientHeight || 400;
  renderer.setSize(w, h); composer.setSize(w, h); labelR.setSize(w, h);
  bloomPass.resolution.set(w / 2, h / 2);
  camera.aspect = w / h; camera.updateProjectionMatrix();
  bahanGaris.forEach((m) => m.resolution.set(w, h));
}
function kosongkan(o) {
  if (!o) return;
  o.traverse((x) => {
    if (x.geometry) x.geometry.dispose();
    if (x.material && !bahanGaris.includes(x.material) && x.material.dispose) x.material.dispose();
    if (x.isCSS2DObject && x.element.parentNode) x.element.remove();
  });
  if (o.parent) o.parent.remove(o);
}

function teksturData(arr, w, h) {
  const t = new THREE.DataTexture(arr, w, h, THREE.RedFormat, THREE.FloatType);
  t.minFilter = t.magFilter = THREE.NearestFilter;
  t.needsUpdate = true;
  return t;
}
function warnaDasar(jenis) {
  const n = D.N, out = new Uint8Array(n * 4);
  if (jenis === 'fuel') {
    const cache = {};
    for (let i = 0; i < n; i++) {
      const k = D.fuel[i], c = cache[k] || (cache[k] = hexRgb((T.WARNA_FUEL || {})[k] || '#8a8a8a'));
      out[i * 4] = c[0]; out[i * 4 + 1] = c[1]; out[i * 4 + 2] = c[2]; out[i * 4 + 3] = 255;
    }
  } else {
    const rg = Math.max(1, D.elevMaks - D.elevMin);
    const stops = HIPSO.map((s) => [s[0], hexRgb(s[1])]);
    for (let i = 0; i < n; i++) {
      const x = (D.elev[i] - D.elevMin) / rg; let k = 0;
      while (k < stops.length - 2 && x > stops[k + 1][0]) k++;
      const f = Math.max(0, Math.min(1, (x - stops[k][0]) / (stops[k + 1][0] - stops[k][0])));
      for (let j = 0; j < 3; j++) out[i * 4 + j] = Math.round(stops[k][1][j] + (stops[k + 1][1][j] - stops[k][1][j]) * f);
      out[i * 4 + 3] = 255;
    }
  }
  const t = new THREE.DataTexture(out, D.nx, D.ny, THREE.RGBAFormat);
  t.colorSpace = THREE.SRGBColorSpace;
  t.minFilter = THREE.LinearMipmapLinearFilter; t.magFilter = THREE.LinearFilter; t.generateMipmaps = true;
  t.needsUpdate = true;
  return t;
}
const DASAR = {};
function aturDasar() {
  const j = $('t3Dasar').value;
  $('t3Atribusi').textContent = j === 'citra' ? 'Citra © Esri, Maxar, Earthstar Geographics' : '';
  if (j === 'citra') {
    U.uDasar.value = 1;
    if (!DASAR.citra) {
      DASAR.citra = 'memuat';
      muatCitra().then((t) => { DASAR.citra = t; U.uSat.value = t; }).catch(() => {
        DASAR.citra = null; $('t3Dasar').value = 'hipso'; aturDasar();
        catatan('Citra satelit tidak bisa dimuat. Peta dasar kembali ke hipsometrik.');
      });
    } else if (DASAR.citra !== 'memuat') U.uSat.value = DASAR.citra;
    if (!U.uSat.value) U.uSat.value = DASAR.hipso || (DASAR.hipso = warnaDasar('hipso'));
    return;
  }
  U.uDasar.value = 0;
  U.uBase.value = DASAR[j] || (DASAR[j] = warnaDasar(j));
}
function catatan(t) { const e = $('t3Catatan'); e.textContent = t; e.hidden = !t; clearTimeout(catatan.tm); catatan.tm = setTimeout(() => { e.hidden = true; }, 6000); }
async function muatCitra() {
  const s = D.h.sudut, lats = [s.bl[0], s.tl[0], s.bd[0], s.tg[0]], lons = [s.bl[1], s.tl[1], s.bd[1], s.tg[1]];
  const w = Math.min.apply(null, lons), e = Math.max.apply(null, lons), n = Math.max.apply(null, lats), so = Math.min.apply(null, lats);
  const mx = (lon, z) => (lon + 180) / 360 * 256 * 2 ** z;
  const my = (lat, z) => { const r = lat * Math.PI / 180; return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * 256 * 2 ** z; };
  let z = 17;
  while (z > 10 && Math.max(mx(e, z) - mx(w, z), my(so, z) - my(n, z)) > 2048) z--;
  const x0 = Math.floor(mx(w, z) / 256), x1 = Math.floor(mx(e, z) / 256), y0 = Math.floor(my(n, z) / 256), y1 = Math.floor(my(so, z) / 256);
  const cv = document.createElement('canvas'); cv.width = (x1 - x0 + 1) * 256; cv.height = (y1 - y0 + 1) * 256;
  const ctx = cv.getContext('2d'), tugas = [];
  for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
    tugas.push(new Promise((ok, no) => {
      const im = new Image(); im.crossOrigin = 'anonymous';
      im.onload = () => { ctx.drawImage(im, (x - x0) * 256, (y - y0) * 256); ok(); };
      im.onerror = no;
      im.src = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/' + z + '/' + y + '/' + x;
    }));
  }
  const hasil = await Promise.allSettled(tugas);
  if (hasil.every((r) => r.status === 'rejected')) throw new Error('citra gagal');
  const t = new THREE.CanvasTexture(cv);
  t.flipY = false; t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; t.needsUpdate = true;
  // uv grid (0..1) -> uv mosaik, affine dari tiga sudut
  const titik = (u, v) => { const ll = lonlat(u * D.nx, v * D.ny); return [(mx(ll[0], z) - x0 * 256) / cv.width, (my(ll[1], z) - y0 * 256) / cv.height]; };
  const a = titik(0, 0), b = titik(1, 0), c = titik(0, 1);
  U.uSatM.value.set(b[0] - a[0], c[0] - a[0], a[0], b[1] - a[1], c[1] - a[1], a[1], 0, 0, 1);
  return t;
}

function bangunMedan() {
  kosongkan(grup);
  grup = new THREE.Group();
  const det = +$('t3Detail').value, sx = Math.max(2, Math.min(D.nx - 1, det)), sy = Math.max(2, Math.min(D.ny - 1, Math.round(sx * D.ny / D.nx)));
  const nv = (sx + 1) * (sy + 1), pos = new Float32Array(nv * 3), uv = new Float32Array(nv * 2), el = new Float32Array(nv);
  for (let j = 0, k = 0; j <= sy; j++) {
    const v = j / sy;
    for (let i = 0; i <= sx; i++, k++) {
      const u = i / sx, e = elevDi(u * D.nx - 0.5, v * D.ny - 0.5);
      pos[k * 3] = (u - 0.5) * D.W; pos[k * 3 + 1] = e - D.elevMin; pos[k * 3 + 2] = (v - 0.5) * D.H;
      uv[k * 2] = u; uv[k * 2 + 1] = v; el[k] = e;
    }
  }
  const idx = new Uint32Array(sx * sy * 6);
  for (let j = 0, q = 0; j < sy; j++) for (let i = 0; i < sx; i++) {
    const a = j * (sx + 1) + i, b = a + 1, c = a + sx + 1, d = c + 1;
    idx.set([a, c, b, b, c, d], q); q += 6;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  g.setAttribute('aElev', new THREE.BufferAttribute(el, 1));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  g.computeVertexNormals();
  medan = new THREE.Mesh(g, bahanMedan());
  medan.castShadow = medan.receiveShadow = true;
  grup.add(medan);
  // dinding monolit
  const dasarY = -Math.max(60, 0.12 * (D.elevMaks - D.elevMin));
  const sisi = [];
  const tepi = (ii) => ii.map((k) => [pos[k * 3], pos[k * 3 + 1], pos[k * 3 + 2]]);
  const kol = (j0) => Array.from({ length: sx + 1 }, (_, i) => j0 * (sx + 1) + i);
  const bar = (i0) => Array.from({ length: sy + 1 }, (_, j) => j * (sx + 1) + i0);
  [[tepi(kol(0)), [0, 0, -1]], [tepi(kol(sy)), [0, 0, 1]], [tepi(bar(0)), [-1, 0, 0]], [tepi(bar(sx)), [1, 0, 0]]].forEach(([p, nrm]) => {
    for (let i = 0; i < p.length - 1; i++) {
      const a = p[i], b = p[i + 1];
      sisi.push([a, [a[0], dasarY, a[2]], b, nrm], [b, [a[0], dasarY, a[2]], [b[0], dasarY, b[2]], nrm]);
    }
  });
  const sp = new Float32Array(sisi.length * 9), sn = new Float32Array(sisi.length * 9), sc = new Float32Array(sisi.length * 9);
  const atas = new THREE.Color('#6b5f52'), bawah = new THREE.Color('#2a241f'), rg = Math.max(1, D.elevMaks - D.elevMin);
  sisi.forEach((tr, q) => {
    for (let v = 0; v < 3; v++) {
      sp.set(tr[v], q * 9 + v * 3); sn.set(tr[3], q * 9 + v * 3);
      const f = (tr[v][1] - dasarY) / (rg - dasarY), cc = bawah.clone().lerp(atas, Math.max(0, Math.min(1, f)) * 0.9 + (Math.floor(tr[v][1] / 40) % 2 ? 0 : 0.06));
      sc.set([cc.r, cc.g, cc.b], q * 9 + v * 3);
    }
  });
  const gs = new THREE.BufferGeometry();
  gs.setAttribute('position', new THREE.BufferAttribute(sp, 3));
  gs.setAttribute('normal', new THREE.BufferAttribute(sn, 3));
  gs.setAttribute('color', new THREE.BufferAttribute(sc, 3));
  const dinding = new THREE.Mesh(gs, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1, side: THREE.DoubleSide }));
  dinding.receiveShadow = true;
  grup.add(dinding);
  const bawahM = new THREE.Mesh(new THREE.PlaneGeometry(D.W, D.H), new THREE.MeshBasicMaterial({ color: 0x0a0c0b }));
  bawahM.rotation.x = Math.PI / 2; bawahM.position.y = dasarY;
  grup.add(bawahM);
  grup.scale.y = +$('t3Exag').value;
  scene.add(grup);
  grupVektor = new THREE.Group(); grup.add(grupVektor);
  grupLabel = new THREE.Group(); grup.add(grupLabel);
  bangunVektor(); bangunLabel(); bangunKepala();
  if (penandaInfo) { const p = penandaInfo.userData.g; penandaInfo = null; taruhPenanda(p[0], p[1]); }
  garisUkur = null; labelUkur = [];
  if (S.titikUkur.length) gambarUkur();
}

function garis(titik, warna, lebar, putus, induk) {
  if (titik.length < 2) return null;
  const g = new LineGeometry();
  g.setPositions(titik.flatMap((p) => [p.x, p.y, p.z]));
  const m = new LineMaterial({ color: warna, linewidth: lebar, worldUnits: false, dashed: !!putus, dashSize: 80, gapSize: 50, transparent: true, opacity: 0.95 });
  m.resolution.set(renderer.domElement.width / renderer.getPixelRatio(), renderer.domElement.height / renderer.getPixelRatio());
  bahanGaris.push(m);
  const l = new Line2(g, m);
  if (putus) l.computeLineDistances();
  (induk || grupVektor).add(l);
  return l;
}
function potong(a, b) { // Liang-Barsky ke kotak [0,nx]x[0,ny]
  let t0 = 0, t1 = 1; const dx = b[0] - a[0], dy = b[1] - a[1];
  const p = [-dx, dx, -dy, dy], q = [a[0], D.nx - a[0], a[1], D.ny - a[1]];
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) { if (q[i] < 0) return null; continue; }
    const r = q[i] / p[i];
    if (p[i] < 0) { if (r > t1) return null; if (r > t0) t0 = r; } else { if (r < t0) return null; if (r < t1) t1 = r; }
  }
  return [[a[0] + t0 * dx, a[1] + t0 * dy], [a[0] + t1 * dx, a[1] + t1 * dy], t0 > 0, t1 < 1];
}
function tampalkan(koord, warna, lebar, putus) {
  let run = [];
  const selesai = () => { if (run.length > 1) garis(run, warna, lebar, putus); run = []; };
  const pk = koord.map((c) => kePiksel(c[0], c[1]));
  for (let i = 0; i < pk.length - 1; i++) {
    const s = potong(pk[i], pk[i + 1]);
    if (!s) { selesai(); continue; }
    const [a, b, masuk, keluar] = s;
    if (masuk) selesai();
    const n = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / 1.5));
    for (let k = run.length ? 1 : 0; k <= n; k++) run.push(keDunia(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n, 5));
    if (keluar) selesai();
  }
  selesai();
}
function cincin(g) { return g.type === 'Polygon' ? g.coordinates : g.type === 'MultiPolygon' ? g.coordinates.flat() : g.type === 'LineString' ? [g.coordinates] : g.type === 'MultiLineString' ? g.coordinates : []; }
const GAYA_JALUR = { 'Jalur Patroli': '#ff6d00', 'Jalur Eksplorasi': '#d500f9', 'Jalur Pendakian': '#00b0ff', 'Jalan Lain': '#ffeb3b' };
const WARNA_RESORT = { 1: '#ff7043', 2: '#ffca28', 3: '#26c6da', 4: '#ab47bc', 5: '#66bb6a', 6: '#ec407a' };
function bangunVektor() {
  if (!grupVektor) return;
  grupVektor.children.slice().forEach((c) => kosongkan(c));
  const DT = T.DATA || {};
  if ($('t3Batas').checked) {
    if (DT.batas_tn) DT.batas_tn.features.forEach((f) => cincin(f.geometry).forEach((r) => tampalkan(r, '#ffffff', 2.6)));
    if (DT.batas_resort) DT.batas_resort.features.forEach((f) => cincin(f.geometry).forEach((r) => tampalkan(r, WARNA_RESORT[f.properties.kode] || '#ffffff', 1.6, true)));
  }
  if ($('t3Jalur').checked && DT.jalur_patroli) {
    DT.jalur_patroli.features.forEach((f) => cincin(f.geometry).forEach((r) => tampalkan(r, GAYA_JALUR[f.properties.tipe] || '#ffffff', 2, f.properties.tipe === 'Jalan Lain')));
  }
}
function label(teks, kelas, p, induk) {
  const e = document.createElement('div'); e.className = 't3-label ' + (kelas || ''); e.innerHTML = teks;
  const o = new CSS2DObject(e); o.position.copy(p); (induk || grupLabel).add(o); return o;
}
function bangunLabel() {
  grupLabel.children.slice().forEach((c) => kosongkan(c));
  if (!$('t3Label').checked) return;
  const ic = D.h.titik_api[0], ir = D.h.titik_api[1], p = keDunia(ic, ir, 0);
  const tinggi = 0.06 * Math.max(D.W, D.H) / Math.max(1, +$('t3Exag').value);
  const tiang = new THREE.Mesh(new THREE.CylinderGeometry(6, 6, tinggi, 8), new THREE.MeshBasicMaterial({ color: 0xffb347 }));
  tiang.position.set(p.x, p.y + tinggi / 2, p.z); grupLabel.add(tiang);
  label('<b>Titik api</b>' + (jamPada(0) ? '<br>' + jamPada(0) + (D.jamAsumsi ? '' : ' WITA') : ''), 'api', new THREE.Vector3(p.x, p.y + tinggi, p.z));
  const c = D.puncak % D.nx, r = (D.puncak / D.nx) | 0;
  label('▲ ' + angka(D.elevMaks) + ' m', 'puncak', keDunia(c + 0.5, r + 0.5, 15));
  (T.DATA && T.DATA.batas_resort ? T.DATA.batas_resort.features : []).forEach((f) => {
    const lb = f.properties.label; if (!lb) return;
    const q = kePiksel(lb[1], lb[0]);
    if (q[0] > 0 && q[1] > 0 && q[0] < D.nx && q[1] < D.ny) label(esc(f.properties.nama), 'resort', keDunia(q[0], q[1], 40));
  });
}
function bangunKepala() {
  // label jam bulat di titik muka api terjauh dari titik api pada jam itu (di garis waktu jam tsb.)
  labelKepala = []; garisKepala = null;
  if (!D.nb || !$('t3Kepala').checked) return;
  const iso = +$('t3IsoJam').value || 1, langkah = Math.max(iso, Math.ceil(D.dur / 60 / 14));
  const g = new THREE.Group(); grupLabel.add(g);
  const ic = D.h.titik_api[0], ir = D.h.titik_api[1], dekat = 0.08 * Math.max(D.nx, D.ny), dipakai = [[ic, ir]];
  for (let k = 1; ; k++) {
    const m = k * 60 - D.offJam;
    if (m > D.dur + 1e-6) break;
    if (m <= 0 || Math.round((m + D.offJam) / 60) % langkah) continue;
    let best = -1, bi = -1;
    for (let j = ub(m - 30), n = ub(m); j < n; j++) {
      const i = D.urut[j], d = (i % D.nx + 0.5 - ic) ** 2 + (((i / D.nx) | 0) + 0.5 - ir) ** 2;
      if (d > best) { best = d; bi = i; }
    }
    if (bi < 0) continue;
    const c = bi % D.nx + 0.5, r = ((bi / D.nx) | 0) + 0.5;
    if (dipakai.some((q) => Math.hypot(q[0] - c, q[1] - r) < dekat)) continue;
    dipakai.push([c, r]);
    const o = label(jamPada(m) || ('T+' + Math.round(m / 60) + ' j'), 'kepala', keDunia(c, r, 8), g);
    o.userData.m = m; labelKepala.push(o);
  }
}

/* ---------- pengaturan tampilan ---------- */
function aturLapis() {
  const k = $('t3Lapis').value, L = LAPIS[k];
  U.uVtiba.value = k === 'tiba' ? 1 : 0;
  U.uVcat.value = L.kategori ? 1 : 0;
  U.uVlog.value = L.log ? 1 : 0;
  U.uRamp.value = ramp(L.kategori ? 'tipe' : L.ramp);
  if (k === 'tiba') { U.uVmin.value = 0; U.uVmax.value = D.dur; }
  else if (!L.kategori) { U.uVmin.value = 0; U.uVmax.value = D.batas[k]; }
  if (k !== 'tiba') {
    const src = k === 'tipe' ? Float32Array.from(D.tipe) : D[k];
    if (U.uVal.value) U.uVal.value.dispose();
    U.uVal.value = teksturData(src, D.nx, D.ny);
  }
  legenda();
}
function legenda() {
  const k = $('t3Lapis').value, L = LAPIS[k], e = $('t3Legenda');
  if (L.kategori) {
    e.innerHTML = '<b>' + L.judul + '</b>' + [1, 2, 3].map((t) => '<div><span class="sw" style="background:' + WARNA_TIPE[t] + '"></span>' + NAMA_TIPE[t] + '</div>').join('');
    return;
  }
  const jt = (m) => { const j = jamPada(m); if (!j) return Math.round(m / 60) + ' j'; const x = new Date(D.t0 + m * 60000); return D.dur >= 20 * 60 ? j + ' (' + x.getUTCDate() + ' ' + BULAN[x.getUTCMonth()] + ')' : j; };
  const kiri = k === 'tiba' ? jt(0) : '0', kanan = k === 'tiba' ? jt(D.dur) : angka(D.batas[k], D.batas[k] < 10 ? 1 : 0) + '+';
  e.innerHTML = '<b>' + L.judul + (k === 'tiba' ? '' : ' (' + L.satuan + ')') + '</b><div class="bar" style="background:' + gradienCss(RAMP[L.ramp]) + '"></div>' +
    '<span>' + kiri + '</span><span style="float:right">' + kanan + '</span>' +
    (k === 'tiba' ? '<div class="kecil">ungu = terbakar lebih awal, kuning = baru terbakar</div>' : L.log ? '<div class="kecil">skala logaritmik</div>' : '') +
    ($('t3Muka').checked ? '<div class="kecil"><span class="sw menyala"></span>muka api menyala (' + U.uFront.value + ' menit terakhir)</div>' : '');
}
function aturMatahari() {
  const az = +$('t3Azimut').value * Math.PI / 180, el = +$('t3Elevasi').value * Math.PI / 180, R = Math.max(D.W, D.H) * 1.2;
  matahari.position.set(Math.sin(az) * Math.cos(el) * R, Math.sin(el) * R + (D.elevMaks - D.elevMin), -Math.cos(az) * Math.cos(el) * R);
  matahari.target.position.set(0, 0, 0);
  const sc = matahari.shadow.camera, s = Math.max(D.W, D.H) * 0.75;
  sc.left = -s; sc.right = s; sc.top = s; sc.bottom = -s; sc.near = 10; sc.far = R * 3; sc.updateProjectionMatrix();
  matahari.shadow.normalBias = D.sel * 0.6;
  matahari.castShadow = $('t3Bayangan').checked;
  matahari.intensity = 1.4 + 1.6 * Math.sin(el);
}
function aturKabut() {
  const R = Math.max(D.W, D.H);
  scene.fog.near = $('t3Kabut').checked ? R * 1.1 : 1e9; scene.fog.far = $('t3Kabut').checked ? R * 3.2 : 2e9;
}
function aturSemua() {
  U.uKontur.value = $('t3Kontur').checked ? +$('t3KonturM').value : 0;
  U.uIso.value = $('t3Iso').checked ? +$('t3IsoJam').value : 0;
  U.uGridOn.value = $('t3Grid').checked ? 1 : 0;
  U.uGlow.value = $('t3Muka').checked ? 1 : 0;
  U.uBayang.value = $('t3Bayang').checked ? 1 : 0;
  U.uOpas.value = +$('t3Opas').value;
  bloomPass.enabled = $('t3Bloom').checked;
  controls.autoRotate = $('t3Putar360').checked;
  $('t3ExagNilai').textContent = '×' + (+$('t3Exag').value).toLocaleString('id-ID');
}

/* ---------- kamera ---------- */
let tween = null;
function kamera(jenis, segera) {
  const R = Math.max(D.W, D.H) * Math.max(1, 1.15 / camera.aspect), ex = +$('t3Exag').value, tengahY = (D.elevMaks - D.elevMin) * 0.4 * ex;
  let tgt = new THREE.Vector3(0, tengahY, 0), pos;
  if (jenis === 'atas') pos = new THREE.Vector3(0, R * 1.35, R * 0.02);
  else if (jenis === 'angin' || jenis === 'api') {
    const n = ub(S.t), ic = D.h.titik_api;
    let hx = 0, hz = -1;
    if (n) {
      const i = D.dArg[n - 1], c = i % D.nx, r = (i / D.nx) | 0;
      hx = c + 0.5 - ic[0]; hz = r + 0.5 - ic[1];
      const L = Math.hypot(hx, hz) || 1; hx /= L; hz /= L;
    }
    const w = cuacaPada(S.t);
    if (jenis === 'angin' && w) { const a = ((+w.wd + 180) % 360) * Math.PI / 180; hx = Math.sin(a); hz = -Math.cos(a); }
    const p = keDunia(ic[0], ic[1], 0); p.y *= ex;
    if (jenis === 'api' && n) { const i = D.dArg[n - 1]; const q = keDunia(i % D.nx + 0.5, ((i / D.nx) | 0) + 0.5, 0); q.y *= ex; p.lerp(q, 0.5); }
    tgt = p;
    const d = jenis === 'api' ? Math.max(1500, Math.min(R * 0.6, (n ? D.dMaks[n - 1] : 1000) * 2.2)) : R * 0.75;
    pos = new THREE.Vector3(p.x - hx * d, p.y + d * 0.55, p.z - hz * d);
  } else pos = new THREE.Vector3(-R * 0.38, R * 0.62, R * 0.92);
  if (segera) { camera.position.copy(pos); controls.target.copy(tgt); controls.update(); return; }
  tween = { a: camera.position.clone(), b: pos, ta: controls.target.clone(), tb: tgt, t0: performance.now(), dur: 900 };
}

/* ---------- waktu ---------- */
function aturT(t, dariGeser) {
  if (!D) return;
  S.t = Math.max(0, Math.min(D.dur, t));
  U.uT.value = S.t;
  if (!dariGeser) $('t3Geser').value = String(Math.round(S.t));
  const now = performance.now();
  if (now - S.hudTerakhir > 90 || !S.main) { S.hudTerakhir = now; hud(); }
}
function hud() {
  const t = S.t, cel = D.cel, n = ub(t), j1 = ub(t - 60), jf = ub(t - U.uFront.value);
  const jamT = jamPada(t);
  $('t3Jam').innerHTML = '<b>' + (jamT || 'T+' + lamaTeks(t)) + (jamT && !D.jamAsumsi ? '<i> WITA</i>' : '') + '</b><span>' +
    (jamT ? tglPada(t) : '') + '</span><small>' + (D.jamAsumsi && jamT ? 'MTT: jam dihitung dari waktu mulai yang diisi · ' : '') + lamaTeks(t) + ' sejak api mulai</small>';
  $('t3DataJam').textContent = jamT ? jamT + (D.jamAsumsi ? '' : ' WITA') : 'T+' + lamaTeks(t);
  // tumbuh luas
  const ha = n * cel, haAkhir = D.nb * cel, dtj = Math.min(60, t) / 60;
  const tumbuh = dtj > 0 ? (n - j1) * cel / dtj : 0;
  // muka api: sel yang terbakar 60 menit terakhir
  const r = [], fl = [], fi = []; let rMax = 0, flMax = 0, fiMax = 0;
  const step = Math.max(1, Math.floor((n - j1) / 6000));
  for (let j = j1; j < n; j += step) { const i = D.urut[j]; r.push(D.ros[i]); fl.push(D.nyala[i]); fi.push(D.fli[i]); }
  for (let j = j1; j < n; j++) { const i = D.urut[j]; if (D.ros[i] > rMax) rMax = D.ros[i]; if (D.nyala[i] > flMax) flMax = D.nyala[i]; if (D.fli[i] > fiMax) fiMax = D.fli[i]; }
  const med = (a) => { if (!a.length) return null; a.sort((x, y) => x - y); return a[a.length >> 1]; };
  const rMed = med(r), flMed = med(fl);
  const dNow = n ? D.dMaks[n - 1] : 0, dPrev = j1 ? D.dMaks[j1 - 1] : 0, maju = dtj > 0 ? (dNow - dPrev) / (dtj * 60) : 0;
  let arah = null;
  if (n) { const i = D.dArg[n - 1], ic = D.h.titik_api; arah = (Math.atan2(i % D.nx + 0.5 - ic[0], -(((i / D.nx) | 0) + 0.5 - ic[1])) * 180 / Math.PI + 360) % 360; }
  const tp = [1, 2, 3].map((q) => D.kum[q][n]), tTot = tp[0] + tp[1] + tp[2];
  const pr = (x) => tTot ? angka(100 * x / tTot) + '%' : '–';
  const aktif = (n - jf) * cel;
  const kotak = [
    ['Luas terbakar', angka(ha, ha < 10 ? 1 : 0) + ' ha', haAkhir ? angka(100 * ha / haAkhir) + '% dari luas akhir ' + angka(haAkhir) + ' ha' : '', 'api'],
    ['Pertambahan luas', angka(tumbuh, tumbuh < 10 ? 1 : 0) + ' ha/jam', t >= 60 ? 'selama 1 jam terakhir' : 'sejak api mulai', ''],
    ['Laju api (ROS) di muka api', rMed === null ? '–' : angka(rMed, 1) + ' m/menit', rMed === null ? 'tidak ada sel baru terbakar' : 'median · maks ' + angka(rMax, 1) + ' m/menit (' + angka(rMax * 0.06, 1) + ' km/j)', ''],
    ['Kemajuan kepala api', angka(maju, 1) + ' m/menit', arah === null ? '' : 'ke arah ' + mata(arah) + ' (' + Math.round(arah) + '°) · ' + angka(dNow / 1000, 2) + ' km dari titik api', ''],
    ['Panjang nyala', flMed === null ? '–' : angka(flMed, 1) + ' m', flMed === null ? '' : 'median · maks ' + angka(flMax, 1) + ' m · intensitas maks ' + angka(fiMax) + ' kW/m', ''],
    ['Tipe api sejauh ini', tTot ? pr(tp[0]) + ' permukaan' : '–', tTot ? 'tajuk pasif ' + pr(tp[1]) + ' · tajuk aktif ' + pr(tp[2]) : '', ''],
    ['Sedang menyala', angka(aktif, aktif < 10 ? 1 : 0) + ' ha', 'terbakar dalam ' + U.uFront.value + ' menit terakhir', '']
  ];
  $('t3Stat').innerHTML = kotak.map((k) => '<div class="' + k[3] + '"><span>' + k[0] + '</span><b>' + k[1] + '</b><small>' + k[2] + '</small></div>').join('');
  const w = cuacaPada(t);
  if (w) {
    $('t3Cuaca').innerHTML = '<div><span>Angin</span><b>' + angka(w.ws) + ' km/j dari ' + mata(w.wd) + ' (' + Math.round(w.wd) + '°)</b></div>' +
      (w.suhu !== undefined ? '<div><span>Suhu · RH</span><b>' + angka(w.suhu) + ' °C · ' + angka(w.rh) + '%</b></div>' : '') +
      (w.hujan ? '<div><span>Hujan</span><b>' + angka(w.hujan, 1) + ' mm/jam</b></div>' : '') +
      (w.m1 !== undefined && w.m1 !== null ? '<div><span>Kadar air 1-jam rumput</span><b>' + angka(w.m1, 1) + '%</b></div>' : '') +
      '<p class="petunjuk">' + (D.jam.length ? 'Cuaca per jam yang dipakai simulasi pada jam ini.' : 'MTT: angin tetap selama simulasi.') + '</p>';
    $('t3Angin').style.transform = 'rotate(' + ((+w.wd + 180) % 360) + 'deg)';
    $('t3Angin').style.opacity = 1;
    $('t3KompasTeks').textContent = angka(w.ws) + ' km/j dari ' + mata(w.wd);
  } else { $('t3Cuaca').innerHTML = ''; $('t3Angin').style.opacity = 0; $('t3KompasTeks').textContent = ''; }
  labelKepala.forEach((o) => { o.visible = o.userData.m <= t + 1e-6; });
  gambarGrafik();
  if (penandaInfo) infoTitik(penandaInfo.userData.g[0], penandaInfo.userData.g[1], true);
}

/* ---------- grafik waktu ---------- */
let SERI = null;
function seri() {
  const n = 240, a = new Float32Array(n + 1), cel = D.cel;
  for (let k = 0; k <= n; k++) a[k] = ub(D.dur * k / n) * cel;
  SERI = a;
}
function gambarGrafik(hover) {
  const cv = $('t3Grafik'), w = cv.clientWidth, h = cv.clientHeight, dpr = window.devicePixelRatio || 1;
  if (!w || !SERI) return;
  if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  const ctx = cv.getContext('2d'), st = getComputedStyle(document.documentElement);
  const api = st.getPropertyValue('--fire').trim() || '#c4501b', garisW = st.getPropertyValue('--line').trim() || '#ccc', redup = st.getPropertyValue('--muted').trim() || '#777', fg = st.getPropertyValue('--fg').trim() || '#222';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
  const mx = Math.max(1e-6, SERI[SERI.length - 1]), atas = 6, bawah = h - 2, X = (m) => m / D.dur * w, Y = (v) => bawah - (v / mx) * (bawah - atas);
  // garis jam
  ctx.strokeStyle = garisW; ctx.lineWidth = 1;
  const tiap = D.dur > 48 * 60 ? 6 : D.dur > 24 * 60 ? 3 : D.dur > 8 * 60 ? 2 : 1;
  for (let k = 1; ; k++) { const m = k * 60 - D.offJam; if (m > D.dur) break; if (m <= 0 || Math.round((m + D.offJam) / 60) % tiap) continue; ctx.beginPath(); ctx.moveTo(X(m) + 0.5, atas); ctx.lineTo(X(m) + 0.5, bawah); ctx.stroke(); }
  // luas
  ctx.beginPath(); ctx.moveTo(0, bawah);
  for (let k = 0; k < SERI.length; k++) ctx.lineTo(w * k / (SERI.length - 1), Y(SERI[k]));
  ctx.lineTo(w, bawah); ctx.closePath(); ctx.globalAlpha = 0.18; ctx.fillStyle = api; ctx.fill(); ctx.globalAlpha = 1;
  ctx.beginPath();
  for (let k = 0; k < SERI.length; k++) { const x = w * k / (SERI.length - 1), y = Y(SERI[k]); if (k) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
  ctx.strokeStyle = api; ctx.lineWidth = 2; ctx.stroke();
  // penunjuk waktu
  const xt = X(S.t);
  ctx.strokeStyle = fg; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(xt, 0); ctx.lineTo(xt, h); ctx.stroke();
  ctx.fillStyle = fg; ctx.beginPath(); ctx.arc(xt, Y(ub(S.t) * D.cel), 4, 0, 7); ctx.fill();
  ctx.fillStyle = redup; ctx.font = '11px system-ui,sans-serif'; ctx.textAlign = 'left';
  ctx.fillText('Luas terbakar · akhir ' + angka(mx) + ' ha', 4, 12);
  if (hover !== undefined) {
    const m = Math.max(0, Math.min(D.dur, hover / w * D.dur));
    ctx.strokeStyle = redup; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(X(m), 0); ctx.lineTo(X(m), h); ctx.stroke(); ctx.setLineDash([]);
  }
}
function skalaWaktu() {
  const e = $('t3Skala'), lebar = e.clientWidth || 500, tiap0 = D.dur > 48 * 60 ? 12 : D.dur > 24 * 60 ? 6 : D.dur > 8 * 60 ? 3 : 1;
  const tiap = lebar < 360 ? tiap0 * 2 : tiap0, teks = (m) => jamPada(m) || Math.round(m / 60) + ' j';
  const lab = ['<span class="kiri" style="left:0">' + teks(0) + '</span>'];
  for (let k = 1; ; k++) {
    const m = k * 60 - D.offJam; if (m >= D.dur) break; if (m <= 0) continue;
    if (Math.round((m + D.offJam) / 60) % tiap) continue;
    const f = m / D.dur; if (f * lebar < 48 || (1 - f) * lebar < 48) continue;
    lab.push('<span style="left:' + (100 * f).toFixed(2) + '%">' + teks(m) + '</span>');
  }
  lab.push('<span class="kanan">' + teks(D.dur) + '</span>');
  e.innerHTML = lab.join('');
}

/* ---------- info titik & ukur ---------- */
function ambil(e) {
  const r = renderer.domElement.getBoundingClientRect();
  const v = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  const rc = new THREE.Raycaster(); rc.setFromCamera(v, camera);
  const hit = rc.intersectObject(medan, false)[0];
  if (!hit) return null;
  const p = grup.worldToLocal(hit.point.clone());
  return [(p.x / D.W + 0.5) * D.nx, (p.z / D.H + 0.5) * D.ny];
}
function taruhPenanda(gx, gy) {
  if (!penandaInfo) {
    penandaInfo = new THREE.Group();
    const tinggi = 0.035 * Math.max(D.W, D.H);
    const t = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, tinggi, 6), new THREE.MeshBasicMaterial({ color: 0x7fe3ff }));
    t.position.y = tinggi / 2; penandaInfo.add(t);
    const b = new THREE.Mesh(new THREE.SphereGeometry(18, 12, 8), new THREE.MeshBasicMaterial({ color: 0x7fe3ff }));
    b.position.y = tinggi; penandaInfo.add(b);
    grup.add(penandaInfo);
  }
  penandaInfo.position.copy(keDunia(gx, gy, 0));
  penandaInfo.scale.y = 1 / grup.scale.y;   // tiang tidak ikut diregangkan pembesaran tinggi
  penandaInfo.userData.g = [gx, gy];
}
function lereng(c, r) {
  const e = (cc, rr) => D.elev[Math.max(0, Math.min(D.ny - 1, rr)) * D.nx + Math.max(0, Math.min(D.nx - 1, cc))];
  const gx = (e(c + 1, r) - e(c - 1, r)) / (2 * D.sel), gy = (e(c, r - 1) - e(c, r + 1)) / (2 * D.sel);
  return [Math.atan(Math.hypot(gx, gy)) * 180 / Math.PI, (Math.atan2(-gx, -gy) * 180 / Math.PI + 360) % 360];
}
function infoTitik(gx, gy, diam) {
  const c = Math.max(0, Math.min(D.nx - 1, Math.floor(gx))), r = Math.max(0, Math.min(D.ny - 1, Math.floor(gy))), i = r * D.nx + c;
  const ll = lonlat(c + 0.5, r + 0.5), lok = T.lokasi(ll[1], ll[0]), a = D.tiba[i], bakar = a < 1e6, sl = lereng(c, r);
  const fu = D.fuel[i], nf = (T.NAMA_FUEL || {})[fu] || '';
  let status = 'Tidak terbakar dalam simulasi ini';
  if (bakar) {
    const u = S.t - a;
    status = u < 0 ? 'Belum terbakar; api tiba ' + lamaTeks(-u) + ' lagi' : u < U.uFront.value ? '<b class="api-teks">Sedang menyala</b> (tiba ' + lamaTeks(u) + ' lalu)' : 'Sudah terbakar ' + lamaTeks(u) + ' lalu';
  }
  const pot = !bakar && D.mode === 'mtt' && D.ros[i] > 0 ? ' <span class="muted">(potensi)</span>' : '';
  const ada = bakar || pot;
  const baris = [
    ['Koordinat', ll[1].toFixed(5) + ', ' + ll[0].toFixed(5)],
    ['Lokasi', lok ? esc(lok.teks) : '–'],
    ['Elevasi', angka(D.elev[i]) + ' m'],
    ['Lereng · aspek', angka(sl[0]) + '° · menghadap ' + mata(sl[1])],
    ['Bahan bakar', fu + (nf ? ' ' + esc(nf) : '')],
    ['<b>Api tiba</b>', bakar ? '<b>' + waktuTeks(a, true) + '</b><br><span class="muted">' + lamaTeks(a) + ' setelah api mulai</span>' : '–'],
    ['Pada waktu ini', status],
    ['Laju api (ROS)', ada ? angka(D.ros[i], 1) + ' m/menit (' + angka(D.ros[i] * 0.06, 2) + ' km/j)' + pot : '–'],
    ['Panjang nyala', ada ? angka(D.nyala[i], 2) + ' m' + pot : '–'],
    ['Intensitas', ada ? angka(D.fli[i]) + ' kW/m' + pot : '–'],
    ['Tipe api', bakar ? NAMA_TIPE[D.tipe[i]] || '–' : '–']
  ];
  $('t3Info').innerHTML = '<table>' + baris.map((b) => '<tr><td>' + b[0] + '</td><td>' + b[1] + '</td></tr>').join('') + '</table>';
  if (!diam) { taruhPenanda(gx, gy); if (HP) $('t3InfoKartu').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
}
function gambarUkur() {
  if (garisUkur) { kosongkan(garisUkur); garisUkur = null; }
  labelUkur.forEach((o) => kosongkan(o)); labelUkur = [];
  const P = S.titikUkur;
  P.forEach((p, k) => labelUkur.push(label(k ? 'B' : 'A', 'ukur', keDunia(p[0], p[1], 20), grup)));
  if (P.length < 2) { $('t3HasilUkur').innerHTML = P.length ? '<p class="muted small">Klik titik B.</p>' : ''; return; }
  const n = Math.max(2, Math.ceil(Math.hypot(P[1][0] - P[0][0], P[1][1] - P[0][1]) / 1.5)), pts = [];
  let d3 = 0, prev = null;
  for (let k = 0; k <= n; k++) {
    const q = keDunia(P[0][0] + (P[1][0] - P[0][0]) * k / n, P[0][1] + (P[1][1] - P[0][1]) * k / n, 6);
    pts.push(q); if (prev) d3 += q.distanceTo(prev); prev = q;
  }
  garisUkur = garis(pts, '#7fe3ff', 3, false, grup);
  const dx = (P[1][0] - P[0][0]) * D.sel, dy = (P[1][1] - P[0][1]) * D.sel, dh = Math.hypot(dx, dy);
  const ia = Math.floor(P[0][1]) * D.nx + Math.floor(P[0][0]), ib = Math.floor(P[1][1]) * D.nx + Math.floor(P[1][0]);
  const ta = D.tiba[ia], tb = D.tiba[ib], ea = D.elev[ia], eb = D.elev[ib];
  const arah = (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360;
  let laju = '<p class="petunjuk">Salah satu titik tidak terbakar, jadi laju A→B tidak bisa dihitung.</p>';
  if (ta < 1e6 && tb < 1e6) {
    const sd = tb - ta;
    laju = Math.abs(sd) < 0.5 ? '<p class="petunjuk">Kedua titik terbakar hampir bersamaan.</p>'
      : '<table><tr><td>Api tiba di A</td><td>' + waktuTeks(ta) + '</td></tr><tr><td>Api tiba di B</td><td>' + waktuTeks(tb) + '</td></tr>' +
        '<tr><td><b>Laju rata-rata ' + (sd > 0 ? 'A→B' : 'B→A') + '</b></td><td><b>' + angka(dh / Math.abs(sd), 1) + ' m/menit</b> (' + angka(dh / Math.abs(sd) * 0.06, 2) + ' km/j)</td></tr>' +
        '<tr><td>Selang waktu</td><td>' + lamaTeks(Math.abs(sd)) + '</td></tr></table>';
  }
  $('t3HasilUkur').innerHTML = '<table><tr><td>Jarak datar</td><td>' + angka(dh) + ' m</td></tr><tr><td>Jarak di permukaan</td><td>' + angka(d3 / 1) + ' m</td></tr>' +
    '<tr><td>Beda tinggi B − A</td><td>' + (eb - ea >= 0 ? '+' : '') + angka(eb - ea) + ' m</td></tr><tr><td>Arah A→B</td><td>' + mata(arah) + ' (' + Math.round(arah) + '°)</td></tr></table>' + laju;
}

/* ---------- kendali ---------- */
const IKON = {
  main: '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M7 4.5v15l12-7.5z" fill="currentColor"/></svg>',
  jeda: '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z" fill="currentColor"/></svg>'
};
// lompat ke jam bulat (pukul hh:00) sebelum / sesudah t
function jamSebelum(t) { return Math.max(0, (Math.ceil((t + D.offJam) / 60 - 1e-6) - 1) * 60 - D.offJam); }
function jamBerikut(t) { return Math.min(D.dur, (Math.floor((t + D.offJam) / 60 + 1e-6) + 1) * 60 - D.offJam); }
function aturTombolMain() {
  $('t3Putar').innerHTML = (S.main ? IKON.jeda : IKON.main) + '<span>' + (S.main ? 'Jeda' : 'Putar') + '</span>';
  $('t3Putar').setAttribute('aria-pressed', String(S.main));
}
function main(on) {
  if (!D) return;
  S.main = on === undefined ? !S.main : on;
  if (S.main && S.t >= D.dur - 0.5) aturT(0);
  S.waktuAkhir = performance.now();
  aturTombolMain();
}
function pasangKendali() {
  $('t3Putar').addEventListener('click', () => main());
  $('t3Awal').addEventListener('click', () => { main(false); aturT(0); });
  $('t3Akhir').addEventListener('click', () => { main(false); aturT(D.dur); });
  $('t3Mundur').addEventListener('click', () => { if (D) aturT(jamSebelum(S.t)); });
  $('t3Maju').addEventListener('click', () => { if (D) aturT(jamBerikut(S.t)); });
  const geser = $('t3Geser');
  geser.addEventListener('input', () => aturT(+geser.value, true));
  geser.addEventListener('pointerdown', () => main(false));
  const gr = $('t3Grafik');
  const keT = (e) => { const r = gr.getBoundingClientRect(); return Math.max(0, Math.min(D.dur, (e.clientX - r.left) / r.width * D.dur)); };
  let seret = false;
  gr.addEventListener('pointerdown', (e) => { if (!D) return; seret = true; gr.setPointerCapture(e.pointerId); main(false); aturT(keT(e)); });
  gr.addEventListener('pointermove', (e) => {
    if (!D) return;
    const r = gr.getBoundingClientRect(), m = keT(e);
    if (seret) aturT(m);
    const tip = $('t3Tip'); tip.hidden = false;
    tip.style.left = Math.max(0, Math.min(r.width - 120, e.clientX - r.left + 8)) + 'px';
    tip.innerHTML = '<b>' + (jamPada(m) || 'T+' + lamaTeks(m)) + '</b> · ' + angka(ub(m) * D.cel) + ' ha';
    gambarGrafik(e.clientX - r.left);
  });
  gr.addEventListener('pointerup', () => { seret = false; });
  gr.addEventListener('pointerleave', () => { $('t3Tip').hidden = true; if (D) gambarGrafik(); });
  ['t3Kontur', 't3KonturM', 't3Iso', 't3Grid', 't3Muka', 't3Bayang', 't3Opas', 't3Bloom', 't3Putar360'].forEach((id) => $(id).addEventListener('input', () => { if (D) { aturSemua(); legenda(); } }));
  const labelUlang = () => { if (D) { aturSemua(); bangunLabel(); bangunKepala(); hud(); } };
  ['t3IsoJam', 't3Kepala', 't3Label'].forEach((id) => $(id).addEventListener('change', labelUlang));
  $('t3Lapis').addEventListener('change', () => { if (D) aturLapis(); });
  $('t3Dasar').addEventListener('change', () => { if (D) aturDasar(); });
  $('t3Exag').addEventListener('input', () => {
    if (!D) return;
    grup.scale.y = +$('t3Exag').value; aturSemua();
    if (penandaInfo) taruhPenanda(penandaInfo.userData.g[0], penandaInfo.userData.g[1]);
  });
  $('t3Exag').addEventListener('change', labelUlang);
  $('t3Detail').addEventListener('change', () => { if (D) { bangunMedan(); hud(); } });
  ['t3Jalur', 't3Batas'].forEach((id) => $(id).addEventListener('change', () => { if (D) bangunVektor(); }));
  ['t3Azimut', 't3Elevasi', 't3Bayangan'].forEach((id) => $(id).addEventListener('input', () => { if (D) aturMatahari(); }));
  $('t3Kabut').addEventListener('input', () => { if (D) aturKabut(); });
  $('t3Ulang').addEventListener('change', () => { S.ulang = $('t3Ulang').checked; });
  document.querySelectorAll('[data-kamera]').forEach((b) => b.addEventListener('click', () => { if (D) kamera(b.dataset.kamera); }));
  $('t3Ukur').addEventListener('click', () => {
    S.ukur = !S.ukur; S.titikUkur = [];
    $('t3Ukur').textContent = S.ukur ? 'Selesai mengukur' : 'Mulai ukur';
    $('t3ModeUkur').hidden = !S.ukur;
    if (D) gambarUkur();
  });
  // klik medan
  let turun = null;
  renderer.domElement.addEventListener('pointerdown', (e) => { turun = [e.clientX, e.clientY, performance.now()]; });
  renderer.domElement.addEventListener('pointerup', (e) => {
    if (!D || !turun || Math.hypot(e.clientX - turun[0], e.clientY - turun[1]) > 6 || performance.now() - turun[2] > 600) return;
    const g = ambil(e); if (!g) return;
    if (S.ukur) {
      if (S.titikUkur.length >= 2) S.titikUkur = [];
      S.titikUkur.push(g); gambarUkur();
    } else infoTitik(g[0], g[1]);
  });
  controls.addEventListener('start', () => { tween = null; });
  // papan ketik
  document.addEventListener('keydown', (e) => {
    const el = e.target || {}, tag = el.tagName || '';
    if ($('panel-tiga').hidden || !D || e.ctrlKey || e.metaKey || e.altKey) return;
    if (/SELECT|TEXTAREA|BUTTON/.test(tag) && (e.key === ' ' || e.key === 'Enter')) return;
    if (tag === 'INPUT' && el.id !== 't3Geser') return;
    if (e.key === ' ') { e.preventDefault(); main(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); aturT(S.t + (e.shiftKey ? 60 : 10)); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); aturT(S.t - (e.shiftKey ? 60 : 10)); }
    else if (e.key === 'PageUp') { e.preventDefault(); aturT(jamBerikut(S.t)); }
    else if (e.key === 'PageDown') { e.preventDefault(); aturT(jamSebelum(S.t)); }
    else if (e.key === 'Home') { e.preventDefault(); aturT(0); }
    else if (e.key === 'End') { e.preventDefault(); aturT(D.dur); }
  });
  $('t3Foto').addEventListener('click', foto);
  $('t3Penuh').addEventListener('click', () => {
    const el = $('t3Utama');
    if (document.fullscreenElement) document.exitFullscreen();
    else if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
  });
  $('t3Peta2d').addEventListener('click', () => { if (D && D.rel) T.tampil2d(D.rel); });
  $('t3Pilih').addEventListener('change', () => { const id = $('t3Pilih').value; if (id) { T.setHash('tiga/' + id); muatSim(id); } });
  window.addEventListener('resize', () => { if (D) { gambarGrafik(); skalaWaktu(); } });
}
function foto() {
  if (!D) return;
  composer.render();
  const src = renderer.domElement, cv = document.createElement('canvas'); cv.width = src.width; cv.height = src.height;
  const ctx = cv.getContext('2d'), s = renderer.getPixelRatio();
  ctx.drawImage(src, 0, 0);
  ctx.fillStyle = 'rgba(10,14,12,.72)'; ctx.fillRect(0, cv.height - 64 * s, cv.width, 64 * s);
  ctx.fillStyle = '#fff'; ctx.font = (600 * 1) + ' ' + Math.round(18 * s) + 'px system-ui,sans-serif';
  const n = ub(S.t), ha = n * D.cel;
  ctx.fillText('Simulasi ' + D.mode.toUpperCase() + ' · ' + waktuTeks(S.t, true) + ' · ' + angka(ha) + ' ha terbakar', 16 * s, cv.height - 36 * s);
  ctx.font = Math.round(13 * s) + 'px system-ui,sans-serif'; ctx.fillStyle = '#cfd8d3';
  ctx.fillText('TN Tambora · ' + LAPIS[$('t3Lapis').value].judul + ' · ' + lamaTeks(S.t) + ' sejak api mulai', 16 * s, cv.height - 14 * s);
  cv.toBlob((b) => {
    const a = document.createElement('a'); a.href = URL.createObjectURL(b);
    a.download = 'simulasi3d_' + S.idAktif + '_' + (jamPada(S.t) || 'T' + Math.round(S.t)).replace(':', '') + '.png';
    document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 800);
  }, 'image/png');
}

/* ---------- putaran gambar ---------- */
function putaran(now) {
  requestAnimationFrame(putaran);
  if ($('panel-tiga').hidden || document.hidden || !D) return;
  if (S.main) {
    const dt = Math.min(0.1, (now - S.waktuAkhir) / 1000);
    let t = S.t + dt * +$('t3Laju').value;
    if (t >= D.dur) { if (S.ulang) t = 0; else { t = D.dur; main(false); } }
    aturT(t);
  }
  S.waktuAkhir = now;
  if (tween) {
    const f = Math.min(1, (now - tween.t0) / tween.dur), e = f < 0.5 ? 2 * f * f : 1 - Math.pow(-2 * f + 2, 2) / 2;
    camera.position.lerpVectors(tween.a, tween.b, e); controls.target.lerpVectors(tween.ta, tween.tb, e);
    if (f >= 1) tween = null;
  }
  controls.update();
  U.uWaktu.value = now / 1000;
  const az = controls.getAzimuthalAngle() * 180 / Math.PI;
  $('t3Mawar').style.transform = 'rotate(' + az.toFixed(1) + 'deg)';
  composer.render();
  labelR.render(scene, camera);
}

/* ---------- memuat simulasi ---------- */
function isiPilihan() {
  const sel = $('t3Pilih'), R = T.rilis();
  if (!R.length) { sel.innerHTML = '<option value="">Belum ada simulasi di riwayat</option>'; return; }
  sel.innerHTML = R.map((rel) => {
    const d = T.dataDari(rel) || {}, m = d.masukan || {}, t = (m.titik || '').split(','), lok = T.lokasi(+t[0], +t[1]);
    const id = rel.tag_name.slice(4), mode = (m.mode || d.mode || '').toUpperCase();
    const mulai = m.mulai ? T.tglID(String(m.mulai).replace(' ', 'T'), true) : T.tglID(T.keWITA(rel.created_at), true);
    return '<option value="' + esc(id) + '">' + esc(mulai + ' · ' + mode + ' · ' + (lok && lok.resort ? lok.resort.nama : (m.titik || '')) + ' · ' + angka(d.luas_terbakar_ha) + ' ha') + '</option>';
  }).join('');
  if (S.idAktif) sel.value = S.idAktif;
}
async function muatSim(id) {
  if (!siapkan()) return;
  const no = ++S.muatNo;
  S.idAktif = id; main(false);
  $('t3Pilih').value = id;
  const rel = T.rilis().filter((r) => r.tag_name === 'sim-' + id)[0] || null;
  pesan('Memuat data 3D…');
  let buf;
  try { buf = await unduh(id); } catch (e) {
    if (no !== S.muatNo) return;
    D = null; SERI = null; $('t3DataJam').textContent = '–'; $('t3Skala').innerHTML = '';
    { const cv = $('t3Grafik'); cv.getContext('2d').clearRect(0, 0, cv.width, cv.height); } $('t3Stat').innerHTML = ''; $('t3Cuaca').innerHTML = ''; $('t3Ringkas').innerHTML = ''; $('t3Jam').innerHTML = '';
    pesan(e.kode === 404 ? 'Data 3D untuk simulasi ini belum tersedia.<br><small>Simulasi baru otomatis punya data 3D. Untuk simulasi lama, data 3D sedang atau akan dibuat oleh workflow <i>Data 3D</i> di GitHub Actions; coba lagi beberapa menit lagi.</small>'
      : 'Data 3D tidak bisa dimuat: ' + esc(e.message), 'err');
    return;
  }
  if (no !== S.muatNo) return;
  pesan('Menyiapkan medan 3D…');
  await new Promise((r) => setTimeout(r, 30));
  try {
    const x = urai(buf);
    D = olah(x.h, x.L, rel);
  } catch (e) { pesan('Data 3D rusak: ' + esc(e.message), 'err'); return; }
  Object.keys(DASAR).forEach((k) => { if (DASAR[k] && DASAR[k].dispose) DASAR[k].dispose(); delete DASAR[k]; });
  U.uSat.value = null;
  if (U.uArr.value) U.uArr.value.dispose();
  U.uArr.value = teksturData(D.tiba, D.nx, D.ny);
  U.uGrid.value.set(D.nx, D.ny); U.uSel.value = D.sel; U.uUtm.value.set(D.h.utm.barat, D.h.utm.utara); U.uIsoOff.value = D.offJam;
  if (!U.uVal.value) U.uVal.value = teksturData(new Float32Array(1), 1, 1);
  S.titikUkur = []; penandaInfo = null;
  $('t3Info').innerHTML = '<p class="muted small">Klik medan 3D untuk melihat waktu tiba api, laju api, dan data lain di titik itu.</p>';
  $('t3HasilUkur').innerHTML = '';
  await (T.siapData || Promise.resolve());
  const jamDur = D.dur / 60;
  $('t3IsoJam').value = jamDur <= 8 ? '1' : jamDur <= 18 ? '2' : jamDur <= 36 ? '3' : '6';
  bangunMedan(); aturDasar(); aturLapis(); aturSemua(); aturMatahari(); aturKabut();
  const R = Math.max(D.W, D.H);
  camera.near = Math.max(5, R / 2000); camera.far = R * 12; camera.updateProjectionMatrix();
  controls.minDistance = R * 0.05; controls.maxDistance = R * 3.5;
  kamera('miring', true);
  $('t3Geser').max = String(D.dur);
  seri(); skalaWaktu();
  ringkas();
  pesan('');
  aturT(D.dur);   // tampil hasil akhir dulu; tombol Putar mulai dari awal
  hud();
}
function ringkas() {
  const d = D.d, m = D.mas, s = D.h.simulasi || {}, tp = s.luas_per_tipe_api_ha || d.luas_per_tipe_api_ha || {};
  const baris = [
    ['Mode', D.mode.toUpperCase()],
    ['Mulai', jamPada(0) ? waktuTeks(0, true) + (D.jamAsumsi ? ' (waktu isian)' : '') : '–'],
    ['Lama', angka(D.dur / 60, 1) + ' jam'],
    ['Angin', m.angin ? esc(m.angin).replace(',', ' km/j dari ') + '° (tetap)' : (D.jam.length ? 'cuaca per jam' : '–')],
    ['Api tajuk', esc((T.NAMA_TAJUK || {})[s.metode_tajuk || d.metode_tajuk] || s.metode_tajuk || '–')],
    ['<b>Luas terbakar akhir</b>', '<b>' + angka(D.h.luas_terbakar_ha, 1) + ' ha</b>'],
    ['· api permukaan', angka(tp.permukaan, 1) + ' ha'],
    ['· tajuk pasif', angka(tp.tajuk_pasif, 1) + ' ha'],
    ['· tajuk aktif', angka(tp.tajuk_aktif, 1) + ' ha'],
    ['Jarak terjauh dari titik api', angka((D.nb ? D.dMaks[D.nb - 1] : 0) / 1000, 2) + ' km'],
    ['Sel 3D', angka(D.sel) + ' m' + (D.h.perkecil > 1 ? ' (diperkecil dari ' + angka(D.h.sel_asli_m) + ' m)' : '') + ' · ' + angka(D.nx) + ' × ' + angka(D.ny)],
    D.h.catatan ? ['Catatan', esc(D.h.catatan)] : null
  ].filter(Boolean);
  const zip = D.rel && (D.rel.assets || []).filter((a) => a.name === 'hasil.zip')[0];
  $('t3Ringkas').innerHTML = '<table>' + baris.map((b) => '<tr><td>' + b[0] + '</td><td>' + b[1] + '</td></tr>').join('') + '</table>' +
    (zip ? '<p class="petunjuk"><a href="' + zip.browser_download_url + '">Unduh hasil (GeoTIFF, shapefile, PNG)</a></p>' : '');
}

/* ---------- antarmuka untuk app.js ---------- */
export function buka(id) {
  if (!siapkan()) return;
  T.saatRilis(() => {
    isiPilihan();
    const R = T.rilis();
    const pilih = id || S.idAktif || (R[0] && R[0].tag_name.slice(4));
    if (pilih && pilih !== S.idAktif) muatSim(pilih);
    else if (pilih && !D) muatSim(pilih);
    else if (!pilih) pesan('Belum ada simulasi. Jalankan simulasi di tab Simulasi; hasilnya muncul di sini dalam 3D.');
  });
  setTimeout(() => { ukurUlang(); if (D) gambarGrafik(); }, 50);
}
aturTombolMain();
