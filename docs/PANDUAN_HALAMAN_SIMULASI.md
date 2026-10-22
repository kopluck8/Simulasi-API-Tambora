# Menyalakan halaman peta simulasi

Halaman `simulasi.html` di GitHub Pages mengirim permintaan ke Web App Apps Script yang sama dengan penghubung Google Drive. Web App itu menyalakan workflow **Simulasi api** di GitHub memakai token GitHub milik Anda. Token disimpan di Apps Script dan tidak pernah tampil di halaman.

Cukup dikerjakan sekali.

## 1. Buat token GitHub (fine-grained)

1. Di GitHub, klik foto profil (kanan atas) › **Settings** › **Developer settings** (paling bawah kiri) › **Personal access tokens** › **Fine-grained tokens** › **Generate new token**.
2. Isi:
   - **Token name**: `simulasi-api-tambora`
   - **Expiration**: misalnya 1 tahun (catat tanggalnya, nanti perlu diperbarui).
   - **Repository access**: *Only select repositories* › pilih `Simulasi-API-Tambora`.
   - **Permissions** › **Repository permissions** › **Actions**: *Read and write*. Yang lain biarkan.
3. Klik **Generate token**, lalu salin token (diawali `github_pat_`). Token hanya tampil sekali.

## 2. Perbarui Apps Script

1. Buka proyek Apps Script penghubung Drive.
2. Ganti seluruh isi `Kode.gs` dengan isi terbaru dari [`apps-script/Kode.gs`](../apps-script/Kode.gs). Simpan.
3. Klik ikon roda (**Project Settings**) › **Script Properties** › **Add script property**:
   - Property: `GITHUB_TOKEN`
   - Value: token dari langkah 1
   - **Save script properties**. Properti `KUNCI` yang sudah ada jangan dihapus.
4. Kembali ke **Editor**, pilih fungsi `ujiGitHub` di atas, klik **Run**. Di Execution log harus muncul `200` dan nama repo.

## 3. Terbitkan versi baru Web App (URL tetap sama)

1. **Deploy** › **Manage deployments**.
2. Klik ikon pensil pada deployment yang ada.
3. **Version** › **New version** › **Deploy**.

Jangan membuat *New deployment*, karena itu menghasilkan URL baru. Kalau terlanjur, salin URL barunya ke `docs/simulasi_config.js` dan ke secret `GDRIVE_TOKEN_URL`.

## 4. Pakai

Buka `https://kopluck8.github.io/Simulasi-API-Tambora/simulasi.html`.

1. Klik peta untuk menaruh titik api. Kotak oranye adalah Analysis Area.
2. Pilih **FARSITE** atau **MTT** (angin tetap, wajib isi kecepatan dan arah). FARSITE memakai cuaca per jam dari ERA5/ECMWF di titik api, atau dari file **.wxs** yang Anda unggah (misalnya dari pembuat WXS). Unggahan .wxs disimpan sementara di folder `simulasi_wxs` di Google Drive Anda. Waktu mulai dan lama harus berada di dalam rentang tanggal file .wxs.
3. Pilih **Crown Fire Calculation Method**: Finney (1998) atau Scott/Reinhardt (2001), sama dengan pilihan di FlamMap.
4. Klik **Jalankan simulasi**. Status berjalan tampil di bawah tombol, biasanya selesai dalam 5 sampai 20 menit.
5. Setelah selesai, peta waktu tiba muncul di atas peta. Tombol **Unduh semua** berisi GeoTIFF (waktu tiba, ROS, panjang nyala, intensitas, tipe api), shapefile perimeter per jam, dan PNG.

Semua simulasi tersimpan di **Riwayat simulasi** (dari GitHub Releases repo) dan juga di folder `simulasi/` Google Drive.

Batasan: satu simulasi per menit, titik api harus di dalam AOI Tambora, lama maksimal 72 jam.
