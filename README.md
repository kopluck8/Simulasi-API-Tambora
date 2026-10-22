# Simulasi API Tambora

Pendamping FlamMap/FARSITE untuk savana TN Gunung Tambora. Repo ini berisi:

- **Mesin rambatan api** (Python): model permukaan Rothermel, mode **MTT** (angin dan kadar air tetap, seperti MTT FlamMap) dan mode **FARSITE** (cuaca dan angin berubah per jam dari `.wxs`).
- **Angin di atas medan**: memakai WindNinja bila `WindNinja_cli` terpasang. Kalau tidak ada, dipakai model medan bawaan (MicroMet).
- **Cuaca**: unduh data per jam ERA5 / ECMWF lewat Open-Meteo dan tulis `.wxs`. Ini port dari pembuat WXS yang sudah dipakai (`docs/pembuat_wxs.html`).
- **Landscape mingguan**: GitHub Actions mengambil Sentinel-2 terbaru tiap Senin, menandai bekas terbakar baru, menulis `.lcp` 10 m, lalu menyimpannya ke **Google Drive** Anda.
- **Fuel terkalibrasi**: `kalibrasi/tambora_savana_cepat20.fmd` (fmd20) + `kalibrasi/tambora_latihan.fms`.

> Status: alat latihan/kalibrasi. Kelas K1–K4 masih kelas relatif dari NDVI, belum dari biomassa plot survei.

## Seberapa sama dengan FlamMap?

Dibandingkan langsung dengan `flammap.dll` dan `mtt.dll` FlamMap 6.2 asli, dijalankan lewat Wine pada landscape Piong pra-kebakaran 9 Sep 2026, fmd20, angin 20 km/j dari 70°, kadar air tetap:

| Yang dibandingkan | Hasil |
|---|---|
| ROS kepala per sel (K1–K4, TU1, TL6) | selisih median 0,3%; 98% sel < 1% |
| Rasio panjang/lebar elips, arah rambat maksimum | selisih < 1% / < 1° |
| Waktu tiba MTT (resolusi 30 m, 4 jam) | median selisih 1%; area terbakar 30/60/120/240 menit tumpang tindih 96–98% (Sørensen) |
| Bekas terbakar 9 Sep (dNBR) | FlamMap 0,736 / repo ini 0,729; arah pusat 250° (obs. 248°) |

Yang **berbeda** dari FlamMap/FARSITE:
- Tidak ada *spotting* (loncatan bara). Kalibrasi selama ini juga memakai spot 0.
- Api tajuk disederhanakan (Van Wagner + Rothermel 1991). Panjang nyala sel api tajuk pasif bisa berbeda dari FlamMap. Ini tidak berpengaruh di savana.
- Kadar air mati di mode FARSITE memakai model timelag EMC sederhana, bukan model Nelson. Untuk kalibrasi, pakai `--kadar-air-tetap`, sama seperti yang selama ini dilakukan di FlamMap.
- Perambatan memakai Dijkstra 32 arah (seperti MTT). Mode FARSITE mengganti laju tiap jam, bukan propagasi perimeter Huygens.

## Pasang di PC (Windows)

1. Pasang Python 3.12 dari python.org (centang *Add to PATH*).
2. Unduh repo (Code → Download ZIP), ekstrak, misalnya ke `D:\API\Simulasi-API-Tambora`.
3. Di Command Prompt:
   ```
   cd D:\API\Simulasi-API-Tambora
   pip install -e .
   ```
4. WindNinja (opsional): pasang WindNinja 3.x dari firelab.org. `WindNinja_cli.exe` di `C:\WindNinja\...\bin` dicari otomatis. Bisa juga diisi lewat variabel `WINDNINJA_CLI`.

## Contoh pemakaian

Semua koordinat UTM 50S (EPSG:32750), waktu WITA, angin = arah **asal** (seperti Azimuth FlamMap), kecepatan angin 20-ft dalam km/j.

```bash
# MTT: kalibrasi arah kejadian 9 Sep (landscape contoh 30 m di repo)
tambora mtt --lcp contoh/piong_9sep/landscape_prakebakaran_9sep_30m.lcp \
  --api contoh/piong_9sep/september_poligon60m.shp --kecepatan 20 --arah 70 --menit 480 \
  --validasi contoh/piong_9sep/bekas_terbakar_9sep2026_dNBR.shp --out hasil/mtt_9sep

# FARSITE: 48 jam dengan cuaca per jam dari ERA5 (diunduh otomatis di titik api)
tambora farsite --lcp landscape_terbaru_10m.lcp --latlon=-8.2585,118.1506 --buffer 30 \
  --radius-km 8 --mulai "2026-09-09 13:00" --jam 48 --out hasil/farsite_9sep

# FARSITE dengan .wxs sendiri, angin dipaksa 70°/20 km/j, kadar air tetap .fms
tambora farsite --lcp landscape_terbaru_10m.lcp --api titik.shp --wxs cuaca.wxs \
  --mulai "2026-09-09 13:00" --jam 24 --arah 70 --kecepatan 20 --kadar-air-tetap --out hasil/f2

# Buat .wxs saja (pengganti halaman pembuat WXS)
tambora wxs --lat -8.2585 --lon 118.1506 --mulai 2026-09-09 --sampai 2026-09-11 --out piong.wxs
```

Pilihan penting:
- `--angin otomatis|medan|windninja|seragam`. Bawaannya `otomatis`: WindNinja kalau ada, kalau tidak model medan.
- `--radius-km 8` membuat *Analysis Area* di sekitar titik api. `--res 30` mempercepat hitungan untuk AOI penuh.
- `--burn 9 21` mengatur burn period FARSITE.
- `--fmd/--fms` mengganti fuel. Bawaannya diambil dari `config/tambora.toml` (fmd20).

Keluaran di folder `--out`:
- GeoTIFF `waktu_tiba_menit`, `ros_m_menit`, `panjang_nyala_m`, `intensitas_kw_m`, `tipe_api`.
- `perimeter.shp` (per jam, berisi kolom waktu dan luas).
- `peta_waktu_tiba.png` dan `ringkasan.json` (berisi skor Sørensen kalau `--validasi` diisi).

Semua GeoTIFF dan shapefile bisa langsung dibuka di ArcGIS Pro.

## Google Drive (penyimpanan data dasar dan hasil)

Struktur folder di Drive (dibuat otomatis kecuali `dasar`):
```
<folder induk>/
  dasar/        GeoTIFF dasar: isi geotiff_junijuli2026_10m.zip (topografi, kanopi, fuel awal kemarau, median S2)
  landscape/
    terbaru/    landscape_terbaru_10m.lcp/.zip, fuel_model.tif, bekas_terbakar_musim.tif, pratinjau, ringkasan
    arsip/<tanggal>/
  simulasi/     hasil dari workflow "Simulasi api"
```

Langkah sekali saja:
1. **Folder**: buat folder di Google Drive, misalnya `Simulasi API Tambora`. Salin ID-nya (bagian terakhir URL folder).
   - Buat subfolder `dasar` dan unggah isi `geotiff_junijuli2026_10m.zip` ke sana, bisa lewat browser.
2. **Kunci akses**:
   - Di console.cloud.google.com, buat project, aktifkan **Google Drive API**.
   - Buka *Credentials → Create credentials → OAuth client ID → Desktop app*. Catat Client ID dan Client secret.
   - Di *OAuth consent screen*, tambahkan email Anda sebagai *test user*, lalu ubah status ke **In production** supaya token tidak kedaluwarsa setelah 7 hari.
3. **Refresh token**: di PC jalankan
   `tambora drive-auth --client-id <ID> --client-secret <SECRET>`, lalu izinkan di browser. Refresh token akan tercetak.
4. **Secrets GitHub** (repo → Settings → Secrets and variables → Actions): `GDRIVE_FOLDER_ID`, `GDRIVE_CLIENT_ID`, `GDRIVE_CLIENT_SECRET`, `GDRIVE_REFRESH_TOKEN`.

Kenapa bukan service account: service account tidak punya kuota penyimpanan di Drive akun Gmail pribadi, jadi tidak bisa menulis file. Service account (`GDRIVE_SERVICE_ACCOUNT_JSON`) hanya berfungsi untuk Shared Drive Google Workspace.

## Update landscape mingguan

Workflow `Update landscape mingguan` berjalan tiap Senin 07:00 WITA. Bisa juga dijalankan manual dari tab Actions.

1. Unduh GeoTIFF dasar dari Drive (di-cache di Actions).
2. Cari Sentinel-2 L2A 21 hari terakhir (Planetary Computer, tanpa akun). Untuk tiap piksel, ambil pengamatan cerah paling baru (awan dan bayangan dimasking dengan SCL).
3. dNBR = NBR awal kemarau − NBR terbaru. Piksel dengan dNBR > 0,10 (petak ≥ 1 ha) ditandai sebagai bekas terbakar. Bekas terbakar diakumulasi sejak awal musim (1 Juni) dan diberi fuel 99 (tidak terbakar lagi).
4. Tulis `.lcp` 10 m, unggah ke Drive, dan catat ringkasan di `docs/status/`. Ringkasan ini tampil di GitHub Pages.

Mode kelas (`config/tambora.toml`, `[mingguan] mode_kelas`):
- `dasar` (bawaan): K1–K4 tetap dari awal kemarau, sesuai aturan proyek bahwa peta fuel tidak dibangun ulang dari citra sesudah kebakaran.
- `batas`: K1–K4 dihitung ulang dari NDVI terbaru dengan batas tetap. Pakai setelah batas dikunci dari survei, dengan citra musim yang sama.
- `kuantil`: K1–K4 dari kuantil NDVI terbaru dengan proporsi tetap.

NDVI/NBR dihitung dari DN mentah (termasuk offset +1000), sama dengan file latihan. Dengan mode `kuantil` pada median Juni–Juli, batas yang dihasilkan persis 0,317/0,593/0,781.

## Simulasi dari GitHub (tanpa PC)

Buka tab Actions → **Simulasi api** → *Run workflow*. Isi titik api (lintang,bujur), mode, dan waktu. Hasilnya bisa diunduh sebagai artifact dan juga disimpan ke Drive `simulasi/`.

## Struktur kode

| File | Isi |
|---|---|
| `tamborafire/rothermel.py` | Rothermel + vektor angin–lereng + WAF + elips (sama dengan flammap.dll) |
| `tamborafire/spread.py` | Dijkstra 32 arah (numba), status bisa dilanjutkan per jam |
| `tamborafire/simulate.py` | mode MTT dan FARSITE |
| `tamborafire/wind.py` | angin seragam, model medan MicroMet, pembungkus WindNinja_cli |
| `tamborafire/moisture.py` | kadar air tetap (.fms) atau conditioning per jam |
| `tamborafire/weather.py` | Open-Meteo → .wxs, baca/tulis .wxs |
| `tamborafire/landscape.py`, `sentinel2.py` | landscape dasar dan update mingguan |
| `tamborafire/drive.py` | Google Drive (OAuth / service account) |
| `tamborafire/lcp.py` | baca/tulis .lcp (header 7316 byte) |
| `tests/` | uji terhadap angka flammap.dll dan bekas terbakar 9 Sep |

## Catatan untuk dokumen latihan lama

Di file inputs FlamMap (`WIND_SPEED:`), satuan kecepatan angin adalah **mil/jam**, bukan km/j. Ini terbukti dari pembandingan di atas. Akibatnya, tabel di folder latihan yang dibuat lewat mesin uji (`uji/mesin_flammap`), misalnya ROS fmd15/fmd20 "angin 20 km/j", sebenarnya memakai 20 mil/jam (32 km/j).

Kalibrasi Anda di GUI FlamMap kemungkinan tidak terpengaruh kalau GUI memakai satuan Metric. Cara memastikan: buka `FlamMap6_inputs_*.txt` di folder temp. Angin 20 km/j harus tertulis `WIND_SPEED: 12.4` (dalam mil/jam). Arah hasil uji tidak berubah: Sørensen 0,70–0,73 dan arah 249–250°.
