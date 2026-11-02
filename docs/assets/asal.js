/* Tab Asal Api: hasil pemodelan terbalik (inverse modelling) titik asal, jam mulai, dan arah datang api
   tiap kejadian kebakaran musim ini. Data: docs/status/asal_api.json, docs/data/asal_api/ (dibuat
   `tambora asal-api` di GitHub Actions). Modul ini dimuat app.js saat tab dibuka. */
const T = window.TAMBORA;
const $ = (id) => document.getElementById(id);
const esc = T.esc, angka = T.angka;
const AOI = [[-8.4307, 117.7631], [-8.0860, 118.1980]];
const BULAN = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
const BULAN_PANJANG = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'];
const MATA = ['utara', 'timur laut', 'timur', 'tenggara', 'selatan', 'barat daya', 'barat', 'barat laut'];
const MATA_SINGKAT = ['U', 'TL', 'T', 'TG', 'S', 'BD', 'B', 'BL'];
const NAMA_MUTU = { baik: 'Baik', sedang: 'Sedang', rendah: 'Rendah', gagal: 'Tidak terhitung' };
const KET_MUTU = {
  baik: 'Terlihat di 2 lintasan satelit atau lebih, bentuk areal cocok (Sørensen ≥ 0,5) dan galat waktu ≤ 2 jam.',
  sedang: 'Bentuk areal cukup cocok (Sørensen ≥ 0,35) dan galat waktu ≤ 4 jam, atau hanya 1 lintasan satelit.',
  rendah: 'Bentuk areal atau waktu deteksi kurang cocok dengan simulasi. Pakai dengan hati-hati.',
  gagal: 'Kejadian ini tidak bisa dihitung.'
};
const KELAS_JAM = [
  { nama: 'Malam–dini hari', rentang: '18:00–05:59', css: 'var(--as-s1)' },
  { nama: 'Pagi', rentang: '06:00–10:59', css: 'var(--as-s3)' },
  { nama: 'Siang–sore', rentang: '11:00–17:59', css: 'var(--as-s2)' }
];
const RAMP = ['#fcffa4', '#f98e09', '#bc3754', '#57106e'];
const NAMA_KELAS = { '30': 'K1 (fuel 30)', '31': 'K2 (fuel 31)', '32': 'K3 (fuel 32)', '33': 'K4 (fuel 33)', lain: 'Lain (hutan, semak)' };

let ST = null, ASAL = null, HS = null, HS_EV = {};
let siap = null, peta = null, lapis = {}, legenda = null, pilihan = null, nomorPilih = 0;
const DETAIL = {};
const S = { dari: '', sampai: '', mutu: 'bs', letak: 'semua', resort: '', luas: 0 };
let urut = { kol: 'deteksi_awal', arah: -1 };

/* ---------- utilitas waktu (ISO lokal WITA tanpa zona) ---------- */
function ms(iso) { return Date.parse(String(iso).slice(0, 16) + ':00Z'); }
function jamAntara(a, b) { return (ms(b) - ms(a)) / 3.6e6; }
function isoDari(t) { return new Date(t).toISOString().slice(0, 16); }
function jam(iso) { return iso ? String(iso).slice(11, 16) : '–'; }
function tgl(iso) { if (!iso) return '–'; const s = String(iso); return (+s.slice(8, 10)) + ' ' + BULAN[+s.slice(5, 7) - 1] + ' ' + s.slice(0, 4); }
function tglPendek(iso) { const s = String(iso); return (+s.slice(8, 10)) + ' ' + BULAN[+s.slice(5, 7) - 1]; }
function tglJam(iso) { return iso ? tgl(iso) + ' ' + jam(iso) : '–'; }
function durasi(h) {
  if (h == null || !isFinite(h)) return '–';
  const a = Math.abs(h);
  if (a < 1) return Math.round(a * 60) + ' menit';
  return angka(a, a < 10 ? 1 : 0) + ' jam';
}
function mulaiDari(e) { return e.t_mulai_p50 || e.t_mulai; }
function kelasJam(iso) { const h = +String(iso).slice(11, 13); return (h >= 18 || h < 6) ? 0 : (h < 11 ? 1 : 2); }
function arahIdx(deg) { return Math.round((((deg % 360) + 360) % 360) / 45) % 8; }
function datangIdx(e) { return e.arah_rambat == null ? -1 : arahIdx(e.arah_rambat + 180); }
function css(nama) { return getComputedStyle(document.documentElement).getPropertyValue(nama).trim(); }
function warnaRamp(f) {
  f = Math.max(0, Math.min(1, f));
  const x = f * (RAMP.length - 1), i = Math.min(Math.floor(x), RAMP.length - 2), u = x - i;
  const a = RAMP[i], b = RAMP[i + 1];
  const c = [1, 3, 5].map((k) => Math.round(parseInt(a.substr(k, 2), 16) * (1 - u) + parseInt(b.substr(k, 2), 16) * u));
  return '#' + c.map((v) => v.toString(16).padStart(2, '0')).join('');
}
function median(a) { if (!a.length) return NaN; const b = a.slice().sort((x, y) => x - y), m = b.length >> 1; return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2; }
function kuantil(a, q) { if (!a.length) return NaN; const b = a.slice().sort((x, y) => x - y), p = (b.length - 1) * q, i = Math.floor(p); return b[i] + (b[Math.min(i + 1, b.length - 1)] - b[i]) * (p - i); }
function langkahRapi(maks, n) {
  const kasar = maks / Math.max(n, 1), p = Math.pow(10, Math.floor(Math.log10(kasar || 1))), r = kasar / p;
  return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 2.5 ? 2.5 : r <= 5 ? 5 : 10) * p;
}
function lencanaMutu(m) { return '<span class="mutu ' + esc(m || 'rendah') + '" title="' + esc(KET_MUTU[m] || '') + '">' + esc(NAMA_MUTU[m] || m || '–') + '</span>'; }
function versi() { return ST && ST.dibuat ? '?v=' + encodeURIComponent(ST.dibuat) : ''; }

/* ---------- muat data ---------- */
function ambilJSON(url, opsi) { return fetch(url, opsi).then((r) => { if (!r.ok) throw new Error(r.status === 404 ? 'belum ada' : 'HTTP ' + r.status); return r.json(); }); }
function muat() {
  return ambilJSON('status/asal_api.json', { cache: 'no-cache' }).then((st) => {
    ST = st;
    return Promise.all([
      ambilJSON('data/asal_api/peta.geojson' + versi()).catch(() => ({ features: [] })),
      ambilJSON('data/asal_api/titik_panas.geojson' + versi()).catch(() => ({ features: [] })),
      fetch('data/areal_terbakar.geojson').then((r) => r.ok ? r.json() : null).catch(() => null),
      T.siapData
    ]);
  }).then(([pg, hs, areal]) => {
    ASAL = pg; HS = hs; lapis.arealData = areal;
    HS_EV = {};
    (HS.features || []).forEach((f) => { const ev = f.properties.ev; if (ev) (HS_EV[ev] = HS_EV[ev] || []).push(f); });
    siapkanSaring();
    $('asInfo').innerHTML = infoData();
  });
}
function infoData() {
  const d = ST.data || {}, th = d.titik_panas || {}, r = th.rentang_wita || [];
  const sumber = (th.impor || []).map((i) => i.file).concat(th.firms ? ['NASA FIRMS'] : []);
  return 'Diperbarui <b>' + esc(T.tglID(T.keWITA(ST.dibuat), true)) + ' WITA</b><br>' +
    'Titik panas ' + esc(r.length ? tgl(r[0]) + ' – ' + tgl(r[1]) : '–') + (sumber.length ? ' (' + esc(sumber.join(', ')) + ')' : '') + '<br>' +
    'Cuaca: ' + esc(d.cuaca || '–') + '<br>Bahan bakar: ' + esc((d.fmd || '').split('/').pop()) + ', ' + esc((d.fms || '').split('/').pop());
}

/* ---------- penyaring ---------- */
function siapkanSaring() {
  const bulan = Array.from(new Set((ST.kejadian || []).map((e) => e.deteksi_awal.slice(0, 7)))).sort();
  const opsi = bulan.map((b) => '<option value="' + b + '">' + BULAN_PANJANG[+b.slice(5, 7) - 1] + ' ' + b.slice(0, 4) + '</option>').join('');
  $('asDari').innerHTML = opsi; $('asSampai').innerHTML = opsi;
  S.dari = bulan[0] || ''; S.sampai = bulan[bulan.length - 1] || '';
  $('asDari').value = S.dari; $('asSampai').value = S.sampai;
  const resort = Array.from(new Set((ST.kejadian || []).map((e) => e.resort).filter(Boolean))).sort();
  $('asResort').innerHTML = '<option value="">Semua resort</option>' + resort.map((r) => '<option>' + esc(r) + '</option>').join('') +
    '<option value="-">Di luar resort</option>';
  if (!(ST.kejadian || []).some((e) => e.mutu === 'baik' || e.mutu === 'sedang')) S.mutu = 'semua';
  $('asMutu').value = S.mutu;
  const ubah = () => {
    S.dari = $('asDari').value; S.sampai = $('asSampai').value;
    if (S.sampai < S.dari) { S.sampai = S.dari; $('asSampai').value = S.dari; }
    S.mutu = $('asMutu').value; S.letak = $('asLetak').value; S.resort = $('asResort').value; S.luas = +$('asLuas').value;
    gambarSemua();
  };
  ['asDari', 'asSampai', 'asMutu', 'asLetak', 'asResort', 'asLuas'].forEach((id) => $(id).addEventListener('change', ubah));
  $('asReset').addEventListener('click', () => {
    $('asDari').value = bulan[0] || ''; $('asSampai').value = bulan[bulan.length - 1] || '';
    $('asMutu').value = (ST.kejadian || []).some((e) => e.mutu === 'baik' || e.mutu === 'sedang') ? 'bs' : 'semua';
    $('asLetak').value = 'semua'; $('asResort').value = ''; $('asLuas').value = '0';
    ubah();
  });
  $('asWarna').addEventListener('change', () => { gambarAsal(); gambarLegenda(); });
}
function lolos(e) {
  const b = e.deteksi_awal.slice(0, 7);
  if (S.dari && b < S.dari) return false;
  if (S.sampai && b > S.sampai) return false;
  if (S.luas && !(e.luas_ha >= S.luas)) return false;
  if (S.resort === '-' && e.resort) return false;
  if (S.resort && S.resort !== '-' && e.resort !== S.resort) return false;
  if (S.mutu === 'bs' && !(e.mutu === 'baik' || e.mutu === 'sedang')) return false;
  if (S.mutu === 'baik' && e.mutu !== 'baik') return false;
  if (S.letak !== 'semua') {
    if (!e.t_mulai || !e.asal) return false;
    if ((S.letak === 'dalam') !== !!e.asal.di_tn) return false;
  }
  return true;
}
function tersaring() { return (ST.kejadian || []).filter(lolos); }
function berhasil(list) { return list.filter((e) => e.t_mulai && e.asal); }

/* ---------- buka tab ---------- */
export function buka(arg) {
  if (!siap) {
    siap = muat().then(() => { siapkanPeta(); gambarSemua(); }).catch((e) => {
      siap = null;
      const belum = String(e && e.message).indexOf('belum ada') >= 0;
      $('asInfo').innerHTML = belum
        ? '<b>Hasil belum tersedia.</b> Workflow <i>Asal api</i> di GitHub Actions menghitungnya setiap hari pukul 06:40 WITA dan setiap ada delineasi areal terbakar baru.'
        : '<span class="err-teks">Hasil tidak bisa dimuat (' + esc(e && e.message ? e.message : e) + ').</span>';
      $('asTabel').innerHTML = '<div class="riwayat-kosong">Belum ada hasil pemodelan terbalik.</div>';
      $('asKalibrasi').textContent = 'Belum ada hasil.';
      throw e;
    });
  }
  siap.then(() => {
    setTimeout(() => peta && peta.invalidateSize(), 60);
    if (arg && (!pilihan || pilihan.id !== arg)) pilih(arg, { gulir: true });
  }).catch(() => {});
}

function gambarSemua() {
  const list = tersaring();
  $('asJumlah').textContent = list.length + ' kejadian · ' + angka(list.reduce((s, e) => s + (e.luas_ha || 0), 0)) + ' ha';
  gambarKpi(list);
  gambarAsal();
  gambarTitikPanas(list);
  gambarJam(list);
  gambarMawar(list);
  gambarTabel(list);
  gambarKalibrasi();
  gambarMetode();
  gambarLegenda();
}

/* ---------- KPI ---------- */
function jendelaRawan(v, lebar) {
  let best = -1, mulai = 0;
  for (let h = 0; h < 24; h++) {
    let s = 0;
    for (let k = 0; k < lebar; k++) s += v[(h + k) % 24];
    if (s > best) { best = s; mulai = h; }
  }
  return { mulai, jumlah: best };
}
function jamRawan(list) {
  const v = new Array(24).fill(0), det = new Array(24).fill(0);
  list.forEach((e) => {
    if (e.jam_hari) e.jam_hari.forEach((p, h) => { v[h] += p; });
    if (e.t_mulai) det[+e.deteksi_awal.slice(11, 13)] += 1;
  });
  return { v, det };
}
function teksJendela(w) { const a = String(w.mulai).padStart(2, '0'), b = String((w.mulai + 3) % 24).padStart(2, '0'); return a + ':00–' + b + ':00'; }
function gambarKpi(list) {
  const ok = berhasil(list);
  const bs = ok.filter((e) => e.mutu === 'baik' || e.mutu === 'sedang').length;
  $('asKpiN').textContent = angka(ok.length);
  $('asKpiNSub').textContent = angka(ok.reduce((s, e) => s + e.luas_ha, 0)) + ' ha · ' + bs + ' mutu baik/sedang' + (list.length > ok.length ? ' · ' + (list.length - ok.length) + ' tidak terhitung' : '');
  const jr = jamRawan(ok), tot = jr.v.reduce((a, b) => a + b, 0);
  if (tot > 0) {
    const w = jendelaRawan(jr.v, 3);
    $('asKpiJam').textContent = teksJendela(w);
    $('asKpiJamSub').textContent = angka(100 * w.jumlah / tot) + '% api mulai di rentang 3 jam ini (WITA)';
  } else { $('asKpiJam').textContent = '–'; $('asKpiJamSub').textContent = 'belum ada kejadian'; }
  const jeda = ok.map((e) => jamAntara(mulaiDari(e), e.deteksi_awal)).filter(isFinite);
  $('asKpiJeda').textContent = jeda.length ? durasi(median(jeda)) : '–';
  $('asKpiJedaSub').textContent = jeda.length ? 'median selisih ke deteksi satelit pertama (separuh kejadian ' + durasi(kuantil(jeda, 0.25)) + '–' + durasi(kuantil(jeda, 0.75)) + ')' : '–';
  const luar = ok.filter((e) => !e.asal.di_tn), masuk = luar.filter((e) => e.masuk_tn);
  $('asKpiLuar').textContent = ok.length ? luar.length + ' dari ' + ok.length : '–';
  const pintu = {};
  masuk.forEach((e) => { const r = e.masuk_tn.resort || 'luar resort'; pintu[r] = (pintu[r] || 0) + 1; });
  const top = Object.keys(pintu).sort((a, b) => pintu[b] - pintu[a])[0];
  $('asKpiLuarSub').textContent = ok.length ? angka(100 * luar.length / ok.length) + '% · ' + masuk.length + ' merambat masuk kawasan' + (top ? ', terbanyak lewat ' + top : '') : '–';
  const kal = ST.kalibrasi || {};
  if (kal.cukup) {
    $('asKpiK').textContent = '×' + angka(kal.global_faktor, 2);
    $('asKpiKSub').textContent = 'laju api nyata dibanding fmd20, dari ' + kal.n + ' kejadian (90%: ' + angka(kal.global_p05, 2) + '–' + angka(kal.global_p95, 2) + ')';
  } else {
    $('asKpiK').textContent = '–';
    $('asKpiKSub').textContent = 'kalibrasi belum cukup kejadian (' + (kal.n || 0) + ' dari minimal 3)';
  }
}

/* ---------- peta ---------- */
function siapkanPeta() {
  if (peta) return;
  peta = L.map('asPeta', { zoomSnap: 0.5 }).fitBounds(AOI);
  [['asPArea', 405], ['asPZona', 412], ['asPBatas', 418], ['asPJalur', 422], ['asPIso', 430], ['asPTitik', 440]].forEach((p) => { peta.createPane(p[0]).style.zIndex = p[1]; });
  const citra = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Citra © Esri' }).addTo(peta);
  const topo = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', { maxZoom: 17, attribution: '© OpenTopoMap, © OpenStreetMap' });
  const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' });
  const kontrol = L.control.layers({ 'Citra satelit': citra, 'Topografi': topo, 'OpenStreetMap': osm }, {}, { collapsed: window.innerWidth < 1080 }).addTo(peta);
  lapis.kanvas = L.canvas({ padding: 0.3, pane: 'asPTitik' });
  if (lapis.arealData) {
    lapis.areal = L.geoJSON(lapis.arealData, { pane: 'asPArea', interactive: false, style: { color: '#ff5a36', weight: 1, opacity: 0.85, fillColor: '#ff5a36', fillOpacity: 0.16 } }).addTo(peta);
    kontrol.addOverlay(lapis.areal, 'Areal terbakar (delineasi)');
  }
  lapis.titik = L.layerGroup().addTo(peta);
  kontrol.addOverlay(lapis.titik, 'Titik panas');
  lapis.asal = L.layerGroup().addTo(peta);
  kontrol.addOverlay(lapis.asal, 'Titik asal dan arah rambat');
  lapis.pilih = L.layerGroup().addTo(peta);
  const D = T.DATA;
  if (D.batas_tn) {
    const tn = L.layerGroup([
      L.geoJSON(D.batas_tn, { pane: 'asPBatas', interactive: false, style: { color: '#ffffff', weight: 4.5, opacity: 0.5, fill: false } }),
      L.geoJSON(D.batas_tn, { pane: 'asPBatas', interactive: false, style: { color: '#0b6b35', weight: 2.4, fill: false } })
    ]).addTo(peta);
    kontrol.addOverlay(tn, 'Batas TN Tambora');
  }
  if (D.batas_resort) {
    const rs = L.layerGroup();
    L.geoJSON(D.batas_resort, { pane: 'asPBatas', interactive: false, style: (f) => {
      const c = (T.WARNA_RESORT || {})[f.properties.kode] || '#ffffff';
      return { color: c, weight: 1.6, dashArray: '7 4', fill: false };
    } }).addTo(rs);
    D.batas_resort.features.forEach((f) => {
      if (f.properties.label) L.tooltip(f.properties.label, { content: esc(f.properties.nama), permanent: true, direction: 'center', className: 'lbl-resort' }).addTo(rs);
    });
    rs.addTo(peta);
    kontrol.addOverlay(rs, 'Batas resort');
  }
  if (D.jalur_patroli) {
    const G = T.GAYA_JALUR || {};
    const jl = L.geoJSON(D.jalur_patroli, { pane: 'asPJalur', style: (f) => L.extend({ opacity: 0.9 }, G[f.properties.tipe] || { color: '#ffffff', weight: 2 }),
      onEachFeature: (f, l) => l.bindTooltip(esc(f.properties.nama) + ' <span class="muted">(' + esc(f.properties.tipe) + ')</span>', { sticky: true }) });
    kontrol.addOverlay(jl, 'Jalur patroli &amp; jalan');
  }
  if (D.grid_1km) {
    const gr = L.geoJSON(D.grid_1km, { pane: 'asPArea', interactive: false, style: { color: '#ffffff', weight: 0.6, opacity: 0.6, fill: false } });
    kontrol.addOverlay(gr, 'Grid 1 km');
  }
  legenda = L.control({ position: 'bottomleft' });
  legenda.onAdd = () => { const e = L.DomUtil.create('div', 'legenda as-leg'); L.DomEvent.disableClickPropagation(e); L.DomEvent.disableScrollPropagation(e); return e; };
  legenda.addTo(peta);
}
function ukuranIkon(ha) { return Math.round(Math.max(20, Math.min(40, 16 + 6 * Math.log10(Math.max(ha, 1)) * 2))); }
function ikonAsal(e, dipilih) {
  const ukur = ukuranIkon(e.luas_ha) + (dipilih ? 6 : 0), c = ukur / 2, r = Math.max(5, ukur * 0.24);
  const modeMutu = $('asWarna').value === 'mutu';
  const warna = modeMutu ? ({ baik: 'var(--ok)', sedang: 'var(--warn)' }[e.mutu] || '#9e9e9e') : KELAS_JAM[kelasJam(mulaiDari(e))].css;
  const lemah = e.mutu === 'rendah';
  let panah = '';
  if (e.arah_rambat != null) {
    const u = ukur * 0.5 - 1;
    panah = '<g transform="rotate(' + e.arah_rambat + ' ' + c + ' ' + c + ')">' +
      '<path d="M' + c + ' ' + (c - u) + 'L' + (c + 5.5) + ' ' + (c - u + 8) + 'L' + (c + 1.6) + ' ' + (c - u + 7) + 'L' + (c + 1.6) + ' ' + (c - r) +
      'L' + (c - 1.6) + ' ' + (c - r) + 'L' + (c - 1.6) + ' ' + (c - u + 7) + 'L' + (c - 5.5) + ' ' + (c - u + 8) + 'Z" fill="#fff" stroke="#1d2420" stroke-width="1.1" stroke-linejoin="round"/></g>';
  }
  const html = '<svg width="' + ukur + '" height="' + ukur + '" viewBox="0 0 ' + ukur + ' ' + ukur + '" aria-hidden="true">' + panah +
    '<circle cx="' + c + '" cy="' + c + '" r="' + r + '" style="fill:' + (lemah ? '#fff' : warna) + ';stroke:' + (lemah ? warna : '#fff') + '" stroke-width="' + (dipilih ? 3 : 2) + '"/>' +
    (dipilih ? '<circle cx="' + c + '" cy="' + c + '" r="' + (r + 3.2) + '" fill="none" stroke="#1d2420" stroke-width="1.6"/>' : '') + '</svg>';
  return L.divIcon({ className: 'as-ikon', iconSize: [ukur, ukur], iconAnchor: [c, c], html });
}
function gambarAsal() {
  if (!peta) return;
  lapis.asal.clearLayers();
  lapis.penanda = {};
  const list = berhasil(tersaring()).sort((a, b) => b.luas_ha - a.luas_ha);
  list.forEach((e) => {
    const dip = pilihan && pilihan.id === e.id;
    const m = L.marker([e.asal.lat, e.asal.lon], { icon: ikonAsal(e, dip), pane: 'asPTitik', zIndexOffset: dip ? 1000 : 0, keyboard: true, title: 'Kejadian ' + tglPendek(e.deteksi_awal) });
    m.bindTooltip('<b>' + esc(tgl(e.deteksi_awal)) + '</b> · ' + esc(e.resort || 'luar resort') + '<br>Api mulai ±' + esc(jam(mulaiDari(e))) + ' WITA · ' + angka(e.luas_ha) + ' ha' +
      (e.api_datang_dari ? '<br>Api datang dari ' + esc(e.api_datang_dari) : ''), { direction: 'top', offset: [0, -8] });
    m.on('click', (ev) => { L.DomEvent.stopPropagation(ev); pilih(e.id, { gulir: false, dariPeta: true }); });
    m.addTo(lapis.asal);
    lapis.penanda[e.id] = m;
  });
}
function gambarTitikPanas(list) {
  if (!peta) return;
  lapis.titik.clearLayers();
  const ids = new Set(list.map((e) => e.id));
  const warna = '#ff3d00';
  (HS.features || []).forEach((f) => {
    const p = f.properties;
    if (!p.ev || !ids.has(p.ev)) return;
    if (pilihan && p.ev === pilihan.id) return;
    const c = f.geometry.coordinates;
    L.circleMarker([c[1], c[0]], { renderer: lapis.kanvas, radius: 3, weight: 0.8, color: '#ffffff', fillColor: warna, fillOpacity: 0.85, interactive: false }).addTo(lapis.titik);
  });
}
function gambarLegenda() {
  if (!legenda) return;
  const e = legenda.getContainer();
  const modeMutu = $('asWarna').value === 'mutu';
  let h = '<details' + (window.innerWidth >= 1080 ? ' open' : '') + '><summary><b style="display:inline">Keterangan</b></summary>';
  h += '<div class="as-leg-judul">Titik asal (warna = ' + (modeMutu ? 'mutu dugaan' : 'jam api mulai, WITA') + ')</div>';
  if (modeMutu) {
    h += [['baik', 'var(--ok)'], ['sedang', 'var(--warn)']].map((m) => '<div><span class="as-bulat" style="background:' + m[1] + '"></span>Mutu ' + NAMA_MUTU[m[0]].toLowerCase() + '</div>').join('');
    h += '<div><span class="as-bulat kosong" style="border-color:#9e9e9e"></span>Mutu rendah</div>';
  } else {
    h += KELAS_JAM.map((k) => '<div><span class="as-bulat" style="background:' + k.css + '"></span>' + k.nama + ' ' + k.rentang + '</div>').join('');
    h += '<div><span class="as-bulat kosong" style="border-color:var(--muted)"></span>Lingkaran kosong = mutu rendah</div>';
  }
  h += '<div class="as-leg-panah"><svg width="22" height="14" viewBox="0 0 22 14" aria-hidden="true"><path d="M2 7h12M14 2l6 5-6 5z" fill="#fff" stroke="#1d2420" stroke-width="1.1"/></svg>Arah api merambat dari titik asal</div>';
  h += '<div><span class="kot" style="border-color:#ff5a36;background:rgba(255,90,54,.25)"></span>Areal terbakar (delineasi)</div>';
  h += '<div><span class="as-bulat kecil" style="background:#ff3d00"></span>Titik panas satelit</div>';
  if (pilihan && pilihan.det) {
    const d = pilihan.det, iso = d.isokron || [];
    h += '<div class="as-leg-judul">Kejadian ' + esc(tgl(pilihan.e.deteksi_awal)) + '</div>';
    h += '<div><span class="kot" style="border-color:#ff6f00;background:rgba(255,143,0,.5)"></span>Zona asal peluang 50%</div>';
    h += '<div><span class="kot" style="border-color:#ffca28;background:rgba(255,224,130,.35)"></span>Zona asal peluang 90%</div>';
    if (iso.length) {
      h += '<div>Garis waktu rambat (simulasi terbaik)</div><div class="as-ramp"></div><div class="as-ramp-label"><span>' + esc(jam(pilihan.e.t_mulai)) + '</span><span>' + esc(tglPendek(iso[iso.length - 1].t) + ' ' + jam(iso[iso.length - 1].t)) + '</span></div>';
      h += '<div class="small muted">Titik panas kejadian ini diwarnai dengan skala yang sama menurut jam terdeteksi.</div>';
    }
    if (pilihan.e.masuk_tn) h += '<div><span class="as-belah"></span>Api masuk kawasan TN</div>';
  }
  e.innerHTML = h + '</details>';
}

/* ---------- pilih kejadian ---------- */
function muatDetail(id) {
  if (!DETAIL[id]) DETAIL[id] = ambilJSON('data/asal_api/k/' + encodeURIComponent(id) + '.json' + versi()).catch((err) => { delete DETAIL[id]; throw err; });
  return DETAIL[id];
}
function pilih(id, opsi) {
  opsi = opsi || {};
  const e = (ST.kejadian || []).find((k) => k.id === id);
  if (!e) return;
  const nomor = ++nomorPilih;
  pilihan = { id, e, det: null };
  T.setHash('asal/' + id);
  tandaiBaris();
  gambarAsal();
  gambarTitikPanas(tersaring());
  lapis.pilih.clearLayers();
  const kartu = $('asDetail');
  kartu.hidden = false;
  kartu.innerHTML = '<p class="muted">Memuat detail kejadian…</p>';
  if (!e.t_mulai) { gambarDetail(e, null); if (opsi.gulir) kartu.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
  muatDetail(id).then((det) => {
    if (nomor !== nomorPilih) return;
    pilihan.det = det;
    gambarPilihan(e, det, opsi);
    gambarDetail(e, det);
    gambarLegenda();
    if (opsi.gulir) kartu.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }).catch((err) => {
    if (nomor !== nomorPilih) return;
    kartu.innerHTML = '<p class="err-teks">Detail kejadian tidak bisa dimuat (' + esc(err.message) + ').</p>';
  });
}
function lepasPilihan() {
  pilihan = null; nomorPilih++;
  lapis.pilih.clearLayers();
  $('asDetail').hidden = true;
  T.setHash('asal');
  tandaiBaris(); gambarAsal(); gambarTitikPanas(tersaring()); gambarLegenda();
}
function gambarPilihan(e, det, opsi) {
  const g = lapis.pilih;
  g.clearLayers();
  const t0 = ms(e.t_mulai), iso = det.isokron || [];
  const tAkhir = iso.length ? ms(iso[iso.length - 1].t) : t0 + 6 * 3.6e6;
  const skala = (iso_) => warnaRamp((ms(iso_) - t0) / Math.max(tAkhir - t0, 3.6e6));
  let batas = null;
  if (det.areal) {
    const a = L.geoJSON(det.areal, { pane: 'asPZona', interactive: false, style: { color: '#ff1744', weight: 2.2, opacity: 1, fillColor: '#ff1744', fillOpacity: 0.06 } }).addTo(g);
    batas = a.getBounds();
  }
  if (det.z90) L.geoJSON(det.z90, { pane: 'asPZona', interactive: false, style: { color: '#ffca28', weight: 1.6, fillColor: '#ffe082', fillOpacity: 0.35 } }).addTo(g);
  if (det.z50) L.geoJSON(det.z50, { pane: 'asPZona', interactive: false, style: { color: '#ff6f00', weight: 1.8, fillColor: '#ff8f00', fillOpacity: 0.5 } }).addTo(g);
  iso.forEach((i) => {
    L.geoJSON(i.g, { pane: 'asPIso', bubblingMouseEvents: false, style: { color: skala(i.t), weight: 2, opacity: 0.95 } })
      .bindTooltip('Muka api pukul ' + esc(jam(i.t)) + ' WITA (' + esc(tglPendek(i.t)) + ')', { sticky: true }).addTo(g);
  });
  (HS_EV[e.id] || []).forEach((f) => {
    const p = f.properties, c = f.geometry.coordinates;
    L.circleMarker([c[1], c[0]], { pane: 'asPTitik', radius: 5.5, weight: 1.6, color: '#ffffff', fillColor: skala(p.t), fillOpacity: 1, bubblingMouseEvents: false })
      .bindTooltip('<b>' + esc(tglJam(p.t)) + ' WITA</b><br>' + esc(p.s) + ' · keyakinan ' + esc({ H: 'tinggi', M: 'sedang', L: 'rendah' }[p.c] || p.c) +
        (p.desa ? '<br>Desa ' + esc(p.desa) : ''), { direction: 'top' }).addTo(g);
  });
  if (e.masuk_tn) {
    L.marker([e.masuk_tn.lat, e.masuk_tn.lon], { pane: 'asPTitik', icon: L.divIcon({ className: 'as-ikon', iconSize: [18, 18], iconAnchor: [9, 9],
      html: '<span class="as-belah besar"></span>' }) })
      .bindTooltip('Api masuk kawasan TN sekitar pukul ' + esc(jam(e.masuk_tn.t)) + ' WITA' + (e.masuk_tn.resort ? ' (' + esc(e.masuk_tn.resort) + ')' : ''), { direction: 'top' }).addTo(g);
  }
  if (det.z90) { const z = L.geoJSON(det.z90).getBounds(); batas = batas ? batas.extend(z) : z; }
  if (batas && batas.isValid() && !opsi.tanpaZoom) peta.flyToBounds(batas.pad(0.25), { maxZoom: 15, duration: 0.6 });
}
function tandaiBaris() {
  document.querySelectorAll('#asTabel tbody tr').forEach((tr) => tr.classList.toggle('dipilih', !!pilihan && tr.dataset.id === pilihan.id));
}

/* ---------- detail kejadian ---------- */
function fakta(judul, nilai, sub, api) { return '<div' + (api ? ' class="api"' : '') + '><span>' + judul + '</span><b>' + nilai + '</b>' + (sub ? '<small>' + sub + '</small>' : '') + '</div>'; }
function teksJalur(j) {
  if (!j) return '';
  const k = Object.keys(j);
  if (!k.length) return '';
  const a = j[k[0]];
  return angka(a.jarak_m / 1000, a.jarak_m < 10000 ? 1 : 0) + ' km dari ' + esc(k[0].toLowerCase()) + ' ' + esc(a.nama);
}
function teksK(e) {
  if (e.k_p50 == null) return '';
  const k = e.k_p50;
  const beda = Math.round(Math.abs(k - 1) * 100);
  return k > 1.08 ? 'api nyata ±' + beda + '% lebih cepat dari simulasi fmd20' : (k < 0.92 ? 'api nyata ±' + beda + '% lebih lambat dari simulasi fmd20' : 'laju api nyata ≈ simulasi fmd20');
}
function gambarDetail(e, det) {
  const kartu = $('asDetail');
  const sensor = Object.keys(e.sensor || {}).map((s) => s + ' ' + e.sensor[s]).join(', ');
  let h = '<div class="as-detail-kepala"><div><h2>Kejadian ' + esc(tgl(e.deteksi_awal)) + (e.resort ? ' · ' + esc(e.resort) : '') + (e.desa ? ' · Desa ' + esc(e.desa) : '') + '</h2>' +
    '<p class="small muted">' + esc(e.id) + ' · ' + angka(e.luas_ha, 1) + ' ha areal terbakar (' + angka(e.tn_persen) + '% di kawasan TN) · ' + e.n_titik + ' titik panas di ' + e.n_lintasan + ' lintasan satelit (' + esc(sensor) + ')</p></div>' +
    '<div class="aksi-baris">' + lencanaMutu(e.mutu) + '<button class="garis kecil" id="asTutup" aria-label="Tutup detail">Tutup</button></div></div>';
  if (!e.t_mulai) {
    kartu.innerHTML = h + '<p class="pesan warn">' + esc(e.catatan || 'Kejadian ini tidak bisa dihitung.') + '</p>';
    $('asTutup').addEventListener('click', lepasPilihan);
    return;
  }
  const a = e.asal, lok = T.lokasi ? T.lokasi(a.lat, a.lon) : null;
  const jeda = jamAntara(mulaiDari(e), e.deteksi_awal);
  const cm = e.cuaca_mulai || {};
  const NF = T.NAMA_FUEL || {};
  h += '<div class="as-fakta">' +
    fakta('Perkiraan api mulai', esc(tglPendek(mulaiDari(e)) + ' · ' + jam(mulaiDari(e))) + ' WITA',
      'selang 90%: ' + esc(tglJam(e.t_mulai_p05)) + ' s.d. ' + esc(e.t_mulai_p95.slice(0, 10) === e.t_mulai_p05.slice(0, 10) ? jam(e.t_mulai_p95) : tglJam(e.t_mulai_p95)) +
      '<br>simulasi paling cocok: ' + esc(jam(e.t_mulai)), true) +
    fakta('Terdeteksi satelit pertama', esc(jam(e.deteksi_awal)) + ' WITA', durasi(jeda) + ' setelah perkiraan api mulai') +
    fakta('Titik asal', esc(a.lat.toFixed(5) + ', ' + a.lon.toFixed(5)),
      (a.di_tn ? 'di dalam kawasan TN' : 'di luar kawasan TN') + (lok && lok.resort ? ' · ' + esc(lok.resort.nama) : '') + (lok && lok.grid ? ' · Grid ' + esc(lok.grid.id) : '') +
      '<br>' + angka(a.elev) + ' mdpl · ' + esc(NF[a.fuel] || ('fuel ' + a.fuel)) +
      (teksJalur(a.jalur) ? '<br>' + teksJalur(a.jalur) : '')) +
    fakta('Arah api', e.api_datang_dari ? 'datang dari ' + esc(e.api_datang_dari) : '–',
      e.arah_rambat != null ? 'merambat ke ' + esc(e.arah_rambat_teks) + ' (' + e.arah_rambat + '°), ' + angka(e.jarak_ke_pusat_m / 1000, 1) + ' km dari titik asal ke pusat areal' : '') +
    (e.masuk_tn ? fakta('Masuk kawasan TN', 'pukul ' + esc(jam(e.masuk_tn.t)) + ' WITA', esc(tglPendek(e.masuk_tn.t)) + (e.masuk_tn.resort ? ' · ' + esc(e.masuk_tn.resort) : '') + ' · dari arah ' + esc(MATA[arahIdx(azimutLL(e.masuk_tn, a))])) : '') +
    fakta('Faktor laju api (k)', '×' + angka(e.k_p50, 2), 'selang 90%: ' + angka(e.k_p05, 2) + '–' + angka(e.k_p95, 2) + '<br>' + esc(teksK(e))) +
    fakta('Kecocokan simulasi', 'Sørensen ' + angka(e.sorensen, 2),
      'galat waktu ' + (e.galat_waktu_jam == null ? '–' : durasi(e.galat_waktu_jam)) + ' · ' + angka(e.titik_cocok_persen) + '% titik panas pas' + (e.titik_tak_tercapai ? ' · ' + e.titik_tak_tercapai + ' tidak terjangkau' : '')) +
    fakta('Cuaca saat api mulai', angka(cm.ws) + ' km/j dari ' + esc(cm.wd != null ? MATA[arahIdx(cm.wd)] : '–'),
      'suhu ' + angka(cm.suhu) + '°C · RH ' + angka(cm.rh) + '% · kadar air 1-jam ' + angka(cm.m1, 1) + '%') +
    '</div>';
  h += '<h3>Kapan api mulai</h3><div class="as-legenda" id="asWaktuLeg"></div><div class="as-grafik as-waktu" id="asWaktu" role="img" aria-label="Sebaran perkiraan jam api mulai dan lintasan satelit"></div>';
  h += '<div class="aksi-baris as-aksi">' +
    '<button id="asSim">Simulasikan dari titik asal ini</button>' +
    '<a class="tombol garis" href="data/asal_api/wxs/' + encodeURIComponent(e.id) + '.wxs" download="cuaca_' + esc(e.id) + '.wxs">Unduh cuaca .wxs</a>' +
    '<button class="garis" id="asSalin">Salin koordinat</button>' +
    '<button class="garis" id="asKePeta">Lihat di peta</button></div>' +
    '<p class="petunjuk" id="asSimInfo">Membuka tab Simulasi dengan titik asal dan jam mulai ini (FARSITE, cuaca otomatis). Bandingkan hasilnya dengan areal terbakar.</p>';
  h += '<details><summary>Titik panas yang dipakai untuk menilai waktu (' + (det && det.residu ? det.residu.length : 0) + ')</summary><div class="tw">' + tabelResidu(det) + '</div></details>';
  h += '<details><summary>Cuaca per jam yang dipakai (ERA5)</summary><div class="tabel-cuaca">' + tabelCuaca(det, e) + '</div></details>';
  h += '<details><summary>Komposisi bahan bakar areal kejadian</summary><div class="tw">' + tabelKomposisi(e) + '</div></details>';
  kartu.innerHTML = h;
  $('asTutup').addEventListener('click', lepasPilihan);
  $('asKePeta').addEventListener('click', () => { $('asPeta').scrollIntoView({ behavior: 'smooth', block: 'center' }); if (det) gambarPilihan(e, det, {}); });
  $('asSalin').addEventListener('click', () => {
    const t = a.lat.toFixed(5) + ',' + a.lon.toFixed(5);
    (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).then(() => { $('asSalin').textContent = 'Tersalin'; })
      .catch(() => { window.prompt('Salin koordinat:', t); });
  });
  $('asSim').addEventListener('click', () => {
    const m = mulaiDari(e).slice(0, 13) + ':00';
    const lama = Math.max(12, Math.min(72, Math.ceil(jamAntara(m, e.deteksi_akhir)) + 2));
    T.isiSimulasi({ lat: a.lat, lon: a.lon, mulai: m, lama,
      pesan: 'Titik dan jam mulai diisi dari hasil pemodelan terbalik kejadian ' + tgl(e.deteksi_awal) + '. Periksa cuaca, lalu klik Jalankan simulasi.' });
  });
  gambarWaktu(e, det);
}
function azimutLL(dari, ke) {
  const y = (ke.lat - dari.lat) * 110.6, x = (ke.lon - dari.lon) * 111.3 * Math.cos(dari.lat * Math.PI / 180);
  return ((Math.atan2(x, y) * 180 / Math.PI) + 360) % 360;
}
function tabelResidu(det) {
  const r = (det && det.residu) || [];
  if (!r.length) return '<p class="muted small">Tidak ada.</p>';
  return '<table><thead><tr><th>Terdeteksi (WITA)</th><th class="kiri">Satelit</th><th>Api di piksel menurut simulasi</th><th>Selisih</th></tr></thead><tbody>' +
    r.map((x) => '<tr><td>' + esc(tglJam(x.t)) + '</td><td class="kiri">' + esc(x.s) + '</td><td>' +
      (x.masuk ? esc(jam(x.masuk)) + ' – ' + esc(x.padam ? jam(x.padam) : '…') : 'tidak terjangkau') + '</td><td>' +
      (x.galat_jam == null ? '–' : (Math.abs(x.galat_jam) < 0.01 ? 'pas' : (x.galat_jam < 0 ? 'simulasi terlambat ' + durasi(-x.galat_jam) : 'simulasi terlalu awal ' + durasi(x.galat_jam)))) + '</td></tr>').join('') +
    '</tbody></table><p class="petunjuk">Titik panas "pas" bila simulasi terbaik menyala di piksel satelit itu (375 m VIIRS, 1 km MODIS) pada jam lintasan.</p>';
}
function tabelCuaca(det, e) {
  const c = (det && det.cuaca) || [];
  if (!c.length) return '<p class="muted small">Tidak ada.</p>';
  const t0 = mulaiDari(e).slice(0, 13);
  return '<table><thead><tr><th>Jam (WITA)</th><th>Angin km/j</th><th>Dari</th><th>Suhu °C</th><th>RH %</th><th>Hujan mm</th><th>Kadar air 1-jam %</th><th>ROS rumput m/mnt</th></tr></thead><tbody>' +
    c.map((x) => '<tr' + (x.t.slice(0, 13) === t0 ? ' class="total"' : '') + '><td>' + esc(tglPendek(x.t) + ' ' + jam(x.t)) + '</td><td>' + angka(x.ws) + '</td><td>' + esc(MATA_SINGKAT[arahIdx(x.wd)]) + ' ' + Math.round(x.wd) + '°</td><td>' +
      angka(x.suhu) + '</td><td>' + angka(x.rh) + '</td><td>' + angka(x.hujan, 1) + '</td><td>' + angka(x.m1, 1) + '</td><td>' + angka(x.ros_rumput, 1) + '</td></tr>').join('') + '</tbody></table>';
}
function tabelKomposisi(e) {
  const k = e.komposisi || {};
  return '<table><thead><tr><th>Kelas</th><th>Porsi areal</th></tr></thead><tbody>' +
    ['30', '31', '32', '33', 'lain'].map((c) => '<tr><td>' + esc(NAMA_KELAS[c]) + '</td><td>' + angka(100 * (k[c] || 0)) + '%</td></tr>').join('') + '</tbody></table>';
}

/* ---------- grafik: kapan api mulai (per kejadian) ---------- */
function lintasanKejadian(e) {
  const g = {};
  (HS_EV[e.id] || []).forEach((f) => {
    const p = f.properties, k = p.s + '|' + Math.round(ms(p.t) / 9e5);
    (g[k] = g[k] || { t: ms(p.t), s: p.s, n: 0 }).n++;
  });
  return Object.values(g).sort((a, b) => a.t - b.t);
}
function gambarWaktu(e, det) {
  const el = $('asWaktu');
  if (!el) return;
  const seb = ((det && det.sebaran_mulai) || []).map((x) => [ms(x[0]), x[1]]);
  const deteksi = lintasanKejadian(e);
  const kosong = ((det && det.lintasan_kosong) || []).map((x) => ({ t: ms(x[0]), s: x[1] }));
  const p05 = ms(e.t_mulai_p05), p95 = ms(e.t_mulai_p95), p50 = ms(mulaiDari(e)), d0 = ms(e.deteksi_awal);
  let x0 = Math.min(p05, seb.length ? seb[0][0] : p05) - 2 * 3.6e6;
  const sebelum = kosong.filter((k) => k.t < x0 && k.t > p05 - 30 * 3.6e6).map((k) => k.t);
  if (sebelum.length) x0 = Math.max(...sebelum) - 3.6e6;
  let x1 = Math.max(d0 + 3 * 3.6e6, Math.min(ms(e.deteksi_akhir), d0 + 24 * 3.6e6) + 3.6e6);
  x0 = Math.floor(x0 / 3.6e6) * 3.6e6; x1 = Math.ceil(x1 / 3.6e6) * 3.6e6;
  const W = Math.max(el.clientWidth, 300), H = 176, kiri = 8, kanan = 8, atas = 18, tinggiArea = 82, yStrip = atas + tinggiArea + 26, bawah = yStrip + 22;
  const X = (t) => kiri + (t - x0) / (x1 - x0) * (W - kiri - kanan);
  const per = seb.map((s) => s[1] / 0.25), maks = Math.max(...per, 1e-9);
  const Y = (v) => atas + tinggiArea - v / maks * tinggiArea;
  let s = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '">';
  s += '<rect x="' + X(p05) + '" y="' + atas + '" width="' + Math.max(X(p95) - X(p05), 1) + '" height="' + tinggiArea + '" class="as-pita"/>';
  const span = (x1 - x0) / 3.6e6, lk = span <= 18 ? 2 : span <= 36 ? 3 : span <= 72 ? 6 : 12;
  s += '<text x="' + kiri + '" y="' + (bawah + 26) + '" class="as-sumbu-teks kuat">' + esc(tglPendek(isoDari(x0))) + '</text>';
  for (let t = x0; t <= x1; t += 3.6e6) {
    const hh = new Date(t).getUTCHours();
    if (hh === 0 && X(t) - kiri > 56) s += '<text x="' + X(t) + '" y="' + (bawah + 26) + '" class="as-sumbu-teks kuat" text-anchor="middle">' + esc(tglPendek(isoDari(t))) + '</text>';
    if (hh % lk) continue;
    s += '<line x1="' + X(t) + '" x2="' + X(t) + '" y1="' + atas + '" y2="' + (yStrip + 10) + '" class="as-grid"/>';
    if (X(t) > kiri + 14 && X(t) < W - kanan - 14) s += '<text x="' + X(t) + '" y="' + (bawah + 12) + '" class="as-sumbu-teks" text-anchor="middle">' + String(hh).padStart(2, '0') + ':00</text>';
  }
  s += '<line x1="' + kiri + '" x2="' + (W - kanan) + '" y1="' + (atas + tinggiArea) + '" y2="' + (atas + tinggiArea) + '" class="as-dasar"/>';
  if (seb.length) {
    let pth = 'M' + X(seb[0][0]) + ' ' + Y(0);
    seb.forEach((x, i) => { pth += 'L' + X(x[0]) + ' ' + Y(per[i]); });
    pth += 'L' + X(seb[seb.length - 1][0]) + ' ' + Y(0) + 'Z';
    s += '<path d="' + pth + '" class="as-area"/>';
    let ln = '';
    seb.forEach((x, i) => { ln += (i ? 'L' : 'M') + X(x[0]) + ' ' + Y(per[i]); });
    s += '<path d="' + ln + '" class="as-garis"/>';
  }
  s += '<line x1="' + X(p50) + '" x2="' + X(p50) + '" y1="' + (atas - 4) + '" y2="' + (atas + tinggiArea) + '" class="as-p50"/>';
  const lbl = 'perkiraan mulai ' + jam(mulaiDari(e));
  const xl = Math.min(Math.max(X(p50), kiri + 60), W - kanan - 60);
  s += '<text x="' + xl + '" y="' + (atas - 7) + '" class="as-label-kuat" text-anchor="middle">' + esc(lbl) + '</text>';
  s += '<text x="' + kiri + '" y="' + (yStrip - 12) + '" class="as-sumbu-teks">Lintasan satelit</text>';
  s += '<line x1="' + kiri + '" x2="' + (W - kanan) + '" y1="' + yStrip + '" y2="' + yStrip + '" class="as-grid"/>';
  kosong.forEach((k) => { if (k.t >= x0 && k.t <= x1) s += '<circle cx="' + X(k.t) + '" cy="' + yStrip + '" r="4.5" class="as-kosong"><title>' + esc(tglJam(isoDari(k.t))) + ' ' + esc(k.s) + ': lewat, tanpa titik panas</title></circle>'; });
  deteksi.forEach((d) => { if (d.t >= x0 && d.t <= x1) s += '<circle cx="' + X(d.t) + '" cy="' + yStrip + '" r="' + Math.min(4.5 + Math.sqrt(d.n), 9) + '" class="as-deteksi"><title>' + esc(tglJam(isoDari(d.t))) + ' ' + esc(d.s) + ': ' + d.n + ' titik panas</title></circle>'; });
  s += '<line class="as-silang" x1="0" x2="0" y1="' + atas + '" y2="' + (atas + tinggiArea) + '" visibility="hidden"/>';
  s += '</svg><div class="as-tip" hidden></div>';
  el.innerHTML = s;
  $('asWaktuLeg').innerHTML = '<span><i style="background:var(--as-s2)"></i>Peluang jam api mulai</span><span><i style="background:var(--as-pita)"></i>Selang 90%</span>' +
    '<span><i class="bulat" style="background:var(--as-s1)"></i>Lintasan dengan titik panas</span><span><i class="bulat kosong"></i>Lintasan tanpa titik panas</span>';
  const svg = el.querySelector('svg'), tip = el.querySelector('.as-tip'), sil = el.querySelector('.as-silang');
  const cdf = []; let acc = 0; seb.forEach((x) => { acc += x[1]; cdf.push([x[0], acc]); });
  svg.addEventListener('pointermove', (ev) => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width;
    const t = x0 + (px - kiri) / (W - kiri - kanan) * (x1 - x0);
    if (!cdf.length || t < x0 || t > x1) { tip.hidden = true; sil.setAttribute('visibility', 'hidden'); return; }
    let c = 0; for (const q of cdf) { if (q[0] <= t) c = q[1]; else break; }
    sil.setAttribute('x1', px); sil.setAttribute('x2', px); sil.setAttribute('visibility', 'visible');
    tip.hidden = false;
    tip.innerHTML = '<b>' + angka(100 * c) + '%</b> kemungkinan api sudah menyala pada ' + esc(tglPendek(isoDari(t)) + ' ' + jam(isoDari(Math.round(t / 6e4) * 6e4))) + ' WITA';
    const lx = (ev.clientX - r.left);
    tip.style.left = Math.min(Math.max(lx - tip.offsetWidth / 2, 0), r.width - tip.offsetWidth) + 'px';
    tip.style.top = '0px';
  });
  svg.addEventListener('pointerleave', () => { tip.hidden = true; sil.setAttribute('visibility', 'hidden'); });
}

/* ---------- grafik jam rawan ---------- */
function gambarJam(list) {
  const el = $('asJam');
  const ok = berhasil(list), jr = jamRawan(ok), tot = jr.v.reduce((a, b) => a + b, 0);
  $('asJamLegenda').innerHTML = '<span><i style="background:var(--as-s2)"></i>Perkiraan api mulai (model)</span><span><i style="background:var(--as-abu)"></i>Deteksi satelit pertama</span><span>sumbu: jam WITA, tinggi: jumlah kejadian</span>';
  if (!ok.length) { el.innerHTML = '<p class="muted small">Tidak ada kejadian pada saringan ini.</p>'; $('asJamTeks').textContent = ''; $('asJamTabel').innerHTML = ''; return; }
  const w = jendelaRawan(jr.v, 3);
  const det = jr.det.reduce((a, b) => a + b, 0), puncakDet = jr.det.indexOf(Math.max(...jr.det));
  $('asJamTeks').innerHTML = 'Api paling sering mulai pukul <b>' + teksJendela(w) + ' WITA</b> (' + angka(100 * w.jumlah / tot) + '% dari ' + ok.length + ' kejadian). ' +
    'Jam deteksi satelit tidak sama dengan jam pembakaran: satelit hanya lewat sekitar pukul 01–03 dan 12–15, sehingga deteksi pertama menumpuk di jam lintasan (terbanyak pukul ' + String(puncakDet).padStart(2, '0') + ':00).';
  const Wd = Math.max(el.clientWidth, 280), Hd = 200, kiri = 30, kanan = 6, atas = 12, bawah = 24, ph = Hd - atas - bawah;
  const maks = Math.max(...jr.v, ...jr.det, 1);
  const lt = langkahRapi(maks, 4), ymaks = Math.ceil(maks / lt) * lt;
  const gw = (Wd - kiri - kanan) / 24, bw = Math.max(2, Math.min(10, (gw - 4) / 2));
  const Y = (v) => atas + ph - v / ymaks * ph;
  let s = '<svg viewBox="0 0 ' + Wd + ' ' + Hd + '" width="' + Wd + '" height="' + Hd + '">';
  for (let v = 0; v <= ymaks + 1e-9; v += lt) {
    s += '<line x1="' + kiri + '" x2="' + (Wd - kanan) + '" y1="' + Y(v) + '" y2="' + Y(v) + '" class="' + (v === 0 ? 'as-dasar' : 'as-grid') + '"/>';
    s += '<text x="' + (kiri - 6) + '" y="' + (Y(v) + 4) + '" class="as-sumbu-teks" text-anchor="end">' + angka(v, lt < 1 ? 1 : 0) + '</text>';
  }
  for (let h = 0; h < 24; h++) {
    const cx = kiri + gw * h + gw / 2;
    const xa = cx - bw - 1, xb = cx + 1;
    s += batangSvg(xa, Y(jr.v[h]), bw, Y(0) - Y(jr.v[h]), 'as-b-model');
    s += batangSvg(xb, Y(jr.det[h]), bw, Y(0) - Y(jr.det[h]), 'as-b-det');
    if (h % 3 === 0) s += '<text x="' + (kiri + gw * h) + '" y="' + (Hd - 6) + '" class="as-sumbu-teks" text-anchor="middle">' + String(h).padStart(2, '0') + '</text>';
    s += '<rect x="' + (kiri + gw * h) + '" y="' + atas + '" width="' + gw + '" height="' + ph + '" class="as-hit" data-h="' + h + '"/>';
  }
  s += '</svg><div class="as-tip" hidden></div>';
  el.innerHTML = s;
  const tip = el.querySelector('.as-tip');
  el.querySelectorAll('.as-hit').forEach((r) => {
    r.addEventListener('pointerenter', () => {
      const h = +r.dataset.h;
      tip.hidden = false;
      tip.innerHTML = '<b>' + angka(jr.v[h], 1) + '</b> kejadian mulai pukul ' + String(h).padStart(2, '0') + ':00–' + String(h).padStart(2, '0') + ':59 (' + angka(100 * jr.v[h] / tot) + '%)<br><b>' + jr.det[h] + '</b> deteksi pertama satelit';
      const bx = r.getBoundingClientRect(), ex = el.getBoundingClientRect();
      tip.style.left = Math.min(Math.max(bx.left - ex.left + bx.width / 2 - tip.offsetWidth / 2, 0), ex.width - tip.offsetWidth) + 'px';
      tip.style.top = '-8px';
      r.classList.add('aktif');
    });
    r.addEventListener('pointerleave', () => { tip.hidden = true; r.classList.remove('aktif'); });
  });
  $('asJamTabel').innerHTML = '<table><thead><tr><th>Jam (WITA)</th><th>Perkiraan api mulai</th><th>%</th><th>Deteksi pertama</th></tr></thead><tbody>' +
    jr.v.map((v, h) => '<tr><td>' + String(h).padStart(2, '0') + ':00–' + String(h).padStart(2, '0') + ':59</td><td>' + angka(v, 1) + '</td><td>' + angka(100 * v / tot, 1) + '</td><td>' + jr.det[h] + '</td></tr>').join('') +
    '</tbody></table><p class="petunjuk">Perkiraan api mulai = jumlah peluang jam mulai semua kejadian (tiap kejadian bernilai 1, disebar menurut ketidakpastiannya).</p>';
}
function batangSvg(x, y, w, h, kelas) {
  if (h <= 0.4) return '';
  const r = Math.min(4, w / 2, h);
  return '<path class="' + kelas + '" d="M' + x + ' ' + (y + h) + 'V' + (y + r) + 'Q' + x + ' ' + y + ' ' + (x + r) + ' ' + y + 'H' + (x + w - r) + 'Q' + (x + w) + ' ' + y + ' ' + (x + w) + ' ' + (y + r) + 'V' + (y + h) + 'Z"/>';
}

/* ---------- mawar arah datang api ---------- */
function gambarMawar(list) {
  const ok = berhasil(list).filter((e) => datangIdx(e) >= 0);
  const n = new Array(8).fill(0), ha = new Array(8).fill(0), luar = new Array(8).fill(0);
  ok.forEach((e) => { const i = datangIdx(e); n[i]++; ha[i] += e.luas_ha; if (!e.asal.di_tn) luar[i]++; });
  const el = $('asMawar');
  if (!ok.length) { el.innerHTML = '<p class="muted small">Tidak ada kejadian.</p>'; $('asMawarTeks').innerHTML = ''; $('asMawarTabel').innerHTML = ''; return; }
  const W = 210, c = W / 2, R = 78, maks = Math.max(...n);
  let s = '<svg viewBox="0 0 ' + W + ' ' + W + '" width="' + W + '" height="' + W + '">';
  [0.5, 1].forEach((f) => { s += '<circle cx="' + c + '" cy="' + c + '" r="' + (R * Math.sqrt(f)) + '" class="as-grid" fill="none"/>'; });
  for (let i = 0; i < 8; i++) {
    const a0 = (i * 45 - 19) * Math.PI / 180, a1 = (i * 45 + 19) * Math.PI / 180, r = R * Math.sqrt(n[i] / maks);
    if (n[i] > 0) {
      const p = (a, rr) => (c + rr * Math.sin(a)).toFixed(1) + ' ' + (c - rr * Math.cos(a)).toFixed(1);
      s += '<path class="as-irisan" d="M' + c + ' ' + c + 'L' + p(a0, r) + 'A' + r + ' ' + r + ' 0 0 1 ' + p(a1, r) + 'Z" data-i="' + i + '"><title>Dari ' + MATA[i] + ': ' + n[i] + ' kejadian, ' + angka(ha[i]) + ' ha</title></path>';
    }
    const al = i * 45 * Math.PI / 180;
    s += '<text x="' + (c + (R + 14) * Math.sin(al)) + '" y="' + (c - (R + 14) * Math.cos(al) + 4) + '" text-anchor="middle" class="as-sumbu-teks' + (i % 2 ? '' : ' kuat') + '">' + MATA_SINGKAT[i] + '</text>';
  }
  s += '</svg>';
  el.innerHTML = s;
  const urutan = n.map((v, i) => i).sort((a, b) => n[b] - n[a]);
  const top = urutan[0], kedua = urutan[1];
  $('asMawarTeks').innerHTML = 'Api paling sering datang dari <b>' + MATA[top] + '</b> (' + n[top] + ' dari ' + ok.length + ' kejadian, ' + angka(ha[top]) + ' ha)' +
    (n[kedua] ? ', lalu dari ' + MATA[kedua] + ' (' + n[kedua] + ')' : '') + '. Panjang irisan sebanding akar jumlah kejadian (luas irisan = jumlah).' +
    '<br><span class="muted">Arah datang = arah dari pusat areal terbakar ke titik asal.</span>';
  $('asMawarTabel').innerHTML = '<table><thead><tr><th>Datang dari</th><th>Kejadian</th><th>Asal di luar TN</th><th>Luas (ha)</th></tr></thead><tbody>' +
    MATA.map((m, i) => '<tr><td>' + m + '</td><td>' + n[i] + '</td><td>' + luar[i] + '</td><td>' + angka(ha[i]) + '</td></tr>').join('') + '</tbody></table>';
}

/* ---------- tabel kejadian ---------- */
const KOLOM = [
  { k: 'deteksi_awal', j: 'Kejadian', kiri: true, nilai: (e) => e.deteksi_awal },
  { k: 'resort', j: 'Lokasi', kiri: true, nilai: (e) => e.resort || '~' },
  { k: 'luas_ha', j: 'Luas (ha)', nilai: (e) => e.luas_ha },
  { k: 'mulai', j: 'Api mulai (tanggal)', nilai: (e) => e.t_mulai ? mulaiDari(e) : '' },
  { k: 'jam', j: 'Jam mulai', nilai: (e) => e.t_mulai ? mulaiDari(e).slice(11, 16) : '99' },
  { k: 'jeda', j: 'Sebelum terdeteksi', nilai: (e) => e.t_mulai ? jamAntara(mulaiDari(e), e.deteksi_awal) : -1 },
  { k: 'asal', j: 'Titik asal', kiri: true, nilai: (e) => e.asal ? (e.asal.di_tn ? 1 : 0) : -1 },
  { k: 'datang', j: 'Api datang dari', kiri: true, nilai: (e) => datangIdx(e) },
  { k: 'k', j: 'Faktor k', nilai: (e) => e.k_p50 == null ? -1 : e.k_p50 },
  { k: 'sorensen', j: 'Sørensen', nilai: (e) => e.sorensen == null ? -1 : e.sorensen },
  { k: 'mutu', j: 'Mutu', kiri: true, nilai: (e) => ({ baik: 3, sedang: 2, rendah: 1 }[e.mutu] || 0) }
];
function gambarTabel(list) {
  const kol = KOLOM.find((k) => k.k === urut.kol) || KOLOM[0];
  const baris = list.slice().sort((a, b) => { const x = kol.nilai(a), y = kol.nilai(b); return (x < y ? -1 : x > y ? 1 : 0) * urut.arah; });
  if (!baris.length) { $('asTabel').innerHTML = '<div class="riwayat-kosong">Tidak ada kejadian pada saringan ini.</div>'; return; }
  let h = '<table class="as-tabel"><thead><tr>' + KOLOM.map((k) => '<th class="' + (k.kiri ? 'kiri' : '') + '" aria-sort="' + (k.k === urut.kol ? (urut.arah > 0 ? 'ascending' : 'descending') : 'none') + '"><button data-k="' + k.k + '">' + k.j +
    (k.k === urut.kol ? (urut.arah > 0 ? ' ▲' : ' ▼') : '') + '</button></th>').join('') + '</tr></thead><tbody>';
  baris.forEach((e) => {
    const ok = !!e.t_mulai;
    h += '<tr data-id="' + esc(e.id) + '" tabindex="0">' +
      '<td class="kiri"><b>' + esc(tgl(e.deteksi_awal)) + '</b><br><span class="muted small">deteksi ' + esc(jam(e.deteksi_awal)) + ' · ' + e.n_lintasan + ' lintasan</span></td>' +
      '<td class="kiri">' + esc(e.resort || 'luar resort') + (e.desa ? '<br><span class="muted small">' + esc(e.desa) + '</span>' : '') + '</td>' +
      '<td>' + angka(e.luas_ha, e.luas_ha < 10 ? 1 : 0) + '</td>' +
      (ok ? '<td>' + esc(tglPendek(mulaiDari(e))) + '</td>' +
        '<td><span class="as-bulat" style="background:' + KELAS_JAM[kelasJam(mulaiDari(e))].css + '"></span><b>' + esc(jam(mulaiDari(e))) + '</b><br><span class="muted small">90%: ' + esc(jam(e.t_mulai_p05)) + '–' + esc(jam(e.t_mulai_p95)) + '</span></td>' +
        '<td>' + durasi(jamAntara(mulaiDari(e), e.deteksi_awal)) + '</td>' +
        '<td class="kiri">' + (e.asal.di_tn ? 'dalam TN' : '<b>luar TN</b>') + (teksJalurPendek(e.asal.jalur) ? '<br><span class="muted small">' + teksJalurPendek(e.asal.jalur) + '</span>' : '') + '</td>' +
        '<td class="kiri">' + esc(e.api_datang_dari || '–') + '</td>' +
        '<td>×' + angka(e.k_p50, 2) + '</td><td>' + angka(e.sorensen, 2) + '</td>'
        : '<td colspan="7" class="kiri muted small">' + esc((e.catatan || 'tidak terhitung').slice(0, 120)) + '</td>') +
      '<td class="kiri">' + lencanaMutu(e.mutu) + '</td></tr>';
  });
  h += '</tbody></table>';
  h += '<div class="aksi-baris" style="margin-top:10px"><button class="garis kecil" id="asCsv">Unduh tabel (CSV)</button><a class="tombol garis kecil" href="data/asal_api/peta.geojson" download="asal_api_titik_asal.geojson">Unduh titik asal &amp; zona (GeoJSON)</a></div>';
  $('asTabel').innerHTML = h;
  $('asTabel').querySelectorAll('th button').forEach((b) => b.addEventListener('click', () => {
    const k = b.dataset.k;
    urut = { kol: k, arah: urut.kol === k ? -urut.arah : (k === 'deteksi_awal' || k === 'luas_ha' || k === 'mutu' ? -1 : 1) };
    gambarTabel(tersaring());
  }));
  $('asTabel').querySelectorAll('tbody tr').forEach((tr) => {
    const go = () => pilih(tr.dataset.id, { gulir: true });
    tr.addEventListener('click', go);
    tr.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); } });
  });
  $('asCsv').addEventListener('click', () => unduhCsv(baris));
  tandaiBaris();
}
function teksJalurPendek(j) {
  if (!j) return '';
  const k = Object.keys(j)[0];
  if (!k) return '';
  return angka(j[k].jarak_m / 1000, 1) + ' km dari ' + esc(k.toLowerCase());
}
function unduhCsv(list) {
  const kol = ['id', 'deteksi_awal_wita', 'deteksi_akhir_wita', 'resort', 'desa', 'luas_ha', 'tn_persen', 'n_titik', 'n_lintasan',
    'mulai_wita', 'mulai_p05_wita', 'mulai_p95_wita', 'mulai_paling_cocok_wita', 'jam_sebelum_deteksi', 'asal_lat', 'asal_lon', 'asal_di_tn',
    'asal_resort', 'asal_elev_m', 'asal_fuel', 'arah_rambat_deg', 'api_datang_dari', 'masuk_tn_wita', 'masuk_tn_resort', 'faktor_k', 'k_p05', 'k_p95', 'sorensen', 'galat_waktu_jam', 'mutu'];
  const q = (v) => { const s = v == null ? '' : String(v); return /[",\n;]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  const rows = list.map((e) => {
    const a = e.asal || {}, ok = !!e.t_mulai;
    return [e.id, e.deteksi_awal, e.deteksi_akhir, e.resort, e.desa, e.luas_ha, e.tn_persen, e.n_titik, e.n_lintasan,
      ok ? mulaiDari(e) : '', e.t_mulai_p05, e.t_mulai_p95, e.t_mulai, ok ? jamAntara(mulaiDari(e), e.deteksi_awal).toFixed(2) : '',
      a.lat, a.lon, ok ? (a.di_tn ? 'ya' : 'tidak') : '', a.resort, a.elev, a.fuel, e.arah_rambat, e.api_datang_dari,
      e.masuk_tn ? e.masuk_tn.t : '', e.masuk_tn ? e.masuk_tn.resort : '', e.k_p50, e.k_p05, e.k_p95, e.sorensen, e.galat_waktu_jam, e.mutu].map(q).join(',');
  });
  const blob = new Blob(['﻿' + kol.join(',') + '\n' + rows.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'asal_api_tambora.csv';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/* ---------- kalibrasi ---------- */
function gambarKalibrasi() {
  const kal = ST.kalibrasi || {}, el = $('asKalibrasi');
  const ra = (ST.data && ST.data.ros_adjust) || {};
  if (!kal.cukup) {
    el.className = 'small';
    el.innerHTML = '<p>Kalibrasi butuh minimal 3 kejadian yang terlihat di 2 lintasan satelit atau lebih dan cocok dengan simulasi (Sørensen ≥ 0,3, galat waktu ≤ 3 jam). Saat ini baru ' + (kal.n || 0) + ' kejadian. ' +
      'Faktor k per kejadian tetap bisa dilihat di daftar kejadian.</p>';
    return;
  }
  el.className = '';
  const kelas = ['30', '31', '32', '33', 'lain'];
  const PENDEK = { '30': 'K1 · 30', '31': 'K2 · 31', '32': 'K3 · 32', '33': 'K4 · 33', lain: 'Lain' };
  const W = Math.max(Math.min(el.clientWidth || 640, 720), 300), baris = 30, kiri = 70, kanan = 118, atas = 24, H = atas + baris * kelas.length + 26;
  const lo = Math.log(0.25), hi = Math.log(4);
  const X = (v) => kiri + (Math.log(Math.max(0.25, Math.min(4, v))) - lo) / (hi - lo) * (W - kiri - kanan);
  let s = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" class="as-hutan" role="img" aria-label="Faktor laju api per kelas bahan bakar">';
  [0.25, 0.5, 1, 2, 4].forEach((v) => {
    s += '<line x1="' + X(v) + '" x2="' + X(v) + '" y1="' + (atas - 6) + '" y2="' + (H - 22) + '" class="' + (v === 1 ? 'as-acuan' : 'as-grid') + '"/>';
    s += '<text x="' + X(v) + '" y="' + (H - 8) + '" text-anchor="middle" class="as-sumbu-teks">×' + angka(v, 2) + '</text>';
  });
  s += '<text x="' + X(1) + '" y="' + (atas - 10) + '" text-anchor="middle" class="as-sumbu-teks">fmd20 apa adanya</text>';
  kelas.forEach((c, i) => {
    const p = kal.kelas[c] || {}, y = atas + baris * i + baris / 2;
    const lemah = (p.n_efektif || 0) < 1;
    s += '<text x="0" y="' + (y + 4) + '" class="as-label' + (lemah ? ' redup' : '') + '">' + esc(PENDEK[c]) + '</text>';
    if (p.faktor != null) {
      s += '<line x1="' + X(p.p05) + '" x2="' + X(p.p95) + '" y1="' + y + '" y2="' + y + '" class="as-selang' + (lemah ? ' redup' : '') + '"/>';
      s += '<circle cx="' + X(p.faktor) + '" cy="' + y + '" r="5" class="as-titik' + (lemah ? ' redup' : '') + '"/>';
      s += '<text x="' + (W - kanan + 10) + '" y="' + (y + 4) + '" class="as-label' + (lemah ? ' redup' : '') + '">×' + angka(p.faktor, 2) + ' (' + angka(p.p05, 2) + '–' + angka(p.p95, 2) + ')</text>';
    }
  });
  s += '</svg>';
  let t = '<p class="small">Dari <b>' + kal.n + ' kejadian</b> yang terlihat di 2 lintasan satelit atau lebih, laju api nyata rata-rata <b>×' + angka(kal.global_faktor, 2) + '</b> dari simulasi fmd20 saat ini ' +
    '(selang 90%: ' + angka(kal.global_p05, 2) + '–' + angka(kal.global_p95, 2) + '). Di bawah ini faktor per kelas bahan bakar, dipisahkan dengan regresi menurut komposisi kelas di tiap areal kejadian. ' +
    'Faktor &lt; 1 berarti simulasi lebih cepat dari api nyata; &gt; 1 berarti lebih lambat.</p>';
  t += '<div class="as-grafik-hutan">' + s + '</div>';
  t += '<div class="tw"><table><thead><tr><th>Kelas</th><th>Faktor</th><th>Selang 90%</th><th>Bobot data (kejadian setara)</th><th>ros_adjust sekarang</th><th>Saran ros_adjust</th></tr></thead><tbody>' +
    kelas.map((c) => {
      const p = kal.kelas[c] || {}, now = +(ra[c] != null ? ra[c] : (ra[+c] != null ? ra[+c] : 1));
      const saran = c === 'lain' ? '–' : ((p.n_efektif || 0) >= 1 ? angka(now * p.faktor, 2) : '<span class="muted">data kurang</span>');
      return '<tr><td>' + esc(NAMA_KELAS[c]) + '</td><td>×' + angka(p.faktor, 2) + '</td><td>' + angka(p.p05, 2) + '–' + angka(p.p95, 2) + '</td><td>' + angka(p.n_efektif, 1) + '</td><td>' + (c === 'lain' ? '–' : angka(now, 2)) + '</td><td>' + saran + '</td></tr>';
    }).join('') + '</tbody></table></div>';
  t += '<p class="petunjuk">Saran ini <b>belum diterapkan</b> ke simulasi. Bila disetujui, nilainya diisi di <code>[kalibrasi] ros_adjust</code> pada <code>config/tambora.toml</code> (contoh <code>{ 30 = 0.9, 31 = 1.1 }</code>), lalu simulasi dan pemodelan terbalik memakai fmd20 yang sudah dikoreksi. ' +
    'Kelas dengan bobot data &lt; 1 belum cukup terwakili. Faktor k satu kejadian juga memuat galat angin ERA5 dan peta bahan bakar, jadi pakai nilai gabungan, bukan satu kejadian.</p>';
  if ((kal.kejadian || []).length) {
    t += '<details><summary>Kejadian yang dipakai (' + kal.kejadian.length + ')</summary><div class="tw"><table><thead><tr><th>Kejadian</th><th>Faktor k</th><th>Selang 90%</th><th>Sørensen</th><th>K1</th><th>K2</th><th>K3</th><th>K4</th><th>Lain</th></tr></thead><tbody>' +
      kal.kejadian.map((k) => '<tr><td><a href="#asal/' + esc(k.id) + '">' + esc(tgl(cariKejadian(k.id).deteksi_awal || '')) + '</a></td><td>×' + angka(k.k, 2) + '</td><td>' + angka(k.k_p05, 2) + '–' + angka(k.k_p95, 2) + '</td><td>' + angka(k.sorensen, 2) + '</td>' +
        ['30', '31', '32', '33', 'lain'].map((c) => '<td>' + angka(100 * ((k.komposisi || {})[c] || 0)) + '%</td>').join('') + '</tr>').join('') + '</tbody></table></div></details>';
  }
  el.innerHTML = t;
}
function cariKejadian(id) { return (ST.kejadian || []).find((e) => e.id === id) || {}; }

/* ---------- metode ---------- */
function gambarMetode() {
  const m = ST.metode || {}, p = m.parameter || {}, d = ST.data || {}, r = ST.ringkasan || {};
  const mutu = r.mutu || {};
  const tanpa = d.poligon_tanpa_titik_panas || [];
  $('asMetode').innerHTML =
    '<h3>Langkah perhitungan</h3><ol>' +
    '<li><b>Titik panas</b> VIIRS (375 m) dan MODIS (1 km) dari SiPongi dan NASA FIRMS dikonversi ke WITA. Titik berkeyakinan rendah hanya dipakai bila berada ≤ 400 m dari areal terbakar.</li>' +
    '<li><b>Kejadian</b>: titik panas digabung bila berjarak ≤ ' + angka((p.jarak_kelompok_m || 1500) / 1000, 1) + ' km dan ≤ ' + (p.jeda_kelompok_jam || 36) + ' jam, atau tersambung oleh areal terbakar (api cepat di antara dua lintasan satelit). Areal terbakar hasil delineasi dibagi ke kejadian menurut titik panas terdekat. Areal kejadian lebih awal menjadi tidak bisa terbakar (fuel 99) untuk kejadian sesudahnya.</li>' +
    '<li><b>Cuaca</b> per jam ERA5/ERA5-Land (Open-Meteo, sama dengan pembuat .wxs) di lokasi tiap kejadian, mulai 3 hari sebelumnya untuk pengkondisian kadar air bahan bakar (fms). Angin diarahkan medan seperti pada simulasi.</li>' +
    '<li><b>Simulasi mundur</b>: dari tiap titik panas, waktu rambat dihitung terbalik (arah angin dibalik) di landscape dan fmd20. Hasilnya, untuk tiap sel peta, jam paling akhir api bisa menyala di sel itu agar masih sampai di piksel satelit pada jam deteksinya. Sel yang cocok dengan semua titik panas sekaligus menjadi kandidat titik asal, dengan jam mulai yang sesuai. Lintasan satelit yang lewat sebelum deteksi pertama tanpa melihat api ikut membatasi jam mulai.</li>' +
    '<li><b>Simulasi maju</b> dari kandidat terbaik (FARSITE, ' + (p.k_bebas || []).length + ' faktor laju api k dari ×' + angka((p.k_bebas || [0.35])[0], 2) + ' sampai ×' + angka((p.k_bebas || [2.83]).slice(-1)[0], 2) + '): dinilai dari kecocokan waktu (api menyala di piksel satelit pada jam lintasan) dan kecocokan bentuk areal terbakar (Sørensen). Hasilnya zona asal peluang 50% dan 90%, sebaran jam mulai, faktor k, dan garis waktu rambat.</li>' +
    '<li><b>Kalibrasi</b>: faktor k kejadian yang terlihat di ≥ 2 lintasan satelit digabung per kelas bahan bakar K1–K4 (regresi berbobot dengan bootstrap). Kejadian yang hanya terlihat 1 kali memakai faktor gabungan ini sebagai dugaan awal.</li>' +
    '<li><b>Jam rawan</b> = jumlah sebaran jam mulai semua kejadian per jam WITA. Arah datang api = arah dari pusat areal terbakar ke titik asal.</li></ol>' +
    '<h3>Data dan hasil saat ini</h3><ul>' +
    '<li>' + angka(d.titik_aoi) + ' titik panas di landscape, ' + angka(d.titik_dipakai) + ' dipakai; ' + angka(d.kejadian) + ' kelompok titik panas, ' + angka(r.dianalisis) + ' kejadian dengan areal terbakar ≥ 1 ha (' + angka(r.luas_dianalisis_ha) + ' ha).</li>' +
    '<li>Berhasil dihitung ' + angka(r.berhasil) + ' kejadian: mutu baik ' + (mutu.baik || 0) + ', sedang ' + (mutu.sedang || 0) + ', rendah ' + (mutu.rendah || 0) + (r.gagal ? ', tidak terhitung ' + r.gagal : '') + '.</li>' +
    '<li>Areal terbakar tanpa titik panas di dekatnya: ' + angka(d.areal_tanpa_titik_panas_ha) + ' ha' + (tanpa.length ? ' (' + tanpa.length + ' poligon ≥ 5 ha, terbesar ' + angka(tanpa[0].luas_ha) + ' ha di ' + esc(tanpa[0].resort || 'luar resort') + ')' : '') + '. Areal ini tidak bisa diduga asalnya.</li>' +
    '<li>Landscape: ' + esc(d.landscape || '–') + '. Bahan bakar: ' + esc(d.fmd || '–') + ', ' + esc(d.fms || '–') + '. Sel hitung ' + (p.sel_m || 30) + ' m.</li></ul>' +
    '<h3>Keterbatasan</h3><ul>' +
    '<li>Titik asal adalah <b>zona peluang</b>, bukan titik pasti. Piksel VIIRS 375 m dan MODIS 1 km, jadi zona 50% biasanya puluhan sampai ratusan hektare.</li>' +
    '<li>Satelit hanya lewat beberapa kali sehari (sekitar pukul 01–03 dan 12–15 WITA). Kejadian yang hanya terlihat sekali punya selang jam mulai yang lebar dan sangat bergantung pada faktor k dari kalibrasi.</li>' +
    '<li>Angin ERA5 adalah data model grid 9–25 km. Angin lokal (lembah, angin gunung-laut) bisa berbeda dan menggeser titik asal ke arah angin.</li>' +
    '<li>Peta bahan bakar dari citra awal kemarau; perubahan sesudahnya (rumput mengering, bekas terbakar tanpa delineasi) belum terwakili.</li>' +
    '<li>Satu kejadian bisa berasal dari lebih dari satu titik penyulutan. Bila areal sangat luas dengan titik panas pertama berjauhan, hasil menunjuk titik asal yang paling menjelaskan seluruh data.</li>' +
    '<li>Hasil ini untuk membaca pola (jam rawan, arah datang, pintu masuk api) dan kalibrasi, bukan bukti untuk satu pelaku atau satu titik.</li></ul>';
}

/* gambar ulang grafik saat lebar berubah */
let timerUkur = null;
window.addEventListener('resize', () => {
  clearTimeout(timerUkur);
  timerUkur = setTimeout(() => {
    if (!ST || $('panel-asal').hidden) return;
    const list = tersaring();
    gambarJam(list);
    if (pilihan && pilihan.det) gambarWaktu(pilihan.e, pilihan.det);
  }, 200);
});
