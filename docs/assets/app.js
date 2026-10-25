/* Simulasi API Tambora: dashboard bertab (ringkasan, simulasi, riwayat, landscape, panduan). */
(function () {
'use strict';
var C = window.SIM_CONFIG || {};
var REPO = C.repo || 'kopluck8/Simulasi-API-Tambora';
var API = 'https://api.github.com/repos/' + REPO;
var AOI = [[-8.4307, 117.7631], [-8.0860, 118.1980]];
var $ = function (id) { return document.getElementById(id); };

/* ---------- utilitas ---------- */
function angka(x, d) {
  if (x === undefined || x === null || x === '' || !isFinite(x)) return '–';
  return Number(x).toLocaleString('id-ID', { maximumFractionDigits: d || 0, minimumFractionDigits: 0 });
}
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
var BULAN = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
function tglID(iso, jam) {
  if (!iso) return '–';
  var s = String(iso), y = +s.slice(0, 4), m = +s.slice(5, 7), d = +s.slice(8, 10);
  var t = d + ' ' + BULAN[m - 1] + ' ' + y;
  if (jam && s.length >= 16) t += ' ' + s.slice(11, 16);
  return t;
}
function bagianWITA(date) {
  var o = {};
  new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Makassar', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date).forEach(function (p) { o[p.type] = p.value; });
  return o;
}
function sekarangWITA() { var o = bagianWITA(new Date()); return o.year + '-' + o.month + '-' + o.day + 'T' + o.hour + ':00'; }
function hariIniWITA() { return sekarangWITA().slice(0, 10); }
function keWITA(isoUtc) { var o = bagianWITA(new Date(isoUtc)); return o.year + '-' + o.month + '-' + o.day + 'T' + o.hour + ':' + o.minute; }
function tambahHari(iso, n) { var d = new Date(iso + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
function tambahJam(lokal, jam) { return new Date(Date.parse(lokal + ':00Z') + jam * 3600e3).toISOString().slice(0, 16); }
function pad(v, n) { return String(v).padStart(n); }
var MATA = ['U', 'TL', 'T', 'TG', 'S', 'BD', 'B', 'BL'];
function mata(d) { return MATA[Math.round((((d % 360) + 360) % 360) / 45) % 8]; }
function batang(p, api) { p = Math.max(0, Math.min(100, p || 0)); return '<div class="batang"><i class="' + (api ? 'api' : '') + '" style="width:' + p.toFixed(1) + '%"></i></div>'; }

var NAMA_FUEL = { 30: 'K1 savana', 31: 'K2 savana', 32: 'K3 savana', 33: 'K4 savana', 91: 'Permukiman (NB1)', 93: 'Pertanian (NB3)',
  98: 'Air (NB8)', 99: 'Batuan / bekas terbakar (NB9)', 142: 'Semak SH2', 161: 'Hutan TU1', 186: 'Hutan TL6' };
var WARNA_FUEL = { 30: '#f7e08a', 31: '#e9b949', 32: '#d08a2c', 33: '#9b5a1a', 91: '#7a7a7a', 93: '#c9a7e0', 98: '#4a7fb5',
  99: '#cfcfcf', 142: '#8fbf6f', 161: '#4f9a4f', 186: '#1f5f2f' };
var KELAS = ['30', '31', '32', '33'];
function sisaK(o) { return KELAS.reduce(function (s, k) { return s + (+((o || {})[k]) || 0); }, 0); }

/* ---------- tab ---------- */
var TABS = ['ringkasan', 'simulasi', 'riwayat', 'landscape', 'panduan'];
function buka(tab) {
  if (TABS.indexOf(tab) < 0) tab = 'ringkasan';
  document.querySelectorAll('.tab').forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.tab === tab)); });
  TABS.forEach(function (t) { $('panel-' + t).hidden = t !== tab; });
  if (location.hash !== '#' + tab) history.replaceState(null, '', '#' + tab);
  if (tab === 'simulasi') siapkanPeta();
}
document.querySelectorAll('.tab').forEach(function (b) { b.addEventListener('click', function () { buka(b.dataset.tab); }); });
document.querySelectorAll('[data-ke]').forEach(function (b) { b.addEventListener('click', function () { buka(b.dataset.ke); window.scrollTo(0, 0); }); });
window.addEventListener('hashchange', function () { buka(location.hash.slice(1)); });
$('linkRepo').href = 'https://github.com/' + REPO;
$('linkActions').href = 'https://github.com/' + REPO + '/actions/workflows/update-landscape.yml';

/* ---------- status landscape ---------- */
var STATUS = null;
function citraTerakhir(d) {
  if (d.citra_terakhir) return keWITA(d.citra_terakhir);
  var t = (d.citra || []).map(function (c) { var m = /_(\d{8})T(\d{4})/.exec(c); return m ? m[1] + m[2] : ''; }).sort().pop();
  return t ? keWITA(t.slice(0, 4) + '-' + t.slice(4, 6) + '-' + t.slice(6, 8) + 'T' + t.slice(8, 10) + ':' + t.slice(10, 12) + ':00Z') : null;
}
function isiStatus(d) {
  STATUS = d;
  var umur = (Date.now() - Date.parse(d.tanggal + 'T00:00:00+08:00')) / 864e5;
  var lc = $('lencanaLandscape');
  lc.querySelector('i').style.background = umur <= 7 ? 'var(--ok)' : 'var(--warn)';
  lc.querySelector('span').textContent = 'Landscape ' + tglID(d.tanggal);
  var ct = citraTerakhir(d);
  $('kpiTanggal').textContent = tglID(d.tanggal);
  $('kpiCitra').textContent = ct ? 'citra Sentinel-2 terakhir ' + tglID(ct) : (d.citra || []).length + ' citra';
  $('kpiTerbakar').textContent = angka(d.bekas_terbakar_musim_ha) + ' ha';
  $('kpiTerbakarSub').textContent = 'sejak ' + tglID(d.awal_musim) + ' · +' + angka(d.bekas_terbakar_baru_ha) + ' ha baru' +
    (d.baru_sejak ? ' sejak ' + tglID(d.baru_sejak) : '');
  var kw = d.kawasan && d.kawasan.total;
  if (kw) {
    var s = sisaK(kw.sisa), a = sisaK(kw.awal);
    $('kpiSisaJudul').textContent = 'Sisa K1–K4 di kawasan TN';
    $('kpiSisa').textContent = angka(s) + ' ha';
    $('kpiSisaSub').textContent = angka(100 * s / a) + '% dari ' + angka(a) + ' ha awal musim';
  } else {
    $('kpiSisaJudul').textContent = 'Sisa K1–K4 (seluruh AOI)';
    $('kpiSisa').textContent = angka(sisaK(d.luas_fuel_ha)) + ' ha';
    $('kpiSisaSub').textContent = 'angka per resort muncul setelah update berikutnya';
  }
  // tabel K1-K4
  var h;
  if (kw) {
    $('judulKomposisi').textContent = 'Bahan bakar K1–K4 di kawasan TN';
    h = '<table><tr><th>Kelas</th><th>Awal musim (ha)</th><th>Sisa (ha)</th><th>Sisa</th><th></th></tr>';
    KELAS.forEach(function (k) {
      var a = kw.awal[k], s = kw.sisa[k];
      h += '<tr><td><span class="sw" style="background:' + WARNA_FUEL[k] + '"></span>' + NAMA_FUEL[k] + '</td><td>' + angka(a) + '</td><td>' + angka(s) +
        '</td><td>' + angka(100 * s / a) + '%</td><td>' + batang(100 * s / a) + '</td></tr>';
    });
    h += '<tr class="total"><td>Total</td><td>' + angka(sisaK(kw.awal)) + '</td><td>' + angka(sisaK(kw.sisa)) + '</td><td>' +
      angka(100 * sisaK(kw.sisa) / sisaK(kw.awal)) + '%</td><td></td></tr></table>';
  } else {
    var tot = sisaK(d.luas_fuel_ha);
    h = '<table><tr><th>Kelas</th><th>Luas (ha)</th><th>Porsi</th><th></th></tr>';
    KELAS.forEach(function (k) {
      var v = (d.luas_fuel_ha || {})[k];
      h += '<tr><td><span class="sw" style="background:' + WARNA_FUEL[k] + '"></span>' + NAMA_FUEL[k] + '</td><td>' + angka(v) + '</td><td>' +
        angka(100 * v / tot) + '%</td><td>' + batang(100 * v / tot) + '</td></tr>';
    });
    h += '<tr class="total"><td>Total</td><td>' + angka(tot) + '</td><td></td><td></td></tr></table>';
  }
  $('tabelK').innerHTML = h;
  // per resort
  if (d.kawasan && d.kawasan.resort && d.kawasan.resort.length) {
    h = '<table><tr><th>Resort</th><th>Luas (ha)</th><th>Sisa K1–K4 (ha)</th><th>Terbakar musim ini (ha)</th><th></th></tr>';
    d.kawasan.resort.forEach(function (r) {
      var s = sisaK(r.sisa), a = sisaK(r.awal);
      h += '<tr><td>' + esc(r.nama) + '</td><td>' + angka(r.luas_ha) + '</td><td>' + angka(s) + '</td><td>' + angka(r.terbakar_musim_ha) +
        '</td><td title="sisa ' + angka(100 * s / a) + '% dari awal musim">' + batang(100 * s / a) + '</td></tr>';
    });
    h += '<tr class="total"><td>Total</td><td>' + angka(kw.luas_ha) + '</td><td>' + angka(sisaK(kw.sisa)) + '</td><td>' + angka(kw.terbakar_musim_ha) +
      '</td><td></td></tr></table><p class="petunjuk">Batang = sisa K1–K4 dibanding awal musim.</p>';
    $('tabelResort').innerHTML = h;
    $('kartuResort').hidden = false;
  }
  // grafik jendela
  var J = d.jendela || [];
  if (J.length) {
    var mx = Math.max.apply(null, J.map(function (j) { return j.baru_ha || 0; })) || 1;
    var bars = J.map(function (j) {
      var p = j.jendela.split('/');
      return '<div class="kol" title="' + tglID(p[0]) + ' – ' + tglID(p[1]) + ': ' + angka(j.baru_ha) + ' ha, ' + j.citra + ' citra"><b>' +
        angka(j.baru_ha) + '</b><i style="height:' + (100 * (j.baru_ha || 0) / mx).toFixed(1) + '%"></i></div>';
    }).join('');
    var lab = J.map(function (j) { var p = j.jendela.split('/')[0]; return '<span>' + (+p.slice(8, 10)) + ' ' + BULAN[+p.slice(5, 7) - 1] + '</span>'; }).join('');
    $('grafikJendela').className = '';
    $('grafikJendela').innerHTML = '<div class="grafik">' + bars + '</div><div class="grafik-label">' + lab + '</div>' +
      '<p class="petunjuk">Hektar bekas terbakar baru per jendela (tanggal awal jendela). Total musim ini ' + angka(d.bekas_terbakar_musim_ha) + ' ha.</p>';
  } else $('grafikJendela').textContent = 'Belum ada data jendela.';
  // tab landscape
  var baris = [
    ['Tanggal update', tglID(d.tanggal)],
    ['Citra Sentinel-2 terakhir', ct ? tglID(ct, true) + ' WITA' : '–'],
    ['Citra dipakai pada update ini', angka((d.citra || []).length) + ' scene'],
    ['Status jendela tersimpan s.d.', d.status_jendela ? tglID(d.status_jendela) : '–'],
    ['Awal musim', tglID(d.awal_musim)],
    ['Bekas terbakar musim ini', angka(d.bekas_terbakar_musim_ha) + ' ha'],
    ['Metode', esc(d.metode || '') + (d.hotspot ? ' + titik panas FIRMS' : '')],
    ['Piksel savana cerah (jendela terakhir)', d.piksel_cerah_persen != null ? angka(d.piksel_cerah_persen, 1) + '%' : '–'],
    ['Jadwal', esc(d.jadwal || 'tiap ada citra Sentinel-2 baru (dicek 2x sehari)')],
    ['File landscape', esc(d.lcp || '–')]
  ];
  $('tabelStatus').innerHTML = '<table>' + baris.map(function (b) { return '<tr><td>' + b[0] + '</td><td>' + b[1] + '</td></tr>'; }).join('') + '</table>';
  var lf = d.luas_fuel_ha || {}, tl = Object.keys(lf).reduce(function (s, k) { return s + lf[k]; }, 0);
  $('tabelFuel').innerHTML = '<table><tr><th>Fuel model</th><th>Luas (ha)</th><th>Porsi</th><th></th></tr>' + Object.keys(lf).sort(function (a, b) { return a - b; }).map(function (k) {
    return '<tr><td><span class="sw" style="background:' + (WARNA_FUEL[k] || '#999') + '"></span>' + k + ' ' + (NAMA_FUEL[k] || '') + '</td><td>' + angka(lf[k]) +
      '</td><td>' + angka(100 * lf[k] / tl, 1) + '%</td><td>' + batang(100 * lf[k] / tl) + '</td></tr>';
  }).join('') + '</table>';
  $('tabelJendela').innerHTML = J.length ? '<table><tr><th>Jendela</th><th>Citra</th><th>Terbakar baru (ha)</th><th>Gelap tanpa titik panas (ha)</th><th>Validasi 9 Sep</th></tr>' +
    J.map(function (j) {
      var p = j.jendela.split('/');
      return '<tr><td>' + tglID(p[0]) + ' – ' + tglID(p[1]) + '</td><td>' + angka(j.citra) + '</td><td>' + angka(j.baru_ha, 1) + '</td><td>' +
        angka(j.tanpa_hotspot_diabaikan_ha, 1) + '</td><td>' + (j.validasi_9sep_persen != null ? angka(j.validasi_9sep_persen, 1) + '%' : '–') + '</td></tr>';
    }).join('') + '</table>' : '<p class="muted">Belum ada data.</p>';
}
fetch('status/landscape_terbaru.json', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(isiStatus).catch(function () {
  $('lencanaLandscape').querySelector('span').textContent = 'Status landscape belum ada';
});

/* ---------- riwayat (GitHub Release sim-*) ---------- */
var RILIS = [];
function dataDari(rel) {
  var m = /```json\s*([\s\S]*?)```/.exec(rel.body || '');
  if (!m) return null;
  try { return JSON.parse(m[1]); } catch (e) { return null; }
}
function aset(rel, nama) { var a = (rel.assets || []).filter(function (x) { return x.name === nama; })[0]; return a && a.browser_download_url; }
var NAMA_TAJUK = { finney: 'Finney (1998)', scott: 'Scott/Reinhardt (2001)', tanpa: 'tanpa tajuk' };
function muatRilis() {
  $('tabelRiwayat').innerHTML = '<div class="riwayat-kosong">Memuat…</div>';
  return fetch(API + '/releases?per_page=60', { cache: 'no-store' }).then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function (list) {
    RILIS = (list || []).filter(function (r) { return /^sim-/.test(r.tag_name || ''); });
    isiRiwayat();
  }).catch(function () {
    $('tabelRiwayat').innerHTML = '<div class="riwayat-kosong">Riwayat tidak bisa dimuat (batas API GitHub per jam). Coba lagi nanti.</div>';
    $('kpiSim').textContent = '–'; $('kpiSimSub').textContent = 'riwayat belum bisa dimuat';
  });
}
function isiRiwayat() {
  $('kpiSim').textContent = angka(RILIS.length);
  $('kpiSimSub').textContent = RILIS.length ? 'terakhir ' + tglID(keWITA(RILIS[0].created_at), true) + ' WITA' : 'belum ada';
  if (!RILIS.length) { $('tabelRiwayat').innerHTML = '<div class="riwayat-kosong">Belum ada simulasi.</div>'; return; }
  var h = '<table><tr><th>Dijalankan (WITA)</th><th class="kiri">Mode</th><th class="kiri">Titik api</th><th class="kiri">Lokasi</th><th>Mulai</th><th>Lama</th><th class="kiri">Api tajuk</th><th>Luas terbakar</th><th></th></tr>';
  RILIS.forEach(function (rel, i) {
    var d = dataDari(rel) || {}, m = d.masukan || {}, mode = (m.mode || d.mode || '').toLowerCase();
    var t = (m.titik || '').split(','), lok = lokasi(+t[0], +t[1]);
    h += '<tr><td>' + tglID(keWITA(rel.created_at), true) + '</td><td class="kiri"><span class="lencana-mode ' + mode + '">' + mode.toUpperCase() + '</span></td><td class="kiri">' +
      esc(m.titik || '') + '</td><td class="kiri">' + esc(lok ? lok.teks : '') + '</td><td>' + (mode === 'farsite' && m.mulai ? tglID(m.mulai.replace(' ', 'T'), true) : '–') +
      '</td><td>' + angka(m.lama) + ' jam</td><td class="kiri">' + esc(NAMA_TAJUK[d.metode_tajuk] || d.metode_tajuk || '') + '</td><td><b>' + angka(d.luas_terbakar_ha) + ' ha</b></td>' +
      '<td><button class="garis kecil" data-rilis="' + i + '">Peta</button> ' + (aset(rel, 'hasil.zip') ? '<a class="tombol garis kecil" style="padding:5px 10px;font-size:13px" href="' + aset(rel, 'hasil.zip') + '">Unduh</a>' : '') + '</td></tr>';
  });
  $('tabelRiwayat').innerHTML = h + '</table>';
  $('tabelRiwayat').querySelectorAll('[data-rilis]').forEach(function (b) {
    b.addEventListener('click', function () { buka('simulasi'); tampil(RILIS[+b.dataset.rilis]); window.scrollTo(0, 0); });
  });
}
$('muatUlang').addEventListener('click', muatRilis);
muatRilis();

/* ---------- data lapisan (GeoJSON) ---------- */
var DATA = {};
function muatData(nama) {
  return fetch('data/' + nama + '.geojson').then(function (r) { if (!r.ok) throw new Error(nama); return r.json(); })
    .then(function (j) { DATA[nama] = j; return j; });
}
var siapData = Promise.all(['batas_tn', 'batas_resort', 'grid_1km', 'jalur_patroli'].map(function (n) {
  return muatData(n).catch(function () { return null; });
})).then(function () { if (RILIS.length) isiRiwayat(); });
function dalamCincin(x, y, r) {
  var c = false;
  for (var i = 0, j = r.length - 1; i < r.length; j = i++) {
    var xi = r[i][0], yi = r[i][1], xj = r[j][0], yj = r[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) c = !c;
  }
  return c;
}
function dalamFitur(x, y, g) {
  var ps = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
  return ps.some(function (p) { return dalamCincin(x, y, p[0]) && !p.slice(1).some(function (h) { return dalamCincin(x, y, h); }); });
}
function cari(nama, lat, lon) {
  var fc = DATA[nama]; if (!fc || !isFinite(lat) || !isFinite(lon)) return null;
  for (var i = 0; i < fc.features.length; i++) if (dalamFitur(lon, lat, fc.features[i].geometry)) return fc.features[i].properties;
  return null;
}
function dalamAOI(lat, lon) { return lat >= AOI[0][0] && lat <= AOI[1][0] && lon >= AOI[0][1] && lon <= AOI[1][1]; }
function lokasi(lat, lon) {
  if (!isFinite(lat) || !isFinite(lon)) return null;
  var r = cari('batas_resort', lat, lon), g = cari('grid_1km', lat, lon), tn = r || cari('batas_tn', lat, lon);
  var bag = [];
  bag.push(r ? r.nama : (tn ? 'Kawasan TN' : 'Di luar kawasan TN'));
  if (g) bag.push('Grid ' + g.id + (g.nilai !== null && g.nilai !== undefined ? ' (Nilai ' + g.nilai + ')' : ''));
  return { teks: bag.join(' · '), resort: r, grid: g, aoi: dalamAOI(lat, lon) };
}

/* ---------- peta ---------- */
var peta = null, kontrol = null, penanda = null, kotakAA = null, lapisHasil = null, legendaWaktu = null;
var WARNA_RESORT = { 1: '#ff7043', 2: '#ffca28', 3: '#26c6da', 4: '#ab47bc', 5: '#66bb6a', 6: '#ec407a' };
var GAYA_JALUR = {
  'Jalur Patroli': { color: '#ff6d00', weight: 2.4 },
  'Jalur Eksplorasi': { color: '#d500f9', weight: 2 },
  'Jalur Pendakian': { color: '#00b0ff', weight: 2.6 },
  'Jalan Lain': { color: '#ffeb3b', weight: 2, dashArray: '5 4' }
};
function siapkanPeta() {
  if (peta) { setTimeout(function () { peta.invalidateSize(); }, 60); return; }
  peta = L.map('peta', { zoomSnap: 0.5 }).fitBounds(AOI);
  [['pGrid', 410], ['pHasil', 420], ['pBatas', 430], ['pJalur', 440]].forEach(function (p) { peta.createPane(p[0]).style.zIndex = p[1]; });
  var citra = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 19, attribution: 'Citra © Esri' }).addTo(peta);
  var topo = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', { maxZoom: 17, attribution: '© OpenTopoMap, © OpenStreetMap' });
  var osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' });
  kontrol = L.control.layers({ 'Citra satelit': citra, 'Topografi': topo, 'OpenStreetMap': osm }, {}, { collapsed: window.innerWidth < 980 }).addTo(peta);
  var aoi = L.rectangle(AOI, { color: '#2bb3a0', weight: 1.2, fill: false, dashArray: '5 5', interactive: false, pane: 'pBatas' }).addTo(peta);
  siapData.then(function () {
    var urut = [];
    if (DATA.batas_tn) {
      var tn = L.layerGroup([
        L.geoJSON(DATA.batas_tn, { pane: 'pBatas', interactive: false, style: { color: '#ffffff', weight: 5, opacity: 0.55, fill: false } }),
        L.geoJSON(DATA.batas_tn, { pane: 'pBatas', interactive: false, style: { color: '#0b6b35', weight: 2.6, fill: false } })
      ]).addTo(peta);
      urut.push([tn, 'Batas TN Tambora']);
    }
    if (DATA.batas_resort) {
      var rs = L.layerGroup().addTo(peta);
      L.geoJSON(DATA.batas_resort, { pane: 'pBatas', interactive: false, style: function (f) {
        var c = WARNA_RESORT[f.properties.kode] || '#ffffff';
        return { color: c, weight: 1.8, dashArray: '7 4', fillColor: c, fillOpacity: 0.07 };
      } }).addTo(rs);
      DATA.batas_resort.features.forEach(function (f) {
        if (f.properties.label) L.tooltip(f.properties.label, { content: esc(f.properties.nama), permanent: true, direction: 'center', className: 'lbl-resort' }).addTo(rs);
      });
      urut.push([rs, 'Batas resort']);
    }
    if (DATA.jalur_patroli) {
      var jl = L.geoJSON(DATA.jalur_patroli, { pane: 'pJalur', bubblingMouseEvents: false,
        style: function (f) { var g = GAYA_JALUR[f.properties.tipe] || { color: '#ffffff', weight: 2 }; return L.extend({ opacity: 0.95 }, g); },
        onEachFeature: function (f, l) {
          var p = f.properties;
          l.bindPopup('<b>' + esc(p.nama) + '</b><table><tr><td>Tipe</td><td>' + esc(p.tipe) + '</td></tr><tr><td>Keterangan</td><td>' + esc(p.ket || '–') +
            '</td></tr><tr><td>Panjang</td><td>' + angka(p.panjang_km, 2) + ' km</td></tr>' + (p.kmh ? '<tr><td>Kecepatan tempuh</td><td>' + p.kmh + ' km/j</td></tr>' : '') + '</table>');
        } }).addTo(peta);
      urut.push([jl, 'Jalur patroli &amp; jalan']);
    }
    if (DATA.grid_1km) {
      var gr = L.layerGroup();
      L.geoJSON(DATA.grid_1km, { pane: 'pGrid', interactive: false, style: function (f) {
        var n = f.properties.nilai;
        return { color: '#ffffff', weight: 0.7, opacity: 0.75, fillColor: n === 1 ? '#43a047' : '#9e9e9e', fillOpacity: n === 1 ? 0.22 : n === 0 ? 0.16 : 0 };
      } }).addTo(gr);
      DATA.grid_1km.features.forEach(function (f) {
        var c = f.geometry.coordinates[0], lat = 0, lon = 0;
        for (var i = 0; i < c.length - 1; i++) { lon += c[i][0]; lat += c[i][1]; }
        L.tooltip([lat / (c.length - 1), lon / (c.length - 1)], { content: esc(f.properties.id), permanent: true, direction: 'center', className: 'lbl-grid' }).addTo(gr);
      });
      urut.push([gr, 'Grid 1 km']);
    }
    urut.push([aoi, 'Batas landscape (AOI)']);
    urut.forEach(function (u) { kontrol.addOverlay(u[0], u[1]); });
  });
  var leg = L.control({ position: 'bottomleft' });
  leg.onAdd = function () {
    var e = L.DomUtil.create('div', 'legenda');
    e.innerHTML = '<details' + (window.innerWidth >= 980 ? ' open' : '') + '><summary><b style="display:inline">Keterangan</b></summary>' +
      '<div><span class="gar" style="border-color:#0b6b35"></span>Batas TN</div>' +
      '<div><span class="gar" style="border-top-style:dashed;border-color:#ff7043"></span>Batas resort</div>' +
      Object.keys(GAYA_JALUR).map(function (k) { return '<div><span class="gar" style="border-color:' + GAYA_JALUR[k].color + (GAYA_JALUR[k].dashArray ? ';border-top-style:dashed' : '') + '"></span>' + k + '</div>'; }).join('') +
      '<div><span class="kot" style="border-color:#fff;background:rgba(67,160,71,.4)"></span>Grid 1 km, Nilai 1</div>' +
      '<div><span class="kot" style="border-color:#fff;background:rgba(158,158,158,.35)"></span>Grid 1 km, Nilai 0</div>' +
      '<div><span class="kot" style="border-style:dashed;border-color:#f98e09"></span>Analysis Area</div></details>';
    L.DomEvent.disableClickPropagation(e);
    return e;
  };
  leg.addTo(peta);
  function aturZoom() { peta.getContainer().classList.toggle('zoom-jauh', peta.getZoom() < 13); }
  peta.on('zoomend', aturZoom); aturZoom();
  peta.on('click', function (e) { taruh(e.latlng.lat, e.latlng.lng); });
  var t = baca(); if (t) taruh(t[0], t[1]);
  setTimeout(function () { peta.invalidateSize(); if (!lapisHasil) peta.fitBounds(AOI); }, 80);
}

/* ---------- formulir simulasi ---------- */
function baca() {
  var p = $('titik').value.split(','), a = parseFloat(p[0]), b = parseFloat(p[1]);
  return (isFinite(a) && isFinite(b)) ? [a, b] : null;
}
var IKON_API = L.divIcon({ className: '', iconSize: [30, 40], iconAnchor: [15, 39], html:
  '<svg width="30" height="40" viewBox="0 0 30 40" aria-label="Titik api"><path d="M15 39C15 39 2 23.5 2 14.5a13 13 0 0 1 26 0C28 23.5 15 39 15 39z" fill="#c4501b" stroke="#fff" stroke-width="2"/>' +
  '<path d="M15 6c1.5 3.2 5.5 5.3 5.5 9.6A5.5 5.5 0 0 1 9.5 16c0-2.2 1.2-3.6 2.4-4.6.2 1.5.9 2.6 2 3.1-.4-3.1.1-6.1 1.1-8.5z" fill="#ffd54f"/></svg>' });
function pasangPenanda(ll) {
  if (penanda) penanda.setLatLng(ll); else penanda = L.marker(ll, { icon: IKON_API, keyboard: false }).addTo(peta);
}
function taruh(lat, lon) {
  $('titik').value = lat.toFixed(5) + ',' + lon.toFixed(5);
  if (peta) {
    pasangPenanda([lat, lon]);
    gambarAA();
  }
  infoTitik();
  jadwalCuaca();
}
function infoTitik() {
  var t = baca(), el = $('infoTitik');
  if (!t) { el.innerHTML = '<span class="muted">Klik peta untuk menaruh titik api.</span>'; return; }
  siapData.then(function () {
    var l = lokasi(t[0], t[1]);
    el.innerHTML = l.aoi ? esc(l.teks) : '<span class="err-teks">Di luar batas landscape (AOI); simulasi akan ditolak.</span>';
  });
}
function gambarAA() {
  var t = baca(); if (!t || !peta) return;
  var r = parseFloat($('radius').value || 8), dLat = r / 110.6, dLon = r / (111.3 * Math.cos(t[0] * Math.PI / 180));
  var b = [[t[0] - dLat, t[1] - dLon], [t[0] + dLat, t[1] + dLon]];
  if (kotakAA) kotakAA.setBounds(b); else kotakAA = L.rectangle(b, { color: '#f98e09', weight: 1.4, fill: false, dashArray: '6 4', interactive: false }).addTo(peta);
}
$('titik').addEventListener('change', function () { var t = baca(); if (t) { taruh(t[0], t[1]); if (peta) peta.panTo(t); } });
$('radius').addEventListener('input', gambarAA);
$('mulai').value = sekarangWITA();
$('mulai').max = tambahHari(hariIniWITA(), 15) + 'T23:00';

function sumberCuaca() { return document.querySelector('input[name=sumberCuaca]:checked').value; }
function aturMode() {
  var mtt = $('mode').value === 'mtt';
  $('hintAngin').textContent = mtt ? 'MTT: angin tetap selama simulasi. Diisi otomatis dari cuaca jam mulai; boleh diubah.'
    : 'FARSITE: kosongkan agar angin mengikuti cuaca per jam. Isi untuk memaksa angin tetap.';
  $('kec').placeholder = mtt ? 'wajib' : 'dari cuaca'; $('arah').placeholder = mtt ? 'wajib' : 'dari cuaca';
  if (!mtt && $('kec').dataset.auto === '1') { $('kec').value = ''; $('arah').value = ''; $('kec').dataset.auto = '0'; }
  isiAnginMtt();
}
$('mode').addEventListener('change', aturMode);
['kec', 'arah'].forEach(function (id) { $(id).addEventListener('input', function () { $('kec').dataset.auto = '0'; }); });
document.querySelectorAll('input[name=sumberCuaca]').forEach(function (r) {
  r.addEventListener('change', function () { $('blokFile').hidden = sumberCuaca() !== 'file'; CUACA = null; tampilCuaca(); jadwalCuaca(); });
});
['mulai', 'lama'].forEach(function (id) { $(id).addEventListener('change', jadwalCuaca); });

/* ---------- cuaca per jam -> .wxs ---------- */
var OM_ARSIP = 'https://archive-api.open-meteo.com/v1/archive', OM_PRAKIRAAN = 'https://api.open-meteo.com/v1/forecast';
var VARS = ['temperature_2m', 'relative_humidity_2m', 'precipitation', 'wind_speed_10m', 'wind_direction_10m', 'cloud_cover'];
var CUACA = null, nomorPermintaan = 0, timerCuaca = null, teksFile = '', namaFile = '';
function jadwalCuaca() { clearTimeout(timerCuaca); timerCuaca = setTimeout(perbaruiCuaca, 500); }
function pesanCuaca(teks, kelas) { var e = $('kotakCuaca'); e.textContent = teks || ''; e.className = 'pesan ' + (kelas || 'ok'); }
function ambil(h, v) { if (h[v]) return h[v]; var k = Object.keys(h).filter(function (x) { return x.indexOf(v + '_') === 0; })[0]; return k ? h[k] : null; }
function dariOpenMeteo(data) {
  if (data.error) throw new Error('Open-Meteo menolak: ' + (data.reason || ''));
  var h = data.hourly; if (!h || !h.time) throw new Error('Respons Open-Meteo tanpa data per jam.');
  var col = VARS.map(function (v) { var c = ambil(h, v); if (!c) throw new Error('Variabel ' + v + ' tidak ada.'); return c; });
  var rows = [], kosong = [];
  h.time.forEach(function (t, i) {
    var v = col.map(function (c) { return c[i]; });
    if (v.some(function (x) { return x === null || x === undefined || Number.isNaN(x); })) { kosong.push(t); return; }
    rows.push({ iso: t.slice(0, 16), temp: Math.round(v[0]), rh: Math.max(0, Math.min(99, Math.round(v[1]))), pcp: Math.max(0, v[2]),
      ws: Math.round(v[3]), wsRaw: v[3], wd: Math.round(v[4]) % 361, cc: Math.max(0, Math.min(100, Math.round(v[5]))) });
  });
  return { rows: rows, kosong: kosong, elev: Math.round(data.elevation || 0), lat: data.latitude, lon: data.longitude };
}
function keWxs(rows, elev) {
  var L2 = ['RAWS_ELEVATION: ' + Math.round(elev), 'RAWS_UNITS: Metric', 'RAWS: ' + rows.length,
    'Year  Mth  Day  Time  Temp   RH  HrlyPcp  WindSpd  WindDir  CloudCov'];
  rows.forEach(function (r) {
    var s = r.iso;
    L2.push([pad(+s.slice(0, 4), 4), pad(+s.slice(5, 7), 4), pad(+s.slice(8, 10), 4), pad(s.slice(11, 13) + s.slice(14, 16), 5), pad(r.temp, 5), pad(r.rh, 4),
      pad(r.pcp.toFixed(2), 8), pad(r.ws, 8), pad(r.wd, 8), pad(r.cc, 9)].join(' '));
  });
  return L2.join('\r\n') + '\r\n';
}
function bacaWxs(teks) {
  var metric = !/RAWS_UNITS:\s*English/i.test(teks), elev = 0, rows = [];
  teks.split(/\r?\n/).forEach(function (l) {
    var s = l.trim(); if (!s) return;
    if (/^RAWS_ELEVATION/i.test(s)) { elev = parseFloat(s.split(':')[1]) || 0; return; }
    var a = s.split(/\s+/); if (a.length < 9 || !/^\d{4}$/.test(a[0])) return;
    var hm = ('0000' + a[3]).slice(-4), t = +a[4], ws = +a[7], pcp = +a[6];
    if (!metric) { t = (t - 32) * 5 / 9; ws = ws * 1.609344; pcp = pcp * 0.254; }
    rows.push({ iso: a[0] + '-' + ('0' + a[1]).slice(-2) + '-' + ('0' + a[2]).slice(-2) + 'T' + hm.slice(0, 2) + ':' + hm.slice(2), temp: Math.round(t), rh: +a[5],
      pcp: pcp, ws: Math.round(ws), wsRaw: ws, wd: +a[8], cc: a.length > 9 ? +a[9] : 0 });
  });
  if (!/RAWS/i.test(teks.slice(0, 400)) || !rows.length) throw new Error('File ini bukan .wxs yang dikenali.');
  rows.sort(function (x, y) { return x.iso < y.iso ? -1 : 1; });
  return { rows: rows, kosong: [], elev: metric ? elev : elev * 0.3048 };
}
function periodeSim() { var m = $('mulai').value, l = parseFloat($('lama').value) || 0; return { mulai: m, akhir: tambahJam(m, l), lama: l }; }
function perbaruiCuaca() {
  var p = periodeSim();
  if (!p.mulai) { CUACA = null; tampilCuaca(); pesanCuaca('Isi tanggal dan jam mulai.', 'warn'); return; }
  if (sumberCuaca() === 'file') {
    if (!teksFile) { CUACA = null; tampilCuaca(); pesanCuaca('Pilih file .wxs.', 'warn'); return; }
    try { var w = bacaWxs(teksFile); CUACA = L.extend(w, { teks: teksFile, sumber: 'File ' + namaFile, periode: p }); tampilCuaca(); }
    catch (e) { CUACA = null; tampilCuaca(); pesanCuaca(e.message, 'err'); }
    return;
  }
  var t = baca();
  if (!t) { CUACA = null; tampilCuaca(); pesanCuaca('Cuaca per jam diambil otomatis setelah titik api dipilih.'); return; }
  var mulai = p.mulai.slice(0, 10), awal = tambahHari(mulai, -3), akhir = p.akhir.slice(0, 10), kini = hariIniWITA();
  if (akhir > tambahHari(kini, 15)) { CUACA = null; tampilCuaca(); pesanCuaca('Prakiraan cuaca hanya tersedia sampai 16 hari ke depan. Majukan tanggal mulai.', 'err'); return; }
  var prakiraan = akhir >= tambahHari(kini, -5);
  var q = new URLSearchParams({ latitude: t[0].toFixed(4), longitude: t[1].toFixed(4), start_date: awal, end_date: akhir, hourly: VARS.join(','),
    timezone: 'Asia/Makassar', wind_speed_unit: 'kmh', temperature_unit: 'celsius', precipitation_unit: 'mm' });
  var url = (prakiraan ? OM_PRAKIRAAN : OM_ARSIP) + '?' + q.toString();
  var no = ++nomorPermintaan;
  pesanCuaca('Mengambil cuaca per jam ' + tglID(awal) + ' – ' + tglID(akhir) + '…');
  fetch(url).then(function (r) { return r.json(); }).then(function (data) {
    if (no !== nomorPermintaan) return;
    var w = dariOpenMeteo(data);
    if (!w.rows.length) throw new Error('Data cuaca kosong untuk tanggal ini.');
    CUACA = L.extend(w, { teks: keWxs(w.rows, w.elev), url: url, periode: p,
      sumber: prakiraan ? 'Prakiraan Open-Meteo (model terbaik: ECMWF IFS dan lainnya)' : 'Reanalisis ERA5 / ERA5-Land (arsip Open-Meteo)' });
    tampilCuaca();
  }).catch(function (e) {
    if (no !== nomorPermintaan) return;
    CUACA = null; tampilCuaca();
    pesanCuaca('Cuaca tidak bisa diambil: ' + e.message + '\nSimulasi tetap bisa dijalankan; cuaca lalu diambil di server.', 'warn');
  });
}
function dalamPeriode(rows, p) { return rows.filter(function (r) { return r.iso >= p.mulai && r.iso < p.akhir; }); }
function tampilCuaca() {
  var w = CUACA;
  $('ringkasCuaca').hidden = !w;
  if (!w) return;
  var p = periodeSim(), rs = dalamPeriode(w.rows, p);
  var jamPerlu = Math.round(p.lama), kurang = rs.length < jamPerlu;
  if (!rs.length) {
    pesanCuaca((w.sumber || 'Cuaca') + ' tidak mencakup ' + tglID(p.mulai, true) + ' – ' + tglID(p.akhir, true) + '.', 'err');
    $('ringkasCuaca').hidden = true; return;
  }
  var u = 0, v = 0, mx = rs[0];
  rs.forEach(function (r) { var a = r.wd * Math.PI / 180; u += r.wsRaw * Math.sin(a); v += r.wsRaw * Math.cos(a); if (r.wsRaw > mx.wsRaw) mx = r; });
  var rata = rs.reduce(function (s, r) { return s + r.wsRaw; }, 0) / rs.length, dom = (Math.atan2(u, v) * 180 / Math.PI + 360) % 360;
  var tmin = Math.min.apply(null, rs.map(function (r) { return r.temp; })), tmax = Math.max.apply(null, rs.map(function (r) { return r.temp; }));
  var rh = Math.min.apply(null, rs.map(function (r) { return r.rh; })), hujan = rs.reduce(function (s, r) { return s + r.pcp; }, 0);
  var r0 = rs[0];
  $('statCuaca').innerHTML = [
    [r0.ws + ' km/j ' + mata(r0.wd), 'Angin jam mulai (dari ' + r0.wd + '°)'],
    [angka(rata, 1) + ' km/j', 'Angin rata-rata'],
    [mx.ws + ' km/j', 'Maks, pukul ' + mx.iso.slice(11, 16) + (mx.iso.slice(0, 10) !== r0.iso.slice(0, 10) ? ' (' + tglID(mx.iso) + ')' : '')],
    [mata(dom) + ' (' + Math.round(dom) + '°)', 'Arah dominan (asal)'],
    [tmin + '–' + tmax + ' °C · RH ' + rh + '%', 'Suhu · RH minimum'],
    [angka(hujan, 1) + ' mm', 'Total hujan']
  ].map(function (s) { return '<div><b>' + s[0] + '</b><span>' + s[1] + '</span></div>'; }).join('');
  $('tabelCuaca').innerHTML = '<table><tr><th>Waktu WITA</th><th>Angin km/j</th><th>Arah</th><th>Suhu °C</th><th>RH %</th><th>Hujan mm</th></tr>' + rs.map(function (r) {
    return '<tr><td>' + tglID(r.iso, true) + '</td><td>' + r.ws + '</td><td><span class="panah" style="transform:rotate(' + ((r.wd + 180) % 360) + 'deg)">↑</span> ' + r.wd + '° ' + mata(r.wd) +
      '</td><td>' + r.temp + '</td><td>' + r.rh + '</td><td>' + angka(r.pcp, 1) + '</td></tr>';
  }).join('') + '</table>';
  var info = w.sumber + '. ' + w.rows.length + ' jam (' + tglID(w.rows[0].iso, true) + ' – ' + tglID(w.rows[w.rows.length - 1].iso, true) + '), elevasi ' + angka(w.elev) + ' m' +
    (w.lat !== undefined ? ', titik grid ' + (+w.lat).toFixed(3) + ', ' + (+w.lon).toFixed(3) : '') + '.';
  if (kurang || (w.kosong && w.kosong.length)) pesanCuaca(info + '\nPerhatian: hanya ' + rs.length + ' dari ' + jamPerlu + ' jam simulasi ada datanya. Coba tanggal lain atau file .wxs sendiri.', 'warn');
  else pesanCuaca(info, 'ok');
  $('infoWxs').textContent = sumberCuaca() === 'file' ? '' : 'Termasuk 3 hari pengkondisian kadar air.';
  isiAnginMtt();
}
function isiAnginMtt() {
  if ($('mode').value !== 'mtt' || !CUACA) return;
  if ($('kec').value !== '' && $('kec').dataset.auto !== '1') return;
  var rs = dalamPeriode(CUACA.rows, periodeSim()); if (!rs.length) return;
  $('kec').value = rs[0].ws; $('arah').value = rs[0].wd % 360; $('kec').dataset.auto = '1';
}
$('wxs').addEventListener('change', function () {
  var f = this.files[0]; teksFile = ''; namaFile = '';
  if (!f) { jadwalCuaca(); return; }
  f.text().then(function (t) { teksFile = t; namaFile = f.name; perbaruiCuaca(); });
});
$('unduhWxs').addEventListener('click', function () {
  if (!CUACA) return;
  var t = baca() || [0, 0];
  var nama = 'cuaca_' + t[0].toFixed(4) + '_' + t[1].toFixed(4) + '_' + CUACA.rows[0].iso.slice(0, 10).replace(/-/g, '') + '_' +
    CUACA.rows[CUACA.rows.length - 1].iso.slice(0, 10).replace(/-/g, '') + '.wxs';
  var a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([CUACA.teks], { type: 'text/plain' })); a.download = nama;
  document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
});
aturMode();

/* ---------- menjalankan dan memantau simulasi ---------- */
function status(t, kelas) { var s = $('status'); s.textContent = t; s.className = 'pesan ' + (kelas || 'ok'); }
$('jalan').addEventListener('click', function () {
  var t = baca();
  if (!t) return status('Klik peta dulu untuk menaruh titik api.', 'err');
  if (!dalamAOI(t[0], t[1])) return status('Titik api di luar batas landscape (AOI).', 'err');
  if (!C.webapp) return status('URL Apps Script belum diisi di docs/simulasi_config.js.', 'err');
  var mode = $('mode').value, kec = $('kec').value, arah = $('arah').value, p = periodeSim();
  if (!p.mulai) return status('Isi tanggal dan jam mulai.', 'err');
  if (!(p.lama > 0 && p.lama <= 72)) return status('Lama simulasi 1–72 jam.', 'err');
  if (mode === 'mtt' && (kec === '' || arah === '')) return status('MTT perlu kecepatan dan arah angin (tunggu cuaca terisi, atau isi sendiri).', 'err');
  if ((kec === '') !== (arah === '')) return status('Isi kecepatan dan arah angin sekaligus, atau kosongkan keduanya.', 'err');
  if (sumberCuaca() === 'file' && !CUACA) return status('File .wxs belum dipilih atau tidak terbaca.', 'err');
  if (mode === 'farsite' && CUACA && dalamPeriode(CUACA.rows, p).length < Math.round(p.lama) && sumberCuaca() === 'file')
    return status('File .wxs tidak mencakup seluruh waktu simulasi.', 'err');
  var id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  var q = { mode: mode, titik: $('titik').value, mulai: p.mulai.replace('T', ' '), lama_jam: String(p.lama), angin: kec === '' ? '' : kec + ',' + arah,
    tajuk: $('tajuk').value, radius_km: $('radius').value, res: $('res').value, landscape: $('ls').value, id: id };
  if (mode === 'farsite' && CUACA && CUACA.teks) q.wxs = CUACA.teks;
  $('jalan').disabled = true;
  status('Mengirim permintaan…');
  fetch(C.webapp, { method: 'POST', body: JSON.stringify(q) }).then(function (r) { return r.json(); }).then(function (d) {
    if (!d.ok) throw new Error(d.error || 'ditolak');
    status('Permintaan diterima. Menunggu GitHub Actions memulai…');
    pantau(id, Date.now());
  }).catch(function (e) { status('Gagal: ' + e.message, 'err'); $('jalan').disabled = false; });
});
function pantau(id, t0) {
  var runId = null, runUrl = null;
  function menit() { return Math.round((Date.now() - t0) / 60000); }
  function selesai(pesan, kelas) { status(pesan, kelas); $('jalan').disabled = false; }
  function ambilRilis(coba) {
    fetch(API + '/releases/tags/sim-' + id, { cache: 'no-store' }).then(function (r) {
      if (r.ok) return r.json().then(function (rel) { tampil(rel); selesai('Selesai dalam ' + menit() + ' menit.', 'ok'); muatRilis(); });
      if (coba < 4) setTimeout(function () { ambilRilis(coba + 1); }, 10000);
      else selesai('Simulasi selesai tetapi hasil belum terbit. Lihat log: ' + runUrl, 'warn');
    }).catch(function () { setTimeout(function () { ambilRilis(coba + 1); }, 20000); });
  }
  function langkah() {
    var url = runId ? API + '/actions/runs/' + runId : API + '/actions/workflows/simulasi.yml/runs?per_page=10&event=workflow_dispatch';
    fetch(url, { cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(function (d) {
      var run = runId ? d : (d.workflow_runs || []).filter(function (x) { return (x.display_title || '').indexOf('[' + id + ']') >= 0; })[0];
      if (!run) {
        if (menit() > 15) return selesai('GitHub Actions belum memulai simulasi setelah 15 menit. Cek tab Actions di repo.', 'warn');
        status('Menunggu GitHub Actions memulai… (' + menit() + ' menit)'); return setTimeout(langkah, 20000);
      }
      runId = run.id; runUrl = run.html_url;
      if (run.status === 'completed') {
        if (run.conclusion === 'success') { status('Selesai, mengambil hasil…'); return ambilRilis(0); }
        return selesai('Simulasi gagal (' + run.conclusion + '). Lihat log: ' + runUrl, 'err');
      }
      status((run.status === 'queued' ? 'Antre' : 'Berjalan') + ' di GitHub Actions, ' + menit() + ' menit. Biasanya 2–20 menit.\nLog: ' + runUrl);
      setTimeout(langkah, 40000);
    }).catch(function () { status('Menunggu… (batas API GitHub, dicoba lagi)'); setTimeout(langkah, 60000); });
  }
  langkah();
}
function tampil(rel) {
  siapkanPeta();
  var d = dataDari(rel); if (!d) return;
  if (lapisHasil) { peta.removeLayer(lapisHasil); kontrol.removeLayer(lapisHasil); }
  var png = aset(rel, 'overlay.png');
  if (png && d.batas) {
    lapisHasil = L.imageOverlay(png, d.batas, { opacity: 0.85, pane: 'pHasil' }).addTo(peta);
    kontrol.addOverlay(lapisHasil, 'Hasil simulasi');
    peta.fitBounds(d.batas);
  }
  if (d.titik_latlon) {
    $('titik').value = d.titik_latlon[0].toFixed(5) + ',' + d.titik_latlon[1].toFixed(5);
    pasangPenanda(d.titik_latlon);
    infoTitik();
  }
  if (!legendaWaktu) {
    legendaWaktu = L.control({ position: 'bottomright' });
    legendaWaktu.onAdd = function () { var e = L.DomUtil.create('div', 'legenda'); e.id = 'legWaktu'; return e; };
    legendaWaktu.addTo(peta);
  }
  $('legWaktu').innerHTML = '<b>Waktu tiba api (jam)</b><div class="bar-waktu"></div><span>0</span><span style="float:right">' + angka(d.jam_maks, 1) + '</span>';
  var m = d.masukan || {}, t = d.luas_per_tipe_api_ha || {}, mode = (m.mode || d.mode || '').toLowerCase();
  var tt = (m.titik || '').split(','), lok = lokasi(+tt[0], +tt[1]);
  var baris = [
    ['Mode', mode.toUpperCase()],
    ['Lokasi', lok ? esc(lok.teks) : '–'],
    mode === 'farsite' && m.mulai ? ['Mulai', tglID(m.mulai.replace(' ', 'T'), true) + ' WITA'] : null,
    ['Lama', angka(m.lama) + ' jam'],
    ['Angin', m.angin ? esc(m.angin).replace(',', ' km/j dari ') + '°' : (d.wxs ? 'cuaca per jam (.wxs)' : 'cuaca per jam ERA5/ECMWF')],
    ['Api tajuk', esc(NAMA_TAJUK[d.metode_tajuk] || d.metode_tajuk || '–')],
    ['<b>Luas terbakar</b>', '<b>' + angka(d.luas_terbakar_ha, 1) + ' ha</b>'],
    ['· api permukaan', angka(t.permukaan, 1) + ' ha'],
    ['· tajuk pasif', angka(t.tajuk_pasif, 1) + ' ha'],
    ['· tajuk aktif', angka(t.tajuk_aktif, 1) + ' ha'],
    ['Resolusi', angka(d.sel_m) + ' m']
  ].filter(Boolean);
  $('ringkasHasil').innerHTML = '<table>' + baris.map(function (b) { return '<tr><td>' + b[0] + '</td><td>' + b[1] + '</td></tr>'; }).join('') + '</table>';
  $('unduhHasil').innerHTML = (aset(rel, 'hasil.zip') ? '<a href="' + aset(rel, 'hasil.zip') + '">Unduh semua (GeoTIFF, shapefile, PNG)</a>' : '') +
    (aset(rel, 'peta_waktu_tiba.png') ? '<a href="' + aset(rel, 'peta_waktu_tiba.png') + '" target="_blank" rel="noopener">Peta PNG</a>' : '') +
    (d.run ? '<a href="' + d.run + '" target="_blank" rel="noopener">Log run</a>' : '');
  $('kotakHasil').hidden = false;
}

buka(location.hash.slice(1) || 'ringkasan');
})();
