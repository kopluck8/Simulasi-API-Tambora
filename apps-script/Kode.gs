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
