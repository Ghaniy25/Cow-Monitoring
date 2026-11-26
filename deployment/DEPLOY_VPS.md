# Panduan Deploy BOVISION 24/7 di VPS Kampus

Agar website dashboard, database log CSV, statistik real-time, export PDF, dan bot Telegram **tetap nyala 24/7 meskipun laptop Anda mati** dan **bisa diakses oleh teman-teman / dosen penguji**, ikuti langkah berikut di VPS Linux (Ubuntu/Debian) kampus Anda.

---

## 1. Persiapan di VPS Kampus (SSH ke VPS)

```bash
# Clone atau update repository
git clone https://github.com/Ghaniy25/Cow-Monitoring.git Cow-Monitoring-CV
cd Cow-Monitoring-CV

# Buat virtual environment Python khusus backend (ringan, tidak butuh GPU)
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Tes jalan sebentar:
```bash
python main.py
# Buka terminal lain dan cek: curl http://127.0.0.1:8000/health
# Jika muncul {"status":"ok",...}, tekan CTRL+C lalu lanjut ke langkah 2.
```

---

## 2. Jalankan Terus 24/7 (Walau SSH Ditutup & Laptop Mati)

### Cara A (Paling Stabil & Auto-Restart) — Menggunakan `systemd` (Jika punya akses `sudo`)
1. Sesuaikan `User` dan path `/home/ubuntu/...` di file `deployment/bovision.service` dengan username VPS Anda (`whoami` & `pwd`).
2. Pasang dan aktifkan service:
```bash
sudo cp deployment/bovision.service /etc/systemd/system/bovision.service
sudo systemctl daemon-reload
sudo systemctl enable --now bovision
sudo systemctl status bovision
```
Cek log kapan saja:
```bash
sudo journalctl -u bovision -f
```

### Cara B (Jika TIDAK punya akses `sudo` di VPS Kampus) — Menggunakan `tmux` atau `nohup`
```bash
tmux new -s bovision
cd ~/Cow-Monitoring-CV
source .venv/bin/activate
python main.py
```
Tekan **`Ctrl + B`**, lalu tekan **`D`** (detach). Sekarang Anda boleh keluar dari SSH dan mematikan laptop — server tetap menyala 24/7!

---

## 3. Cara Supaya Teman-Teman Bisa Akses (Sesuai Aturan Firewall VPS Kampus)

### Opsi 1: VPS Kampus Punya Domain / Subdomain atau Port Publik
- Jika dikelola admin kampus (minta dibuka port 80/443 dengan Nginx): pakai file `deployment/nginx-bovision.conf` (perhatikan `proxy_buffering off;` agar live stream lancar).
- Jika admin kampus memberi port khusus (misal port `8080` atau `8012`), jalankan dengan:
  ```bash
  PORT=8080 python main.py
  ```
  Teman Anda tinggal buka `http://IP-VPS-KAMPUS:8080`.

### Opsi 2: Firewall VPS Kampus Ketat (Tidak Boleh Buka Port Publik) — Pakai Cloudflare Tunnel (GRATIS & Tanpa `sudo`!)
Cloudflare Tunnel membuat koneksi **keluar** dari VPS, sehingga **tidak perlu buka port firewall kampus** dan langsung memberi link **HTTPS publik** yang aman:

```bash
# Download binary cloudflared (tanpa perlu root/sudo)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o ~/cloudflared
chmod +x ~/cloudflared

# Jalankan di sesi tmux terpisah agar aktif 24/7:
tmux new -s tunnel
~/cloudflared tunnel --url http://127.0.0.1:8000
```
Cloudflare akan menampilkan URL publik seperti `https://xxxx-xxxx.trycloudflare.com` (atau gunakan Named Tunnel dengan domain sendiri agar linknya permanen tidak berubah saat restart). Tekan `Ctrl + B` lalu `D` untuk keluar dari tmux.

### Opsi 3: Akses Privat Khusus Tim & Dosen (Tailscale)
Jika kampus melarang publikasi ke internet umum, instal **Tailscale** di VPS dan perangkat teman-teman. Semua yang tergabung di tailnet yang sama bisa langsung mengakses `http://100.x.y.z:8000` dengan aman dan terenkripsi.

---

## 4. Menghubungkan Live AI YOLO (`infer_rtsp.py`) ke VPS

- **Skenario 1 (CCTV/Laptop Edge terpisah dari VPS):**
  Di laptop/komputer yang tersambung ke CCTV kandang, cukup jalankan `infer_rtsp.py` sambil mengarahkannya ke alamat VPS:
  ```powershell
  $env:BOVISION_BACKEND_URL="https://domain-atau-tunnel-vps-anda"
  python ai-cctv/infer_rtsp.py
  ```
  Semua frame bounding box, log CSV, statistik, dan auto-alert Telegram langsung mengalir real-time ke VPS!

- **Skenario 2 (Jalankan `infer_rtsp.py` langsung di VPS 24/7 tanpa laptop):**
  Pastikan stream RTSP (atau file video demo) bisa diakses dari VPS, instal `ultralytics opencv-python-headless requests`, lalu jalankan mode tanpa layar (`BOVISION_SHOW=false`):
  ```bash
  BOVISION_SHOW=false BOVISION_RTSP_SOURCE="rtsp://alamat-kamera-atau-video" python ai-cctv/infer_rtsp.py
  ```
