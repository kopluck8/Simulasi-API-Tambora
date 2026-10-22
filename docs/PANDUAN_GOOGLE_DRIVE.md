# Panduan menyambungkan Google Drive (sekali saja)

Tujuannya: GitHub Actions bisa **mengunduh** data dasar dari Drive Anda dan **mengunggah** landscape mingguan serta hasil simulasi ke sana.
Waktu yang dibutuhkan sekitar 20–30 menit. Semua langkah bisa dilakukan lewat browser, tidak perlu memasang Python.

Ringkasnya ada 3 bagian:
- **A. Folder Drive**: tempat menyimpan data. Hasilnya **ID folder**.
- **B. Apps Script penghubung**: Web App kecil milik Anda yang memberi izin Drive ke GitHub Actions. Hasilnya **URL Web App** dan **KUNCI**. Tidak perlu Google Cloud, consent screen, atau verifikasi.
- **C. GitHub Secrets**: ketiga nilai di atas disimpan di repo, lalu update pertama dijalankan.

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

## B. Apps Script penghubung

1. Buka **script.google.com** dengan akun Gmail yang sama, lalu klik **New project** (Proyek baru). Ganti namanya menjadi `Penghubung Drive Tambora`.
2. Hapus isi `Code.gs`, lalu tempel seluruh isi file [`apps-script/Kode.gs`](../apps-script/Kode.gs) dari repo. Klik **Save** (ikon disket).
3. Atur manifest:
   - Klik **Project Settings** (ikon roda gigi di kiri) dan centang **Show "appsscript.json" manifest file in editor**.
   - Kembali ke **Editor**, buka `appsscript.json`, ganti isinya dengan [`apps-script/appsscript.json`](../apps-script/appsscript.json), lalu Save.
4. **Buat kunci**:
   1. Di bilah atas, pilih fungsi **`buatKunci`** lalu klik **Run**.
   2. Muncul permintaan izin: klik **Review permissions**, pilih akun Anda.
   3. Kalau muncul "Google hasn't verified this app", klik **Advanced → Go to Penghubung Drive Tambora (unsafe)** lalu **Allow**. Ini normal karena script-nya milik Anda sendiri.
   4. Buka **Execution log** di bawah dan salin teks panjang setelah `KUNCI ... :`. Inilah **KUNCI**.
5. (Opsional) Pilih fungsi **`uji`** lalu **Run**. Log harus menampilkan beberapa nama file/folder Drive Anda.
6. **Deploy**:
   1. Klik **Deploy → New deployment**. Klik ikon roda gigi di samping "Select type", lalu pilih **Web app**.
   2. Execute as: **Me**. Who has access: **Anyone**.
   3. Klik **Deploy** dan salin **Web app URL** (berakhiran `/exec`).

Tanpa KUNCI yang benar, Web App ini hanya menjawab "kunci salah". Jangan bagikan KUNCI. Kalau bocor, jalankan `buatKunci` lagi dan perbarui secret di GitHub.

---

## C. Simpan di GitHub dan jalankan

1. Buka repo **github.com/kopluck8/Simulasi-API-Tambora → Settings → Secrets and variables → Actions**.
2. Klik **New repository secret** tiga kali:

| Name | Secret |
|---|---|
| `GDRIVE_FOLDER_ID` | ID folder dari A.3 |
| `GDRIVE_TOKEN_URL` | Web app URL dari B.6 (berakhiran `/exec`) |
| `GDRIVE_KUNCI` | KUNCI dari B.4 |

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
| `Apps Script: kunci salah` | `GDRIVE_KUNCI` tidak sama dengan KUNCI di script | Salin ulang dari log `buatKunci` |
| `tidak mengembalikan JSON` | Web App belum di-deploy untuk **Anyone**, atau URL bukan yang `/exec` | Ulangi B.6 |
| `403 … insufficientPermissions` | Manifest belum diganti, jadi izin Drive tidak lengkap | Ulangi B.3, jalankan `uji`, lalu buat **New deployment** |
| `Isi drive.folder_id…` | `GDRIVE_FOLDER_ID` kosong | Tambahkan secret tersebut |
| `No such file … elevasi_m.tif` | Folder `dasar` belum berisi 9 file .tif atau namanya beda | Cek nama folder dan file di A.4–A.5 |
| `Tidak ada citra cerah` | Awan menutup semua citra 21 hari terakhir | Jalankan lagi minggu depan, atau naikkan `hari` di `config/tambora.toml` |

## Catatan

- Cara lama (Google Cloud OAuth client + refresh token, secret `GDRIVE_CLIENT_ID/SECRET/REFRESH_TOKEN`) masih didukung kode, tetapi tidak perlu dipakai.
- Project Google Cloud "APITambora" yang sempat dibuat boleh dibiarkan atau dihapus. Tidak dipakai.
