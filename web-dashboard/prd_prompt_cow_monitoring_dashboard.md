# Product Requirement Document (PRD)
## Sub-Sistem: Frontend, Backend API & Web Server (Orang 2 — AMJ)
**Proyek:** Smart Phishing Detection System  
**Peran & Tanggung Jawab:** Orang 2 — Web Server dan Backend (AMJ)  
**Tujuan Dokumen:** Menjadi acuan spesifikasi teknis dan *system prompt* langsung untuk menginstruksikan AI / pengembang dalam membuat antarmuka web, backend FastAPI, serta konfigurasi web server Apache2.

---

## 1. Ringkasan Eksekutif & Batasan Peran

Sebagai **Orang 2**, Anda bertanggung jawab penuh atas penyediaan lapisan antarmuka pengguna (Frontend), logika aplikasi API (Backend FastAPI), serta konfigurasi pemrosesan *traffic* web server lokal/gateway (Apache2) yang mengintegrasikan modul klasifikasi dari **Orang 1** dan infrastruktur penyimpanan dari **Orang 3**.

### Matriks Keterhubungan Antar Peran:
*   **Orang 1 (DNS & Analisis Phishing):** Menyediakan logika Python untuk `Feature Extraction` (ekstraksi fitur URL) dan `Rule-Based Classifier` (klasifikasi Safe, Suspicious, Phishing).
*   **Orang 2 (Anda - Web & Backend):** Mengemas logika Orang 1 ke dalam REST API FastAPI, membangun UI/UX antarmuka pemeriksaan web, dan mengonfigurasi Web Server Apache2 sebagai penyaji static file / reverse proxy.
*   **Orang 3 (Cloud Infrastructure):** Menyediakan koneksi Firestore untuk riwayat data dan tempat *deployment* container backend ke Google Cloud Run.

---

## 2. Arsitektur & Spesifikasi Komponen (Orang 2)

### 2.1 Arsitektur Antarmuka & API
```
[ Browser / User ] 
       │
       ▼ (Port 80/443 - HTTP/HTTPS)
┌─────────────────────────────────────────────────────────────┐
│ Web Server Apache2                                          │
│  ├─ Serving Static Files (HTML/CSS/JS Frontend Dashboard)  │
│  └─ Reverse Proxy (/api/v1/*) ──► FastAPI Backend (:8000)   │
└─────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Backend FastAPI (Python)                                    │
│  ├─ API Routes (/analyze, /history, /statistics, /health)   │
│  ├─ URL Input Validator & Normalizer                        │
│  ├─ Engine Ekstraksi Fitur (Modul Orang 1)                  │
│  ├─ Rule-Based Classifier Engine (Modul Orang 1)            │
│  └─ Client Firestore / Cloud Storage Connector (Orang 3)    │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Kebutuhan Fungsional Backend (FastAPI)

### 3.1 Struktur Endpoint REST API
Backend harus menyediakan endpoint berikut sesuai dengan standar RESTful JSON API:

#### 1. `POST /api/v1/analyze`
*   **Fungsi:** Menerima input URL, mengekstrak fitur, mengalkulasi skor risiko, menentukan status klasifikasi, dan menyimpan histori.
*   **Request Body:**
    ```json
    {
      "url": "https://login-bank-secure-update.com/verify"
    }
    ```
*   **Response Body (200 OK):**
    ```json
    {
      "scan_id": "scan_8f91a2b3",
      "url": "https://login-bank-secure-update.com/verify",
      "classification": "PHISHING", // Options: SAFE, SUSPICIOUS, PHISHING
      "risk_score": 85,             // Range: 0 - 100
      "scanned_at": "2026-11-11T18:30:00Z",
      "features": {
        "url_length": 45,
        "has_ip": false,
        "has_at_symbol": false,
        "subdomain_count": 3,
        "has_https": true,
        "suspicious_keywords_found": ["login", "secure", "verify"],
        "is_shortened": false,
        "domain_age_days": 12,
        "ssl_valid": false
      },
      "rules_triggered": [
        "R-02: Domain baru (<30 hari) dengan banyak subdomain",
        "R-03: Mengandung lebih dari 2 kata kunci mencurigakan"
      ]
    }
    ```

#### 2. `GET /api/v1/history`
*   **Fungsi:** Mengambil daftar histori pemeriksaan URL terakhir.
*   **Query Params:** `limit` (default: 10)
*   **Response Body (200 OK):**
    ```json
    {
      "total": 1,
      "items": [
        {
          "scan_id": "scan_8f91a2b3",
          "url": "https://login-bank-secure-update.com/verify",
          "classification": "PHISHING",
          "risk_score": 85,
          "scanned_at": "2026-11-11T18:30:00Z"
        }
      ]
    }
    ```

#### 3. `GET /api/v1/history/{scan_id}`
*   **Fungsi:** Mengambil detail lengkap hasil analisis berdasarkan ID scan.

#### 4. `GET /api/v1/statistics`
*   **Fungsi:** Memberikan statistik pemindaian untuk ditampilkan di dashboard.
*   **Response Body (200 OK):**
    ```json
    {
      "total_scans": 1250,
      "safe_count": 820,
      "suspicious_count": 180,
      "phishing_count": 250
    }
    ```

#### 5. `GET /api/v1/health`
*   **Fungsi:** Health-check endpoint untuk monitoring server/cloud.
*   **Response Body (200 OK):** `{"status": "ok", "service": "phishing-detector-backend"}`

---

## 4. Kebutuhan Antarmuka Pengguna (Frontend Web UI)

Frontend dirancang sebagai **Single Page Application (SPA)** berbasis HTML5, Modern CSS (Tailwind CSS via CDN), dan Vanilla JavaScript / Alpine.js agar ringan dan mudah disajikan oleh Apache2.

### 4.1 Elemen Layar & Layout Halaman Utama
1.  **Header / Navigation:**
    *   Logo & Nama Sistem ("Smart Phishing Detection").
    *   Navigasi: *Checker*, *Dashboard Statistics*, *Scan History*, *API Docs link*.
    *   Status Indikator API (Online / Offline badge).

2.  **Section Utama - URL Analyzer Box:**
    *   Input text bar besar dengan placeholder: `Masukkan URL yang ingin diperiksa (contoh: https://example.com)`.
    *   Tombol "Periksa Sekarang" (*Analyse URL*) dengan ikon pencarian/keamanan.
    *   State Loading: Animasi *spinner* atau indikator proses scanning saat API dipanggil.

3.  **Section Hasil Pemeriksaan (Result View):**
    *   **Status Badge Utama:**
        *   🟢 **SAFE** (Warna Hijau) - Website terindikasi aman.
        *   🟡 **SUSPICIOUS** (Warna Kuning/Orange) - Memiliki indikator mencurigakan.
        *   🔴 **PHISHING** (Warna Merah) - Berbahaya / Indikasi kuat phishing.
    *   **Gauge / Progress Bar Risk Score:** Visualisasi tingkat risiko (0% hingga 100%).
    *   **Breakdown Fitur Ekstraksi (Grid Cards):**
        *   Penggunaan HTTPS (Valid / Tidak).
        *   Pemeriksaan IP Address.
        *   Analisis Subdomain & Panjang URL.
        *   Temuan Kata Kunci Mencurigakan (*Keywords*).
        *   Umur Domain & Sertifikat SSL.
    *   **Daftar Aturan Terdeteksi (*Triggered Rules*):** Menjelaskan alasan mengapa URL mendapat kategori tersebut.

4.  **Section Statistik & Histori Terbaru:**
    *   Kartu Ringkasan Total Scan, Total Safe, Total Suspicious, dan Total Phishing.
    *   Tabel *Recent History* (URL, Status Badge, Waktu Scan, Waktu Pemeriksaan).

---

## 5. Integrasi Kode Klasifikasi (Dari Orang 1)

Backend FastAPI harus mengimpor dan memanggil modul analisis buatan Orang 1.
Struktur kode backend dirancang secara modular:

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # Inisialisasi FastAPI & Middleware CORS
│   ├── schemas.py           # Model data Pydantic (Request/Response)
│   ├── services/
│   │   ├── feature_extractor.py  # Kode ekstraksi fitur dari Orang 1
│   │   ├── classifier.py         # Kode aturan klasifikasi Rule-Based dari Orang 1
│   │   └── storage_service.py    # Handler simpan ke Firestore (Orang 3)
│   └── routes/
│       ├── analyze.py       # Handler POST /api/v1/analyze
│       ├── history.py       # Handler GET /api/v1/history
│       └── stats.py         # Handler GET /api/v1/statistics
├── requirements.txt
└── Dockerfile
```

---

## 6. Konfigurasi Web Server Apache2

### 6.1 Direktori Kerja
*   Frontend files: `/var/www/html/phishing-app/`
*   VirtualHost Config: `/etc/apache2/sites-available/phishing-app.conf`

### 6.2 Contoh Konfigurasi VirtualHost Apache2
Apache2 bertindak sebagai server berkas statis sekaligus **Reverse Proxy** ke backend FastAPI yang berjalan di port 8000:

```apache
<VirtualHost *:80>
    ServerName phishing-detector.local
    ServerAdmin admin@domain.local

    DocumentRoot /var/www/html/phishing-app

    <Directory /var/www/html/phishing-app>
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
        DirectoryIndex index.html
    </Directory>

    # Module Proxy Reverse ke FastAPI Backend
    ProxyPreserveHost On
    ProxyPass /api/v1/ http://127.0.0.1:8000/api/v1/
    ProxyPassReverse /api/v1/ http://127.0.0.1:8000/api/v1/

    ErrorLog ${APACHE_LOG_DIR}/phishing_error.log
    CustomLog ${APACHE_LOG_DIR}/phishing_access.log combined
</VirtualHost>
```

---

## 7. Rencana Pengujian API (Postman Collection)

Sebagai Orang 2, Anda harus memverifikasi endpoint backend menggunakan Postman sebelum diintegrasikan penuh ke antarmuka frontend:

1.  **Test Case 1: Post URL Normal (Safe)**
    *   Input: `https://www.google.com`
    *   Expected: Status `200 OK`, classification `SAFE`, risk_score `< 30`.
2.  **Test Case 2: Post URL Phishing (Suspicious/Phishing)**
    *   Input: `http://192.168.1.1/login-bank-account-update/verify.php`
    *   Expected: Status `200 OK`, classification `PHISHING`, risk_score `> 70`.
3.  **Test Case 3: Invalid Input URL Validation**
    *   Input: `not_a_valid_url`
    *   Expected: Status `422 Unprocessable Entity` (FastAPI Validation Error).
4.  **Test Case 4: Fetch Scan History**
    *   Input: `GET /api/v1/history`
    *   Expected: Status `200 OK`, mengembalikan array berisi item histori.

---

## 8. Panduan Penggunaan PRD Ini Sebagai Prompt

Saat meminta AI (misal: Gemini) membuatkan kode program untuk tugas Orang 2, gunakan perintah berikut:

> *"Berdasarkan PRD `prd_web_backend_prompt.md`, buatkan file lengkap `index.html` (Frontend single-file dengan Tailwind CSS) dan aplikasi backend FastAPI `main.py` beserta modul integrasi klasifikasi URL."*