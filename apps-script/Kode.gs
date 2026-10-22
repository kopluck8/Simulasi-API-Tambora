/**
 * Penghubung Google Drive untuk GitHub Actions (Simulasi API Tambora).
 *
 * Web App ini memberi token akses Drive milik Anda kepada workflow GitHub yang membawa
 * KUNCI rahasia yang sama. Token berlaku 1 jam. Tidak perlu Google Cloud / OAuth consent screen.
 *
 * Pasang: lihat docs/PANDUAN_GOOGLE_DRIVE.md.
 */

function doGet(e) {
  var kunci = PropertiesService.getScriptProperties().getProperty('KUNCI');
  var p = (e && e.parameter) || {};
  if (!kunci || p.kunci !== kunci) {
    return ContentService.createTextOutput(JSON.stringify({ error: 'kunci salah' }))
      .setMimeType(ContentService.MimeType.JSON);
  }
  DriveApp.getRootFolder(); // memastikan izin Drive aktif
  return ContentService.createTextOutput(JSON.stringify({
    access_token: ScriptApp.getOAuthToken(),
    expires_in: 3000
  })).setMimeType(ContentService.MimeType.JSON);
}

/** Jalankan sekali dari editor: membuat KUNCI acak dan menampilkannya di Log. */
function buatKunci() {
  var k = Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, '');
  PropertiesService.getScriptProperties().setProperty('KUNCI', k);
  Logger.log('KUNCI (simpan sebagai secret GDRIVE_KUNCI di GitHub): ' + k);
}

/** Uji dari editor: harus menampilkan nama folder Drive Anda. */
function uji() {
  var r = UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/files?pageSize=3&fields=files(name)', {
    headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() }
  });
  Logger.log(r.getContentText());
}

/* ------------------------------------------------------------------------------------------
 * Halaman peta simulasi (docs/simulasi.html) mengirim permintaan ke sini (POST, teks JSON).
 * Web App meneruskannya ke GitHub Actions (workflow "Simulasi api") memakai token GitHub
 * yang disimpan di Script Properties sebagai GITHUB_TOKEN. Token itu tidak pernah keluar dari sini.
 * ------------------------------------------------------------------------------------------ */
var REPO = 'kopluck8/Simulasi-API-Tambora';
var BATAS = { latMin: -8.45, latMax: -8.05, lonMin: 117.75, lonMax: 118.23 }; // AOI Tambora + tepi

function doPost(e) {
  try {
    var q = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    var inputs = periksa_(q);
    var cache = CacheService.getScriptCache();
    if (cache.get('terakhir')) throw new Error('Tunggu 1 menit sebelum menjalankan simulasi berikutnya.');
    var token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
    if (!token) throw new Error('GITHUB_TOKEN belum diisi di Script Properties.');
    var r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/simulasi.yml/dispatches', {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
      payload: JSON.stringify({ ref: 'main', inputs: inputs }),
      muteHttpExceptions: true
    });
    if (r.getResponseCode() !== 204) throw new Error('GitHub menolak (' + r.getResponseCode() + '): ' + r.getContentText().slice(0, 300));
    cache.put('terakhir', '1', 60);
    return json_({ ok: true, id: inputs.id });
  } catch (err) {
    return json_({ ok: false, error: String(err.message || err) });
  }
}

function periksa_(q) {
  function cek(ok, pesan) { if (!ok) throw new Error(pesan); }
  var mode = String(q.mode || 'farsite');
  cek(mode === 'farsite' || mode === 'mtt', 'mode harus farsite atau mtt');
  var t = String(q.titik || '').split(',');
  var lat = parseFloat(t[0]), lon = parseFloat(t[1]);
  cek(isFinite(lat) && isFinite(lon) && lat >= BATAS.latMin && lat <= BATAS.latMax && lon >= BATAS.lonMin && lon <= BATAS.lonMax,
      'titik api di luar AOI Tambora');
  var mulai = String(q.mulai || '');
  cek(mode === 'mtt' || /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(mulai), 'waktu mulai harus YYYY-MM-DD HH:MM');
  var lama = parseFloat(q.lama_jam);
  cek(isFinite(lama) && lama > 0 && lama <= 72, 'lama simulasi 0-72 jam');
  var angin = String(q.angin || '').replace(/\s/g, '');
  cek(angin === '' || /^\d+(\.\d+)?,\d+(\.\d+)?$/.test(angin), "angin harus 'km/j,arah' atau kosong");
  cek(mode === 'farsite' || angin !== '', 'MTT perlu angin');
  var tajuk = String(q.tajuk || 'finney');
  cek(['finney', 'scott', 'tanpa'].indexOf(tajuk) >= 0, 'metode tajuk tidak dikenal');
  var radius = parseFloat(q.radius_km || 8);
  cek(isFinite(radius) && radius >= 1 && radius <= 20, 'radius 1-20 km');
  var res = String(q.res || '10');
  cek(res === '10' || res === '30', 'resolusi 10 atau 30');
  var ls = String(q.landscape || 'drive');
  cek(ls === 'drive' || ls === 'contoh', 'landscape drive atau contoh');
  var id = String(q.id || '');
  cek(/^[a-z0-9]{6,24}$/.test(id), 'id permintaan tidak sah');
  return { mode: mode, titik: lat.toFixed(5) + ',' + lon.toFixed(5), mulai: mulai || '2026-09-09 13:00',
           lama_jam: String(lama), angin: angin, tajuk: tajuk, radius_km: String(radius), res: res,
           landscape: ls, id: id };
}

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

/** Uji dari editor setelah GITHUB_TOKEN diisi: harus menampilkan nama repo. */
function ujiGitHub() {
  var token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  var r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO, {
    headers: { Authorization: 'Bearer ' + token }, muteHttpExceptions: true });
  Logger.log(r.getResponseCode() + ' ' + r.getContentText().slice(0, 200));
}
