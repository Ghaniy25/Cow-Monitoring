from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
import io
import time
import csv
import json
import asyncio
import random
import string
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

# Pastikan working directory selalu berada di folder proyek Cow-Monitoring-CV
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# Cek ketersediaan OpenCV
try:
    import cv2
    OPENCV_AVAILABLE = True
except ImportError:
    cv2 = None
    OPENCV_AVAILABLE = False

# Cek ketersediaan Pillow untuk fallback generator frame 1280x720
try:
    from PIL import Image, ImageDraw
    PILLOW_AVAILABLE = True
except ImportError:
    Image = None
    ImageDraw = None
    PILLOW_AVAILABLE = False

app = FastAPI(title="BOVISION Backend")

# Enable CORS agar API dapat diakses dari origin/port mana pun (Live Server port 5500, Vite, localhost, IP LAN)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inisialisasi Database JSON Lokal (data/users.json)
DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")

# Inisialisasi Database CSV Deteksi Sapi (data/cow_detections.csv)
CSV_FILE = os.path.join(DATA_DIR, "cow_detections.csv")
CSV_HEADERS = ["timestamp", "camera_id", "tag_number", "behavior", "confidence", "status", "notes"]

DEFAULT_CSV_LOGS = [
    ["2026-11-18 18:30:10", "CAM-01", "COW-104", "Standing", "95.4%", "Normal", "Postur tegak aktif di sektor pakan"],
    ["2026-11-18 18:28:05", "CAM-01", "COW-105", "Lying", "92.1%", "Normal", "Ruminasi istirahat wajar"],
    ["2026-11-18 18:25:00", "CAM-02", "COW-108", "Lying", "96.8%", "Warning", "Inaktivitas > 4 jam berturut-turut"],
    ["2026-11-18 18:15:22", "CAM-01", "COW-101", "Standing", "94.0%", "Normal", "Aktivitas makan normal"],
    ["2026-11-18 18:02:11", "CAM-02", "COW-103", "Standing", "89.5%", "Normal", "Pergerakan kawanan normal"],
    ["2026-11-18 17:45:30", "CAM-01", "COW-104", "Standing", "93.7%", "Normal", "Sektor utama kandang A"],
    ["2026-11-18 17:30:15", "CAM-01", "COW-107", "Lying", "91.2%", "Normal", "Berbaring istirahat petang"],
    ["2026-11-18 17:10:00", "CAM-02", "COW-102", "Standing", "96.1%", "Normal", "Minum di sektor air minum"]
]

def init_csv():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)
            writer.writerows(DEFAULT_CSV_LOGS)

def read_csv_logs(limit: int = 50):
    init_csv()
    logs = []
    try:
        with open(CSV_FILE, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = row.get("timestamp", "")
                row["time"] = ts.split(" ")[-1] if " " in ts else ts
                logs.append(row)
    except Exception as e:
        print(f"Error reading CSV logs: {e}")
    logs.reverse()  # Urutan paling baru di paling atas
    if limit and limit > 0:
        return logs[:limit]
    return logs

def parse_conf_float(conf_str: str) -> float:
    try:
        return float(str(conf_str).replace("%", "").strip())
    except Exception:
        return 0.0

def is_lying_behavior(beh: str) -> bool:
    b = (beh or "").lower().strip()
    return b in ("lying", "lying down", "lying_down", "rumination", "resting")

def compute_bucket_series(rows_chronological, num_buckets: int, label_formatter):
    """Membagi rows kronologis ke dalam num_buckets kelompok dan menghitung persentase Standing vs Lying riil."""
    if not rows_chronological:
        return {
            "labels": ["-"] * num_buckets,
            "standing": [0] * num_buckets,
            "lying": [0] * num_buckets,
            "counts": [0] * num_buckets,
            "peakStanding": "-",
            "peakLying": "-",
            "standingAvg": "0%",
            "lyingAvg": "0%",
        }

    n = len(rows_chronological)
    actual_buckets = min(num_buckets, n)
    labels = []
    standing_arr = []
    lying_arr = []
    counts_arr = []

    for i in range(actual_buckets):
        start_idx = int(i * n / actual_buckets)
        end_idx = int((i + 1) * n / actual_buckets)
        chunk = rows_chronological[start_idx:end_idx]
        if not chunk:
            continue
        lbl = label_formatter(chunk, i)
        l_cnt = sum(1 for r in chunk if is_lying_behavior(r.get("behavior", "")))
        s_cnt = len(chunk) - l_cnt
        s_pct = round((s_cnt / len(chunk)) * 100)
        l_pct = 100 - s_pct
        labels.append(lbl)
        standing_arr.append(s_pct)
        lying_arr.append(l_pct)
        counts_arr.append(len(chunk))

    total_l = sum(1 for r in rows_chronological if is_lying_behavior(r.get("behavior", "")))
    total_s = n - total_l
    s_avg = round((total_s / n) * 100)
    l_avg = 100 - s_avg

    # Cari bucket dengan aktivitas berdiri & berbaring tertinggi
    best_s_idx = max(range(len(standing_arr)), key=lambda idx: (standing_arr[idx], counts_arr[idx]))
    best_l_idx = max(range(len(lying_arr)), key=lambda idx: (lying_arr[idx], counts_arr[idx]))

    peak_s = f"{labels[best_s_idx]} ({standing_arr[best_s_idx]}% • {counts_arr[best_s_idx]} log)"
    peak_l = f"{labels[best_l_idx]} ({lying_arr[best_l_idx]}% • {counts_arr[best_l_idx]} log)"

    return {
        "labels": labels,
        "standing": standing_arr,
        "lying": lying_arr,
        "counts": counts_arr,
        "peakStanding": peak_s,
        "peakLying": peak_l,
        "standingAvg": f"{s_avg}%",
        "lyingAvg": f"{l_avg}%",
    }

init_csv()

DEFAULT_USERS = [
    {
        "id": 1,
        "name": "Dr. Hendra Gunawan, M.Sc.",
        "username": "hendra_super",
        "email": "admin@bovision.com",
        "password": "Bovision@2025",
        "role": "Admin",
        "status": "Aktif",
        "last_login": "Hari ini, 09:15 WIB"
    },
    {
        "id": 2,
        "name": "Budi Santoso",
        "username": "budi_op1",
        "email": "operator@bovision.com",
        "password": "Bovision@2025",
        "role": "Operator",
        "status": "Aktif",
        "last_login": "Hari ini, 08:30 WIB"
    },
    {
        "id": 3,
        "name": "Siti Rahmawati, S.Pt",
        "username": "siti_paramedis",
        "email": "budi@bovision.id",
        "password": "Bovision@2025",
        "role": "Operator",
        "status": "Aktif",
        "last_login": "Kemarin, 16:45 WIB"
    },
    {
        "id": 4,
        "name": "Rian Anggara",
        "username": "rian_op3",
        "email": "rian@bovision.id",
        "password": "Bovision@2025",
        "role": "Operator",
        "status": "Nonaktif",
        "last_login": "3 hari yang lalu"
    }
]

def load_users():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(USERS_FILE):
        save_users(DEFAULT_USERS)
        return DEFAULT_USERS
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        save_users(DEFAULT_USERS)
        return DEFAULT_USERS

def save_users(users):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

# Inisialisasi awal database json
load_users()

# 1. Mount folder web-dashboard agar file HTML, CSS, & JS bisa diakses browser
app.mount("/assets", StaticFiles(directory="web-dashboard/assets"), name="assets")
app.mount("/pages", StaticFiles(directory="web-dashboard/pages", html=True), name="pages")

# Route untuk Halaman Utama / Login (Mendukung berbagai variasi path login)
@app.get("/")
@app.get("/login")
@app.get("/login.html")
@app.get("/index.html")
def serve_login():
    return FileResponse("web-dashboard/index.html")

@app.get("/dashboard")
@app.get("/dashboard.html")
def serve_dashboard():
    return FileResponse("web-dashboard/pages/dashboard.html")

# Schemas
class LoginPayload(BaseModel):
    identifier: str
    password: str

class GoogleAuthPayload(BaseModel):
    email: str
    name: str

class CreateUserPayload(BaseModel):
    name: str
    username: str
    email: str
    role: str
    password: str = "Bovision@2025"

class UpdateStatusPayload(BaseModel):
    status: str = None

class UpdateProfilePayload(BaseModel):
    name: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None

# 2. Endpoint Autentikasi Kredensial (POST /api/auth/login)
@app.post("/api/auth/login")
def login(payload: LoginPayload):
    users = load_users()
    identifier = payload.identifier.strip().lower()
    
    # Validasi keberadaan user berdasarkan email atau username
    user = next((u for u in users if u["email"].lower() == identifier or u["username"].lower() == identifier), None)
    
    if not user:
        raise HTTPException(
            status_code=401, 
            detail="Email atau username tidak terdaftar di sistem."
        )
    
    # Validasi kata sandi
    if user.get("password") != payload.password:
        raise HTTPException(
            status_code=401, 
            detail="Password yang Anda masukkan salah. Periksa kembali kata sandi Anda."
        )
        
    # Validasi status akun
    if user.get("status") != "Aktif":
        raise HTTPException(
            status_code=401, 
            detail="Akun Anda berstatus Nonaktif. Silakan hubungi Administrator untuk mengaktifkan kembali akun Anda."
        )
        
    # Update stempel last login
    now_str = datetime.now().strftime("Hari ini, %H:%M WIB")
    user["last_login"] = now_str
    save_users(users)
    
    # Buat copy user tanpa password untuk dikirim ke frontend
    user_data = {k: v for k, v in user.items() if k != "password"}
    
    return {
        "status": "success",
        "message": "Login berhasil",
        "user": user_data
    }

# 3. Endpoint Autentikasi Google Whitelist (Tersinkronisasi dengan Database Users)
@app.post("/api/auth/google")
def google_auth(payload: GoogleAuthPayload):
    users = load_users()
    email = payload.email.strip().lower()
    user = next((u for u in users if u["email"].lower() == email), None)
    
    if not user:
        raise HTTPException(
            status_code=403, 
            detail="Akses ditolak: Email Anda belum didaftarkan oleh Admin."
        )
    
    if user.get("status") != "Aktif":
        raise HTTPException(
            status_code=403, 
            detail="Akses ditolak: Akun Anda sedang dinonaktifkan oleh Admin. Hubungi Superadmin."
        )
    
    # Update stempel last login
    now_str = datetime.now().strftime("Hari ini, %H:%M WIB")
    user["last_login"] = now_str
    save_users(users)
    
    user_data = {k: v for k, v in user.items() if k != "password"}
    return {
        "status": "success",
        "message": "Login berhasil",
        "email": user["email"],
        "name": user["name"],
        "role": user["role"].lower(),
        "user": user_data
    }

# 4. Endpoint Manajemen Pengguna (CRUD)
@app.get("/api/users")
def get_users():
    users = load_users()
    # Sertakan password agar Admin dapat melihat kredensial akun staf
    return [{**u} for u in users]

@app.post("/api/users")
def create_user(payload: CreateUserPayload):
    users = load_users()
    email_clean = payload.email.strip().lower()
    username_clean = payload.username.strip().lower()
    
    # Cek duplikasi email
    if any(u["email"].lower() == email_clean for u in users):
        raise HTTPException(status_code=400, detail="Email ini sudah terdaftar dalam sistem.")
    
    # Cek duplikasi username
    if any(u["username"].lower() == username_clean for u in users):
        raise HTTPException(status_code=400, detail="Username sudah digunakan oleh staf lain.")
    
    new_id = max([u.get("id", 0) for u in users], default=0) + 1
    new_user = {
        "id": new_id,
        "name": payload.name.strip(),
        "username": payload.username.strip(),
        "email": payload.email.strip(),
        "password": payload.password or "Bovision@2025",
        "role": payload.role.strip().capitalize(),
        "status": "Aktif",
        "last_login": "Baru dibuat"
    }
    users.append(new_user)
    save_users(users)
    return {**new_user}

@app.put("/api/users/{user_id}/profile")
def update_user_profile(user_id: int, payload: UpdateProfilePayload):
    users = load_users()
    user = next((u for u in users if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")
    
    # Update Nama jika ada
    if payload.name and payload.name.strip():
        user["name"] = payload.name.strip()
    
    # Jika pengguna ingin mengganti kata sandi
    if payload.new_password:
        if not payload.current_password:
            raise HTTPException(status_code=400, detail="Password saat ini wajib diisi untuk mengubah kata sandi.")
        if user.get("password") != payload.current_password:
            raise HTTPException(status_code=400, detail="Password saat ini salah. Periksa kembali kata sandi Anda.")
        if len(payload.new_password) < 6:
            raise HTTPException(status_code=400, detail="Password baru minimal 6 karakter.")
        user["password"] = payload.new_password
        
    save_users(users)
    
    user_data = {k: v for k, v in user.items() if k != "password"}
    return {
        "status": "success",
        "message": "Profil dan pengaturan akun berhasil diperbarui.",
        "user": user_data
    }

@app.delete("/api/users/{user_id}")
def delete_user(user_id: int):
    users = load_users()
    user = next((u for u in users if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")
    
    users = [u for u in users if u["id"] != user_id]
    save_users(users)
    return {"status": "success", "message": f"Pengguna {user['name']} berhasil dihapus dari sistem."}

@app.patch("/api/users/{user_id}/status")
def update_user_status(user_id: int, payload: UpdateStatusPayload = None):
    users = load_users()
    user = next((u for u in users if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")
    
    if payload and payload.status:
        user["status"] = payload.status
    else:
        user["status"] = "Nonaktif" if user.get("status") == "Aktif" else "Aktif"
        
    save_users(users)
    return {
        "status": "success", 
        "user_id": user_id, 
        "new_status": user["status"], 
        "message": f"Status {user['name']} diubah menjadi {user['status']}."
    }

@app.post("/api/users/{user_id}/reset-password")
def reset_password(user_id: int):
    users = load_users()
    user = next((u for u in users if u["id"] == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")
    
    random_str = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
    new_pass = f"Bov#{random_str}!"
    user["password"] = new_pass
    save_users(users)
    return {
        "status": "success",
        "message": f"Password untuk {user['name']} berhasil direset.",
        "temporary_password": new_pass
    }

# ==========================================
# 5. Endpoint Sistem Logging & Export CSV
# ==========================================

class DetectionLogCreate(BaseModel):
    camera_id: Optional[str] = "CAM-01"
    tag_number: str
    behavior: str
    confidence: Optional[str] = "95.0%"
    status: Optional[str] = "Normal"
    notes: Optional[str] = "Deteksi otomatis YOLOv11"

@app.get("/api/logs")
def get_detection_logs(limit: int = 50):
    """Membaca riwayat deteksi perilaku sapi dari file data/cow_detections.csv."""
    return read_csv_logs(limit=limit)

@app.post("/api/logs")
def create_detection_log(payload: DetectionLogCreate):
    """Menyimpan data deteksi baru dari pipeline AI YOLOv11 ke file CSV lokal."""
    init_csv()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [
        now_str,
        payload.camera_id,
        payload.tag_number,
        payload.behavior,
        payload.confidence,
        payload.status,
        payload.notes
    ]
    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)
        
    return {
        "status": "success",
        "message": "Data deteksi berhasil dicatat ke CSV.",
        "entry": {
            "timestamp": now_str,
            "camera_id": payload.camera_id,
            "tag_number": payload.tag_number,
            "behavior": payload.behavior,
            "confidence": payload.confidence,
            "status": payload.status,
            "notes": payload.notes
        }
    }

@app.get("/api/export/csv")
def export_csv_logs():
    """Mengunduh file rekaman deteksi cow_detections.csv secara langsung."""
    init_csv()
    filename = f"cow_detections_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return FileResponse(
        path=CSV_FILE,
        filename=filename,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/api/stats")
def get_realtime_stats():
    """Menghitung statistik real-time, grafik tren, dan ringkasan laporan PDF langsung dari data/cow_detections.csv & live telemetry."""
    all_logs_newest_first = read_csv_logs(limit=0)
    all_logs_chrono = list(reversed(all_logs_newest_first))

    is_live = (time.time() - LATEST_YOLO_TELEMETRY.get("last_update", 0.0)) < 6.0

    # Jika ada frame live terbaru yang belum masuk interval 3 detik CSV, ikutkan dalam kalkulasi real-time
    combined_chrono = list(all_logs_chrono)
    if is_live and LATEST_YOLO_TELEMETRY.get("detections"):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cam_live = (LATEST_YOLO_TELEMETRY.get("cam_id") or "CAM-01").upper()
        for d in LATEST_YOLO_TELEMETRY["detections"]:
            beh_raw = d.get("behavior", "stand")
            beh_lower = beh_raw.lower().strip()
            combined_chrono.append({
                "timestamp": now_str,
                "time": now_str.split(" ")[-1],
                "camera_id": cam_live,
                "tag_number": d.get("tag_number", "COW-??"),
                "behavior": beh_raw,
                "confidence": d.get("confidence", "90.0%"),
                "status": "Normal",
                "notes": BEHAVIOR_NOTES_MAP.get(beh_lower, f"Deteksi otomatis YOLO ({beh_raw})"),
            })

    total_logs = len(combined_chrono)
    if total_logs == 0:
        return {"total_logs": 0, "online": is_live}

    # Hitung agregat utama
    lying_total = sum(1 for r in combined_chrono if is_lying_behavior(r.get("behavior", "")))
    standing_total = total_logs - lying_total
    standing_pct = round((standing_total / total_logs) * 100)
    lying_pct = 100 - standing_pct

    # Jika sedang live dan ada deteksi aktif, sediakan juga persentase frame detik ini
    if is_live and LATEST_YOLO_TELEMETRY.get("active_count", 0) > 0:
        live_standing_pct = LATEST_YOLO_TELEMETRY.get("standing_pct", standing_pct)
        live_lying_pct = LATEST_YOLO_TELEMETRY.get("lying_pct", lying_pct)
    else:
        live_standing_pct = standing_pct
        live_lying_pct = lying_pct

    conf_vals = [parse_conf_float(r.get("confidence", "0")) for r in combined_chrono if parse_conf_float(r.get("confidence", "0")) > 0]
    avg_conf = round(sum(conf_vals) / len(conf_vals), 1) if conf_vals else 0.0
    max_conf = round(max(conf_vals), 1) if conf_vals else 0.0

    # Breakdown per behavior
    behavior_counts = {}
    for r in combined_chrono:
        b = (r.get("behavior") or "stand").lower().strip()
        behavior_counts[b] = behavior_counts.get(b, 0) + 1

    # Kelompokkan berdasarkan tanggal
    by_date = {}
    for r in combined_chrono:
        ts = r.get("timestamp", "")
        dt_part = ts.split(" ")[0] if " " in ts else ts[:10]
        if dt_part:
            by_date.setdefault(dt_part, []).append(r)

    sorted_dates = sorted(by_date.keys())
    latest_date = sorted_dates[-1] if sorted_dates else datetime.now().strftime("%Y-%m-%d")
    latest_day_rows = by_date.get(latest_date, combined_chrono)

    unique_tags_all = sorted(set(r.get("tag_number", "COW-??") for r in combined_chrono if r.get("tag_number")))
    unique_tags_latest = sorted(set(r.get("tag_number", "COW-??") for r in latest_day_rows if r.get("tag_number")))

    # Rata-rata objek sapi per timestamp snapshot pada sesi terbaru
    ts_groups = {}
    for r in latest_day_rows:
        ts_groups.setdefault(r.get("timestamp", ""), []).append(r)
    avg_cows_per_frame = round(sum(len(v) for v in ts_groups.values()) / max(len(ts_groups), 1), 1)

    display_cow_count = LATEST_YOLO_TELEMETRY.get("active_count", 0) if (is_live and LATEST_YOLO_TELEMETRY.get("active_count", 0) > 0) else len(unique_tags_latest)

    # 1. Chart "today" (Sesi Hari Terakhir Tercatat - 7 titik kronologis)
    def fmt_today_lbl(chunk, idx):
        t0 = chunk[0].get("time") or chunk[0].get("timestamp", "").split(" ")[-1]
        return t0[:5] if len(t0) >= 5 else t0

    chart_today = compute_bucket_series(latest_day_rows, 7, fmt_today_lbl)
    chart_today["subtitle"] = f"Komparasi proporsi postur tercatat (Sesi {latest_date} • {len(latest_day_rows)} log deteksi)"

    # 2. Chart "week" (7 Segmen / Tanggal Riwayat Minggu Ini)
    if len(sorted_dates) >= 4:
        recent_dates = sorted_dates[-7:]
        w_labels, w_stand, w_lying, w_counts = [], [], [], []
        for d_str in recent_dates:
            d_rows = by_date[d_str]
            l_c = sum(1 for r in d_rows if is_lying_behavior(r.get("behavior", "")))
            s_c = len(d_rows) - l_c
            s_p = round((s_c / len(d_rows)) * 100)
            w_labels.append(d_str[5:])
            w_stand.append(s_p)
            w_lying.append(100 - s_p)
            w_counts.append(len(d_rows))
        best_s = max(range(len(w_stand)), key=lambda i: (w_stand[i], w_counts[i]))
        best_l = max(range(len(w_lying)), key=lambda i: (w_lying[i], w_counts[i]))
        chart_week = {
            "subtitle": f"Komparasi proporsi postur kawanan ({len(recent_dates)} Tanggal Tercatat • {total_logs} log)",
            "labels": w_labels,
            "standing": w_stand,
            "lying": w_lying,
            "counts": w_counts,
            "standingAvg": f"{standing_pct}%",
            "lyingAvg": f"{lying_pct}%",
            "peakStanding": f"{w_labels[best_s]} ({w_stand[best_s]}% • {w_counts[best_s]} log)",
            "peakLying": f"{w_labels[best_l]} ({w_lying[best_l]}% • {w_counts[best_l]} log)",
        }
    else:
        def fmt_week_lbl(chunk, idx):
            ts0 = chunk[0].get("timestamp", "")
            parts = ts0.split(" ")
            if len(parts) == 2:
                return f"{parts[0][5:]} {parts[1][:5]}"
            return ts0[:10]
        chart_week = compute_bucket_series(combined_chrono, 7, fmt_week_lbl)
        chart_week["subtitle"] = f"Komparasi proporsi postur kawanan (7 Segmen Riwayat Tercatat • {total_logs} log deteksi)"

    # 3. Chart "month" (4 Siklus / Kuartil Data Tercatat)
    def fmt_month_lbl(chunk, idx):
        ts0 = chunk[0].get("timestamp", "").split(" ")[0][5:]
        ts1 = chunk[-1].get("timestamp", "").split(" ")[-1][:5]
        return f"Segmen {idx + 1} ({ts0} {ts1})"

    chart_month = compute_bucket_series(combined_chrono, 4, fmt_month_lbl)
    chart_month["subtitle"] = f"Komparasi proporsi postur kawanan (4 Segmen Kumulatif • {total_logs} log deteksi)"

    # 4. Ringkasan Per Tag Sapi untuk Tabel Rekap & PDF
    cow_groups = {}
    for r in combined_chrono:
        tag = r.get("tag_number") or "COW-??"
        cow_groups.setdefault(tag, []).append(r)

    cow_summary = []
    for tag, rows in sorted(cow_groups.items(), key=lambda x: (-len(x[1]), x[0])):
        c_total = len(rows)
        c_lying = sum(1 for r in rows if is_lying_behavior(r.get("behavior", "")))
        c_stand = c_total - c_lying
        c_stand_pct = round((c_stand / c_total) * 100)
        c_lying_pct = 100 - c_stand_pct

        b_freq = {}
        for r in rows:
            b_name = (r.get("behavior") or "stand").replace("_", " ").strip()
            b_freq[b_name.lower()] = b_freq.get(b_name.lower(), 0) + 1
        dom_beh = max(b_freq.items(), key=lambda x: x[1])[0].title() if b_freq else "Stand"
        breakdown_str = ", ".join(f"{k.title()}: {v}" for k, v in sorted(b_freq.items(), key=lambda x: -x[1]))

        c_confs = [parse_conf_float(r.get("confidence", "0")) for r in rows if parse_conf_float(r.get("confidence", "0")) > 0]
        c_avg_conf = f"{round(sum(c_confs) / len(c_confs), 1)}%" if c_confs else "-"
        has_warn = any((r.get("status") or "").lower() != "normal" for r in rows)

        cow_summary.append({
            "tag_number": tag,
            "camera_id": rows[-1].get("camera_id", "CAM-01"),
            "total_detections": c_total,
            "standing_pct": c_stand_pct,
            "lying_pct": c_lying_pct,
            "dominant_behavior": dom_beh,
            "behavior_breakdown": breakdown_str,
            "avg_confidence": c_avg_conf,
            "first_seen": rows[0].get("timestamp", "-"),
            "last_seen": rows[-1].get("timestamp", "-"),
            "status": "Warning" if has_warn else "Normal",
        })

    # 5. Kurasi Log Penting untuk PDF (Deduplikasi beruntun per sapi + semua Warning, maks 22 baris)
    curated_logs = []
    last_state_per_tag = {}
    warning_logs = []
    transition_logs = []

    for r in reversed(combined_chrono):  # Dari terbaru ke terlama
        tag = r.get("tag_number", "COW-??")
        beh = (r.get("behavior") or "").lower().strip()
        st = (r.get("status") or "Normal").strip()
        if st.lower() != "normal":
            warning_logs.append(r)
            continue
        if last_state_per_tag.get(tag) != beh:
            last_state_per_tag[tag] = beh
            transition_logs.append(r)

    curated_logs = (warning_logs + transition_logs)[:22]
    curated_logs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    standing_hours = round((standing_pct / 100.0) * 24, 1)
    lying_hours = round((lying_pct / 100.0) * 24, 1)

    return {
        "online": is_live,
        "total_logs": total_logs,
        "latest_date": latest_date,
        "latest_day_logs": len(latest_day_rows),
        "unique_cows_total": len(unique_tags_all),
        "unique_cows_latest_day": len(unique_tags_latest),
        "display_cow_count": display_cow_count,
        "avg_cows_per_frame": avg_cows_per_frame,
        "standing_count": standing_total,
        "lying_count": lying_total,
        "standing_pct": standing_pct,
        "lying_pct": lying_pct,
        "live_standing_pct": live_standing_pct,
        "live_lying_pct": live_lying_pct,
        "standing_hours_est": standing_hours,
        "lying_hours_est": lying_hours,
        "avg_confidence": f"{avg_conf}%",
        "max_confidence": f"{max_conf}%",
        "behavior_counts": behavior_counts,
        "charts": {
            "today": chart_today,
            "week": chart_week,
            "month": chart_month,
        },
        "cow_summary": cow_summary,
        "curated_logs": curated_logs,
    }

# ==========================================
# 6. RTSP & Live YOLO Streaming Gateway (1280x720 HD)
# ==========================================

# Konfigurasi URL RTSP Kamera (Dapat diatur melalui environment variable)
RTSP_CAMERAS = {
    "cam-01": os.getenv("RTSP_CAM_01", "rtsp://localhost:8554/kandang-raw"),
    "cam-02": os.getenv("RTSP_CAM_02", "rtsp://localhost:8554/kandang-raw"),
}

# Buffer memori untuk menerima push frame ber-bounding box & telemetri langsung dari infer_rtsp.py
LATEST_YOLO_FRAMES = {}  # cam_id -> {"frame": bytes, "timestamp": float}
LATEST_YOLO_TELEMETRY = {
    "cam_id": "cam-01",
    "online": False,
    "last_update": 0.0,
    "fps": 0.0,
    "active_count": 0,
    "standing_count": 0,
    "lying_count": 0,
    "standing_pct": 55,
    "lying_pct": 45,
    "behavior_counts": {},
    "detections": []
}
LAST_CSV_WRITE_TIME = 0.0
CSV_WRITE_INTERVAL = 3.0  # Catat ke CSV setiap 3 detik agar tidak duplikat berlebihan
COW_BEHAVIOR_STREAK = {}  # tag_number -> {"behavior": str, "count": int, "first_ts": float}

BEHAVIOR_NOTES_MAP = {
    "stand": "Postur berdiri tegak aktif",
    "standing": "Postur berdiri tegak aktif",
    "foraging": "Aktivitas makan di area pakan",
    "drinking water": "Aktivitas minum di area air",
    "drinking_water": "Aktivitas minum di area air",
    "lying down": "Berbaring istirahat",
    "lying": "Berbaring istirahat",
    "rumination": "Berbaring fase ruminasi sehat",
}

@app.post("/api/yolo/push")
async def push_yolo_frame(request: Request):
    """Menerima frame JPEG ber-bounding box (1280x720) & metadata deteksi real-time dari infer_rtsp.py."""
    global LAST_CSV_WRITE_TIME, LATEST_YOLO_TELEMETRY, LAST_AUTO_TELEGRAM_TS
    frame_bytes = await request.body()
    meta_raw = request.headers.get("X-Yolo-Meta", "{}")
    try:
        meta = json.loads(meta_raw)
    except Exception:
        meta = {}

    cam_id = meta.get("cam_id", "cam-01")
    now_ts = time.time()

    if frame_bytes:
        LATEST_YOLO_FRAMES[cam_id] = {
            "frame": frame_bytes,
            "timestamp": now_ts
        }

    dets = meta.get("detections", [])
    fps = float(meta.get("fps", 0.0))

    standing_cnt = 0
    lying_cnt = 0
    b_counts = {}

    for d in dets:
        beh = (d.get("behavior") or "").lower().strip()
        b_counts[beh] = b_counts.get(beh, 0) + 1
        if beh in ("lying down", "lying", "rumination"):
            lying_cnt += 1
        else:
            standing_cnt += 1

    total = len(dets)
    if total > 0:
        s_pct = round((standing_cnt / total) * 100)
        l_pct = 100 - s_pct
    else:
        s_pct = LATEST_YOLO_TELEMETRY.get("standing_pct", 55)
        l_pct = LATEST_YOLO_TELEMETRY.get("lying_pct", 45)

    LATEST_YOLO_TELEMETRY = {
        "cam_id": cam_id,
        "online": True,
        "last_update": now_ts,
        "fps": fps,
        "active_count": total,
        "standing_count": standing_cnt,
        "lying_count": lying_cnt,
        "standing_pct": s_pct,
        "lying_pct": l_pct,
        "behavior_counts": b_counts,
        "detections": dets,
    }

    # Catat otomatis ke file data/cow_detections.csv setiap CSV_WRITE_INTERVAL detik
    if dets and (now_ts - LAST_CSV_WRITE_TIME >= CSV_WRITE_INTERVAL):
        LAST_CSV_WRITE_TIME = now_ts
        init_csv()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        triggered_warnings = []
        try:
            with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                for d in dets:
                    tag = d.get("tag_number", "COW-??")
                    beh_raw = d.get("behavior", "stand")
                    beh_lower = beh_raw.lower().strip()
                    conf_str = d.get("confidence", "90.0%")
                    conf_val = parse_conf_float(conf_str)

                    # Lacak durasi beruntun perilaku per sapi
                    prev = COW_BEHAVIOR_STREAK.get(tag)
                    if prev and prev["behavior"] == beh_lower:
                        prev["count"] += 1
                    else:
                        COW_BEHAVIOR_STREAK[tag] = {"behavior": beh_lower, "count": 1, "first_ts": now_ts}
                    streak_cnt = COW_BEHAVIOR_STREAK[tag]["count"]
                    streak_sec = int(now_ts - COW_BEHAVIOR_STREAK[tag]["first_ts"])

                    row_status = "Normal"
                    note = BEHAVIOR_NOTES_MAP.get(beh_lower, f"Deteksi otomatis YOLO ({beh_raw})")

                    if beh_lower in ("lying down", "lying") and streak_cnt >= 20:
                        row_status = "Warning"
                        note = f"Berbaring terus-menerus tanpa berdiri ({streak_sec} detik / {streak_cnt} siklus)"
                        triggered_warnings.append((tag, beh_raw, conf_str, note))
                    elif total >= 6:
                        row_status = "Warning"
                        note = f"Kepadatan sektor kamera tinggi ({total} ekor terdeteksi bersamaan)"
                        triggered_warnings.append((tag, beh_raw, conf_str, note))

                    writer.writerow([
                        now_str,
                        cam_id.upper(),
                        tag,
                        beh_raw,
                        conf_str,
                        row_status,
                        note
                    ])
        except Exception as e:
            print(f"Gagal menulis log YOLO ke CSV: {e}")

        # Auto-Notify ke Telegram jika ada Warning baru dan cooldown sudah lewat
        if triggered_warnings:
            tg_cfg = load_telegram_config()
            cooldown = int(tg_cfg.get("cooldown_seconds", 120))
            token = (tg_cfg.get("bot_token") or "").strip()
            cid = (tg_cfg.get("chat_id") or "").strip()
            if tg_cfg.get("auto_notify", True) and token and cid and (now_ts - LAST_AUTO_TELEGRAM_TS >= cooldown):
                LAST_AUTO_TELEGRAM_TS = now_ts
                w_tag, w_beh, w_conf, w_note = triggered_warnings[0]
                auto_msg = (
                    f"🚨 <b>[BOVISION AUTO-ALERT - WARNING]</b>\n"
                    f"<b>Anomali Terdeteksi pada {w_tag} ({cam_id.upper()})</b>\n\n"
                    f"• Klasifikasi: <b>{w_beh.upper()}</b> ({w_conf})\n"
                    f"• Keterangan: {w_note}\n"
                    f"• Jumlah Sapi di Frame: <b>{total} Ekor</b> (Standing {s_pct}% | Lying {l_pct}%)\n"
                    f"🕒 <i>{now_str} WIB</i>"
                )
                use_snap = bool(tg_cfg.get("include_snapshot", True))

                def _bg_send():
                    try:
                        send_telegram_message_sync(token, cid, auto_msg, include_snapshot=use_snap, cam_id=cam_id)
                        save_telegram_config({"last_sent_at": f"{now_str} WIB (Auto)", "last_sent_status": "success"})
                    except Exception as ex:
                        print(f"Auto Telegram gagal: {ex}")

                threading.Thread(target=_bg_send, daemon=True).start()

    return {"status": "ok", "received_detections": total}

@app.get("/api/telemetry")
def get_live_telemetry():
    """Mengembalikan statistik real-time dari YOLO infer_rtsp.py untuk kartu & OSD dashboard."""
    is_fresh = (time.time() - LATEST_YOLO_TELEMETRY.get("last_update", 0.0)) < 6.0
    data = dict(LATEST_YOLO_TELEMETRY)
    data["online"] = is_fresh
    return data

@app.get("/api/snapshot/{cam_id}")
def get_camera_snapshot(cam_id: str, download: int = 0):
    """Mengambil 1 frame JPEG terbaru (1280x720) untuk fitur Capture Snapshot di dashboard."""
    yolo_data = LATEST_YOLO_FRAMES.get(cam_id) or LATEST_YOLO_FRAMES.get("cam-01")
    if yolo_data and (time.time() - yolo_data["timestamp"] < 10.0):
        frame_bytes = yolo_data["frame"]
    else:
        frame_bytes = generate_placeholder_frame(cam_id, "Snapshot Mode (Stream YOLO Belum Aktif)")
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    if download:
        fname = f"snapshot_{cam_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    return Response(content=frame_bytes, media_type="image/jpeg", headers=headers)

def generate_placeholder_frame(cam_id: str, message: str) -> bytes:
    """Membuat frame status resolusi native 1280x720 jika RTSP belum tersambung."""
    if PILLOW_AVAILABLE:
        img = Image.new('RGB', (1280, 720), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)
        
        # Grid & Border luar
        draw.rectangle([(15, 15), (1265, 705)], outline=(51, 65, 85), width=2)
        draw.line([(0, 480), (1280, 480)], fill=(30, 41, 59), width=2)
        
        # OSD Header
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB")
        draw.text((35, 35), f"[BOVISION EDGE AI] GATEWAY CCTV: {cam_id.upper()} (1280x720 16:9)", fill=(248, 113, 113))
        draw.text((35, 60), f"RTSP TARGET: {RTSP_CAMERAS.get(cam_id, 'Unknown')}", fill=(148, 163, 184))
        draw.text((1050, 35), now_str, fill=(226, 232, 240))
        
        # Status Box tengah
        draw.rectangle([(320, 250), (960, 470)], fill=(30, 41, 59), outline=(71, 85, 105), width=2)
        draw.text((350, 285), f"CAMERA FEED: {cam_id.upper()} (HD 720p)", fill=(255, 255, 255))
        draw.text((350, 325), f"STATUS: {message}", fill=(52, 211, 153))
        draw.text((350, 365), "Jalankan YOLO: python ai-cctv/infer_rtsp.py", fill=(251, 191, 36))
        draw.text((350, 405), "Stream video & bounding box akan tampil otomatis di sini.", fill=(148, 163, 184))
        
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        return buf.getvalue()
    else:
        return b""

async def generate_rtsp_stream(cam_id: str):
    """
    Generator stream MJPEG Async Low-Latency (1280x720):
    - Mengirim frame seketika saat frame baru masuk dari infer_rtsp.py (~25-30 FPS, <10ms latency).
    """
    last_sent_ts = 0.0
    while True:
        yolo_data = LATEST_YOLO_FRAMES.get(cam_id) or LATEST_YOLO_FRAMES.get("cam-01")
        if yolo_data and (time.time() - yolo_data["timestamp"] < 5.0):
            if yolo_data["timestamp"] != last_sent_ts:
                last_sent_ts = yolo_data["timestamp"]
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + yolo_data["frame"] + b'\r\n')
            await asyncio.sleep(0.01)
            continue

        msg = "Menunggu Push Frame dari infer_rtsp.py..."
        frame_bytes = generate_placeholder_frame(cam_id, msg)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        await asyncio.sleep(0.5)

@app.get("/api/stream/{cam_id}")
async def stream_camera(cam_id: str):
    """Endpoint streaming live video kamera RTSP dalam resolusi 1280x720 (MJPEG low-latency stream)."""
    if cam_id not in RTSP_CAMERAS:
        raise HTTPException(status_code=404, detail="Kamera tidak ditemukan.")
    
    return StreamingResponse(
        generate_rtsp_stream(cam_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )

@app.get("/api/stream-status")
def get_stream_status():
    """Mendapatkan status gateway streaming RTSP dan resolusi native."""
    return {
        "status": "online",
        "native_resolution": "1280x720 (16:9)",
        "opencv_installed": OPENCV_AVAILABLE,
        "cameras": {
            k: {
                "url": v,
                "stream_endpoint": f"/api/stream/{k}"
            }
            for k, v in RTSP_CAMERAS.items()
        }
    }

# ==========================================
# 7. Real-Time Alerts Engine & Integrasi Notifikasi Telegram
# ==========================================

TELEGRAM_CONFIG_FILE = os.path.join(DATA_DIR, "telegram_config.json")
LAST_AUTO_TELEGRAM_TS = 0.0

DEFAULT_TELEGRAM_CONFIG = {
    "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", "9002161660:AAFARa3HyZyQgH44cOntodc1orDdgxE2KSc"),
    "chat_id": os.getenv("TELEGRAM_CHAT_ID", "6246927036"),
    "auto_notify": True,
    "include_snapshot": True,
    "cooldown_seconds": 120,
    "last_sent_at": "Belum pernah dikirim",
    "last_sent_status": "idle",
}

def load_telegram_config() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    cfg = dict(DEFAULT_TELEGRAM_CONFIG)
    if os.path.exists(TELEGRAM_CONFIG_FILE):
        try:
            with open(TELEGRAM_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    cfg.update(saved)
        except Exception as e:
            print(f"Gagal membaca telegram_config.json: {e}")
    if not cfg.get("bot_token") and os.getenv("TELEGRAM_BOT_TOKEN"):
        cfg["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
    if not cfg.get("chat_id") and os.getenv("TELEGRAM_CHAT_ID"):
        cfg["chat_id"] = os.getenv("TELEGRAM_CHAT_ID")
    return cfg

def save_telegram_config(cfg: dict) -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    merged = load_telegram_config()
    merged.update(cfg)
    with open(TELEGRAM_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    return merged

def mask_token(token: str) -> str:
    t = (token or "").strip()
    if len(t) <= 10:
        return "••••••••" if t else ""
    return f"{t[:6]}••••••••{t[-4:]}"

def build_realtime_alerts() -> dict:
    """Menyusun daftar peringatan & notifikasi real-time berdasarkan data CSV tercatat dan telemetri live YOLO."""
    st = get_realtime_stats()
    all_logs = read_csv_logs(limit=0)  # Urutan terbaru di atas
    is_live = st.get("online", False)
    total_logs = st.get("total_logs", 0)

    alerts = []

    # 1. Alert dari baris log berstatus Warning / Critical di CSV
    warning_rows = [r for r in all_logs if (r.get("status") or "Normal").lower() != "normal"]
    for idx, r in enumerate(warning_rows[:6]):
        ts = r.get("timestamp") or r.get("time") or "-"
        cam = r.get("camera_id") or "CAM-01"
        tag = r.get("tag_number") or "COW-??"
        beh = (r.get("behavior") or "-").replace("_", " ").title()
        conf = r.get("confidence") or "-"
        note = r.get("notes") or "Terdeteksi anomali perilaku sapi"
        is_crit = "inaktivitas" in note.lower() or "ekstrem" in note.lower() or (r.get("status") or "").lower() == "critical"

        alerts.append({
            "id": f"log-warn-{idx}-{tag}",
            "level": "critical" if is_crit else "warning",
            "title": f"Peringatan Anomali Individu: {tag} ({beh})",
            "message": f"{note}. Terdeteksi pada sektor {cam} dengan tingkat akurasi AI {conf}.",
            "time_label": f"{ts} WIB • {cam} • Tag {tag}",
            "camera_id": cam,
            "tag_number": tag,
            "badge": "CRITICAL" if is_crit else "WARNING",
        })

    # 2. Alert Analisis Proporsi Kawanan Real-Time (Standing vs Lying)
    if total_logs > 0:
        s_pct = st.get("standing_pct", 0)
        l_pct = st.get("lying_pct", 0)
        s_cnt = st.get("standing_count", 0)
        l_cnt = st.get("lying_count", 0)
        s_hrs = st.get("standing_hours_est", 0)
        l_hrs = st.get("lying_hours_est", 0)
        latest_date = st.get("latest_date", "-")

        if s_pct >= 85:
            alerts.append({
                "id": "herd-high-standing",
                "level": "warning",
                "title": f"Dominansi Berdiri Tinggi ({s_pct}%) • Minim Istirahat Ruminasi",
                "message": (
                    f"Dari {total_logs} log deteksi tercatat ({s_cnt} aktif berdiri/makan vs {l_cnt} berbaring), "
                    f"proporsi berdiri mencapai {s_pct}% (~{s_hrs} jam/hari). Minimnya aktivitas berbaring dapat mengindikasikan "
                    f"alas kandang kurang nyaman, kepadatan area pakan, atau suhu kandang tinggi."
                ),
                "time_label": f"Sesi {latest_date} • Analisis Agregat Kawanan ({total_logs} sampel)",
                "camera_id": "CAM-01",
                "tag_number": "KAWANAN",
                "badge": f"STANDING {s_pct}%",
            })
        elif l_pct >= 75:
            alerts.append({
                "id": "herd-high-lying",
                "level": "critical",
                "title": f"Peringatan Inaktivitas Kawanan Tinggi (Lying {l_pct}%)",
                "message": (
                    f"Proporsi sapi berbaring mencapai {l_pct}% ({l_cnt} dari {total_logs} deteksi, ~{l_hrs} jam/hari). "
                    f"Segera periksa kondisi fisik kawanan terhadap gejala kelelahan ekstrem, pincang (lameness), atau gangguan kesehatan."
                ),
                "time_label": f"Sesi {latest_date} • Analisis Agregat Kawanan",
                "camera_id": "CAM-01",
                "tag_number": "KAWANAN",
                "badge": f"LYING {l_pct}%",
            })
        else:
            alerts.append({
                "id": "herd-balance-optimal",
                "level": "info",
                "title": "Keseimbangan Aktivitas & Istirahat Kawanan Optimal",
                "message": (
                    f"Proporsi Standing {s_pct}% (~{s_hrs} jam/hari) dan Lying {l_pct}% (~{l_hrs} jam/hari) "
                    f"dari {total_logs} log deteksi berada pada batas indeks kesejahteraan ternak sehat."
                ),
                "time_label": f"Sesi {latest_date} • BOVISION Welfare Index",
                "camera_id": "CAM-01",
                "tag_number": "KAWANAN",
                "badge": "OPTIMAL",
            })

    # 3. Alert Evaluasi Confidence Rendah / Oklusi Visual Kamera per Tag Sapi
    cow_summary = st.get("cow_summary") or []
    low_conf_cows = []
    for c in cow_summary:
        c_val = parse_conf_float(c.get("avg_confidence", "100%"))
        if 0 < c_val < 62.0 and c.get("total_detections", 0) >= 3:
            low_conf_cows.append(f"{c['tag_number']} ({c['avg_confidence']}, {c['total_detections']} sampel)")

    if low_conf_cows:
        alerts.append({
            "id": "low-confidence-occlusion",
            "level": "warning",
            "title": f"Deteksi Akurasi Visual Rendah / Oklusi pada {len(low_conf_cows)} Tag Sapi",
            "message": (
                f"Individu sapi berikut memiliki rata-rata confidence di bawah 62%: {', '.join(low_conf_cows[:5])}. "
                f"Disarankan memeriksa pencahayaan kandang, sudut pandang kamera, atau potensi tumpang-tindih (oklusi) antar sapi."
            ),
            "time_label": f"Kalibrasi YOLOv11 • Rata-rata Global {st.get('avg_confidence', '-')}",
            "camera_id": "CAM-01",
            "tag_number": low_conf_cows[0].split(" ")[0],
            "badge": "LOW CONFIDENCE",
        })

    # 4. Alert Aktivitas Sektor Pakan (Foraging & Drinking)
    b_counts = st.get("behavior_counts") or {}
    forage_cnt = b_counts.get("foraging", 0)
    drink_cnt = b_counts.get("drinking water", 0) + b_counts.get("drinking_water", 0)
    if forage_cnt > 0 or drink_cnt > 0:
        foraging_cows = [c["tag_number"] for c in cow_summary if "foraging" in (c.get("behavior_breakdown") or "").lower()]
        alerts.append({
            "id": "feeding-activity-summary",
            "level": "info",
            "title": f"Pemantauan Aktivitas Makan (Foraging): {forage_cnt} Kejadian Tercatat",
            "message": (
                f"Sistem mencatat {forage_cnt} deteksi aktivitas makan di area palung pakan"
                + (f" dan {drink_cnt} aktivitas minum" if drink_cnt else "")
                + (f" yang didominasi oleh {', '.join(foraging_cows[:5])}." if foraging_cows else ".")
            ),
            "time_label": f"Sektor Pakan Kandang A • {st.get('unique_cows_latest_day', 0)} Ekor Teridentifikasi",
            "camera_id": "CAM-01",
            "tag_number": foraging_cows[0] if foraging_cows else "KAWANAN",
            "badge": "FORAGING",
        })

    # 5. Status Koneksi Live AI Pipeline
    if is_live:
        fps_live = LATEST_YOLO_TELEMETRY.get("fps", 0.0)
        act_cnt = LATEST_YOLO_TELEMETRY.get("active_count", 0)
        alerts.append({
            "id": "pipeline-live-status",
            "level": "info",
            "title": f"Gateway RTSP & YOLO Inference Aktif ({fps_live} FPS)",
            "message": f"Menerima stream real-time dari infer_rtsp.py dengan {act_cnt} objek sapi terdeteksi pada frame saat ini.",
            "time_label": "Real-Time Stream • Latency <15ms",
            "camera_id": (LATEST_YOLO_TELEMETRY.get("cam_id") or "CAM-01").upper(),
            "tag_number": "SYSTEM",
            "badge": "LIVE ONLINE",
        })
    else:
        alerts.append({
            "id": "pipeline-standby-status",
            "level": "info",
            "title": "Stream RTSP Live Standby • Menampilkan Data Historis Tercatat",
            "message": (
                f"Worker infer_rtsp.py sedang tidak mengirim stream aktif. Seluruh statistik dan alert dihitung "
                f"secara real-time dari {total_logs} rekaman deteksi terakhir di database CSV ({st.get('latest_date', '-')})."
            ),
            "time_label": f"Update Terakhir: {all_logs[0].get('timestamp', '-') if all_logs else '-'} WIB",
            "camera_id": "CAM-01",
            "tag_number": "SYSTEM",
            "badge": "CSV SYNCED",
        })

    c_cnt = sum(1 for a in alerts if a["level"] == "critical")
    w_cnt = sum(1 for a in alerts if a["level"] == "warning")
    i_cnt = sum(1 for a in alerts if a["level"] == "info")

    tg_cfg = load_telegram_config()
    tg_configured = bool((tg_cfg.get("bot_token") or "").strip() and (tg_cfg.get("chat_id") or "").strip())

    return {
        "online": is_live,
        "counts": {
            "critical": c_cnt,
            "warning": w_cnt,
            "info": i_cnt,
            "total": len(alerts),
        },
        "alerts": alerts,
        "telegram": {
            "configured": tg_configured,
            "bot_token_masked": mask_token(tg_cfg.get("bot_token", "")),
            "has_token": bool((tg_cfg.get("bot_token") or "").strip()),
            "chat_id": tg_cfg.get("chat_id", ""),
            "auto_notify": bool(tg_cfg.get("auto_notify", True)),
            "include_snapshot": bool(tg_cfg.get("include_snapshot", True)),
            "last_sent_at": tg_cfg.get("last_sent_at", "Belum pernah dikirim"),
            "last_sent_status": tg_cfg.get("last_sent_status", "idle"),
        },
    }

def send_telegram_message_sync(bot_token: str, chat_id: str, html_text: str, include_snapshot: bool = True, cam_id: str = "cam-01") -> dict:
    """Mengirim pesan HTML (dan foto snapshot CCTV 1280x720 jika diminta) ke Telegram Bot API."""
    token = (bot_token or "").strip()
    cid = (chat_id or "").strip()
    if not token or not cid:
        raise ValueError("Bot Token dan Chat ID Telegram wajib diisi terlebih dahulu.")

    # Jika include_snapshot aktif, kirim melalui endpoint sendPhoto dengan multipart/form-data
    if include_snapshot:
        yolo_data = LATEST_YOLO_FRAMES.get(cam_id) or LATEST_YOLO_FRAMES.get("cam-01")
        if yolo_data and (time.time() - yolo_data["timestamp"] < 30.0):
            frame_bytes = yolo_data["frame"]
        else:
            frame_bytes = generate_placeholder_frame(cam_id, "Laporan Monitoring & Alert Real-Time BOVISION")

        if frame_bytes:
            boundary = f"----BovisionTelegramBoundary{int(time.time() * 1000)}"
            body_parts = []

            def add_field(name: str, value: str):
                body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
                body_parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
                body_parts.append(f"{value}\r\n".encode("utf-8"))

            add_field("chat_id", cid)
            add_field("caption", html_text[:1020])
            add_field("parse_mode", "HTML")

            body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
            body_parts.append(b'Content-Disposition: form-data; name="photo"; filename="bovision_snapshot.jpg"\r\n')
            body_parts.append(b"Content-Type: image/jpeg\r\n\r\n")
            body_parts.append(frame_bytes)
            body_parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

            payload_bytes = b"".join(body_parts)
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            req = urllib.request.Request(
                url,
                data=payload_bytes,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))

    # Fallback atau mode teks saja: gunakan sendMessage
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": cid,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

class TelegramConfigPayload(BaseModel):
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    auto_notify: Optional[bool] = True
    include_snapshot: Optional[bool] = True

class TelegramSendPayload(BaseModel):
    alert_id: Optional[str] = None
    title: Optional[str] = None
    message: Optional[str] = None
    level: Optional[str] = "warning"
    camera_id: Optional[str] = "cam-01"
    include_snapshot: Optional[bool] = None
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None

class TelegramDiscoverPayload(BaseModel):
    bot_token: Optional[str] = None

@app.get("/api/alerts")
def get_alerts():
    """Mendapatkan daftar alert real-time dari data deteksi & status integrasi Telegram."""
    return build_realtime_alerts()

@app.get("/api/telegram/config")
def get_telegram_config_endpoint():
    cfg = load_telegram_config()
    return {
        "configured": bool((cfg.get("bot_token") or "").strip() and (cfg.get("chat_id") or "").strip()),
        "has_token": bool((cfg.get("bot_token") or "").strip()),
        "bot_token_masked": mask_token(cfg.get("bot_token", "")),
        "chat_id": cfg.get("chat_id", ""),
        "auto_notify": bool(cfg.get("auto_notify", True)),
        "include_snapshot": bool(cfg.get("include_snapshot", True)),
        "last_sent_at": cfg.get("last_sent_at", "Belum pernah dikirim"),
        "last_sent_status": cfg.get("last_sent_status", "idle"),
    }

@app.post("/api/telegram/config")
def update_telegram_config_endpoint(payload: TelegramConfigPayload):
    current = load_telegram_config()
    updates = {
        "auto_notify": bool(payload.auto_notify),
        "include_snapshot": bool(payload.include_snapshot),
    }
    if payload.bot_token is not None and payload.bot_token.strip() and "•" not in payload.bot_token:
        updates["bot_token"] = payload.bot_token.strip()
    if payload.chat_id is not None:
        updates["chat_id"] = payload.chat_id.strip()

    saved = save_telegram_config(updates)
    return {
        "status": "success",
        "message": "Konfigurasi Telegram Bot berhasil disimpan.",
        "configured": bool((saved.get("bot_token") or "").strip() and (saved.get("chat_id") or "").strip()),
        "bot_token_masked": mask_token(saved.get("bot_token", "")),
        "chat_id": saved.get("chat_id", ""),
        "auto_notify": bool(saved.get("auto_notify", True)),
        "include_snapshot": bool(saved.get("include_snapshot", True)),
    }

def auto_discover_chat_id_from_telegram(token: str) -> tuple[str, str]:
    """Mencari Chat ID terbaru dari getUpdates Telegram API jika chat_id belum diisi."""
    url = f"https://api.telegram.org/bot{token}/getUpdates?limit=10"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    results = data.get("result") or []
    for upd in reversed(results):
        msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member", {}).get("chat")
        chat = msg.get("chat") if isinstance(msg, dict) and "chat" in msg else msg
        if isinstance(chat, dict) and chat.get("id"):
            cid = str(chat["id"])
            title = chat.get("title") or f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip() or chat.get("username") or "Chat Telegram"
            save_telegram_config({"bot_token": token, "chat_id": cid})
            return cid, title
    return "", ""

@app.post("/api/telegram/discover-chat-id")
def discover_telegram_chat_id(payload: TelegramDiscoverPayload):
    """Mendeteksi Chat ID otomatis dari pesan terbaru yang masuk ke Bot Telegram via getUpdates."""
    cfg = load_telegram_config()
    token = (payload.bot_token or "").strip()
    if not token or "•" in token:
        token = (cfg.get("bot_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Masukkan Bot Token Telegram terlebih dahulu.")

    try:
        cid, title = auto_discover_chat_id_from_telegram(token)
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"Token Bot ditolak oleh Telegram (HTTP {e.code}). Periksa kembali Bot Token dari @BotFather.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal menghubungi server Telegram: {e}")

    if cid:
        return {
            "status": "success",
            "chat_id": cid,
            "chat_title": title,
            "message": f"Chat ID berhasil ditemukan: {cid} ({title})",
        }

    raise HTTPException(
        status_code=404,
        detail="Belum ada pesan masuk ke Bot Anda. Silakan buka aplikasi Telegram, cari bot Anda, lalu kirim pesan '/start' atau 'tes', kemudian klik tombol ini lagi."
    )

@app.post("/api/telegram/send")
def send_telegram_alert_endpoint(payload: TelegramSendPayload):
    """Mengirim laporan alert real-time atau alert spesifik ke Telegram (beserta snapshot CCTV)."""
    cfg = load_telegram_config()
    token = (payload.bot_token or "").strip()
    if not token or "•" in token:
        token = (cfg.get("bot_token") or "").strip()
    cid = (payload.chat_id or "").strip() or (cfg.get("chat_id") or "").strip()

    if token and not cid:
        try:
            cid, _ = auto_discover_chat_id_from_telegram(token)
        except Exception:
            pass

    if not token or not cid:
        raise HTTPException(
            status_code=400,
            detail="Chat ID belum ditemukan. Silakan buka bot Anda di aplikasi Telegram, klik Start / kirim pesan '/start', lalu klik tombol 'Deteksi ID' atau 'Kirim Laporan' lagi."
        )

    # Simpan jika user baru saja mengetik token/chat_id langsung lalu klik Kirim
    if (payload.bot_token and "•" not in payload.bot_token) or payload.chat_id:
        save_telegram_config({"bot_token": token, "chat_id": cid})

    st = get_realtime_stats()
    alerts_data = build_realtime_alerts()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB")

    if payload.title and payload.message:
        lvl_emoji = "🚨" if payload.level == "critical" else ("⚠️" if payload.level == "warning" else "ℹ️")
        html_msg = (
            f"{lvl_emoji} <b>[BOVISION AI ALERT - {payload.level.upper()}]</b>\n"
            f"<b>{payload.title}</b>\n\n"
            f"{payload.message}\n\n"
            f"📊 <b>Statistik Kawanan Saat Ini:</b>\n"
            f"• Sapi Teridentifikasi: <b>{st.get('unique_cows_latest_day', 0)} Ekor</b> ({st.get('total_logs', 0)} log)\n"
            f"• Dominansi Berdiri: <b>{st.get('standing_pct', 0)}%</b> (~{st.get('standing_hours_est', 0)} jam/hari)\n"
            f"• Dominansi Berbaring: <b>{st.get('lying_pct', 0)}%</b> (~{st.get('lying_hours_est', 0)} jam/hari)\n"
            f"• Rata-rata Confidence AI: <b>{st.get('avg_confidence', '-')}</b>\n"
            f"🕒 <i>{now_str}</i>"
        )
    else:
        top_alerts = alerts_data.get("alerts", [])[:4]
        bullet_lines = []
        for a in top_alerts:
            em = "🚨" if a["level"] == "critical" else ("⚠️" if a["level"] == "warning" else "ℹ️")
            bullet_lines.append(f"{em} <b>{a['title']}</b>\n   {a['message']}")
        alerts_block = "\n\n".join(bullet_lines) if bullet_lines else "✅ Seluruh indikator kandang normal."

        html_msg = (
            f"🐄 <b>LAPORAN REAL-TIME BOVISION COW MONITORING</b>\n"
            f"🕒 Waktu: <b>{now_str}</b>\n"
            f"📡 Status Stream: <b>{'LIVE ONLINE' if st.get('online') else 'STANDBY (CSV Synced)'}</b>\n\n"
            f"📊 <b>Ringkasan Statistik Tercatat:</b>\n"
            f"• Sapi Teridentifikasi: <b>{st.get('unique_cows_total', 0)} ID Unik</b> ({st.get('total_logs', 0)} sampel log)\n"
            f"• Dominansi Berdiri (Standing): <b>{st.get('standing_pct', 0)}%</b> (~{st.get('standing_hours_est', 0)} jam/hari)\n"
            f"• Dominansi Berbaring (Lying): <b>{st.get('lying_pct', 0)}%</b> (~{st.get('lying_hours_est', 0)} jam/hari)\n"
            f"• Rata-rata Confidence AI: <b>{st.get('avg_confidence', '-')}</b>\n\n"
            f"🔔 <b>Daftar Alert &amp; Peringatan Aktif ({alerts_data['counts']['total']}):</b>\n"
            f"{alerts_block}"
        )

    use_snap = payload.include_snapshot if payload.include_snapshot is not None else bool(cfg.get("include_snapshot", True))
    cam_target = (payload.camera_id or "cam-01").lower()

    try:
        tg_res = send_telegram_message_sync(
            bot_token=token,
            chat_id=cid,
            html_text=html_msg,
            include_snapshot=use_snap,
            cam_id=cam_target,
        )
        save_telegram_config({
            "last_sent_at": now_str,
            "last_sent_status": "success",
        })
        return {
            "status": "success",
            "message": f"Notifikasi berhasil dikirim ke Telegram (Chat ID: {cid})!",
            "sent_at": now_str,
            "telegram_ok": tg_res.get("ok", True),
        }
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            pass
        save_telegram_config({"last_sent_status": f"error: HTTP {e.code}"})
        raise HTTPException(
            status_code=400,
            detail=f"Telegram API menolak permintaan (HTTP {e.code}): {err_body or 'Periksa Bot Token & Chat ID Anda.'}"
        )
    except Exception as e:
        save_telegram_config({"last_sent_status": f"error: {e}"})
        raise HTTPException(status_code=500, detail=f"Gagal mengirim pesan ke Telegram: {e}")

# ==========================================
# 8. Interactive Telegram Bot Responder (/start, /status, /alert, /snapshot)
# ==========================================

_LAST_TG_UPDATE_ID = 0

def process_telegram_bot_updates_once() -> list:
    """Memeriksa pesan masuk terbaru di Bot Telegram dan membalas perintah (/start, /status, /alert, /snapshot) secara otomatis."""
    global _LAST_TG_UPDATE_ID
    cfg = load_telegram_config()
    token = (cfg.get("bot_token") or "").strip()
    if not token:
        return []

    offset_param = f"&offset={_LAST_TG_UPDATE_ID + 1}" if _LAST_TG_UPDATE_ID > 0 else ""
    url = f"https://api.telegram.org/bot{token}/getUpdates?limit=15&timeout=1{offset_param}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = data.get("result") or []
    replied = []
    last_stored_id = int(cfg.get("last_update_id", 0) or 0)

    for upd in results:
        uid = int(upd.get("update_id", 0))
        if uid > _LAST_TG_UPDATE_ID:
            _LAST_TG_UPDATE_ID = uid

        msg = upd.get("message") or upd.get("channel_post")
        if not isinstance(msg, dict):
            continue
        chat = msg.get("chat") or {}
        cid = str(chat.get("id") or "")
        if not cid:
            continue

        # Selalu simpan Chat ID terbaru jika belum tersimpan
        chat_title = chat.get("title") or f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip() or chat.get("username") or "User"
        if not cfg.get("chat_id"):
            save_telegram_config({"chat_id": cid})
            cfg["chat_id"] = cid

        if uid <= last_stored_id:
            continue

        text_raw = (msg.get("text") or "").strip()
        cmd = text_raw.split()[0].lower().split("@")[0] if text_raw else ""

        st = get_realtime_stats()
        alerts_data = build_realtime_alerts()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB")

        if cmd in ("/start", "start", "halo", "tes", "test", "/help"):
            reply_html = (
                f"🐄 <b>Halo {chat_title}! Selamat datang di BOVISION Cow Alert Bot.</b>\n\n"
                f"✅ <b>Chat ID Anda ({cid}) telah otomatis terhubung</b> ke sistem monitoring kandang BOVISION.\n\n"
                f"📊 <b>Status Kandang Saat Ini ({now_str}):</b>\n"
                f"• Sapi Teridentifikasi: <b>{st.get('unique_cows_total', 0)} Ekor Unik</b> ({st.get('total_logs', 0)} log deteksi)\n"
                f"• Dominansi Berdiri (Standing): <b>{st.get('standing_pct', 0)}%</b> (~{st.get('standing_hours_est', 0)} jam/hari)\n"
                f"• Dominansi Berbaring (Lying): <b>{st.get('lying_pct', 0)}%</b> (~{st.get('lying_hours_est', 0)} jam/hari)\n"
                f"• Rata-rata Confidence AI: <b>{st.get('avg_confidence', '-')}</b>\n"
                f"• Alert Aktif: <b>{alerts_data['counts']['critical']} Kritis, {alerts_data['counts']['warning']} Waspada</b>\n\n"
                f"💡 <b>Perintah Bot yang Tersedia:</b>\n"
                f"• /status — Lihat laporan statistik &amp; foto snapshot CCTV\n"
                f"• /alert — Lihat daftar peringatan anomali terkini\n"
                f"• /snapshot — Ambil foto tangkapan layar CCTV terbaru"
            )
            send_telegram_message_sync(token, cid, reply_html, include_snapshot=True, cam_id="cam-01")
            replied.append((uid, cid, cmd or text_raw))
        elif cmd in ("/status", "/laporan", "/snapshot", "/alert"):
            top_alerts = alerts_data.get("alerts", [])[:4]
            bullet_lines = [
                f"{'🚨' if a['level']=='critical' else ('⚠️' if a['level']=='warning' else 'ℹ️')} <b>{a['title']}</b>\n   {a['message']}"
                for a in top_alerts
            ]
            alerts_block = "\n\n".join(bullet_lines) if bullet_lines else "✅ Seluruh indikator kandang normal."
            reply_html = (
                f"🐄 <b>LAPORAN REAL-TIME BOVISION COW MONITORING</b>\n"
                f"🕒 Waktu: <b>{now_str}</b>\n\n"
                f"📊 <b>Statistik Tercatat:</b>\n"
                f"• Sapi Teridentifikasi: <b>{st.get('unique_cows_total', 0)} Ekor</b> ({st.get('total_logs', 0)} log)\n"
                f"• Dominansi Berdiri: <b>{st.get('standing_pct', 0)}%</b> | Berbaring: <b>{st.get('lying_pct', 0)}%</b>\n"
                f"• Rata-rata Confidence AI: <b>{st.get('avg_confidence', '-')}</b>\n\n"
                f"🔔 <b>Alert Aktif ({alerts_data['counts']['total']}):</b>\n{alerts_block}"
            )
            send_telegram_message_sync(token, cid, reply_html, include_snapshot=True, cam_id="cam-01")
            replied.append((uid, cid, cmd))

        save_telegram_config({
            "chat_id": cid,
            "last_update_id": uid,
            "last_sent_at": now_str,
            "last_sent_status": "success",
        })
        last_stored_id = uid

    return replied

def _telegram_bot_polling_worker():
    """Background daemon yang otomatis membalas perintah /start, /status, /alert di Telegram."""
    while True:
        try:
            process_telegram_bot_updates_once()
        except Exception:
            pass
        time.sleep(3.5)

threading.Thread(target=_telegram_bot_polling_worker, daemon=True).start()

@app.get("/health")
def health_check():
    """Endpoint pengecekan status hidup server untuk monitoring VPS/reverse proxy."""
    return {"status": "ok", "service": "bovision-backend", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)