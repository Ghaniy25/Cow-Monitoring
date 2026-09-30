# 🐄 Sistem Monitoring Perilaku Sapi (Cow-Monitoring-CV)

Proyek ini adalah sistem pemantauan cerdas berbasis Computer Vision untuk mendeteksi perilaku sapi (berdiri/berbaring) melalui rekaman CCTV. Analitik dari model AI akan dikirimkan dan ditampilkan secara *real-time* melalui Web Dashboard yang di-*host* di VPS.

Proyek ini dikembangkan menggunakan arsitektur *Monorepo* yang memisahkan lingkungan pengembangan AI, Web, dan Deployment.

## 📂 Struktur Proyek

- `/ai-cctv`: Berisi kode sumber Computer Vision (Python, OpenCV, PyTorch/YOLO), integrasi Google Sheets (gspread), dan script pengirim *stream* video.
- `/web-dashboard`: Berisi kode untuk antarmuka pengguna (Frontend) dan server API (Backend).
- `/deployment`: Berisi konfigurasi Docker, Nginx, dan script VPS.
- `/docs`: Berisi dokumentasi arsitektur dan spesifikasi API.

## 🛠️ Persiapan (Prerequisites)

Sebelum menjalankan proyek ini, pastikan laptop kamu sudah terinstal:
- [Git](https://git-scm.com/)
- [Python 3.x](https://www.python.org/) (Untuk Tim AI)
- [Node.js & npm](https://nodejs.org/) (Untuk Tim Web)

> **Catatan Penting:** Minta file kredensial `cow-monitoring-cv-xxx.json` dan `.env` ke administrator (Ghaniy) karena file tersebut rahasia dan tidak diunggah ke GitHub.

## 🚀 Cara Menjalankan Proyek Secara Lokal

### 1. Untuk Tim AI & CCTV (Python)
Buka terminal, masuk ke folder AI, instal dependensi, dan jalankan script utama:

```bash
cd ai-cctv
pip install -r requirements.txt
python src/app.py
```

### 2. Untuk Tim Web Dashboard
Buka terminal baru, masuk ke folder frontend, instal library, dan jalankan server web:

```bash
cd web-dashboard/frontend
npm install
npm run dev
```

## 🤝 Aturan Kolaborasi Git (SANGAT PENTING)

Agar kode tidak bentrok (*merge conflict*), patuhi alur kerja berikut:

**1. Jangan pernah mengoding langsung di branch `main`!**

**2. Selalu pastikan kodenya *up-to-date* sebelum membuat cabang baru:**
```bash
git checkout main
git pull origin main
```

**3. Buat *branch* baru untuk setiap fitur yang kamu kerjakan:**
Gunakan format `fitur/nama-fitur`.
```bash
git checkout -b fitur/kamera-streaming
```

**4. Simpan perubahan (Commit) secara rutin:**
```bash
git add .
git commit -m "Menambahkan script deteksi sapi dengan OpenCV"
```

**5. Unggah (Push) *branch* kamu ke GitHub:**
```bash
git push -u origin fitur/kamera-streaming
```

**6. Buat Pull Request (PR):**
- Buka halaman *repository* kita di GitHub.
- Klik tombol **Compare & pull request**.
- Minta anggota tim lain untuk me-*review* dan melakukan **Merge**.

## 👥 Tim Pengembang

- **Abrar** - AI / CV Engineer
- **Farhan Nur** - Backend & VPS
- **Zikra** - Web Frontend
- **Ghaniy** - Integrasi & Dokumentasi