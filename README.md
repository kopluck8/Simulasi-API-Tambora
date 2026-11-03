# Simulasi API Tambora

Pendamping FlamMap/FARSITE untuk savana TN Gunung Tambora. Repo ini berisi:

- **Mesin rambatan api** (Python): model permukaan Rothermel, mode **MTT** (angin dan kadar air tetap, seperti MTT FlamMap) dan mode **FARSITE** (cuaca dan angin berubah per jam dari `.wxs`).
- **Angin di atas medan**: memakai WindNinja bila `WindNinja_cli` terpasang. Kalau tidak ada, dipakai model medan bawaan (MicroMet).
- **Cuaca**: unduh data per jam ERA5 / ECMWF lewat Open-Meteo dan tulis `.wxs`. Ini port dari pembuat WXS yang sudah dipakai (`docs/pembuat_wxs.html`).
- **Landscape otomatis**: GitHub Actions mengecek katalog Sentinel-2 dua kali sehari dan, setiap ada citra baru (±tiap 2–5 hari), menandai bekas terbakar baru, menulis `.lcp` 10 m, lalu menyimpannya ke **Google Drive** Anda.
- **Dashboard GitHub Pages** (`docs/index.html`): tab Ringkasan, Simulasi (peta dengan batas TN, resort, jalur patroli, grid 1 km; cuaca .wxs dibuat langsung dari tanggal dan jam kejadian), Simulasi 3D, Asal Api, Riwayat, Landscape, Panduan.
- **Pemodelan terbalik (Asal Api)**: dari titik panas satelit dan areal terbakar, dicari titik asal, jam mulai, dan arah datang api tiap kejadian, jam rawan pembakaran, serta faktor koreksi laju api untuk kalibrasi fmd20.
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
| Api tajuk Finney (1998) dan Scott/Reinhardt (2001), angin 30 mph (13.195 sel tajuk) | tipe api sama di 99,99% sel; ROS, intensitas, panjang nyala, rasio panjang/lebar median selisih < 0,1% |

Yang **berbeda** dari FlamMap/FARSITE:
- Tidak ada *spotting* (loncatan bara). Kalibrasi selama ini juga memakai spot 0.
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

Panduan rinci langkah demi langkah: [docs/PANDUAN_GOOGLE_DRIVE.md](docs/PANDUAN_GOOGLE_DRIVE.md). Ringkasnya:
1. Buat folder di Drive dan unggah 9 GeoTIFF dasar ke subfolder `dasar`.
2. Pasang Web App Apps Script penghubung (`apps-script/Kode.gs`). Tidak perlu Google Cloud atau verifikasi.
3. Isi secrets GitHub: `GDRIVE_FOLDER_ID`, `GDRIVE_TOKEN_URL`, `GDRIVE_KUNCI`.
4. Disarankan: secret `FIRMS_MAP_KEY` (gratis, https://firms.modaps.eosdis.nasa.gov/api/map_key/) supaya bekas terbakar dicocokkan dengan titik panas VIIRS/MODIS.

## Update landscape otomatis

Workflow `Update landscape (tiap citra Sentinel-2 baru)` mengecek katalog Sentinel-2 pukul 11:00 dan 19:00 WITA (`tambora cek-citra`). Landscape hanya dihitung bila ada citra baru (awan < 70%) sejak citra terakhir yang tercatat di `docs/status/landscape_terbaru.json`, jadi praktis tiap 2–5 hari mengikuti lintasan Sentinel-2A/2B/2C. Bisa juga dijalankan manual dari tab Actions (selalu dihitung).

Jendela citra 21 hari berjangkar tetap pada awal musim (1 Jun, 22 Jun, 13 Jul, …). Status bekas terbakar dan NBR acuan (`status_jendela.tif`, `nbr_acuan.tif` di Drive) hanya disimpan di batas jendela yang sudah lengkap; jendela yang sedang berjalan dihitung ulang dengan semua citranya setiap ada citra baru. Dengan begitu update yang sering tetap membandingkan median 21 hari dengan acuan sebelumnya, bukan satu-dua citra saja.

1. Unduh GeoTIFF dasar dari Drive (di-cache di Actions).
2. Cari Sentinel-2 L2A per jendela 21 hari tetap (Planetary Computer, tanpa akun). Tiap piksel diambil median pengamatan cerahnya (awan, bayangan, dan tepi awan 3 piksel dimasking dengan SCL); piksel dengan kurang dari 2 pengamatan cerah dilewati.
3. Deteksi bekas terbakar:
   - dNBR = NBR acuan − NBR jendela ini. NBR acuan adalah pengamatan cerah terakhir *sebelum* jendela ini (`nbr_acuan.tif` di Drive).
   - Pengeringan musiman dikoreksi secara **lokal**: median dNBR per blok 1,5 km per kelompok (rumput, hutan/semak), karena dataran rendah mengering lebih dulu.
   - Petak bekas terbakar harus memuat piksel rumput yang sangat gelap (dNBR terkoreksi > 0,15), lalu tumbuh ke tetangga dengan dNBR terkoreksi > 0,08; petak < 1 ha diabaikan.
   - Dengan `FIRMS_MAP_KEY`: hanya petak yang berjarak ≤ 750 m dari titik panas VIIRS/MODIS pada periode itu yang dihitung. Tanpa kunci: petak menjadi kandidat dulu dan baru dihitung bila jendela berikutnya masih gelap.
   - `bekas_terbakar_musim.tif`: 1 = bekas terbakar (fuel 99, tidak terbakar lagi), 2 = kandidat. Diakumulasi sejak 1 Juni.
   - Pada run pertama, atau dengan opsi *mulai ulang*, seluruh musim dihitung ulang per jendela 21 hari.
4. Tulis `.lcp` 10 m, unggah ke Drive, dan catat ringkasan di `docs/status/`, termasuk sisa K1–K4 per resort (`[kawasan]` di config, poligon `docs/data/batas_resort.geojson`). Ringkasan ini tampil di dashboard GitHub Pages.

Mode kelas (`config/tambora.toml`, `[mingguan] mode_kelas`):
- `dasar` (bawaan): K1–K4 tetap dari awal kemarau, sesuai aturan proyek bahwa peta fuel tidak dibangun ulang dari citra sesudah kebakaran.
- `batas`: K1–K4 dihitung ulang dari NDVI terbaru dengan batas tetap. Pakai setelah batas dikunci dari survei, dengan citra musim yang sama.
- `kuantil`: K1–K4 dari kuantil NDVI terbaru dengan proporsi tetap.

NDVI/NBR dihitung dari DN mentah (termasuk offset +1000), sama dengan file latihan. Dengan mode `kuantil` pada median Juni–Juli, batas yang dihasilkan persis 0,317/0,593/0,781.

## Delineasi manual areal terbakar

Delineasi manual (misalnya tiap 5 hari) cukup diunggah sebagai zip SHP ke folder Drive `delineasi`, dengan tanggal di nama file
(`delineasi_2026-10-30.zip`). Workflow `Areal terbakar (delineasi manual)` memeriksa folder itu setiap jam, menggabungkan semua
delineasi sejak awal musim (tumpang tindih tidak dihitung dua kali), menghitung luas per resort di UTM 50S, lalu menulis
`docs/status/areal_terbakar.json` dan `docs/data/areal_terbakar.geojson` (gabungan per tanggal dan resort, untuk rekap luas),
serta `docs/data/areal_terbakar_bidang.geojson`: **bidang delineasi apa adanya**, tidak digabung, luas sesuai SHP, dengan FID
rekaman asli (bidang yang dikirim ulang di file lebih baru dikenali dari IoU ≥ 0,9). Hasilnya tampil di
[peta areal terbakar](https://kopluck8.github.io/Simulasi-API-Tambora/peta_terbakar.html) dan di dashboard Satu Data Tambora.
Panduan: [docs/PANDUAN_DELINEASI.md](docs/PANDUAN_DELINEASI.md). Uji tanpa Drive: `tambora areal-terbakar --lokal folder_shp`.

## Simulasi dari GitHub (tanpa PC)

Paling mudah lewat **dashboard** `https://kopluck8.github.io/Simulasi-API-Tambora/` tab **Simulasi**: klik titik api, isi tanggal dan jam kejadian (bawaan: jam sekarang; cuaca per jam dan .wxs dibuat otomatis untuk titik itu), pilih FARSITE/MTT dan metode api tajuk (Finney 1998 / Scott-Reinhardt 2001), jalankan, lalu lihat dan unduh hasilnya. Penyiapan sekali (token GitHub di Apps Script): [docs/PANDUAN_HALAMAN_SIMULASI.md](docs/PANDUAN_HALAMAN_SIMULASI.md).

Bisa juga dari tab Actions → **Simulasi api** → *Run workflow*. Hasil diterbitkan sebagai GitHub Release `sim-<id>` (overlay, PNG, `hasil.zip`) dan disimpan ke Drive `simulasi/`.

Di baris perintah, metode api tajuk dipilih dengan `--tajuk finney|scott|tanpa` (bawaan `finney`).

## Simulasi 3D

Tab **Simulasi 3D** di dashboard memutar rambatan api di atas medan 3D (three.js): tombol putar/jeda, penggeser waktu maju-mundur, jam WITA, label jam di muka api, garis waktu (isokron), dan data per jam (luas terbakar, pertambahan luas, laju api/ROS di muka api, kemajuan kepala api, panjang nyala, intensitas, tipe api, cuaca per jam yang dipakai simulasi). Alat lain: warna menurut waktu tiba / ROS / panjang nyala / intensitas / tipe api, peta dasar ketinggian / bahan bakar / citra satelit, pembesaran tinggi, kontur, grid 1 km, jalur patroli, batas resort, arah dan tinggi matahari, kamera, info titik, ukur jarak dan laju api antara dua titik, tangkapan layar, layar penuh. Simulasi dipilih dari Riwayat (tombol **3D**).

- Simulasi dengan `--web` menulis `data3d.bin.gz` (elevasi, bahan bakar, waktu tiba, ROS, nyala, intensitas, tipe api, cuaca per jam; dipotong di sekitar area terbakar, maks 1024 sel per sisi). Format: `tamborafire/tiga_d.py`.
- Workflow **Simulasi api** menyimpannya ke cabang `hasil-3d` (`sim/<id>/data3d.bin.gz`); halaman membacanya dari raw.githubusercontent.com.
- Simulasi lama: workflow **Data 3D** (Actions → *Run workflow*) membuat datanya dari `hasil.zip` di Release + landscape terbaru di Drive (`tambora isi-3d`).

## Asal api (pemodelan terbalik)

Tab **Asal Api** menampilkan, untuk tiap kejadian kebakaran musim ini: titik asal api (zona peluang 50% dan 90%), perkiraan jam api mulai dengan selang 90%, arah datang api, tempat dan jam api masuk kawasan TN bila asalnya di luar, garis waktu rambat, serta grafik **jam rawan pembakaran** dan mawar arah datang api. Bisa disaring per bulan, mutu, resort, letak titik asal, dan luas, diunduh sebagai CSV/GeoJSON, dan tiap titik asal bisa langsung disimulasikan di tab Simulasi.

Cara hitung (`tamborafire/asal.py`, perintah `tambora asal-api`):

1. **Titik panas** VIIRS/MODIS disimpan di `data/titik_panas/titik_panas.geojson` (WITA). Impor KMZ/KML SiPongi atau CSV FIRMS dengan `tambora titik-panas FILE...`; workflow menambah data baru dari NASA FIRMS tiap hari (secret `FIRMS_MAP_KEY`).
2. **Kejadian**: titik panas ≤ 1,5 km dan ≤ 36 jam, atau tersambung oleh areal terbakar, digabung. Kejadian di luar kawasan hanya dihitung bila titik panas atau areal terbakarnya ≤ 1,5 km dari batas TN (`[kawasan] zona_luar_tn_m`); areal kejadian yang lolos tetap utuh. Areal terbakar = bidang delineasi (`docs/data/areal_terbakar_bidang.geojson`) + bekas terbakar Sentinel-2 di luar TN. Tiap bidang utuh milik kejadian yang titik panasnya ada di dalam bidang (bobot 0,2–1 menurut jarak ke tepi bidang, titik ≤ 400 m di luar 0,1); titik di tepi (< 200 m) dari kejadian yang punya bidang sendiri dianggap limpahan piksel dari bidang sebelah. Bidang yang memuat titik panas ≥ 2 kejadian jauh di dalamnya dibagi ke titik panas terdekat. Bila atribut SHP bidang mencatat bulan/tanggal terbakar (kolom `Bulan`, `Tanggal`, `Tahun`; nama bulan Indonesia/Inggris atau angka) dan di sekitar bidang ada titik panas dari beberapa waktu, hanya kejadian yang rentang titik panasnya bersinggungan dengan bulan itu (±3 hari; tanggal ±5 hari) yang boleh memiliki bidang; bila tidak ada, aturan biasa dipakai dan bidang ditandai `cocok_shp: false`. Areal kejadian sebelumnya menjadi fuel 99.
3. **Cuaca** ERA5/ERA5-Land per jam dari Open-Meteo di lokasi kejadian (dengan 3 hari pengkondisian kadar air), angin medan, fmd20 + fms + landscape dasar awal kemarau.
4. **Inversi waktu tempuh**: simulasi mundur dari tiap titik panas memberi, untuk tiap sel, jam paling akhir api bisa mulai di sel itu agar sampai di piksel satelit pada jam deteksi. Kandidat terbaik disimulasikan maju (FARSITE, 7 faktor laju api k ×0,35–×2,83) dan dinilai dari kecocokan waktu (termasuk lintasan satelit tanpa deteksi sebelum deteksi pertama) dan bentuk areal (Sørensen).
5. **Kalibrasi**: faktor k kejadian yang terlihat di ≥ 2 lintasan satelit digabung per kelas K1–K4 (regresi berbobot + bootstrap) → saran `ros_adjust`. Kejadian dengan intervensi pemadaman (catatan lapangan `[[pemadaman]]` di `config/tambora.toml`: rentang tanggal + titik + radius, atau id kejadian) tetap dihitung tetapi tidak dipakai untuk kalibrasi; contohnya api Piong 9–15 Sep 2026 yang dibelokkan pemadaman naik ke Doro Tabe Nae. Saran tidak diterapkan otomatis. Koreksi yang dipakai simulasi ada di `[kalibrasi] ros_adjust` (`config/tambora.toml`), atas persetujuan Deo:
   - Ronde 1 (2 Nov 2026, 33 kejadian): K2 ×0,77, K3 ×0,69, K4 ×1,33, hutan TU1 (161) ×3,13.
   - Ronde 2 (3 Nov 2026, 13 kejadian tanpa api Piong 9 Sep): K3 ×0,529 lagi, tepi hutan TU1 dan TL6 ×1,79; K2 dan K4 tetap. Sebelum dipasang, ronde 2 diuji di 17 kejadian lain yang tidak ikut kalibrasi (faktor k lebih dekat ke 1, galat waktu median 8,8 → 7,7 jam). Nilai sekarang: K2 (31) ×0,77, K3 (32) ×0,365, K4 (33) ×1,33, TU1 (161) ×5,59, TL6 (186) ×1,79.

   Faktor k dihitung terhadap simulasi yang sudah dikoreksi (×1 = koreksinya pas). Untuk menguji koreksi baru di pemodelan terbalik saja tanpa mengubah simulasi, isi `ros_adjust_uji` di bagian yang sama; tab Asal Api lalu menandainya "uji". Untuk FlamMap desktop, nilai `ros_adjust` yang sama ada di `kalibrasi/tambora_ros_adjust.adj` (FlamMap: ROS Adjust File).

6. **Laju api** (m/menit): laju muka api tiap sel dari simulasi terbaik (1/|∇ waktu tiba|, seperti Spread Rate FARSITE) di dalam areal terbakar, per jam WITA; tab menampilkan rata-rata dan kepala api (persentil 90) per pagi, siang, sore, malam.
7. **Waktu terbakar per bidang** (`docs/data/asal_api/bidang_waktu.json`): deteksi titik panas pertama sampai terakhir di dalam bidang (bila tidak ada: titik panas kejadiannya ≤ 400 m, lalu jam tiba api simulasi), ditambah jam tiba api simulasi di bidang (persentil 10/50/90). Jam **mulai** api tetap per kejadian dari langkah 4. Bulan/tanggal dari SHP ikut ditulis (`bulan_shp`, `tanggal_shp`, `cocok_shp`). Klik bidang di peta tab Asal Api atau peta areal terbakar; unduh GeoJSON/CSV dari tab Asal Api.

Hasil: `docs/status/asal_api.json` (ringkasan, jam rawan, kalibrasi, tabel kejadian), `docs/data/asal_api/` (`peta.geojson`, `k/<id>.json` detail, `wxs/<id>.wxs` cuaca, `titik_panas.geojson`, `zona.geojson`, `bidang_waktu.json`).
Workflow **Asal api** (`.github/workflows/asal-api.yml`) berjalan tiap hari 06:40 WITA dan setelah ada delineasi baru; kejadian yang datanya tidak berubah tidak dihitung ulang.
Jalankan manual dari Actions dengan pilihan `ulang` (hitung semua), `hanya` (id kejadian), atau `firms_sejak` (tambah titik panas FIRMS sejak tanggal itu).

Di PC: `tambora asal-api --lcp landscape_10m.lcp --tanpa-firms` (perlu internet untuk Open-Meteo).

## Struktur kode

| File | Isi |
|---|---|
| `tamborafire/rothermel.py` | Rothermel + vektor angin–lereng + WAF + elips (sama dengan flammap.dll) |
| `tamborafire/spread.py` | Dijkstra 32 arah (numba), status bisa dilanjutkan per jam |
| `tamborafire/simulate.py` | mode MTT dan FARSITE |
| `tamborafire/wind.py` | angin seragam, model medan MicroMet, pembungkus WindNinja_cli |
| `tamborafire/moisture.py` | kadar air tetap (.fms) atau conditioning per jam |
| `tamborafire/weather.py` | Open-Meteo → .wxs, baca/tulis .wxs |
| `tamborafire/landscape.py`, `sentinel2.py` | landscape dasar dan update tiap citra baru, statistik per resort |
| `docs/` | dashboard GitHub Pages (`index.html`, `assets/`), lapisan peta `data/*.geojson` |
| `tamborafire/drive.py` | Google Drive (OAuth / service account) |
| `tamborafire/tiga_d.py`, `docs/assets/tiga_d.js` | data dan tampilan tab Simulasi 3D |
| `tamborafire/delineasi.py` | delineasi manual areal terbakar dari Drive → rekap per resort + GeoJSON (`docs/peta_terbakar.html`) |
| `tamborafire/asal.py`, `docs/assets/asal.js` | pemodelan terbalik titik asal dan jam mulai api, tab Asal Api |
| `tamborafire/lcp.py` | baca/tulis .lcp (header 7316 byte) |
| `tests/` | uji terhadap angka flammap.dll dan bekas terbakar 9 Sep |

## Catatan untuk dokumen latihan lama

Di file inputs FlamMap (`WIND_SPEED:`), satuan kecepatan angin adalah **mil/jam**, bukan km/j. Ini terbukti dari pembandingan di atas. Akibatnya, tabel di folder latihan yang dibuat lewat mesin uji (`uji/mesin_flammap`), misalnya ROS fmd15/fmd20 "angin 20 km/j", sebenarnya memakai 20 mil/jam (32 km/j).

Kalibrasi Anda di GUI FlamMap kemungkinan tidak terpengaruh kalau GUI memakai satuan Metric. Cara memastikan: buka `FlamMap6_inputs_*.txt` di folder temp. Angin 20 km/j harus tertulis `WIND_SPEED: 12.4` (dalam mil/jam). Arah hasil uji tidak berubah: Sørensen 0,70–0,73 dan arah 249–250°.
