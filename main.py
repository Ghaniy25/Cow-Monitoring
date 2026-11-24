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
from datetime import datetime

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
    return logs[:limit]

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
    global LAST_CSV_WRITE_TIME, LATEST_YOLO_TELEMETRY
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
        try:
            with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                for d in dets:
                    beh_raw = d.get("behavior", "stand")
                    beh_lower = beh_raw.lower().strip()
                    note = BEHAVIOR_NOTES_MAP.get(beh_lower, f"Deteksi otomatis YOLO ({beh_raw})")
                    writer.writerow([
                        now_str,
                        cam_id.upper(),
                        d.get("tag_number", "COW-??"),
                        beh_raw,
                        d.get("confidence", "90.0%"),
                        "Normal",
                        note
                    ])
        except Exception as e:
            print(f"Gagal menulis log YOLO ke CSV: {e}")

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