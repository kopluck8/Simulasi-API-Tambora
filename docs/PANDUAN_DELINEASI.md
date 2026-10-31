# Panduan unggah delineasi areal terbakar

Delineasi manual areal terbakar (misalnya tiap 5 hari) cukup diunggah ke Google Drive. GitHub Actions membacanya,
menghitung luas per resort, lalu memperbarui:

- **Peta Areal Terbakar**: <https://kopluck8.github.io/Simulasi-API-Tambora/peta_terbakar.html>
- **Rekap areal terbakar** di dashboard Satu Data Tambora (tema Kebakaran Hutan dan beranda)

Tidak ada izin Google baru: alur ini memakai penghubung Drive yang sama dengan update landscape
([PANDUAN_GOOGLE_DRIVE.md](PANDUAN_GOOGLE_DRIVE.md)).

## Cara unggah

1. Buka folder Drive **Simulasi API Tambora → delineasi**. Folder `delineasi` dibuat otomatis oleh workflow.
2. Unggah satu file **.zip** per delineasi, berisi `.shp`, `.shx`, `.dbf`, dan `.prj` (dari QGIS/ArcGIS: kompres
   keempat file itu menjadi satu zip).
3. Tulis **tanggal delineasi di nama file**, misalnya `delineasi_2026-10-30.zip`, `20261030.zip`, atau
   `areal terbakar 30okt2026.zip`. Bila nama file tanpa tanggal, dipakai nama subfolder, lalu tanggal unggah.

Yang juga diterima: `.shp/.shx/.dbf/.prj` lepas dengan nama sama, `.geojson`, dan subfolder per tanggal.
Sistem koordinat bebas (UTM 50S, WGS84, dan lainnya) asalkan ada `.prj`. Hanya poligon yang dihitung.

## Isi file

- File boleh berisi **seluruh areal terbakar sampai tanggal itu** atau **hanya areal baru** sejak delineasi
  sebelumnya. Semua delineasi sejak awal musim digabung, jadi bagian yang tumpang tindih tidak terhitung dua kali.
- Luas dihitung di UTM 50S. Yang di luar batas TN dicatat terpisah ("di luar kawasan").
- Delineasi sebelum awal musim (20 Februari, kebakaran pertama 2026) diabaikan, sehingga musim baru mulai dari nol tanpa
  perlu menghapus file lama. Tanggalnya diatur di `awal_musim` bagian `[delineasi]` di
  [`config/tambora.toml`](../config/tambora.toml); setelah diubah, rekap dihitung ulang otomatis.
- Untuk memperbaiki delineasi: ganti atau hapus file yang salah. Bila tiap file selalu kumulatif dan koreksi yang
  memperkecil areal harus ikut terpakai, ubah `mode = "terbaru"` di [`config/tambora.toml`](../config/tambora.toml).

## Kapan tampil

Workflow **Areal terbakar (delineasi manual)** memeriksa folder setiap jam dan hanya mengolah bila ada file yang
berubah. Untuk memperbarui saat itu juga: tab **Actions → Areal terbakar (delineasi manual) → Run workflow**.

Hasilnya disimpan di repo: [`docs/status/areal_terbakar.json`](status/areal_terbakar.json) (rekap per resort dan
riwayat per delineasi) dan [`docs/data/areal_terbakar.geojson`](data/areal_terbakar.geojson) (poligon per tanggal
dan resort, bisa dibuka di QGIS).

Selama folder masih kosong, dashboard menampilkan estimasi Sentinel-2 seperti sebelumnya.

## Kalau angka tidak berubah

- Buka peta areal terbakar, lalu lihat **Catatan pengolahan** di bawah panel kiri: file tanpa `.prj`, bukan
  poligon, atau di luar musim tercatat di sana.
- Cek log workflow di tab **Actions**. Pesan Google Drive sama dengan tabel di
  [PANDUAN_GOOGLE_DRIVE.md](PANDUAN_GOOGLE_DRIVE.md#kalau-gagal).
