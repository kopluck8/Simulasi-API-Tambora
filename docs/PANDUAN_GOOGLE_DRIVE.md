# Panduan menyambungkan Google Drive (sekali saja)

Tujuannya: GitHub Actions bisa **mengunduh** data dasar dari Drive Anda dan **mengunggah** landscape mingguan serta hasil simulasi ke sana.
Waktu yang dibutuhkan sekitar 20–30 menit. Semua langkah bisa dilakukan lewat browser, tidak perlu memasang Python.

Ringkasnya ada 4 bagian:
- **A. Folder Drive**: tempat menyimpan data. Hasilnya **ID folder**.
- **B. Google Cloud**: membuat "aplikasi" pribadi yang diizinkan mengakses Drive. Hasilnya **Client ID** dan **Client secret**.
- **C. OAuth Playground**: Anda memberi izin sekali. Hasilnya **Refresh token**.
- **D. GitHub Secrets**: keempat nilai di atas disimpan di repo, lalu update pertama dijalankan.

---

## A. Folder di Google Drive

1. Buka drive.google.com dengan akun Gmail yang akan dipakai.
2. **Baru → Folder**, beri nama `Simulasi API Tambora`.
3. Buka folder itu. Lihat alamat di browser, misalnya:
   `https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345`
   Bagian sesudah `folders/` (`1AbCdEf…345`) adalah **ID folder**. Simpan ID ini.
4. Di dalam folder itu, buat subfolder **`dasar`** (huruf kecil semua).
5. Isi folder `dasar`:
   - Unduh `dasar_drive.zip` dari folder proyek `simulasi-api-tambora/dasar_drive/` (78 MB), lalu ekstrak di PC.
   - Unggah **9 file .tif** hasil ekstrak (bukan file zip-nya) ke folder `dasar`:
     `elevasi_m.tif`, `lereng_derajat.tif`, `aspek_derajat.tif`, `fuel_model.tif`, `tutupan_kanopi_persen.tif`,
     `tinggi_kanopi_m_x10.tif`, `cbh_m_x10.tif`, `cbd_kgm3_x100.tif`, `nbr_dasar_x10000.tif`.
   - Isinya sama dengan `geotiff_junijuli2026_10m.zip`. Bedanya, median Sentinel-2 yang 126 MB sudah diringkas menjadi `nbr_dasar_x10000.tif` (NBR awal kemarau), jadi unggahannya lebih kecil.

Folder `landscape/` dan `simulasi/` akan dibuat otomatis oleh sistem.

---

## B. Google Cloud: membuat Client ID dan Client secret

Tampilan Google Cloud kadang berubah nama menu. Kalau tidak menemukan menunya, pakai kotak pencarian di atas.

1. Buka **console.cloud.google.com** dengan akun Gmail yang sama. Setujui syarat layanan bila diminta. Tidak perlu kartu kredit.
2. **Buat project**: klik pemilih project di kiri atas → **New Project** → nama `Simulasi API Tambora` → **Create**. Pastikan project ini yang terpilih.
3. **Aktifkan Google Drive API**: ketik "Google Drive API" di kotak pencarian → buka → **Enable**.
4. **Layar persetujuan (OAuth consent / Google Auth Platform)**:
   1. Cari "OAuth consent screen", lalu klik **Get started**.
   2. App name: `Simulasi API Tambora`. User support email: email Anda. Klik **Next**.
   3. Audience: pilih **External**. Klik **Next**.
   4. Contact information: email Anda. Klik **Next**, centang persetujuan, lalu **Create**.
   5. Buka menu **Audience**, lalu klik **Publish app → Confirm** sehingga statusnya menjadi **In production**.
      - Ini penting. Kalau statusnya masih "Testing", izin akan kedaluwarsa setelah 7 hari dan update mingguan berhenti.
      - Aplikasi tidak perlu diverifikasi Google, karena hanya Anda yang memakainya.
5. **Buat client**:
   1. Buka menu **Clients** (atau *Credentials → Create credentials → OAuth client ID*).
   2. Application type: **Web application**. Name: `github-actions`.
   3. Di **Authorized redirect URIs**, klik **Add URI** dan isi persis:
      `https://developers.google.com/oauthplayground`
   4. Klik **Create**. Akan muncul **Client ID** (berakhiran `.apps.googleusercontent.com`) dan **Client secret** (diawali `GOCSPX-`). Salin keduanya dan simpan.

---

## C. OAuth Playground: mendapatkan Refresh token

1. Buka **developers.google.com/oauthplayground**.
2. Klik ikon **roda gigi** (kanan atas):
   - Centang **Use your own OAuth credentials**.
   - Tempel Client ID dan Client secret dari bagian B, lalu tutup panel.
3. **Step 1** (kiri):
   - Di kotak "Input your own scopes", ketik: `https://www.googleapis.com/auth/drive`
   - Klik **Authorize APIs**, lalu pilih akun Gmail Anda.
4. Akan muncul peringatan **"Google hasn't verified this app"**. Ini normal karena aplikasinya milik Anda sendiri.
   - Klik **Advanced → Go to Simulasi API Tambora (unsafe)**.
   - Centang izin Drive, lalu klik **Continue / Allow**.
5. Kembali ke Playground. Di **Step 2**, klik **Exchange authorization code for tokens**.
6. Salin nilai **Refresh token** (biasanya diawali `1//`).
   - Karena Anda memakai client sendiri, token ini tidak dicabut otomatis oleh Playground.
   - Jangan bagikan token ini ke siapa pun.

Kenapa izinnya "seluruh Drive": supaya sistem bisa membaca file yang Anda unggah sendiri ke folder `dasar`. Sistem hanya membuka folder yang ID-nya Anda berikan.

Cara alternatif kalau Python sudah terpasang di PC: buat client jenis **Desktop app** (bukan Web application), lalu jalankan
`tambora drive-auth --client-id <ID> --client-secret <SECRET>`. Refresh token akan tercetak di layar.

---

## D. Simpan di GitHub dan jalankan

1. Buka repo **github.com/kopluck8/Simulasi-API-Tambora → Settings → Secrets and variables → Actions**.
2. Klik **New repository secret** empat kali:

| Name | Secret |
|---|---|
| `GDRIVE_FOLDER_ID` | ID folder dari A.3 |
| `GDRIVE_CLIENT_ID` | Client ID dari B.5 |
| `GDRIVE_CLIENT_SECRET` | Client secret dari B.5 |
| `GDRIVE_REFRESH_TOKEN` | Refresh token dari C.6 |

3. Buka tab **Actions**. Kalau diminta, klik "I understand my workflows, go ahead and enable them".
4. Pilih **Update landscape mingguan → Run workflow → Run workflow**. Proses pertama memakan waktu sekitar 10–30 menit.
5. Kalau berhasil:
   - Di Drive akan muncul `landscape/terbaru/landscape_terbaru_10m.lcp`, `.zip`, `pratinjau_fuel.png`, dan `ringkasan.json`.
   - Di repo akan muncul commit "Update landscape …".
6. Sesudah itu, workflow berjalan otomatis setiap Senin pukul 07:00 WITA.

Opsional: **Settings → Pages → Branch `main`, folder `/docs` → Save**. Langkah ini membuka halaman status dan pembuat WXS di GitHub Pages.

## Kalau gagal

| Pesan di log Actions | Penyebab | Perbaikan |
|---|---|---|
| `invalid_grant` | Refresh token kedaluwarsa atau dicabut (biasanya app masih "Testing") | Pastikan B.4.5 sudah *In production*, lalu ulangi bagian C |
| `invalid_client` | Client ID/secret salah atau tertukar | Periksa secrets di GitHub |
| `Isi drive.folder_id…` | `GDRIVE_FOLDER_ID` kosong | Tambahkan secret tersebut |
| `No such file … elevasi_m.tif` | Folder `dasar` belum berisi 9 file .tif atau namanya beda | Cek nama folder dan file di A.4–A.5 |
| `Tidak ada citra cerah` | Awan menutup semua citra 21 hari terakhir | Jalankan lagi minggu depan, atau naikkan `hari` di `config/tambora.toml` |
